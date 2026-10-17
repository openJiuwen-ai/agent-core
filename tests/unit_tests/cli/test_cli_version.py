# coding: utf-8
"""The CLI reports the version of the openjiuwen distribution it ships in."""

from __future__ import annotations

import openjiuwen
from openjiuwen.harness.cli import __version__


def test_cli_version_matches_package_version() -> None:
    assert __version__ == openjiuwen.__version__
