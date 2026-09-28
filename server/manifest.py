"""
Project manifests: what each project may send, and how it is shown.

A manifest is the whole contract for one project. The ping endpoint validates
against it and the dashboard is drawn from it, so the two cannot disagree about
what is collected: anything not declared here is dropped on arrival, and
everything declared here is listed on the project's privacy page.

Adding a project is adding a YAML file to `projects/`. Nothing in the server
knows any project by name.
"""

import os
import re

import yaml

# Identifiers that end up in URLs and in the database.
PROJECT_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
METRIC_KEY = re.compile(r"^[a-z][a-z0-9_]{0,47}$")

METRIC_TYPES = ("int", "number", "bool", "enum")
# Which charts each type can be drawn as.
CHARTS = {
	"int": ("histogram", "nonzero", "none"),
	"number": ("histogram", "none"),
	"bool": ("adoption", "none"),
	"enum": ("donut", "bar", "none"),
}
KPIS = ("sum", "mean")

# Bounds on what a manifest may ask the server to do.
MIN_INTERVAL_HOURS = 1
MAX_INTERVAL_HOURS = 24 * 7


class ManifestError(ValueError):
	"""A manifest that would make the server accept or show something wrong."""


def text(value, lang="en"):
	"""
	A label or description, which a manifest may give as a string or as
	{language: string}. Falls back to English, then to whatever there is.
	"""
	if isinstance(value, dict):
		return value.get(lang) or value.get("en") or next(iter(value.values()), "")
	return value or ""


def _require(condition, where, message):
	if not condition:
		raise ManifestError(f"{where}: {message}")


def _parse_bucket(label, where):
	"""
	A histogram bucket, written the way it is shown: "3", "4-5" or "6+".

	Returns (label, low, high) with high None for an open bucket.
	"""
	raw = str(label).strip()
	match = re.fullmatch(r"(-?\d+(?:\.\d+)?)(?:\s*-\s*(-?\d+(?:\.\d+)?)|(\+))?", raw)
	_require(match is not None, where, f"bucket {raw!r} is not N, N-M or N+")
	low = float(match.group(1))
	if match.group(3):
		return raw, low, None
	high = float(match.group(2)) if match.group(2) else low
	_require(high >= low, where, f"bucket {raw!r} ends before it starts")
	return raw, low, high


def _parse_metric(key, spec, where):
	_require(METRIC_KEY.match(key), where, f"metric key {key!r} is not lowercase snake_case")
	_require(isinstance(spec, dict), where, "a metric is a mapping")
	kind = spec.get("type")
	_require(kind in METRIC_TYPES, where, f"type must be one of {METRIC_TYPES}")
	_require(spec.get("description"), where, "every metric needs a description: it is what the privacy page says")

	metric = {
		"key": key,
		"type": kind,
		"label": spec.get("label") or key,
		"description": spec["description"],
		"chart": spec.get("chart", "none"),
		"kpi": spec.get("kpi"),
		# What the figure is called when shown as a kpi, which is often not
		# what its chart is called: "hosts managed" against "hosts per install".
		"kpi_label": spec.get("kpi_label"),
	}
	_require(metric["chart"] in CHARTS[kind], where, f"chart for {kind} must be one of {CHARTS[kind]}")

	if kind in ("int", "number"):
		_require("min" in spec and "max" in spec, where, "numbers need min and max")
		metric["min"] = float(spec["min"])
		metric["max"] = float(spec["max"])
		_require(metric["max"] >= metric["min"], where, "max is below min")
		if metric["chart"] == "histogram":
			buckets = spec.get("buckets")
			_require(isinstance(buckets, list) and buckets, where, "a histogram needs buckets")
			metric["buckets"] = [_parse_bucket(bucket, where) for bucket in buckets]
		_require(metric["kpi"] in (None,) + KPIS, where, f"kpi must be one of {KPIS}")
	else:
		_require(metric["kpi"] is None, where, "only numbers can be a kpi")

	if kind == "enum":
		values = spec.get("values")
		_require(isinstance(values, list) and values, where, "an enum needs its values")
		metric["values"] = [str(value) for value in values]
		metric["value_labels"] = spec.get("value_labels") or {}
	return metric


def _parse_usage(spec, where):
	"""
	Usage counters are validated by shape, not against a list.

	A strict list would make every button added to a project a silent loss
	until someone remembered to declare it. The keys are identifiers from the
	project's code, never user input, so a pattern and a cap on how many are
	enough; the manifest only groups and names them for the dashboard.
	"""
	spec = spec or {}
	_require(isinstance(spec, dict), where, "usage is a mapping")
	_require(spec.get("description"), where, "usage needs a description for the privacy page")
	groups = []
	for index, group in enumerate(spec.get("groups") or []):
		_require(isinstance(group, dict) and group.get("prefix"), where, f"usage group {index} needs a prefix")
		groups.append({
			"prefix": str(group["prefix"]),
			"label": group.get("label") or group["prefix"],
			"description": group.get("description") or "",
			# How a key shows on the dashboard, with {name} for the key
			# without its prefix. "/{name}" turns cmd_list into /list.
			"display": group.get("display") or "{name}",
		})
	return {
		"description": spec["description"],
		"groups": groups,
		"labels": spec.get("labels") or {},
	}


def parse(project_id, document):
	"""Turns one YAML document into a manifest, or raises ManifestError."""
	where = f"projects/{project_id}.yaml"
	_require(PROJECT_ID.match(project_id), where, "the file name is the project id: lowercase, digits and dashes")
	_require(isinstance(document, dict), where, "the document is a mapping")
	_require(document.get("name"), where, "name is required")

	interval = document.get("interval_hours", 24)
	_require(isinstance(interval, (int, float)) and MIN_INTERVAL_HOURS <= interval <= MAX_INTERVAL_HOURS,
			where, f"interval_hours must be between {MIN_INTERVAL_HOURS} and {MAX_INTERVAL_HOURS}")

	metrics = document.get("metrics") or {}
	_require(isinstance(metrics, dict), where, "metrics is a mapping")
	return {
		"id": project_id,
		"name": document["name"],
		"description": document.get("description") or "",
		"repo": document.get("repo") or "",
		# How a user turns it off, shown on the privacy page.
		"opt_out": document.get("opt_out") or "",
		# Off means pings are answered, and told to stop, but not stored.
		"enabled": bool(document.get("enabled", True)),
		"interval_hours": interval,
		"metrics": [_parse_metric(key, spec, where) for key, spec in metrics.items()],
		"usage": _parse_usage(document.get("usage"), where),
	}


def load_all(directory):
	"""
	Every manifest in `directory`, keyed by project id.

	A broken manifest stops the server from starting rather than being
	skipped: skipping it would quietly turn away every ping for that project.
	"""
	manifests = {}
	for name in sorted(os.listdir(directory)):
		if not name.endswith((".yaml", ".yml")):
			continue
		project_id = name.rsplit(".", 1)[0]
		with open(os.path.join(directory, name), "r", encoding="utf-8") as handle:
			manifests[project_id] = parse(project_id, yaml.safe_load(handle))
	return manifests


def bucket_of(buckets, value):
	"""
	The first bucket, in order, whose upper end reaches `value`.

	Upper ends only, so buckets never leave gaps between them: with "2-4" and
	"5-12", 4.5 hours lands in "5-12" instead of in neither. The lower end of
	the first bucket still counts, and an open bucket takes everything above.
	"""
	for index, (label, low, high) in enumerate(buckets):
		if index == 0 and value < low:
			return None
		if high is None or value <= high:
			return label
	return None


def public(manifest):
	"""What the web gets to see of a manifest: all of it but the bucket tuples."""
	shown = dict(manifest)
	shown["metrics"] = []
	for metric in manifest["metrics"]:
		metric = dict(metric)
		if "buckets" in metric:
			metric["buckets"] = [label for label, _, _ in metric["buckets"]]
		shown["metrics"].append(metric)
	return shown
