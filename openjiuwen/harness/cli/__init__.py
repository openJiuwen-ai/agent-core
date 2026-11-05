"""OpenJiuWen CLI — terminal interactive AI programming assistant."""

# The CLI ships inside the openjiuwen distribution and reports that
# distribution's version: --version and the REPL banner must not drift from
# the installed package version when pyproject.toml is bumped.
from openjiuwen import __version__

__all__ = ["__version__"]
