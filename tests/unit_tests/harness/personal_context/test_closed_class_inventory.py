"""Clean-cut checks for the embedded PersonalContext production surface."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import openjiuwen.harness.personal_context as personal_context
from openjiuwen.harness.personal_context import PersonalContext

ROOT = Path(__file__).parents[4]
PERSONAL_CONTEXT = ROOT / "openjiuwen" / "harness" / "personal_context"
RAIL = ROOT / "openjiuwen" / "harness" / "rails" / "personal_context.py"
LEGACY_PACKAGE = ROOT / "openjiuwen" / "proactive_harness"

EXPECTED_CLASSES = {
    "PersonalContextFetchServiceConfig",
    "PersonalContextConfig",
    "ContextPipelineService",
    "ContextFetchService",
    "BrowserBookmarksFetchService",
    "FeishuFetchService",
    "GitHubFetchService",
    "LocalFilesFetchService",
    "ToutiaoReaderFetchService",
    "ZhihuReaderFetchService",
    "PersonalContextStatus",
    "RawChangeItem",
    "FetchBatch",
    "PersonalContext",
    "PersonalContextRail",
}


def _classes(path: Path) -> set[str]:
    files = [path] if path.is_file() else sorted(path.rglob("*.py"))
    result: set[str] = set()
    for file in files:
        tree = ast.parse(file.read_text(encoding="utf-8"))
        result.update(node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef))
    return result


def test_embedded_core_declares_only_the_core_closed_class_inventory() -> None:
    assert _classes(PERSONAL_CONTEXT) == EXPECTED_CLASSES - {"PersonalContextRail"}
    assert _classes(RAIL) == {"PersonalContextRail"}


def test_source_metadata_module_adds_no_production_class() -> None:
    source_metadata = PERSONAL_CONTEXT / "source_metadata.py"
    assert source_metadata.is_file()
    assert _classes(source_metadata) == set()


def test_embedded_core_public_surface_and_personal_context_signatures_match_contract() -> None:
    assert personal_context.__all__ == ["PersonalContext"]
    assert not inspect.iscoroutinefunction(PersonalContext.__init__)
    assert str(inspect.signature(PersonalContext)) == "(*, home: 'str | Path') -> 'None'"
    public_methods = {
        name: method
        for name, method in inspect.getmembers(PersonalContext, inspect.isfunction)
        if not name.startswith("_")
    }
    synchronous_host_methods = {"remove_fetch_cursor", "restore_fetch_cursor"}
    assert all(
        inspect.iscoroutinefunction(method) == (name not in synchronous_host_methods)
        for name, method in public_methods.items()
    )
    assert {name: str(inspect.signature(method)) for name, method in public_methods.items()} == {
        "activate_runtime": "(self) -> 'None'",
        "authorize_provider": "(self, provider: 'str') -> 'dict[str, object]'",
        "deactivate_runtime": "(self, *, timeout_seconds: 'float' = 30.0) -> 'None'",
        "get_authorization_status": "(self, provider: 'str') -> 'dict[str, object]'",
        "get_graph": "(self, *, root_id: 'str | None' = None, depth: 'int' = 3) -> 'dict[str, object]'",
        "get_graph_page": "(self, node_id: 'str') -> 'dict[str, object]'",
        "get_source": "(self, source_id: 'str') -> 'dict[str, object]'",
        "get_tree": "(self, *, root_id: 'str | None' = None, depth: 'int' = 3) -> 'dict[str, object]'",
        "remove_fetch_cursor": "(self, service_id: 'str') -> 'bytes | None'",
        "restore_fetch_cursor": "(self, service_id: 'str', payload: 'bytes | None') -> 'None'",
        "run_fetch": "(self, *, service_id: 'str | None' = None) -> 'dict[str, object]'",
        "search_graph": "(self, query: 'str') -> 'dict[str, object]'",
        "set_configuration": "(self, config: 'PersonalContextConfig') -> 'None'",
        "set_fetch_service_enabled": "(self, service_id: 'str', enabled: 'bool') -> 'None'",
        "snapshot": "(self) -> 'PersonalContextStatus'",
        "start_agent_use": "(self) -> 'None'",
        "start_collection": "(self) -> 'None'",
        "start_fetch_service": "(self, service_id: 'str') -> 'None'",
        "stop_agent_use": "(self) -> 'None'",
        "stop_collection": "(self, *, timeout_seconds: 'float' = 30.0) -> 'None'",
        "stop_fetch_service": "(self, service_id: 'str', *, timeout_seconds: 'float' = 30.0) -> 'None'",
    }


def test_legacy_proactive_harness_package_is_removed() -> None:
    assert not any(LEGACY_PACKAGE.rglob("*.py"))


def test_embedded_core_has_no_legacy_transport_or_storage_imports() -> None:
    source = "\n".join(
        file.read_text(encoding="utf-8")
        for root in (PERSONAL_CONTEXT, RAIL)
        for file in ([root] if root.is_file() else root.rglob("*.py"))
    )
    for forbidden in (
        "openjiuwen.proactive_harness",
        "FastAPI",
        "uvicorn",
        "sqlite",
        "apscheduler",
        "subprocess_runner",
    ):
        assert forbidden not in source
