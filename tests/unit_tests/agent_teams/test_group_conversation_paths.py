"""Group conversation archives keep path names short enough for Windows."""

import hashlib
import json
from pathlib import PureWindowsPath

import pytest

from openjiuwen.agent_teams.group_chat.conversation import GroupConversationLog
from openjiuwen.agent_teams.paths import (
    GROUP_PATH_HASH_LENGTH,
    group_conversation_dir,
    group_conversation_registry_dir,
    reset_task_openjiuwen_home,
    set_task_openjiuwen_home,
    team_workspace_dir,
)

TEAM = "jiuwen_team_web_1a18bde044a_f1ea2469dbcf"
SESSION = "web_1a18bde044a_f1ea2469dbcf"


@pytest.fixture(autouse=True)
def home(tmp_path):
    token = set_task_openjiuwen_home(tmp_path / "home")
    yield
    reset_task_openjiuwen_home(token)


def _digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


# A real reported home; the full-hash layout put history.jsonl at 324 characters.
WINDOWS_TEAMS_HOME = PureWindowsPath(r"C:\Users\75797\.jiuwenswarm\.agent_teams")
WINDOWS_MAX_PATH = 260


def test_new_archive_uses_short_hashes():
    assert GROUP_PATH_HASH_LENGTH == 12
    path = group_conversation_dir(TEAM, SESSION)
    assert path.parent.name == f"{TEAM}-{_digest(TEAM)[:12]}"
    assert path.name == f"{SESSION}-{_digest(SESSION)[:12]}"


def test_new_history_path_fits_windows_max_path():
    workspace = team_workspace_dir(TEAM).resolve()
    relative = GroupConversationLog(TEAM, SESSION).history_path.relative_to(workspace)
    windows_path = WINDOWS_TEAMS_HOME / TEAM / "team-workspace" / PureWindowsPath(*relative.parts)
    assert len(str(windows_path)) < WINDOWS_MAX_PATH


def test_new_registration_uses_short_hash():
    log = GroupConversationLog(TEAM, SESSION)
    log.sync([])
    registry = group_conversation_registry_dir(TEAM)
    assert [path.name for path in registry.glob("*.json")] == [f"{_digest(SESSION)[:GROUP_PATH_HASH_LENGTH]}.json"]
    assert log.history_path.parent == group_conversation_dir(TEAM, SESSION)


def test_legacy_full_hash_archive_and_registration_stay_in_place():
    root = team_workspace_dir(TEAM).resolve() / "conversations"
    legacy_dir = root / f"{TEAM}-{_digest(TEAM)}" / f"{SESSION}-{_digest(SESSION)}"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "history.jsonl").write_text("", encoding="utf-8")
    registry = group_conversation_registry_dir(TEAM)
    registry.mkdir(parents=True)
    legacy_registration = registry / f"{_digest(SESSION)}.json"
    legacy_registration.write_text(json.dumps({
        "team_name": TEAM, "session_id": SESSION, "workspace_path": str(team_workspace_dir(TEAM).resolve()),
    }), encoding="utf-8")

    log = GroupConversationLog(TEAM, SESSION)
    assert log.history_path == legacy_dir / "history.jsonl"
    log.sync([])
    assert [path.name for path in registry.glob("*.json")] == [legacy_registration.name]

    GroupConversationLog.delete_registered(TEAM, SESSION)
    assert not legacy_dir.exists()
    assert not legacy_registration.exists()


def _register(name, session_id):
    registry = group_conversation_registry_dir(TEAM)
    registry.mkdir(parents=True, exist_ok=True)
    path = registry / name
    path.write_text(json.dumps({
        "team_name": TEAM, "session_id": session_id, "workspace_path": str(team_workspace_dir(TEAM).resolve()),
    }), encoding="utf-8")
    return path


def test_delete_removes_both_layouts_when_versions_coexist():
    root = team_workspace_dir(TEAM).resolve() / "conversations"
    archives = [
        root / f"{TEAM}-{_digest(TEAM)[:12]}" / f"{SESSION}-{_digest(SESSION)[:12]}",
        root / f"{TEAM}-{_digest(TEAM)}" / f"{SESSION}-{_digest(SESSION)}",
    ]
    for archive in archives:
        archive.mkdir(parents=True)
        (archive / "history.jsonl").write_text("", encoding="utf-8")
    registrations = [
        _register(f"{_digest(SESSION)[:12]}.json", SESSION),
        _register(f"{_digest(SESSION)}.json", SESSION),
    ]
    other = _register(f"{_digest('other')[:12]}.json", "other")

    # Either enumeration order must succeed; neither layout is left behind.
    GroupConversationLog.delete_registered(TEAM, SESSION)
    assert not any(path.exists() for path in [*archives, *registrations])
    assert other.exists()
