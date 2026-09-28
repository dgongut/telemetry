"""
The server end to end, through the HTTP API, on a temporary database.

	pip install -r server/requirements.txt pytest httpx
	pytest
"""

import json
import os
import sys
import uuid

import pytest
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))

from fastapi.testclient import TestClient  # noqa: E402

import db as db_module  # noqa: E402
import manifest  # noqa: E402
import summary  # noqa: E402
from app import RateLimiter, create_app  # noqa: E402

PROJECT = "docker-controller-bot"


@pytest.fixture
def client(tmp_path):
	app = create_app(data_dir=str(tmp_path), projects_dir=os.path.join(ROOT, "projects"),
					web_dir=os.path.join(ROOT, "web"), rate_limit=1000, cache_seconds=0)
	with TestClient(app) as test_client:
		yield test_client


def ping(**overrides):
	body = {
		"schema": 1,
		"project": PROJECT,
		"install_id": str(uuid.uuid4()),
		"version": "5.0.0",
		"arch": "arm64",
		"metrics": {"hosts": 2, "language": "es", "check_updates": True, "containers": "11-25"},
		"usage": {"cmd_list": 4, "btn_confirmUpdate": 1},
	}
	body.update(overrides)
	return body


def test_every_manifest_in_the_repo_loads():
	manifests = manifest.load_all(os.path.join(ROOT, "projects"))
	assert PROJECT in manifests
	for loaded in manifests.values():
		for metric in loaded["metrics"]:
			assert manifest.text(metric["description"], "es")
			assert manifest.text(metric["description"], "en")


def test_a_ping_is_stored_and_answered_with_the_interval(client):
	response = client.post("/v1/ping", json=ping())
	assert response.status_code == 200
	assert response.json() == {"enabled": True, "next_ping_h": 24}
	assert client.app.state.db.query("SELECT COUNT(*) AS n FROM installs")[0]["n"] == 1


def test_undeclared_and_invalid_metrics_are_dropped_one_by_one(client):
	body = ping(metrics={
		"hosts": 3,
		"container_names": ["plex", "sonarr"],  # not declared
		"language": "klingon",                  # not one of the values
		"check_updates": "yes",                 # not a bool
		"schedules": True,                      # a bool is not a number
		"admins": 1.5,                          # not an int
		"multi_selection": False,
	})
	install_id = body["install_id"]
	assert client.post("/v1/ping", json=body).status_code == 200
	stored = client.app.state.db.query("SELECT metrics FROM installs WHERE install_id = ?", (install_id,))
	assert json.loads(stored[0]["metrics"]) == {"hosts": 3, "multi_selection": False}


def test_usage_keys_are_checked_by_shape(client):
	body = ping(usage={
		"cmd_list": 2,
		"btn_settingsToggle": 1,
		"Has Spaces": 1,
		"plex.example.com": 1,
		"cmd_negative": -1,
		"cmd_bool": True,
		"cmd_huge": 10**9,
	})
	assert client.post("/v1/ping", json=body).status_code == 200
	stored = client.app.state.db.query("SELECT usage FROM daily WHERE install_id = ?", (body["install_id"],))
	assert json.loads(stored[0]["usage"]) == {"cmd_list": 2, "btn_settingsToggle": 1}


@pytest.mark.parametrize("change", [
	{"schema": 2},
	{"project": "nope"},
	{"install_id": "not-a-uuid"},
	{"version": ""},
	{"version": "5.0.0; DROP TABLE"},
])
def test_a_ping_without_a_valid_envelope_is_rejected(client, change):
	assert client.post("/v1/ping", json=ping(**change)).status_code == 400


def test_non_json_and_oversized_bodies_are_rejected(client):
	assert client.post("/v1/ping", content=b"nope").status_code == 400
	assert client.post("/v1/ping", content=b"{" + b" " * 40000 + b"}").status_code == 413


def test_two_pings_on_the_same_day_add_their_counters(client):
	first = ping(usage={"cmd_list": 2})
	client.post("/v1/ping", json=first)
	client.post("/v1/ping", json=dict(first, usage={"cmd_list": 3, "cmd_logs": 1}))
	rows = client.app.state.db.query("SELECT usage FROM daily WHERE install_id = ?", (first["install_id"],))
	assert len(rows) == 1
	assert json.loads(rows[0]["usage"]) == {"cmd_list": 5, "cmd_logs": 1}


def test_a_disabled_project_is_told_to_stop_and_nothing_is_kept(tmp_path):
	projects = tmp_path / "projects"
	projects.mkdir()
	(projects / "quiet.yaml").write_text(yaml.safe_dump({
		"name": "Quiet", "enabled": False, "interval_hours": 48,
		"usage": {"description": "counters"},
	}))
	app = create_app(data_dir=str(tmp_path / "data"), projects_dir=str(projects),
					web_dir=os.path.join(ROOT, "web"), rate_limit=1000, cache_seconds=0)
	with TestClient(app) as test_client:
		response = test_client.post("/v1/ping", json=ping(project="quiet", metrics={}))
		assert response.json() == {"enabled": False, "next_ping_h": 48}
		assert app.state.db.query("SELECT COUNT(*) AS n FROM installs")[0]["n"] == 0


def test_the_rate_limit_is_per_ip_and_hour():
	limiter = RateLimiter(2)
	assert limiter.allow("a", now=0) and limiter.allow("a", now=1)
	assert not limiter.allow("a", now=2)
	assert limiter.allow("b", now=2)
	assert limiter.allow("a", now=3601)


def test_the_summary_counts_and_shares(client):
	for index in range(4):
		client.post("/v1/ping", json=ping(
			version="5.0.0" if index < 3 else "4.2.0",
			metrics={"hosts": index + 1, "check_updates": index % 2 == 0, "language": "es" if index else "en"},
			usage={"cmd_list": 1} if index < 2 else {"cmd_logs": 5}))

	data = client.get(f"/v1/projects/{PROJECT}/summary?days=30&lang=es").json()
	assert data["kpis"]["active"] == 4
	assert data["kpis"]["new"] == 4
	assert data["kpis"]["latest_version"] == "5.0.0"
	assert data["kpis"]["latest_version_share"] == 0.75
	# The series ends yesterday, and nothing was sent before today.
	assert len(data["series"]["days"]) == 30
	assert data["series"]["daily"][-1] == 0

	metrics = {metric["key"]: metric for metric in data["metrics"]}
	assert metrics["hosts"]["kpi"] == {"kind": "sum", "value": 10, "mean": 2.5}
	assert [bucket["count"] for bucket in metrics["hosts"]["data"]] == [1, 1, 1, 1, 0]
	assert metrics["check_updates"]["share"] == 0.5
	assert metrics["check_updates"]["label"] == "Comprobar actualizaciones"
	language = {entry["value"]: entry["count"] for entry in metrics["language"]["data"]}
	assert language["es"] == 3 and language["en"] == 1
	# Not reported by anyone: no share, rather than a share of zero.
	assert metrics["schedules"]["reported"] == 0 and "share" not in metrics["schedules"]

	commands = next(group for group in data["usage"] if group["label"] == "Comandos")
	by_key = {item["key"]: item for item in commands["items"]}
	assert by_key["cmd_list"]["label"] == "/list"
	assert by_key["cmd_list"]["installs"] == 2 and by_key["cmd_list"]["share"] == 0.5
	assert by_key["cmd_logs"]["total"] == 10


def test_a_lone_ping_cannot_claim_the_latest_version(client):
	for _ in range(3):
		client.post("/v1/ping", json=ping(version="5.0.0"))
	client.post("/v1/ping", json=ping(version="99.0.0"))
	data = client.get(f"/v1/projects/{PROJECT}/summary?days=7").json()
	assert data["kpis"]["latest_version"] == "5.0.0"


def test_versions_sort_releases_above_their_prereleases():
	versions = ["5.0.0_RC4", "4.9.9", "5.0.0", "5.0.0_RC10", "junk"]
	assert max(versions, key=summary._version_key) == "5.0.0"
	assert min(versions, key=summary._version_key) == "junk"


def test_old_rows_are_pruned(tmp_path):
	database = db_module.Database(str(tmp_path / "t.db"))
	now = 1_800_000_000
	old = now - (db_module.RETENTION_DAYS + 5) * 86400
	body = {"project": PROJECT, "install_id": str(uuid.uuid4()), "version": "1", "arch": None,
			"metrics": {}, "usage": {"cmd_list": 1}}
	database.record(body, now=old)
	database.record(dict(body, install_id=str(uuid.uuid4())), now=now)
	database.prune(now=now)
	assert database.query("SELECT COUNT(*) AS n FROM daily")[0]["n"] == 1
	assert database.query("SELECT COUNT(*) AS n FROM installs")[0]["n"] == 1


def test_the_overview_and_the_pages(client):
	client.post("/v1/ping", json=ping())
	cards = client.get("/v1/projects").json()
	assert cards[0]["id"] == PROJECT and cards[0]["active_30"] == 1
	assert client.get(f"/v1/projects/{PROJECT}/manifest").json()["name"] == "Docker Controller Bot"
	assert client.get(f"/v1/projects/{PROJECT}/summary?days=12").status_code == 400
	for path in ("/", f"/{PROJECT}", f"/{PROJECT}/privacy"):
		response = client.get(path)
		assert response.status_code == 200 and "text/html" in response.headers["content-type"]
	assert client.get("/nope").status_code == 404
	assert client.get("/healthz").json()["ok"] is True


def test_a_broken_manifest_stops_the_server_from_starting(tmp_path):
	with pytest.raises(manifest.ManifestError):
		manifest.parse("bad", {"name": "Bad", "metrics": {"x": {"type": "int", "description": "d"}}})
	with pytest.raises(manifest.ManifestError):
		manifest.parse("bad", {"name": "Bad", "metrics": {"x": {"type": "bool"}}})
	with pytest.raises(manifest.ManifestError):
		manifest.parse("Bad_Name", {"name": "Bad"})


def test_months_become_totals_that_outlive_the_rows(tmp_path):
	"""
	The rows with an install id go after RETENTION_DAYS; each complete month
	is kept before that as counts alone, and those are never deleted.
	"""
	import rollup

	database = db_module.Database(str(tmp_path / "t.db"))
	manifests = manifest.load_all(os.path.join(ROOT, "projects"))
	july = 1_783_000_000     # 2026-07-02
	august = july + 31 * 86400
	first, second = str(uuid.uuid4()), str(uuid.uuid4())

	def send(install_id, now, hosts, usage):
		database.record({"project": PROJECT, "install_id": install_id, "version": "5.0.0", "arch": "arm64",
						"metrics": {"hosts": hosts, "language": "es", "check_updates": True},
						"usage": usage}, now=now)

	send(first, july, 1, {"cmd_list": 2})
	send(first, july + 86400, 3, {"cmd_list": 1})   # the last report of the month wins
	send(second, july, 1, {"cmd_logs": 4})
	send(first, august, 3, {"cmd_list": 1})

	# Nothing while July is still going on.
	rollup.run(database, manifests, now=july + 5 * 86400)
	assert rollup.history(database, PROJECT) == []

	# Once it is over, July is written; August is not, it has only started.
	rollup.run(database, manifests, now=august + 86400)
	# July is the first month: nothing to compare with, which is not "nobody".
	assert rollup.history(database, PROJECT) == [{"month": "2026-07", "active": 2, "new": 2, "retained": None}]
	facets = {(r["facet"], r["value"]): r["installs"] for r in database.query("SELECT * FROM monthly_facets")}
	assert facets[("metric:hosts", "3")] == 1 and facets[("metric:hosts", "1")] == 1
	assert facets[("metric:check_updates", "true")] == 2
	assert facets[("version", "5.0.0")] == 2
	usage = {r["key"]: (r["installs"], r["total"]) for r in database.query("SELECT * FROM monthly_usage")}
	assert usage == {"cmd_list": (1, 3), "cmd_logs": (1, 4)}
	# No identifier anywhere in what is kept.
	for table in ("monthly_active", "monthly_facets", "monthly_usage"):
		for row in database.query(f"SELECT * FROM {table}"):
			assert first not in str(tuple(row)) and second not in str(tuple(row))

	# Running again does not count July twice.
	rollup.run(database, manifests, now=august + 2 * 86400)
	assert rollup.history(database, PROJECT) == [{"month": "2026-07", "active": 2, "new": 2, "retained": None}]

	# Long after, the rows are gone and the totals are still there.
	later = august + (db_module.RETENTION_DAYS + 40) * 86400
	rollup.run(database, manifests, now=later)
	database.prune(now=later)
	assert database.query("SELECT COUNT(*) AS n FROM daily")[0]["n"] == 0
	assert database.query("SELECT COUNT(*) AS n FROM installs")[0]["n"] == 0
	history = rollup.history(database, PROJECT)
	assert [m["month"] for m in history] == ["2026-07", "2026-08"]
	# Of July's two, only the first one came back in August.
	assert history[1]["active"] == 1 and history[1]["new"] == 0 and history[1]["retained"] == 1


def test_a_database_from_an_earlier_version_gets_the_new_columns(tmp_path):
	"""The first local database had no arch in daily, and every ping then failed."""
	import sqlite3
	path = str(tmp_path / "old.db")
	old = sqlite3.connect(path)
	old.executescript("""CREATE TABLE daily (project TEXT NOT NULL, install_id TEXT NOT NULL, day TEXT NOT NULL,
		version TEXT NOT NULL, metrics TEXT NOT NULL DEFAULT '{}', usage TEXT NOT NULL DEFAULT '{}',
		PRIMARY KEY (project, install_id, day));""")
	old.close()
	database = db_module.Database(path)
	database.record({"project": PROJECT, "install_id": str(uuid.uuid4()), "version": "5.0.0", "arch": "arm64",
					"metrics": {}, "usage": {"cmd_list": 1}})
	assert database.query("SELECT arch FROM daily")[0]["arch"] == "arm64"
