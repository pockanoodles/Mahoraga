"""The package version: the one place a release number is written.

pyproject.toml reads this at build time, so a wheel, an sdist and a source
checkout all report the same string. The release workflow refuses a tag that
does not equal "v" + this value, because PyPI never lets a version be reused.
"""

__version__ = "2.1.0"
