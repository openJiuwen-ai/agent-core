# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for the Code Graph core engine (no LLM)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from openjiuwen.core.retrieval.code_graph.indexing.parser import parser_available
from openjiuwen.core.retrieval.code_graph.manager import (
    CodeGraphManager,
    reset_code_graph_manager,
)
from openjiuwen.core.retrieval.code_graph.models import CodeGraphConfig
from openjiuwen.core.retrieval.code_graph.service import CodeGraphService
from tests.unit_tests.core.retrieval.code_graph.parser_guard import skip_unless_code_graph_parser

pytestmark = pytest.mark.level0
skip_unless_code_graph_parser()

SAMPLE_USER = '''\
class UserService:
    def create_user(self, name: str) -> str:
        self._validate(name)
        return store_user(name)

    def _validate(self, name: str) -> None:
        if not name:
            raise ValueError("empty")


def store_user(name: str) -> str:
    return name
'''

SAMPLE_AUTH = '''\
from service.user import UserService

class AuthMiddleware:
    def authorize(self, user: str) -> bool:
        svc = UserService()
        svc.create_user(user)
        return True
'''

SAMPLE_ADMIN = '''\
from service.user import UserService

class AdminService(UserService):
    def create_admin(self, name: str) -> str:
        return self.create_user(name)
'''


def _write_repo(root: Path) -> Path:
    pkg = root / "src" / "service"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "user.py").write_text(SAMPLE_USER, encoding="utf-8")
    (pkg / "auth.py").write_text(SAMPLE_AUTH, encoding="utf-8")
    (pkg / "admin.py").write_text(SAMPLE_ADMIN, encoding="utf-8")
    (pkg / "convert.py").write_text(
        '''\
def transform_frame(self, obstime):
    """Shift coordinates for this frame."""
    return apply_sidereal_tracking_offset(obstime)
''',
        encoding="utf-8",
    )
    (root / "README.md").write_text(
        "sidereal tracking offset is documented here for observers.\n",
        encoding="utf-8",
    )
    return root


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return _write_repo(tmp_path / "repo")


@pytest.fixture
def service(repo: Path, tmp_path: Path) -> CodeGraphService:
    cache = tmp_path / "cache"
    return CodeGraphService(
        repo,
        CodeGraphConfig(cache_dir=str(cache), max_files=1000, query_timeout_seconds=10),
    )


@pytest.mark.asyncio
async def test_search_code_finds_method_definition(service: CodeGraphService) -> None:
    result = await service.search_code("create_user", symbol_kinds=["method"], limit=10)
    assert result["status"] == "COMPLETE"
    names = {item["name"] for item in result["matches"]}
    files = {item["file"] for item in result["matches"]}
    assert "create_user" in names
    assert any(path.endswith("user.py") for path in files)
    match = next(item for item in result["matches"] if item["name"] == "create_user")
    assert match["start_line"] >= 1
    assert match["end_line"] >= match["start_line"]
    assert "UserService.create_user" in match["symbol_id"]


@pytest.mark.asyncio
async def test_list_symbols_lists_class_methods(service: CodeGraphService) -> None:
    result = await service.list_symbols(
        file="src/service/user.py",
        parent_symbol="UserService",
        kinds=["method"],
        depth=1,
    )
    assert result["status"] == "COMPLETE"
    names = {item["name"] for item in result["symbols"]}
    assert "create_user" in names
    assert "_validate" in names


@pytest.mark.asyncio
async def test_list_symbols_on_a_file_returns_the_synthetic_module(
    service: CodeGraphService,
) -> None:
    result = await service.list_symbols(file="src/service/user.py", depth=1)
    assert result["status"] == "COMPLETE"
    names = {item["name"] for item in result["symbols"]}
    kinds = {item["kind"] for item in result["symbols"]}
    assert names == {"user"}
    assert kinds == {"module"}


@pytest.mark.asyncio
async def test_resolve_symbol_accepts_a_dotted_module_path(
    service: CodeGraphService,
) -> None:
    result = await service.resolve_symbol("src.service.user", kind="module")
    assert result["status"] == "COMPLETE"
    assert result["matches"][0]["file"].endswith("user.py")
    assert result["matches"][0]["kind"] == "module"


@pytest.mark.asyncio
async def test_resolve_symbol_dotted_class_path_needs_the_class_name(
    service: CodeGraphService,
) -> None:
    dotted = await service.resolve_symbol("service.user.UserService", kind="class")
    assert dotted["status"] == "NO_MATCH"
    result = await service.resolve_symbol("UserService", kind="class")
    assert result["status"] == "COMPLETE"
    assert result["name"] == "UserService"


@pytest.mark.asyncio
async def test_read_symbol_requires_the_indexed_symbol_id(
    service: CodeGraphService,
) -> None:
    missing = await service.read_symbol("src/service/user.py::create_user")
    assert missing["status"] == "NO_MATCH"
    listed = await service.list_symbols(
        file="src/service/user.py",
        parent_symbol="UserService",
        kinds=["method"],
        depth=1,
    )
    create_id = next(item["symbol_id"] for item in listed["symbols"] if item["name"] == "create_user")
    result = await service.read_symbol(create_id)
    assert result["status"] == "COMPLETE"
    assert result["name"] == "create_user"
    assert "create_user" in str(result.get("content") or "")


@pytest.mark.asyncio
async def test_search_text_trailing_underscore_is_not_a_prefix(
    service: CodeGraphService,
) -> None:
    from pathlib import Path

    settings = Path(service.repo_root) / "src" / "service" / "settings.py"
    settings.write_text("SECURE_BROWSER_XSS_FILTER = True\n", encoding="utf-8")
    service._index = None
    service._snapshot = None
    result = await service.search_text("SECURE_", path_prefix="src/service/settings.py", limit=5)
    assert result["status"] == "NO_MATCH"


@pytest.mark.asyncio
async def test_expand_related_inheritance_and_calls(service: CodeGraphService) -> None:
    search = await service.search_code("AdminService", symbol_kinds=["class"], limit=5)
    assert search["matches"]
    admin_id = search["matches"][0]["symbol_id"]
    inherited = await service.expand_related(admin_id, relations=["inherits"], depth=1)
    assert inherited["status"] == "COMPLETE"
    targets = {item["name"] for item in inherited["related"]}
    assert "UserService" in targets

    create = await service.search_code("UserService.create_user", symbol_kinds=["method"], limit=5)
    assert create["matches"]
    create_id = create["matches"][0]["symbol_id"]
    callers = await service.expand_related(create_id, relations=["called_by"], depth=1, limit=20)
    caller_names = {item["name"] for item in callers.get("related", [])}
    assert "authorize" in caller_names or "create_admin" in caller_names


@pytest.mark.asyncio
async def test_same_repo_does_not_rebuild_twice(service: CodeGraphService) -> None:
    first = await service.ensure_ready()
    second = await service.ensure_ready()
    assert first is second


@pytest.mark.asyncio
async def test_path_escape_is_rejected(service: CodeGraphService) -> None:
    with pytest.raises(Exception) as exc_info:
        service.resolve_path("../secret")
    assert "outside repo root" in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, ValueError)


@pytest.mark.asyncio
async def test_fork_session_is_constructed_without_mutating_privates(service: CodeGraphService) -> None:
    await service.ensure_ready()
    forked = service.fork_session()
    assert forked is not service
    assert forked._store is None
    assert forked._session_scoped is True
    assert forked._index is not None
    assert forked._index is not service._index


@pytest.mark.asyncio
async def test_disk_cache_roundtrip(repo: Path, tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    config = CodeGraphConfig(cache_dir=str(cache), max_files=1000)
    first = CodeGraphService(repo, config)
    await first.ensure_ready()
    assert list(cache.rglob("*.pkl"))

    second = CodeGraphService(repo, config)
    loaded = await second.ensure_ready()
    assert loaded.snapshot == first._snapshot
    result = await second.search_code("UserService", symbol_kinds=["class"])
    assert result["status"] == "COMPLETE"


@pytest.mark.asyncio
async def test_manager_single_flight(repo: Path) -> None:
    reset_code_graph_manager()
    manager = CodeGraphManager(max_cached_repos=2)
    config = CodeGraphConfig(max_files=1000)
    one, two = await asyncio_gather_services(manager, repo, config)
    assert one is two
    reset_code_graph_manager()


async def asyncio_gather_services(manager: CodeGraphManager, repo: Path, config: CodeGraphConfig):
    import asyncio

    return await asyncio.gather(
        manager.get_service(repo, config),
        manager.get_service(repo, config),
    )


def test_parser_available_when_language_pack_installed() -> None:
    assert parser_available() is True


@pytest.mark.asyncio
async def test_metrics_record_index_and_query(service: CodeGraphService) -> None:
    from openjiuwen.core.retrieval.code_graph.metrics import (
        reset_code_graph_metrics,
        snapshot_code_graph_metrics,
    )

    reset_code_graph_metrics()
    result = await service.search_code("create_user")
    assert result["status"] == "COMPLETE"
    snap = snapshot_code_graph_metrics()
    assert snap["totals"]["index_builds"] == 1
    assert snap["totals"]["search_code_count"] == 1
    assert snap["totals"]["index_build_ms"] >= 0
    assert snap["totals"]["query_ms"] >= 0
    assert snap["last_index"]["file_count"] >= 1
    assert snap["last_index"]["symbol_count"] >= 1


@pytest.mark.asyncio
async def test_search_code_finds_body_term_absent_from_name(service: CodeGraphService) -> None:
    result = await service.search_code("sidereal tracking offset", limit=10)
    assert result["status"] == "COMPLETE"
    names = {item["name"] for item in result["matches"]}
    assert "transform_frame" in names


@pytest.mark.asyncio
async def test_search_text_finds_markdown_chunk(repo: Path, tmp_path: Path) -> None:
    service = CodeGraphService(
        repo,
        CodeGraphConfig(
            cache_dir=str(tmp_path / "md-cache"),
            max_files=1000,
            query_timeout_seconds=10,
            index_text_files=True,
        ),
    )
    result = await service.search_text("sidereal tracking offset", limit=5)
    assert result["status"] == "COMPLETE"
    files = {item["file"] for item in result["chunks"]}
    assert "README.md" in files


@pytest.mark.asyncio
async def test_search_text_skips_markdown_by_default(service: CodeGraphService) -> None:
    result = await service.search_text("sidereal tracking offset", limit=5)
    assert result["status"] == "COMPLETE"
    files = {item["file"] for item in result["chunks"]}
    assert "README.md" not in files
    assert any(path.endswith("convert.py") for path in files)


@pytest.mark.asyncio
async def test_search_text_finds_a_definition_body(service: CodeGraphService) -> None:
    result = await service.search_text("store_user", limit=5)
    assert result["status"] == "COMPLETE"
    files = {item["file"] for item in result["chunks"]}
    assert any("user.py" in file for file in files)
    assert result["matches"] is result["chunks"]


@pytest.mark.asyncio
async def test_resolve_symbol_is_exact_and_unique(service: CodeGraphService) -> None:
    result = await service.resolve_symbol("UserService", kind="class")
    assert result["status"] == "COMPLETE"
    assert result["name"] == "UserService"
    assert result["symbol_id"]
    tools = [item["tool"] for item in result["next_actions"]]
    assert "read_symbol" in tools
    assert "find_importers" in tools


@pytest.mark.asyncio
async def test_read_symbol_returns_definition_span(service: CodeGraphService) -> None:
    resolved = await service.resolve_symbol("create_user", kind="method")
    assert resolved["status"] in {"COMPLETE", "AMBIGUOUS"}
    symbol_id = (
        resolved["symbol_id"]
        if resolved["status"] == "COMPLETE"
        else resolved["matches"][0]["symbol_id"]
    )
    result = await service.read_symbol(symbol_id, context_before=500, context_after=500)
    assert result["status"] == "COMPLETE"
    assert result["symbol_id"] == symbol_id
    assert "create_user" in str(result.get("content") or "")
    assert int(result["symbol_start_line"]) >= 1
    assert int(result["symbol_end_line"]) >= int(result["symbol_start_line"])
    assert result["submit"]["file"]
    assert result["context_before"] <= 5
    assert result["context_after"] <= 5


@pytest.mark.asyncio
async def test_read_symbol_large_class_is_preview_only(service: CodeGraphService) -> None:
    # Grow UserService into a large class so locate refuses to submit it whole.
    from pathlib import Path

    root = Path(service.repo_root)
    body = "\n".join(f"    def method_{i}(self):\n        return {i}" for i in range(40))
    (root / "src" / "service" / "user.py").write_text(
        f"class UserService:\n{body}\n",
        encoding="utf-8",
    )
    service._index = None
    service._snapshot = None
    resolved = await service.resolve_symbol("UserService", kind="class")
    assert resolved["status"] == "COMPLETE"
    tools = [item["tool"] for item in resolved["next_actions"]]
    assert "inspect_code_structure" in tools
    result = await service.read_symbol(resolved["symbol_id"])
    assert result.get("large_class") is True
    assert result.get("submit") is None
    assert int(result["end_line"]) - int(result["start_line"]) + 1 <= 40


@pytest.mark.asyncio
async def test_search_text_no_match_says_whether_the_corpus_has_the_tokens(
    service: CodeGraphService,
) -> None:
    result = await service.search_text("zzzznonexistenttoken123", limit=5)
    assert result["status"] == "NO_MATCH"
    assert result["corpus"]["definition_docs"] > 0
    assert "zzzznonexistenttoken123" in result["corpus"]["tokens_absent"]


@pytest.mark.asyncio
async def test_read_code_returns_numbered_excerpt(service: CodeGraphService) -> None:
    result = await service.read_code("src/service/user.py", start_line=1, end_line=4)
    assert result["status"] == "COMPLETE"
    assert "evidence_id" in result
    assert "     1|" in result["content"]
    assert "UserService" in result["content"]


@pytest.mark.asyncio
async def test_read_code_rejects_path_escape(service: CodeGraphService) -> None:
    result = await service.read_code("../secret")
    assert result["status"] in {"ERROR", "UNAVAILABLE"}


@pytest.mark.asyncio
async def test_get_repo_structure_lists_top_level(service: CodeGraphService) -> None:
    result = await service.get_repo_structure("create_user")
    assert result["status"] == "COMPLETE"
    names = {item["name"] for item in result["roots"]}
    assert "src" in names
    assert result["focus"]


@pytest.mark.asyncio
async def test_expand_file_defs_and_inheritance(service: CodeGraphService) -> None:
    defs = await service.expand_file_defs("src/service/user.py", query="create_user")
    names = {item["name"] for item in defs["definitions"]}
    assert "create_user" in names
    search = await service.search_code("AdminService", symbol_kinds=["class"], limit=5)
    admin_id = search["matches"][0]["symbol_id"]
    inherited = await service.expand_inheritance(admin_id)
    related_names = {item["name"] for item in inherited["related"]}
    assert "UserService" in related_names


@pytest.mark.asyncio
async def test_warm_search_does_not_retokenize_bodies(service: CodeGraphService, monkeypatch) -> None:
    await service.ensure_ready()
    from openjiuwen.core.retrieval.code_graph.query import lexical as lexical_mod

    long_calls: list[int] = []
    original = lexical_mod.tokenize

    def spy(text: str):
        if len(text) > 200:
            long_calls.append(len(text))
        return original(text)

    monkeypatch.setattr(lexical_mod, "tokenize", spy)
    result = await service.search_code("sidereal tracking offset")
    assert result["status"] == "COMPLETE"
    assert long_calls == []


@pytest.mark.asyncio
async def test_old_cache_version_is_ignored(repo: Path, tmp_path: Path) -> None:
    import pickle

    from openjiuwen.core.retrieval.code_graph.store.index_store import DiskIndexStore

    cache = tmp_path / "old-cache"
    cache.mkdir()
    store = DiskIndexStore(cache)
    key = "repo-snap-hash"
    path = store._path(key)  # noqa: SLF001
    path.write_bytes(pickle.dumps({"version": 1, "index": "stale"}, protocol=4))
    assert store.load(key) is None


@pytest.mark.asyncio
async def test_read_symbol_stays_complete_after_clean_overlay_build(repo: Path, tmp_path: Path) -> None:
    reset_code_graph_manager()
    cache = tmp_path / "cache-outside"
    manager = CodeGraphManager(max_cached_repos=2)
    cfg = CodeGraphConfig(cache_dir=str(cache), max_files=100, freshness_check_interval_ms=0)
    service = await manager.get_service(repo, cfg, ensure=True)
    resolved = await service.resolve_symbol("create_user", kind="method")
    assert resolved["status"] in {"COMPLETE", "AMBIGUOUS"}
    symbol_id = (
        resolved["symbol_id"]
        if resolved["status"] == "COMPLETE"
        else resolved["matches"][0]["symbol_id"]
    )
    result = await service.read_symbol(symbol_id)
    assert result["status"] == "COMPLETE"
    assert service.is_stale() is False


@pytest.mark.asyncio
async def test_read_symbol_complete_when_checkpoint_is_inside_git_tree(tmp_path: Path) -> None:
    import subprocess

    reset_code_graph_manager()
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src.py").write_text("def keep():\n    return 1\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "commit", "-m", "init"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    cache = repo / ".code_graph_cache"
    manager = CodeGraphManager(max_cached_repos=2)
    cfg = CodeGraphConfig(cache_dir=str(cache), max_files=50, freshness_check_interval_ms=0)
    service = await manager.get_service(repo, cfg, ensure=True)
    (cache / "noise.pkl").write_bytes(b"checkpoint")
    resolved = await service.resolve_symbol("keep")
    assert resolved["status"] == "COMPLETE"
    result = await service.read_symbol(resolved["symbol_id"])
    assert result["status"] == "COMPLETE"
    assert service.is_stale() is False


@pytest.mark.asyncio
async def test_source_edit_makes_index_stale_until_refresh(tmp_path: Path) -> None:
    reset_code_graph_manager()
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src.py").write_text("def keep():\n    return 1\n", encoding="utf-8")
    manager = CodeGraphManager(max_cached_repos=2)
    cfg = CodeGraphConfig(cache_dir=str(tmp_path / "cache"), max_files=50, freshness_check_interval_ms=0)
    service = await manager.get_service(repo, cfg, ensure=True)
    assert service.is_stale() is False
    (repo / "src.py").write_text(
        "def keep():\n    return 1\n\ndef added_later():\n    return 2\n",
        encoding="utf-8",
    )
    assert service.is_stale() is True
    await manager.ensure_fresh(repo, cfg)
    assert service.is_stale() is False
    resolved = await service.resolve_symbol("added_later")
    assert resolved["status"] == "COMPLETE"


@pytest.mark.asyncio
async def test_two_services_on_same_realpath_share_generation(tmp_path: Path) -> None:
    reset_code_graph_manager()
    real = tmp_path / "repo"
    real.mkdir()
    (real / "src.py").write_text("def keep():\n    return 1\n", encoding="utf-8")
    alias = tmp_path / "alias"
    os.symlink(real, alias)
    manager = CodeGraphManager(max_cached_repos=2)
    cfg = CodeGraphConfig(cache_dir=str(tmp_path / "cache"), max_files=50)
    first = await manager.get_service(real, cfg, ensure=True)
    second = await manager.get_service(alias, cfg, ensure=True)
    assert first is second
    from openjiuwen.core.retrieval.code_graph.identity import RepoIdentity

    assert RepoIdentity.from_path(real).repo_id == RepoIdentity.from_path(alias).repo_id

