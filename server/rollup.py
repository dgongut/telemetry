"""
Monthly totals: the history that outlives the install ids.

The daily rows carry the random install id and are deleted after
db.RETENTION_DAYS. Before that happens, each calendar month is written once,
as soon as it is over, into tables that hold only counts:

	monthly_active   installations active that month, how many were new, and
	                 how many were also active the month before
	monthly_facets   how many had each version, architecture and metric value
	monthly_usage    how many used each counter, and how many times in total

Nothing in them can be traced back to an installation, so they are kept for
good. A month is only rolled up once it is complete, because a ping is filed
under the day it arrives: after the month ends nothing more can land in it.

Metrics are stored the way the dashboard shows them, not raw: a histogram as
its bucket, a count charted as "does it at all" as yes or no. That is also
what keeps a rare exact value from standing out in a table kept forever.
"""

import calendar
import json

from db import today
from manifest import bucket_of
from validate import metric_value


def _month_bounds(month):
	"""First and last day of a YYYY-MM month, as YYYY-MM-DD."""
	year, number = (int(part) for part in month.split("-"))
	last = calendar.monthrange(year, number)[1]
	return f"{month}-01", f"{month}-{last:02d}"


def _month_epochs(month):
	"""Start of `month` and start of the next one, in epoch seconds."""
	year, number = (int(part) for part in month.split("-"))
	start = calendar.timegm((year, number, 1, 0, 0, 0))
	following = (year + number // 12, number % 12 + 1)
	return start, calendar.timegm((following[0], following[1], 1, 0, 0, 0))


def facet_value(metric, value):
	"""
	What a metric's value is kept as, or None to keep nothing.

	Only metrics the dashboard charts are kept: one with chart "none" is
	collected for the kpis alone, and has nothing to say per month.
	"""
	chart = metric["chart"]
	if chart == "none":
		return None
	if chart == "histogram":
		return bucket_of(metric["buckets"], value)
	if chart == "nonzero":
		return "yes" if value > 0 else "no"
	if metric["type"] == "bool":
		return "true" if value else "false"
	return str(value)


def _previous_month(month):
	year, number = (int(part) for part in month.split("-"))
	return f"{year - 1}-12" if number == 1 else f"{year}-{number - 1:02d}"


def _active_ids(db, project, month):
	first_day, last_day = _month_bounds(month)
	return {row["install_id"] for row in db.query(
		"SELECT DISTINCT install_id FROM daily WHERE project = ? AND day BETWEEN ? AND ?",
		(project, first_day, last_day))}


def pending_months(db, project, now=None):
	"""Complete months that still have daily rows and no totals yet."""
	current = today(now)[:7]
	with_rows = {row["month"] for row in db.query(
		"SELECT DISTINCT substr(day, 1, 7) AS month FROM daily WHERE project = ?", (project,))}
	done = {row["month"] for row in db.query(
		"SELECT month FROM monthly_active WHERE project = ?", (project,))}
	return sorted(month for month in with_rows - done if month < current)


def rollup_month(db, manifest, month):
	"""Writes the totals for one complete month, in one transaction."""
	project = manifest["id"]
	first_day, last_day = _month_bounds(month)
	rows = db.query(
		"SELECT install_id, day, version, arch, metrics, usage FROM daily "
		"WHERE project = ? AND day BETWEEN ? AND ? ORDER BY day",
		(project, first_day, last_day))

	# The last report of the month is the one that describes an installation.
	latest = {}
	usage_installs = {}
	usage_total = {}
	for row in rows:
		latest[row["install_id"]] = row
		for key, value in json.loads(row["usage"]).items():
			usage_installs.setdefault(key, set()).add(row["install_id"])
			usage_total[key] = usage_total.get(key, 0) + value

	facets = {}

	def add(facet, value):
		if value is not None:
			key = (facet, value)
			facets[key] = facets.get(key, 0) + 1

	for row in latest.values():
		add("version", row["version"])
		add("arch", row["arch"] or "unknown")
		metrics = json.loads(row["metrics"])
		for metric in manifest["metrics"]:
			value = metric_value(metric, metrics[metric["key"]]) if metric["key"] in metrics else None
			if value is not None:
				add(f"metric:{metric['key']}", facet_value(metric, value))

	# Who came back from last month. It needs both months' install ids, which
	# is why it is counted here: once the rows are gone it cannot be anymore.
	previous = _active_ids(db, project, _previous_month(month))
	retained = len(previous & set(latest)) if previous else None

	start, end = _month_epochs(month)
	new = db.query(
		"SELECT COUNT(*) AS n FROM installs WHERE project = ? AND first_seen >= ? AND first_seen < ?",
		(project, start, end))[0]["n"]

	statements = [(
		"INSERT OR REPLACE INTO monthly_facets (project, month, facet, value, installs) VALUES (?, ?, ?, ?, ?)",
		(project, month, facet, value, count)) for (facet, value), count in facets.items()]
	statements += [(
		"INSERT OR REPLACE INTO monthly_usage (project, month, key, installs, total) VALUES (?, ?, ?, ?, ?)",
		(project, month, key, len(installs), usage_total[key])) for key, installs in usage_installs.items()]
	# Last, so a month only counts as done once everything else is in.
	statements.append((
		"INSERT OR REPLACE INTO monthly_active (project, month, active, new, retained) VALUES (?, ?, ?, ?, ?)",
		(project, month, len(latest), new, retained)))
	db.write(statements)


def run(db, manifests, now=None):
	"""Rolls up every complete month that is still pending, for every project."""
	for manifest in manifests.values():
		for month in pending_months(db, manifest["id"], now):
			rollup_month(db, manifest, month)


def history(db, project):
	"""Every rolled-up month of a project, oldest first."""
	return [{"month": row["month"], "active": row["active"], "new": row["new"], "retained": row["retained"]}
			for row in db.query(
				"SELECT month, active, new, retained FROM monthly_active WHERE project = ? ORDER BY month",
				(project,))]
