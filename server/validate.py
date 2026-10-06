"""
Validation of an incoming ping against its project's manifest.

The rule throughout is to keep what is valid and drop what is not, one field
at a time. Rejecting a whole ping over one bad value would lose a day of an
installation to a bug in a single counter; keeping an undeclared field would
store something nobody agreed to send.

Only the envelope is all-or-nothing: without a project, an install id and a
version there is nothing to file the rest under.
"""

import re
import uuid

SCHEMA_VERSION = 1

VERSION = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+-]{0,31}$")
ARCH = re.compile(r"^[a-z0-9_]{1,16}$")
# Usage keys come from a project's code: identifiers, optionally camelCase.
USAGE_KEY = re.compile(r"^[a-z][a-zA-Z0-9_]{0,63}$")

MAX_USAGE_KEYS = 300
# More than this in a day is not a person pressing buttons.
MAX_USAGE_VALUE = 100_000


class Rejected(ValueError):
	"""A ping that cannot be stored at all."""


def _install_id(value):
	try:
		return str(uuid.UUID(str(value)))
	except (ValueError, AttributeError, TypeError):
		raise Rejected("install_id is not a UUID")


def _number(value):
	# bool is an int in Python, and True must not count as a host.
	if isinstance(value, bool) or not isinstance(value, (int, float)):
		return None
	return value


def metric_value(metric, value):
	"""
	The value if it is valid for `metric`, else None.

	Also applied to what is read back from the database: a value stored under
	an earlier manifest, before the metric changed type or range, no longer
	fits, and is left out rather than mixed with the new ones.
	"""
	kind = metric["type"]
	if kind == "bool":
		return value if isinstance(value, bool) else None
	if kind == "enum":
		return value if isinstance(value, str) and value in metric["values"] else None
	number = _number(value)
	if number is None or not metric["min"] <= number <= metric["max"]:
		return None
	if kind == "int":
		return int(number) if float(number).is_integer() else None
	return float(number)


def ping(body, manifests):
	"""
	Returns (manifest, clean) for a ping body, or raises Rejected.

	`clean` has the envelope and only the metrics and usage that passed.
	"""
	if not isinstance(body, dict):
		raise Rejected("body is not an object")
	if body.get("schema") != SCHEMA_VERSION:
		raise Rejected(f"schema must be {SCHEMA_VERSION}")

	manifest = manifests.get(body.get("project"))
	if manifest is None:
		raise Rejected("unknown project")

	version = body.get("version")
	if not isinstance(version, str) or not VERSION.match(version):
		raise Rejected("version is missing or malformed")

	arch = body.get("arch")
	if not isinstance(arch, str) or not ARCH.match(arch):
		arch = None

	metrics = {}
	sent_metrics = body.get("metrics")
	if isinstance(sent_metrics, dict):
		for metric in manifest["metrics"]:
			if metric["key"] in sent_metrics:
				value = metric_value(metric, sent_metrics[metric["key"]])
				if value is not None:
					metrics[metric["key"]] = value

	usage = {}
	sent_usage = body.get("usage")
	if isinstance(sent_usage, dict):
		for key, value in sent_usage.items():
			if len(usage) >= MAX_USAGE_KEYS:
				break
			if not isinstance(key, str) or not USAGE_KEY.match(key):
				continue
			if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= MAX_USAGE_VALUE:
				continue
			usage[key] = value

	return manifest, {
		"project": manifest["id"],
		"install_id": _install_id(body.get("install_id")),
		"version": version,
		"arch": arch,
		"metrics": metrics,
		"usage": usage,
	}
