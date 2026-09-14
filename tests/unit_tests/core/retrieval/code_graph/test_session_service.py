# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shared workspace graphs: conversations do not fork a private index."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from openjiuwen.core.retrieval.code_graph.identity import RepoIdentity
from openjiuwen.core.retrieval.code_graph.indexing.builder import build_index
from openjiuwen.core.retrieval.code_graph.manager import CodeGraphManager, reset_code_graph_manager
from openjiuwen.core.retrieval.code_graph.models import CodeGraphConfig
from tests.unit_tests.core.retrieval.code_graph.parser_guard import skip_unless_code_graph_parser

pytestmark = pytest.mark.level0
skip_unless_code_graph_parser()

SAMPLE = """\
class UserService:
    def create_user(self, name: str) -> str:
        return name
"""


def _write_repo(root: Path) -> Path:
    src = root / "src"
    src.mkdir(parents=True)
    (src / "user.py").write_text(SAMPLE, encoding="utf-8")
    return root


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return _write_repo(tmp_path / "repo")


@pytest.fixture(autouse=True)
def _reset_manager():
    reset_code_graph_manager()
    yield
    reset_code_graph_manager()


def _config() -> CodeGraphConfig:
    return CodeGraphConfig(cache_dir=None, max_files=100)


def _count_builds(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    builds = {"n": 0}
    real = build_index

    def counting(repo_root, config, **kwargs):
        builds["n"] += 1
        return real(repo_root, config, **kwargs)

    monkeypatch.setattr("openjiuwen.core.retrieval.code_graph.service.build_index", counting)
    monkeypatch.setattr("openjiuwen.core.retrieval.code_graph.manager.build_index", counting)
    return builds


@pytest.mark.asyncio
async def test_the_same_workspace_gets_the_same_service(repo: Path) -> None:
    manager = CodeGraphManager(max_cached_repos=4)
    first = await manager.get_session_service(repo, _config(), session_id="s-1")
    second = await manager.get_session_service(repo, _config(), session_id="s-2")

    assert first is second


@pytest.mark.asyncio
async def test_a_refresh_after_an_edit_does_not_rebuild_the_repo(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    builds = _count_builds(monkeypatch)
    manager = CodeGraphManager(max_cached_repos=4)
    service = await manager.get_service(repo, _config(), ensure=True)
    assert builds["n"] == 1
    entry = manager._peek_entry(repo, _config())
    assert entry is not None
    entry.last_full_build_seconds = 5.0

    time.sleep(0.01)
    (repo / "src" / "user.py").write_text(
        SAMPLE + "\n\ndef delete_user(name: str) -> None:\n    return None\n",
        encoding="utf-8",
    )
    manager.mark_dirty(repo, ["src/user.py"], config=_config())
    payload = await service.search_code("delete_user")

    assert payload["status"] in {"COMPLETE", "PARTIAL"}
    assert any("delete_user" in row["symbol_id"] for row in payload["matches"])
    assert builds["n"] == 1


@pytest.mark.asyncio
async def test_an_edit_is_visible_to_the_shared_index(repo: Path) -> None:
    manager = CodeGraphManager(max_cached_repos=4)
    cfg = _config()
    first = await manager.get_service(repo, cfg, ensure=True)
    second = await manager.get_session_service(repo, cfg, session_id="other")

    time.sleep(0.01)
    (repo / "src" / "user.py").write_text(
        SAMPLE + "\n\ndef only_after_edit() -> None:\n    return None\n",
        encoding="utf-8",
    )
    manager.mark_dirty(repo, ["src/user.py"], config=cfg)
    result = await second.search_code("only_after_edit")

    assert any("only_after_edit" in row["symbol_id"] for row in result["matches"])
    assert first is second


@pytest.mark.asyncio
async def test_two_conversations_build_once(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    builds = _count_builds(monkeypatch)
    manager = CodeGraphManager(max_cached_repos=4)
    await manager.get_session_service(repo, _config(), session_id="s-1")
    await manager.get_session_service(repo, _config(), session_id="s-2")

    assert builds["n"] == 1


@pytest.mark.asyncio
async def test_second_window_uses_the_graph_the_first_window_just_published(repo: Path) -> None:
    manager = CodeGraphManager(max_cached_repos=4)
    cfg = _config()
    window_a = await manager.get_service(repo, cfg, ensure=True)
    window_b = await manager.get_session_service(repo, cfg, session_id="chat-2")
    assert window_a is window_b

    (repo / "src" / "user.py").write_text(
        SAMPLE + "\n\ndef added_by_chat_one() -> None:\n    return None\n",
        encoding="utf-8",
    )
    manager.mark_dirty(repo, ["src/user.py"], config=cfg)
    await window_a.search_code("added_by_chat_one")
    payload = await window_b.resolve_symbol("added_by_chat_one")
    assert payload["status"] == "COMPLETE"
    assert any("added_by_chat_one" in str(row) for row in (payload.get("matches") or []))


@pytest.mark.asyncio
async def test_config_change_rebuilds_the_same_shared_entry(repo: Path) -> None:
    manager = CodeGraphManager(max_cached_repos=4)
    first_cfg = CodeGraphConfig(cache_dir=None, max_files=100)
    second_cfg = CodeGraphConfig(cache_dir=None, max_files=80)
    first = await manager.get_service(repo, first_cfg, ensure=True)
    second = await manager.get_service(repo, second_cfg, ensure=True)
    assert first is second
    assert RepoIdentity.from_path(repo).repo_id in manager._entries
    assert len(manager._entries) == 1
