"""
The Python client against a real local server, without waiting for its timers.
"""

import json
import os
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "clients", "python"))

import telemetry  # noqa: E402


class Receiver:
	"""A local HTTP server that records what it receives."""

	def __init__(self, answer=None, status=200):
		self.received = []
		receiver = self

		class Handler(BaseHTTPRequestHandler):
			def do_POST(self):
				length = int(self.headers["Content-Length"])
				receiver.received.append(json.loads(self.rfile.read(length)))
				self.send_response(status)
				self.send_header("Content-Type", "application/json")
				self.end_headers()
				self.wfile.write(json.dumps(answer or {"enabled": True, "next_ping_h": 24}).encode())

			def log_message(self, *args):
				pass

		self.server = HTTPServer(("127.0.0.1", 0), Handler)
		self.url = f"http://127.0.0.1:{self.server.server_port}/v1/ping"
		threading.Thread(target=self.server.serve_forever, daemon=True).start()

	def close(self):
		self.server.shutdown()


@pytest.fixture
def receiver():
	server = Receiver()
	yield server
	server.close()


def make(tmp_path, url, enabled=True, metrics=None):
	flag = {"on": enabled}
	client = telemetry.Telemetry(
		project="docker-controller-bot", version="5.0.0",
		state_path=str(tmp_path / "state" / "telemetry.json"),
		metrics=metrics or (lambda: {"hosts": 2}),
		enabled=lambda: flag["on"], endpoint=url)
	# As if the start delay had already passed.
	client._started_at -= telemetry.START_DELAY_SECONDS + 1
	return client, flag


def test_a_send_carries_the_counters_and_takes_them_off(tmp_path, receiver):
	client, _ = make(tmp_path, receiver.url)
	client.count("cmd_list")
	client.count("cmd_list")
	client.count("btn_update", 3)
	assert client._tick() is True

	sent = receiver.received[0]
	assert sent["schema"] == 1 and sent["project"] == "docker-controller-bot"
	assert uuid.UUID(sent["install_id"])
	assert sent["usage"] == {"cmd_list": 2, "btn_update": 3}
	assert sent["metrics"] == {"hosts": 2}
	assert client.preview()["usage"] == {}


def test_it_sends_once_per_interval(tmp_path, receiver):
	client, _ = make(tmp_path, receiver.url)
	now = time.time()
	assert client._tick(now) is True
	assert client._tick(now + 3600) is False
	client._last_attempt = 0
	assert client._tick(now + 24 * 3600 + 1) is True
	assert len(receiver.received) == 2
	assert receiver.received[0]["install_id"] == receiver.received[1]["install_id"]


def test_disabled_means_no_counting_no_id_and_no_file(tmp_path, receiver):
	client, _ = make(tmp_path, receiver.url, enabled=False)
	client.count("cmd_list")
	assert client._tick() is False
	assert receiver.received == []
	assert client.preview()["usage"] == {} and client.preview()["install_id"] is None
	assert not os.path.exists(tmp_path / "state" / "telemetry.json")


def test_telemetry_false_wins_over_enabled(tmp_path, receiver, monkeypatch):
	monkeypatch.setenv("TELEMETRY", "false")
	client, _ = make(tmp_path, receiver.url, enabled=True)
	client.count("cmd_list")
	assert client._tick() is False and receiver.received == []


def test_nothing_is_sent_before_the_start_delay(tmp_path, receiver):
	client, _ = make(tmp_path, receiver.url)
	client._started_at = time.monotonic()
	assert client._tick() is False and receiver.received == []


def test_a_failed_send_keeps_the_counters_and_waits_to_retry(tmp_path):
	client, _ = make(tmp_path, "http://127.0.0.1:9/v1/ping")
	client.count("cmd_list")
	now = time.time()
	assert client._tick(now) is False
	assert client.preview()["usage"] == {"cmd_list": 1}
	# Not retried on the very next wake-up.
	assert client._last_attempt == now


def test_state_survives_a_restart(tmp_path, receiver):
	client, _ = make(tmp_path, receiver.url)
	client.count("cmd_logs", 4)
	client._flush(force=True)
	again, _ = make(tmp_path, receiver.url)
	assert again.preview()["usage"] == {"cmd_logs": 4}


def test_forget_starts_over_as_a_new_installation(tmp_path, receiver):
	client, _ = make(tmp_path, receiver.url)
	client._tick()
	first = receiver.received[0]["install_id"]
	client.count("cmd_list")
	client.forget()
	assert client.preview()["install_id"] is None and client.preview()["usage"] == {}
	client._last_attempt = 0
	client._tick()
	assert receiver.received[1]["install_id"] != first


def test_the_server_can_pause_a_project(tmp_path):
	server = Receiver(answer={"enabled": False, "next_ping_h": 48})
	try:
		client, _ = make(tmp_path, server.url)
		now = time.time()
		assert client._tick(now) is True
		client._last_attempt = 0
		# Neither the interval nor the pause has run out.
		assert client._tick(now + 47 * 3600) is False
		client._last_attempt = 0
		assert client._tick(now + 48 * 3600 + 1) is True
	finally:
		server.close()


def test_a_broken_metrics_function_does_not_stop_the_send(tmp_path, receiver):
	def broken():
		raise RuntimeError("docker is down")

	client, _ = make(tmp_path, receiver.url, metrics=broken)
	assert client._tick() is True
	assert receiver.received[0]["metrics"] == {}


def test_architectures_are_normalised(monkeypatch):
	monkeypatch.setattr(telemetry.platform, "machine", lambda: "aarch64")
	assert telemetry.architecture() == "arm64"
	monkeypatch.setattr(telemetry.platform, "machine", lambda: "x86_64")
	assert telemetry.architecture() == "amd64"


def test_debug_sends_on_every_start_after_a_minute(tmp_path, receiver):
	client, _ = make(tmp_path, receiver.url)
	assert client._tick() is True
	# A restart a moment later, in debug: it sends again without waiting a day.
	again = telemetry.Telemetry(project="docker-controller-bot", version="5.0.0",
								state_path=str(tmp_path / "state" / "telemetry.json"),
								endpoint=receiver.url, debug=True)
	assert again._tick() is False, "espera el minuto de arranque"
	again._started_at -= telemetry.DEBUG_WAIT_SECONDS + 1
	assert again._tick() is True
	again._last_attempt = 0
	assert again._tick() is False, "una vez por arranque, no en cada vuelta"
	assert len(receiver.received) == 2


@pytest.mark.parametrize("value, off", [
	("false", True), ("0", True), ("nope", True), ("1", True),
	("true", False), ("TRUE", False), ("", False),
])
def test_anything_but_true_turns_it_off(monkeypatch, value, off):
	monkeypatch.setenv("TELEMETRY", value)
	assert telemetry.disabled_by_environment() is off
