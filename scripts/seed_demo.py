#!/usr/bin/env python3
"""
Fills a database with made-up installations, to work on the dashboard.

	DATA_DIR=./demo-data python3 scripts/seed_demo.py
	DATA_DIR=./demo-data uvicorn --factory app:create_app --app-dir server

Never point it at the real data directory: it writes 90 days of fake pings.
"""

import os
import random
import sys
import time
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))

from db import Database  # noqa: E402

DATA_DIR = os.environ.get("DATA_DIR", os.path.join(ROOT, "demo-data"))
DAYS = 90
INSTALLS = 900

COMMANDS = ["list", "checkupdate", "restart", "logs", "updateall", "stop", "run", "settings",
			"schedule", "compose", "prune", "exec", "changetag", "ports", "mute", "logfile", "info"]
BUTTONS = ["confirmUpdate", "restart", "stop", "run", "logs", "cerrar", "startMenu", "settingsToggle",
			"updateSelected", "toggleUpdate", "enterComposeProject", "pickHost", "settingsHosts"]


def weighted(options):
	values, weights = zip(*options)
	return random.choices(values, weights)[0]


def main():
	if os.path.abspath(DATA_DIR) == os.path.abspath(os.path.join(ROOT, "data")):
		sys.exit("Refusing to seed the real data directory.")
	os.makedirs(DATA_DIR, exist_ok=True)
	path = os.path.join(DATA_DIR, "telemetry.db")
	if os.path.exists(path):
		os.remove(path)
	database = Database(path)
	now = time.time()
	random.seed(7)

	for _ in range(INSTALLS):
		install_id = str(uuid.uuid4())
		start = random.randint(0, DAYS + 60)
		stop = random.choice([0, 0, 0, random.randint(0, start)]) if start else 0
		hosts = weighted([(1, 58), (2, 22), (3, 11), (4, 4), (5, 2), (7, 3)])
		metrics = {
			"hosts": hosts,
			"hosts_paused": weighted([(0, 85), (1, 15)]) if hosts > 1 else 0,
			"hosts_ssh": random.randint(0, hosts - 1) if hosts > 1 and random.random() < 0.6 else 0,
			"hosts_tcp": random.randint(0, hosts - 1) if hosts > 1 and random.random() < 0.3 else 0,
			"containers": weighted([("1-5", 14), ("6-10", 23), ("11-25", 35), ("26-50", 19), ("51-100", 7), ("101+", 2)]),
			"containers_auto_update": weighted([(0, 70), (1, 12), (3, 10), (8, 8)]),
			"containers_ignore_updates": weighted([(0, 80), (2, 20)]),
			"docker_major": weighted([(24, 5), (25, 10), (26, 20), (27, 45), (28, 20)]),
			"schedules": weighted([(0, 69), (1, 20), (3, 11)]),
			"admins": weighted([(1, 88), (2, 10), (3, 2)]),
			"telegram_group": random.random() < 0.18,
			"language": weighted([("es", 46), ("en", 31), ("de", 8), ("it", 5), ("cat", 3), ("gl", 2), ("nl", 3), ("ru", 2)]),
			"button_columns": weighted([(1, 10), (2, 70), (3, 18), (4, 2)]),
			"check_updates": random.random() < 0.88,
			"check_update_every_hours": weighted([(1.0, 8), (4.0, 60), (12.0, 17), (24.0, 15)]),
			"check_update_stopped_containers": random.random() < 0.34,
			"extended_messages": random.random() < 0.19,
			"multi_selection": random.random() < 0.72,
			"notification_channel": random.random() < 0.27,
			"legacy_volume": random.random() < 0.4,
		}
		arch = weighted([("amd64", 55), ("arm64", 38), ("armv7", 7)])
		activity = random.uniform(0.2, 1.0)
		for day in range(min(start, DAYS), stop, -1):
			if random.random() > 0.85:
				continue
			version = "5.0.0" if day < 20 and random.random() < 0.7 else weighted([("4.2.0", 60), ("4.1.3", 25), ("3.9.1", 15)])
			usage = {}
			for command in COMMANDS:
				if random.random() < activity * (0.9 if command == "list" else 0.25):
					usage[f"cmd_{command}"] = random.randint(1, 6)
			for button in BUTTONS:
				if random.random() < activity * 0.3:
					usage[f"btn_{button}"] = random.randint(1, 10)
			if metrics["check_updates"]:
				usage["auto_update_check"] = int(24 / metrics["check_update_every_hours"])
			if metrics["schedules"]:
				usage["sched_restart"] = metrics["schedules"]
			database.record({"project": "docker-controller-bot", "install_id": install_id, "version": version,
							"arch": arch, "metrics": metrics, "usage": usage},
							now=now - day * 86400 + random.randint(0, 3600))
	print(f"Seeded {path}")


if __name__ == "__main__":
	main()
