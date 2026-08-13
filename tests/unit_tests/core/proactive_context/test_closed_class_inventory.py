"""Clean-cut checks for the embedded PCS production surface."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import openjiuwen.core.proactive_context as proactive_context
from openjiuwen.core.proactive_context import PCS

ROOT = Path(__file__).parents[4]
PROACTIVE_CONTEXT = ROOT / "openjiuwen" / "core" / "proactive_context"
RAIL = ROOT / "openjiuwen" / "harness" / "rails" / "proactive_context.py"
LEGACY_PACKAGE = ROOT / "openjiuwen" / "proactive_harness"

EXPECTED_CLASSES = {
    "PCSFetchServiceConfig",
    "PCSConfig",
    "ContextPipelineService",
    "ContextFetchService",
    "BrowserBookmarksFetchService",
    "FeishuFetchService",
    "GitHubFetchService",
    "LocalFilesFetchService",
    "ToutiaoReaderFetchService",
    "ZhihuReaderFetchService",
    "PCSStatus",
    "RawChangeItem",
    "FetchBatch",
    "PCS",
    "PCSContextRail",
}


def _classes(path: Path) -> set[str]:
    files = [path] if path.is_file() else sorted(path.rglob("*.py"))
    result: set[str] = set()
    for file in files:
        tree = ast.parse(file.read_text(encoding="utf-8"))
        result.update(node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef))
    return result


def test_embedded_core_declares_only_the_core_closed_class_inventory() -> None:
    assert _classes(PROACTIVE_CONTEXT) == EXPECTED_CLASSES - {"PCSContextRail"}
    assert _classes(RAIL) == {"PCSContextRail"}


def test_source_metadata_module_adds_no_production_class() -> None:
    source_metadata = PROACTIVE_CONTEXT / "source_metadata.py"
    assert source_metadata.is_file()
    assert _classes(source_metadata) == set()


def test_embedded_core_public_surface_and_pcs_signatures_match_contract() -> None:
    assert proactive_context.__all__ == ["PCS"]
    assert not inspect.iscoroutinefunction(PCS.__init__)
    assert str(inspect.signature(PCS)) == "(*, home: 'str | Path') -> 'None'"
    public_methods = {
        name: method for name, method in inspect.getmembers(PCS, inspect.isfunction) if not name.startswith("_")
    }
    assert all(inspect.iscoroutinefunction(method) for method in public_methods.values())
    assert {name: str(inspect.signature(method)) for name, method in public_methods.items()} == {
        "activate_runtime": "(self) -> 'None'",
        "authorize_provider": "(self, provider: 'str') -> 'dict[str, object]'",
        "deactivate_runtime": "(self, *, timeout_seconds: 'float' = 30.0) -> 'None'",
        "get_graph": "(self) -> 'dict[str, object]'",
        "get_graph_page": "(self, node_id: 'str') -> 'dict[str, object]'",
        "run_fetch": "(self, *, service_id: 'str | None' = None) -> 'dict[str, object]'",
        "search_graph": "(self, query: 'str') -> 'dict[str, object]'",
        "set_configuration": "(self, config: 'PCSConfig') -> 'None'",
        "snapshot": "(self) -> 'PCSStatus'",
        "start_fetch_service": "(self, service_id: 'str') -> 'None'",
        "stop_fetch_service": "(self, service_id: 'str', *, timeout_seconds: 'float' = 30.0) -> 'None'",
    }


def test_legacy_proactive_harness_package_is_removed() -> None:
    assert not any(LEGACY_PACKAGE.rglob("*.py"))


def test_embedded_core_has_no_legacy_transport_or_storage_imports() -> None:
    source = "\n".join(
        file.read_text(encoding="utf-8")
        for root in (PROACTIVE_CONTEXT, RAIL)
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
