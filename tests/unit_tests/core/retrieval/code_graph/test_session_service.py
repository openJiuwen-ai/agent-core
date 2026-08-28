# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Session-sticky services: an edited repo refreshes instead of rebuilding."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

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

    def counting(repo_root, config):
        builds["n"] += 1
        return real(repo_root, config)

    monkeypatch.setattr("openjiuwen.core.retrieval.code_graph.service.build_index", counting)
    return builds


@pytest.mark.asyncio
async def test_the_same_session_gets_the_same_service(repo: Path) -> None:
    manager = CodeGraphManager(max_cached_repos=4)
    first = await manager.get_session_service(repo, _config(), session_id="s-1")
    second = await manager.get_session_service(repo, _config(), session_id="s-1")

    assert first is second


@pytest.mark.asyncio
async def test_a_refresh_after_an_edit_does_not_rebuild_the_repo(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    builds = _count_builds(monkeypatch)
    manager = CodeGraphManager(max_cached_repos=4)
    service = await manager.get_session_service(repo, _config(), session_id="s-1")
    await service.ensure_ready()
    assert builds["n"] == 1

    # Snapshots are mtime-based, so an edit within the same tick would be invisible.
    time.sleep(0.01)
    (repo / "src" / "user.py").write_text(
        SAMPLE + "\n\ndef delete_user(name: str) -> None:\n    return None\n",
        encoding="utf-8",
    )
    payload = await service.refresh_files(["src/user.py"])

    assert payload["updated"] == ["src/user.py"]
    assert builds["n"] == 1
    matches = await service.search_code("delete_user")
    assert any("delete_user" in row["symbol_id"] for row in matches["matches"])
    assert builds["n"] == 1


@pytest.mark.asyncio
async def test_a_session_edit_does_not_leak_into_the_shared_index(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = CodeGraphManager(max_cached_repos=4)
    shared = await manager.get_service(repo, _config(), ensure=True)
    shared_index = await shared.ensure_ready()
    session = await manager.get_session_service(repo, _config(), session_id="s-1")

    time.sleep(0.01)
    (repo / "src" / "user.py").write_text(
        SAMPLE + "\n\ndef only_in_session() -> None:\n    return None\n",
        encoding="utf-8",
    )
    await session.refresh_files(["src/user.py"])

    session_index = await session.ensure_ready()
    assert "src/user.py::only_in_session" in session_index.symbols
    assert "src/user.py::only_in_session" not in shared_index.symbols


@pytest.mark.asyncio
async def test_dropping_a_session_releases_its_service(repo: Path) -> None:
    manager = CodeGraphManager(max_cached_repos=4)
    first = await manager.get_session_service(repo, _config(), session_id="s-1")
    manager.drop_session("s-1")
    second = await manager.get_session_service(repo, _config(), session_id="s-1")

    assert first is not second


@pytest.mark.asyncio
async def test_two_sessions_of_one_repo_are_isolated(repo: Path) -> None:
    manager = CodeGraphManager(max_cached_repos=4)
    one = await manager.get_session_service(repo, _config(), session_id="s-1")
    two = await manager.get_session_service(repo, _config(), session_id="s-2")

    assert one is not two
