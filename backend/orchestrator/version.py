"""The package version, as the installed distribution says it.

pyproject.toml is the one place a version is written. Reading it back from the
install's metadata means `mahoraga version` and /api/engine report what pip
actually installed, and can't drift from it.
"""
from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version


def package_version() -> str:
    try:
        return version("mahoraga")
    except PackageNotFoundError:  # run from a source tree that was never installed
        return "unknown"
