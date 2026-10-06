"""
The client as a package: what the projects install from a tag of this repo.

A release is a tag named client-v<version>. The version lives in
clients/python/pyproject.toml, and the install line the docs show carries it
too: if they drift, whoever copies the line installs an older client.
"""

import os
import re
import tomllib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE = os.path.join(ROOT, "clients", "python")


def _project():
	with open(os.path.join(PACKAGE, "pyproject.toml"), "rb") as handle:
		return tomllib.load(handle)


def test_the_package_installs_the_telemetry_module():
	project = _project()
	assert project["tool"]["setuptools"]["py-modules"] == ["telemetry"]
	assert os.path.isfile(os.path.join(PACKAGE, "telemetry.py"))


def test_the_package_has_no_dependencies():
	"""The point of the client is that it needs nothing but the standard library."""
	assert _project()["project"]["dependencies"] == []


def test_the_version_is_plain_semver():
	assert re.fullmatch(r"\d+\.\d+\.\d+", _project()["project"]["version"])


def test_the_documented_install_line_points_at_the_current_version():
	version = _project()["project"]["version"]
	for relative in ("README.md", os.path.join("clients", "python", "pyproject.toml")):
		with open(os.path.join(ROOT, relative), encoding="utf-8") as handle:
			text = handle.read()
		tags = set(re.findall(r"refs/tags/client-v([0-9.]+)\.zip", text))
		assert tags == {version}, f"{relative} documents {sorted(tags)}, the package is {version}"
