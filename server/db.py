"""
SQLite storage.

Two kinds of table:

	installs   the latest report of each installation, one row per install
	daily      one row per installation and UTC day it reported on

	monthly_*  totals per calendar month, with no install id in them

The first two carry the random install id, so they are kept for
RETENTION_DAYS and no longer. Before that, every complete month is rolled up
into the monthly tables, which are only counts and are kept for good: the
history survives, the identifiers do not. See rollup.py.

The IP a ping came from is never written: it lives in the rate limiter's
memory for an hour and nowhere else.

At the scale this is for, a single connection behind a lock is plenty. Writes
are one small upsert per installation per day.
"""

import json
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

# Rows carrying an install id are deleted after this many days. It covers the
# longest range the dashboard shows, and leaves every month at least two months
# to be rolled up into totals before its rows go.
RETENTION_DAYS = 90

SCHEMA = """
CREATE TABLE IF NOT EXISTS installs (
	project     TEXT NOT NULL,
	install_id  TEXT NOT NULL,
	first_seen  INTEGER NOT NULL,
	last_seen   INTEGER NOT NULL,
	version     TEXT NOT NULL,
	arch        TEXT,
	metrics     TEXT NOT NULL DEFAULT '{}',
	PRIMARY KEY (project, install_id)
);
CREATE INDEX IF NOT EXISTS installs_seen ON installs (project, last_seen);
CREATE INDEX IF NOT EXISTS installs_first ON installs (project, first_seen);

CREATE TABLE IF NOT EXISTS daily (
	project     TEXT NOT NULL,
	install_id  TEXT NOT NULL,
	day         TEXT NOT NULL,
	version     TEXT NOT NULL,
	arch        TEXT,
	metrics     TEXT NOT NULL DEFAULT '{}',
	usage       TEXT NOT NULL DEFAULT '{}',
	PRIMARY KEY (project, install_id, day)
);
CREATE INDEX IF NOT EXISTS daily_day ON daily (project, day);

-- Kept for good. A month is written once, when it is over.
CREATE TABLE IF NOT EXISTS monthly_active (
	project     TEXT NOT NULL,
	month       TEXT NOT NULL,
	active      INTEGER NOT NULL,
	new         INTEGER NOT NULL,
	-- Active this month and the one before. NULL when there is no month
	-- before to compare with. Only computable while the ids still exist.
	retained    INTEGER,
	PRIMARY KEY (project, month)
);
-- How many installations had each value that month: "version" / "5.0.0",
-- "arch" / "arm64", "metric:language" / "es", "metric:hosts" / "2"...
CREATE TABLE IF NOT EXISTS monthly_facets (
	project     TEXT NOT NULL,
	month       TEXT NOT NULL,
	facet       TEXT NOT NULL,
	value       TEXT NOT NULL,
	installs    INTEGER NOT NULL,
	PRIMARY KEY (project, month, facet, value)
);
CREATE TABLE IF NOT EXISTS monthly_usage (
	project     TEXT NOT NULL,
	month       TEXT NOT NULL,
	key         TEXT NOT NULL,
	installs    INTEGER NOT NULL,
	total       INTEGER NOT NULL,
	PRIMARY KEY (project, month, key)
);
"""


def today(now=None):
	"""The UTC day a ping is filed under, as YYYY-MM-DD."""
	moment = datetime.fromtimestamp(now if now is not None else time.time(), tz=timezone.utc)
	return moment.strftime("%Y-%m-%d")


def day_offset(day, days):
	"""`day` moved by `days`, both as YYYY-MM-DD."""
	return (datetime.strptime(day, "%Y-%m-%d") + timedelta(days=days)).strftime("%Y-%m-%d")


class Database:
	def __init__(self, path):
		self._lock = threading.Lock()
		self._connection = sqlite3.connect(path, check_same_thread=False)
		self._connection.row_factory = sqlite3.Row
		with self._lock:
			self._connection.execute("PRAGMA journal_mode=WAL")
			self._connection.execute("PRAGMA synchronous=NORMAL")
			self._connection.executescript(SCHEMA)
			self._add_missing_columns()
			self._connection.commit()

	# Columns added after a table first existed. CREATE TABLE IF NOT EXISTS
	# leaves an existing table as it was, so a database created by an earlier
	# version would fail on the first insert that names them.
	ADDED_COLUMNS = (
		("daily", "arch", "TEXT"),
		("monthly_active", "retained", "INTEGER"),
	)

	def _add_missing_columns(self):
		for table, column, kind in self.ADDED_COLUMNS:
			present = {row["name"] for row in self._connection.execute(f"PRAGMA table_info({table})")}
			if column not in present:
				self._connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")

	def close(self):
		with self._lock:
			self._connection.close()

	def query(self, sql, parameters=()):
		with self._lock:
			return self._connection.execute(sql, parameters).fetchall()

	def write(self, statements):
		"""Runs [(sql, parameters)] as one transaction: all of it or none."""
		with self._lock:
			try:
				for sql, parameters in statements:
					self._connection.execute(sql, parameters)
				self._connection.commit()
			except Exception:
				self._connection.rollback()
				raise

	def record(self, ping, now=None):
		"""
		Files one validated ping.

		A second ping on the same day adds its counters to the first rather
		than replacing them: the client only sends what it counted since its
		last successful send, so a restart that sends twice in a day must not
		lose the morning.
		"""
		now = int(now if now is not None else time.time())
		day = today(now)
		metrics = json.dumps(ping["metrics"], sort_keys=True)
		with self._lock:
			connection = self._connection
			connection.execute(
				"""INSERT INTO installs (project, install_id, first_seen, last_seen, version, arch, metrics)
				VALUES (?, ?, ?, ?, ?, ?, ?)
				ON CONFLICT (project, install_id) DO UPDATE SET
					last_seen = excluded.last_seen, version = excluded.version,
					arch = excluded.arch, metrics = excluded.metrics""",
				(ping["project"], ping["install_id"], now, now, ping["version"], ping["arch"], metrics))

			row = connection.execute(
				"SELECT usage FROM daily WHERE project = ? AND install_id = ? AND day = ?",
				(ping["project"], ping["install_id"], day)).fetchone()
			usage = json.loads(row["usage"]) if row else {}
			for key, value in ping["usage"].items():
				usage[key] = usage.get(key, 0) + value
			connection.execute(
				"""INSERT INTO daily (project, install_id, day, version, arch, metrics, usage)
				VALUES (?, ?, ?, ?, ?, ?, ?)
				ON CONFLICT (project, install_id, day) DO UPDATE SET
					version = excluded.version, arch = excluded.arch,
					metrics = excluded.metrics, usage = excluded.usage""",
				(ping["project"], ping["install_id"], day, ping["version"], ping["arch"], metrics,
				json.dumps(usage, sort_keys=True)))
			connection.commit()

	def prune(self, now=None):
		"""
		Deletes every row with an install id older than the retention period.

		Only after rollup.run(): it is what turns those rows into the totals
		that outlive them.
		"""
		now = int(now if now is not None else time.time())
		cutoff_day = day_offset(today(now), -RETENTION_DAYS)
		cutoff_seen = now - RETENTION_DAYS * 86400
		with self._lock:
			self._connection.execute("DELETE FROM daily WHERE day < ?", (cutoff_day,))
			self._connection.execute("DELETE FROM installs WHERE last_seen < ?", (cutoff_seen,))
			self._connection.commit()
