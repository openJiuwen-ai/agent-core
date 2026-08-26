# coding: utf-8

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from openjiuwen.agent_teams import paths as apaths
from openjiuwen.agent_teams.team_workspace.paths import (
    MEMBER_MODE_DYNAMIC,
    MEMBER_MODE_LEADER,
    MEMBER_MODE_PREDEFINED,
    member_real_dir,
)
from openjiuwen.agent_teams.team_workspace.ref_store import MemberRefStore


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path) -> Iterator[None]:
    apaths.configure_openjiuwen_home(tmp_path / "oj-home")
    yield
    apaths.reset_openjiuwen_home()


def _refs_file(team: str, member: str, mode: str = MEMBER_MODE_DYNAMIC) -> Path:
    return member_real_dir(team, member, mode) / ".refs.json"


@pytest.mark.level0
def test_add_ref_first_team_creates_file() -> None:
    store = MemberRefStore()
    count = store.add_ref("teamA", "memX", mode=MEMBER_MODE_DYNAMIC)
    assert count == 1
    data = json.loads(_refs_file("teamA", "memX").read_text(encoding="utf-8"))
    assert data == {"kind": "dynamic", "teams": ["teamA"]}


@pytest.mark.level0
def test_add_ref_idempotent_per_team() -> None:
    store = MemberRefStore()
    store.add_ref("teamA", "memX", mode=MEMBER_MODE_DYNAMIC)
    assert store.add_ref("teamA", "memX", mode=MEMBER_MODE_DYNAMIC) == 1


@pytest.mark.level0
def test_add_ref_cross_team_accumulates_for_shared_predefined() -> None:
    store = MemberRefStore()
    store.add_ref("teamA", "shared", mode=MEMBER_MODE_PREDEFINED)
    assert store.add_ref("teamB", "shared", mode=MEMBER_MODE_PREDEFINED) == 2
    assert store.get_ref_teams("teamB", "shared", mode=MEMBER_MODE_PREDEFINED) == ["teamA", "teamB"]


@pytest.mark.level0
def test_add_ref_dynamic_is_per_team_isolated() -> None:
    """Dynamic members are per-team: same member in two teams is two dirs."""
    store = MemberRefStore()
    store.add_ref("teamA", "memX", mode=MEMBER_MODE_DYNAMIC)
    assert store.add_ref("teamB", "memX", mode=MEMBER_MODE_DYNAMIC) == 1
    assert store.get_ref_teams("teamA", "memX") == ["teamA"]
    assert store.get_ref_teams("teamB", "memX") == ["teamB"]


@pytest.mark.level0
def test_remove_ref_decrements_and_removes_file_on_zero() -> None:
    store = MemberRefStore()
    store.add_ref("teamA", "shared", mode=MEMBER_MODE_PREDEFINED)
    store.add_ref("teamB", "shared", mode=MEMBER_MODE_PREDEFINED)
    assert store.remove_ref("teamA", "shared", mode=MEMBER_MODE_PREDEFINED) == 1
    assert store.remove_ref("teamB", "shared", mode=MEMBER_MODE_PREDEFINED) == 0
    assert not _refs_file("teamA", "shared", MEMBER_MODE_PREDEFINED).exists()


@pytest.mark.level0
def test_remove_ref_underflow_returns_zero() -> None:
    store = MemberRefStore()
    assert store.remove_ref("teamA", "memX") == 0


@pytest.mark.level0
def test_delete_if_zero_removes_dynamic_dir() -> None:
    store = MemberRefStore()
    store.add_ref("teamA", "memX", mode=MEMBER_MODE_DYNAMIC)
    real = member_real_dir("teamA", "memX", MEMBER_MODE_DYNAMIC)
    (real / "artifact.txt").write_text("x", encoding="utf-8")
    store.remove_ref("teamA", "memX")
    assert store.delete_if_zero("teamA", "memX") is True
    assert not real.exists()


@pytest.mark.level0
def test_delete_if_zero_keeps_predefined_dir() -> None:
    store = MemberRefStore()
    store.add_ref("teamA", "shared", mode=MEMBER_MODE_PREDEFINED)
    indep = apaths.get_agent_teams_home() / "shared"
    indep.mkdir(parents=True, exist_ok=True)
    store.remove_ref("teamA", "shared", mode=MEMBER_MODE_PREDEFINED)
    assert store.delete_if_zero("teamA", "shared", mode=MEMBER_MODE_PREDEFINED) is False
    assert indep.is_dir(), "predefined shared dir must survive zero refs"


@pytest.mark.level0
def test_delete_if_zero_keeps_leader_dir() -> None:
    store = MemberRefStore()
    store.add_ref("teamA", "leader", mode=MEMBER_MODE_LEADER)
    store.remove_ref("teamA", "leader", mode=MEMBER_MODE_LEADER)
    assert store.delete_if_zero("teamA", "leader", mode=MEMBER_MODE_LEADER) is False


@pytest.mark.level0
def test_malformed_refs_file_treated_as_empty() -> None:
    store = MemberRefStore()
    real = member_real_dir("teamA", "memX", MEMBER_MODE_DYNAMIC)
    real.mkdir(parents=True)
    (_refs_file("teamA", "memX")).write_text("{not json", encoding="utf-8")
    assert store.get_ref_count("teamA", "memX") == 0


@pytest.mark.level0
def test_legacy_count_format_normalized() -> None:
    store = MemberRefStore()
    real = member_real_dir("teamA", "memX", MEMBER_MODE_DYNAMIC)
    real.mkdir(parents=True)
    (_refs_file("teamA", "memX")).write_text(
        json.dumps({"count": 3, "kind": "dynamic"}), encoding="utf-8"
    )
    assert store.get_ref_count("teamA", "memX") == 0, "legacy count files read as empty"
    store.add_ref("teamA", "memX", mode=MEMBER_MODE_DYNAMIC)
    assert store.get_ref_teams("teamA", "memX") == ["teamA"], "rewritten with teams list"


@pytest.mark.level0
def test_delete_if_zero_returns_false_when_rmtree_fails(monkeypatch) -> None:
    """A failed rmtree must not be reported as success (no false cleanup)."""
    store = MemberRefStore()
    store.add_ref("teamA", "memX", mode=MEMBER_MODE_DYNAMIC)
    real = member_real_dir("teamA", "memX", MEMBER_MODE_DYNAMIC)
    store.remove_ref("teamA", "memX")

    def boom(*_args, **_kwargs):
        raise OSError(13, "permission denied")

    monkeypatch.setattr(shutil, "rmtree", boom)
    assert store.delete_if_zero("teamA", "memX") is False
    assert real.is_dir(), "directory survives a failed removal"


@pytest.mark.level0
def test_add_ref_and_remove_ref_run_under_cross_process_lock(monkeypatch) -> None:
    """The .refs.json read-modify-write must hold the cross-process lock (P2).

    Serializing the read-modify-write is what prevents lost updates when two
    teams reference the same shared member concurrently. The lock primitive
    itself is already covered by ``test_skill_file_lock``; this test pins that
    the store actually enters it on every mutation.
    """
    from openjiuwen.agent_teams.skill.file_lock import cross_process_file_lock, lock_path_for

    acquired: list[str] = []
    real_lock = cross_process_file_lock

    def guarded(target, **kwargs):
        acquired.append(str(target))
        return real_lock(target, **kwargs)

    monkeypatch.setattr(
        "openjiuwen.agent_teams.team_workspace.ref_store.cross_process_file_lock", guarded
    )
    store = MemberRefStore()
    store.add_ref("teamA", "memX", mode=MEMBER_MODE_DYNAMIC)
    store.remove_ref("teamA", "memX")
    assert [Path(t).name for t in acquired] == [".refs.json", ".refs.json"]
    assert lock_path_for(Path(acquired[0])).name.endswith(".refs.json.lock")
