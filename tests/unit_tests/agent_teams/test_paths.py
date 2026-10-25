# coding: utf-8

"""Tests for openjiuwen.agent_teams.paths."""

from pathlib import Path

import hashlib

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
def test_per_run_journal_and_wal_layout():
    """run_id splits the journal per-run and the WAL into wal/{run_id}.wal."""
    paths.configure_openjiuwen_home(Path("/tmp/custom-home/.jiuwenclaw"))
    base = paths.team_session_dir("demo-team", "sess1") / "workflows" / "wf"

    assert paths.workflow_run_journal_path("demo-team", "sess1", "wf", "wf_ab12cd34ef56") == (
        base / "journal-wf_ab12cd34ef56.jsonl"
    )
    assert paths.workflow_run_wal_path("demo-team", "sess1", "wf", "wf_ab12cd34ef56") == (
        base / "wal" / "wf_ab12cd34ef56.wal"
    )
    # None falls back to the shared legacy layout.
    assert paths.workflow_run_journal_path("demo-team", "sess1", "wf", None) == (
        base / "journal.jsonl"
    )
    assert paths.workflow_run_wal_path("demo-team", "sess1", "wf", None) == (
        base / "journal.jsonl.wal"
    )
    # A traversal-style run_id is sanitized like every other segment.
    assert ".." not in paths.workflow_run_wal_path("demo-team", "sess1", "wf", "../../x").parts


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@pytest.mark.level0
def test_group_conversation_scope_stays_short_enough_for_windows_max_path(tmp_path):
    """Two full sha256 tails used to overflow the default Windows MAX_PATH.

    ``<workspace>/.agent_teams/<team>/team-workspace/conversations`` plus a
    96-char prefix and a 64-char digest per scope level exceeded 260 characters
    and made every group delivery fail with WinError 206, so both scope levels
    now carry a truncated digest.
    """
    team = "group-one"
    session_id = "group-one-b8664196765c95a900ac77d2394882d1c4aef5370c1ad240fd84dcb2f5e0f872"

    path = paths.group_conversation_dir(team, session_id, workspace_path=tmp_path)

    assert path.parent.parent == (tmp_path / "conversations").resolve()
    assert path.parent.name == f"{team}-{_digest(team)[:12]}"
    assert path.name == f"{session_id}-{_digest(session_id)[:12]}"
    # Deterministic and well under a single path component limit.
    assert paths.group_conversation_dir(team, session_id, workspace_path=tmp_path) == path
    assert len(path.name) < 128


@pytest.mark.level1
def test_group_conversation_dir_keeps_legacy_full_digest_archive_reachable(tmp_path):
    """Archives written before the truncation must stay addressable."""
    team, session_id = "group-one", "session-legacy"
    legacy = (
        tmp_path
        / "conversations"
        / f"{team}-{_digest(team)}"
        / f"{session_id}-{_digest(session_id)}"
    )
    legacy.mkdir(parents=True)

    assert paths.group_conversation_dir(team, session_id, workspace_path=tmp_path) == legacy


@pytest.mark.level1
def test_group_conversation_dir_rejects_conflicting_symlinked_scope(tmp_path):
    """A scope component that resolves elsewhere is still rejected."""
    team, session_id = "group-one", "session-link"
    outside = tmp_path / "outside"
    outside.mkdir()
    conversations = tmp_path / "conversations"
    conversations.mkdir()
    link = conversations / f"{team}-{_digest(team)[:12]}"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):  # pragma: no cover - Windows without symlink rights
        pytest.skip("symlinks are not available in this environment")

    with pytest.raises(ValueError):
        paths.group_conversation_dir(team, session_id, workspace_path=tmp_path)
