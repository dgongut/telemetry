"""
Aggregates for the dashboard.

Everything that leaves the server leaves as a count or a share. The web never
sees a row, so publishing the dashboard publishes no installation.

Shares are taken over the installations that reported the field, not over all
of them: an older version that does not send a metric yet must not read as
"nobody uses it".
"""

import calendar
import json
import re
import time
from collections import Counter

from db import day_offset, today
from manifest import bucket_of, text
import rollup
from validate import metric_value

RANGES = (7, 30, 90)
TOP_VERSIONS = 6


def _version_key(version):
	"""
	Sorts 5.0.0 above 5.0.0_RC4, and both above 4.9.9.

	Anything that does not start with a dotted number sorts lowest.
	"""
	match = re.match(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?(.*)", version)
	if not match:
		return (-1, -1, -1, 0, version)
	major, minor, patch, rest = match.groups()
	# A release beats any prerelease of the same number.
	return (int(major), int(minor or 0), int(patch or 0), 0 if rest else 1, rest)


def _day_start(day):
	return calendar.timegm(time.strptime(day, "%Y-%m-%d"))


def _active_between(db, project, first_day, last_day):
	rows = db.query(
		"SELECT COUNT(DISTINCT install_id) AS n FROM daily WHERE project = ? AND day BETWEEN ? AND ?",
		(project, first_day, last_day))
	return rows[0]["n"]


def _new_between(db, project, first_day, last_day):
	rows = db.query(
		"SELECT COUNT(*) AS n FROM installs WHERE project = ? AND first_seen >= ? AND first_seen < ?",
		(project, _day_start(first_day), _day_start(day_offset(last_day, 1))))
	return rows[0]["n"]


def _series(db, project, first_day, last_day):
	"""Active installations per day, and over the seven days ending on each."""
	rows = db.query(
		"SELECT day, install_id FROM daily WHERE project = ? AND day BETWEEN ? AND ?",
		(project, day_offset(first_day, -6), last_day))
	by_day = {}
	for row in rows:
		by_day.setdefault(row["day"], set()).add(row["install_id"])

	days, daily, rolling = [], [], []
	day = first_day
	while day <= last_day:
		days.append(day)
		daily.append(len(by_day.get(day, ())))
		window = set()
		for back in range(7):
			window |= by_day.get(day_offset(day, -back), set())
		rolling.append(len(window))
		day = day_offset(day, 1)
	return {"days": days, "daily": daily, "rolling7": rolling}


def _metric_summary(metric, values, lang):
	"""One metric over the installations that reported it."""
	summary = {
		"key": metric["key"],
		"type": metric["type"],
		"chart": metric["chart"],
		"label": text(metric["label"], lang),
		"description": text(metric["description"], lang),
		"reported": len(values),
	}
	if not values:
		return summary

	if metric["type"] in ("int", "number"):
		total = sum(values)
		summary["kpi_label"] = text(metric.get("kpi_label") or metric["label"], lang)
		if metric.get("kpi") == "sum":
			summary["kpi"] = {"kind": "sum", "value": total, "mean": total / len(values)}
		elif metric.get("kpi") == "mean":
			summary["kpi"] = {"kind": "mean", "value": total / len(values)}
		if metric["chart"] == "histogram":
			counts = Counter(bucket_of(metric["buckets"], value) for value in values)
			summary["data"] = [{"label": label, "count": counts.get(label, 0)}
							for label, _, _ in metric["buckets"]]
		elif metric["chart"] == "nonzero":
			summary["share"] = sum(1 for value in values if value > 0) / len(values)
	elif metric["type"] == "bool":
		summary["share"] = sum(1 for value in values if value) / len(values)
	elif metric["type"] == "enum":
		counts = Counter(values)
		labels = metric.get("value_labels") or {}
		summary["data"] = [{"label": text(labels.get(value), lang) or value, "value": value,
							"count": counts.get(value, 0)} for value in metric["values"]]
	return summary


def _usage_summary(db, manifest, first_day, last_day, active, lang):
	rows = db.query(
		"""SELECT usage.key AS key, COUNT(DISTINCT daily.install_id) AS installs, SUM(usage.value) AS total
		FROM daily, json_each(daily.usage) AS usage
		WHERE daily.project = ? AND daily.day BETWEEN ? AND ?
		GROUP BY usage.key""",
		(manifest["id"], first_day, last_day))

	spec = manifest["usage"]
	groups = [{"prefix": group["prefix"], "label": text(group["label"], lang),
				"description": text(group["description"], lang), "display": group["display"], "items": []}
			for group in spec["groups"]]
	other = {"prefix": "", "label": "Other", "description": "", "display": "{name}", "items": []}

	for row in rows:
		key = row["key"]
		group = next((candidate for candidate in groups if key.startswith(candidate["prefix"])), other)
		name = key[len(group["prefix"]):]
		label = text(spec["labels"].get(key), lang) or group["display"].replace("{name}", name)
		group["items"].append({
			"key": key,
			"label": label,
			"installs": row["installs"],
			"share": row["installs"] / active if active else 0,
			"total": row["total"],
		})

	result = []
	for group in groups + [other]:
		group["items"].sort(key=lambda item: (-item["installs"], -item["total"], item["key"]))
		del group["display"]
		if group["items"]:
			result.append(group)
	return result


def project(db, manifest, days, lang="en", now=None):
	"""The whole dashboard for one project over the last `days` days."""
	last_day = today(now)
	first_day = day_offset(last_day, -(days - 1))
	previous_last = day_offset(first_day, -1)
	previous_first = day_offset(previous_last, -(days - 1))
	project_id = manifest["id"]

	active = _active_between(db, project_id, first_day, last_day)
	installs = db.query(
		"SELECT version, arch, metrics FROM installs WHERE project = ? AND last_seen >= ?",
		(project_id, _day_start(first_day)))

	versions = Counter(row["version"] for row in installs)
	# The newest version anyone is on, as long as enough are on it: a single
	# hand-made ping claiming 99.0.0 must not become "the latest version".
	threshold = max(2, len(installs) // 100)
	credible = [version for version, count in versions.items() if count >= threshold] or list(versions)
	latest = max(credible, key=_version_key) if credible else None

	top = versions.most_common(TOP_VERSIONS)
	shown_versions = [{"label": version, "count": count} for version, count in top]
	rest = sum(versions.values()) - sum(count for _, count in top)
	if rest:
		shown_versions.append({"label": "other", "count": rest})

	parsed = [json.loads(row["metrics"]) for row in installs]
	metrics = []
	for metric in manifest["metrics"]:
		values = [metric_value(metric, entry[metric["key"]]) for entry in parsed if metric["key"] in entry]
		values = [value for value in values if value is not None]
		metrics.append(_metric_summary(metric, values, lang))

	return {
		"project": {"id": project_id, "name": manifest["name"],
					"description": text(manifest["description"], lang), "repo": manifest["repo"]},
		"days": days,
		"generated_at": int(now if now is not None else time.time()),
		"kpis": {
			"active": active,
			"active_previous": _active_between(db, project_id, previous_first, previous_last),
			"new": _new_between(db, project_id, first_day, last_day),
			"new_previous": _new_between(db, project_id, previous_first, previous_last),
			"latest_version": latest,
			"latest_version_share": versions.get(latest, 0) / len(installs) if installs else 0,
		},
		# Up to yesterday: today is still arriving, and would always read as a drop.
		"series": _series(db, project_id, day_offset(first_day, -1), day_offset(last_day, -1)),
		# Every complete month, however old: this is the part that is kept.
		"monthly": rollup.history(db, project_id),
		"versions": shown_versions,
		"arch": [{"label": arch or "unknown", "count": count}
				for arch, count in Counter(row["arch"] for row in installs).most_common()],
		"metrics": metrics,
		"usage": _usage_summary(db, manifest, first_day, last_day, active, lang),
	}


def overview(db, manifests, lang="en", now=None):
	"""One card per project for the landing page."""
	last_day = today(now)
	cards = []
	for manifest in manifests.values():
		first_day = day_offset(last_day, -29)
		cards.append({
			"id": manifest["id"],
			"name": manifest["name"],
			"description": text(manifest["description"], lang),
			"repo": manifest["repo"],
			"active_30": _active_between(db, manifest["id"], first_day, last_day),
			"active_7": _active_between(db, manifest["id"], day_offset(last_day, -6), last_day),
			"sparkline": _series(db, manifest["id"], day_offset(first_day, -1), day_offset(last_day, -1))["rolling7"],
		})
	cards.sort(key=lambda card: -card["active_30"])
	return cards
