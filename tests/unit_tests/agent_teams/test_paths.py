# coding: utf-8

"""Tests for openjiuwen.agent_teams.paths."""

from pathlib import Path

import pytest

from openjiuwen.agent_teams import paths


def teardown_function():
    paths.reset_openjiuwen_home()


@pytest.mark.level0
def test_default_openjiuwen_home(monkeypatch):
    monkeypatch.setattr(paths.Path, "home", lambda: Path("/tmp/test-home"))

    assert paths.get_openjiuwen_home() == Path("/tmp/test-home/.openjiuwen")
    assert paths.OPENJIUWEN_HOME == Path("/tmp/test-home/.openjiuwen")
    assert paths.get_agent_teams_home() == Path("/tmp/test-home/.openjiuwen/.agent_teams")
    assert paths.AGENT_TEAMS_HOME == Path("/tmp/test-home/.openjiuwen/.agent_teams")


@pytest.mark.level1
def test_configure_openjiuwen_home_overrides_paths():
    custom_home = Path("/tmp/custom-home/.jiuwenclaw")
    paths.configure_openjiuwen_home(custom_home)

    assert paths.get_openjiuwen_home() == custom_home
    assert paths.OPENJIUWEN_HOME == custom_home
    assert paths.get_agent_teams_home() == custom_home / ".agent_teams"
    assert paths.AGENT_TEAMS_HOME == custom_home / ".agent_teams"
    assert paths.team_home("demo-team") == custom_home / ".agent_teams" / "demo-team"
    assert paths.independent_member_workspace("alice") == custom_home / "alice_workspace"


@pytest.mark.level1
def test_reset_openjiuwen_home_restores_default(monkeypatch):
    monkeypatch.setattr(paths.Path, "home", lambda: Path("/tmp/reset-home"))
    paths.configure_openjiuwen_home("/tmp/custom-home/.jiuwenclaw")

    paths.reset_openjiuwen_home()

    assert paths.get_openjiuwen_home() == Path("/tmp/reset-home/.openjiuwen")
