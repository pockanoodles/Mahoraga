"""The package version.

backend/orchestrator/__version__.py is the one place a release number is
written, and pyproject.toml reads it at build time, so a wheel, an sdist and a
source checkout all report the same string. Reading the module rather than the
install's metadata also means the answer doesn't depend on what the
distribution is called on PyPI (mahoraga-audit).
"""
from __future__ import annotations

from backend.orchestrator.__version__ import __version__


def package_version() -> str:
    return __version__
