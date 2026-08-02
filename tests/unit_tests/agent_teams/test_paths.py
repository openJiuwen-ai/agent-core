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


@pytest.mark.level1
def test_workflow_journal_path_layout():
    custom_home = Path("/tmp/custom-home/.jiuwenclaw")
    paths.configure_openjiuwen_home(custom_home)
    base = custom_home / ".agent_teams" / "demo-team"

    assert paths.team_sessions_dir("demo-team") == base / "sessions"
    assert paths.team_session_dir("demo-team", "sess1") == base / "sessions" / "sess1"
    assert paths.workflow_run_dir("demo-team", "sess1", "wf") == (
        base / "sessions" / "sess1" / "workflows" / "wf"
    )
    assert paths.workflow_journal_path("demo-team", "sess1", "wf") == (
        base / "sessions" / "sess1" / "workflows" / "wf" / "journal.jsonl"
    )


@pytest.mark.level1
def test_workflow_path_sanitizes_untrusted_segments():
    paths.configure_openjiuwen_home(Path("/tmp/custom-home/.jiuwenclaw"))

    # A traversal-style workflow name must not escape its parent directory.
    journal = paths.workflow_journal_path("demo-team", "s", "../../etc/passwd")
    workflows_dir = paths.team_session_dir("demo-team", "s") / "workflows"
    assert workflows_dir in journal.parents
    assert ".." not in journal.parts

    # Spaces and unsafe characters collapse to underscores.
    assert paths.workflow_run_dir("demo-team", "s", "My Flow!").name == "My_Flow"


@pytest.mark.level1
def test_agent_teams_home_scope_nests_under_project(tmp_path):
    project = tmp_path / "workspace" / "20260803154027"
    project.mkdir(parents=True)
    paths.configure_openjiuwen_home(tmp_path / "user-data")

    with paths.agent_teams_home_scope(project) as teams_home:
        assert teams_home == project.resolve() / ".agent_teams"
        assert paths.get_agent_teams_home() == teams_home
        assert paths.team_home("oc_team_demo") == teams_home / "oc_team_demo"
        assert (teams_home).is_dir()

    assert paths.get_agent_teams_home() == tmp_path / "user-data" / ".agent_teams"


@pytest.mark.level1
def test_agent_teams_home_scope_noop_without_project(tmp_path):
    paths.configure_openjiuwen_home(tmp_path / "user-data")
    with paths.agent_teams_home_scope(None) as teams_home:
        assert teams_home is None
        assert paths.get_agent_teams_home() == tmp_path / "user-data" / ".agent_teams"

