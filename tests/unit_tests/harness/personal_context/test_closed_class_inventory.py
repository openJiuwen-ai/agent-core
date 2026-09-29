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
    "ImLearningTargetConfig",
    "ImLearningConfig",
    "ContextPipelineService",
    "ContextFetchService",
    "BrowserBookmarksFetchService",
    "FeishuFetchService",
    "GitHubFetchService",
    "GitCodeFetchService",
    "LocalFilesFetchService",
    "ToutiaoReaderFetchService",
    "ZhihuReaderFetchService",
    "RssFeedFetchService",
    "PersonalContextStatus",
    "RawChangeItem",
    "FetchBatch",
    "PersonalContext",
    "PersonalContextRail",
    # im/ learning subpackage
    "ImLearningTarget",
    "ImLearningCursor",
    "ImLearningMessage",
    "ImMessageBatch",
    "ImLearningSource",
    "ImCorpusSink",
    "ImLearningFetchProvider",
    "TargetFetchOutcome",
    "BackfillState",
    "StageRunSnapshot",
    "ChangelogEntry",
    "ChangelogRepository",
    "ConsumerCursorRepository",
    "FtsIndexRepository",
    "FtsConsumer",
    "FetchPageRangeResult",
    "PersistResult",
    "NormalizedConversation",
    "NormalizedMessage",
    "NormalizedBatch",
    "ImLearningScheduler",
    "ImLearningSchedulerStatus",
    # im/ search subpackage
    "ImSearchQuery",
    "ImSearchHit",
    "ImSearchPort",
    "SqliteImSearchStore",
    "ImSearchTool",
    # distill/ subpackage (landed separately)
    "AnalyzerPort",
    "CorpusMessage",
    "CorpusPort",
    "DistillCandidates",
    "DistillRunResult",
    "FixtureCorpus",
    "LlmAnalyzer",
    "LlmPort",
    "OpenJiuwenLlm",
    # distill/ periodic schedule (f2c85aad4)
    "DistillScheduleSettings",
    "DistillScheduleConfig",
    "DistillDueDecision",
    "DistillTickResult",
    "DistillRunnerPort",
    # distill/ read-only corpus adapter over the im/ database (7a11dd67e)
    "SqliteImCorpus",
}


def _classes(path: Path) -> set[str]:
    files = [path] if path.is_file() else sorted(path.rglob("*.py"))
    result: set[str] = set()
    for file in files:
        tree = ast.parse(file.read_text(encoding="utf-8"))
        result.update(node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef))
    return result


def _method_shape(method) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Structural signature summary: (positional-or-keyword, keyword-only) names, ``self`` stripped.

    Insensitive to annotation and default-value churn that does not change
    how hosts call the surface, unlike a full ``str(signature)`` comparison.
    """
    params = list(inspect.signature(method).parameters.values())
    positional = tuple(
        p.name
        for p in params[1:]
        if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    )
    keyword_only = tuple(p.name for p in params[1:] if p.kind is inspect.Parameter.KEYWORD_ONLY)
    return positional, keyword_only


# (positional-or-keyword, keyword-only) parameter names per public method.
EXPECTED_METHOD_SHAPES = {
    "activate_runtime": ((), ()),
    "authorize_provider": (("provider",), ("reauthorize",)),
    "deactivate_runtime": ((), ("timeout_seconds",)),
    "get_authorization_status": (("provider",), ()),
    "get_fetch_run_status": (("service_id",), ("run_id",)),
    "get_graph": ((), ("root_id", "depth")),
    "get_graph_page": (("node_id",), ()),
    "get_im_learning_status": ((), ()),
    "get_source": (("source_id",), ()),
    "get_tree": ((), ("root_id", "depth")),
    "remove_fetch_cursor": (("service_id",), ()),
    "remove_fetch_run_history": (("service_id",), ()),
    "restore_fetch_cursor": (("service_id", "payload"), ()),
    "restore_fetch_run_history": (("service_id", "records"), ()),
    "run_fetch": ((), ("service_id",)),
    "run_im_learning_now": ((), ()),
    "search_graph": (("query",), ()),
    "set_configuration": (("config",), ()),
    "set_distill_corpus": (("corpus",), ()),
    "set_distill_runner": (("runner",), ()),
    "set_fetch_service_enabled": (("service_id", "enabled"), ()),
    "shutdown": ((), ()),
    "snapshot": ((), ()),
    "start_agent_use": ((), ()),
    "start_collection": ((), ()),
    "start_fetch_service": (("service_id",), ()),
    "stop_agent_use": ((), ()),
    "stop_collection": ((), ("timeout_seconds",)),
    "stop_fetch_run": (("service_id",), ()),
    "stop_fetch_service": (("service_id",), ("timeout_seconds",)),
}


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
    assert (
        str(inspect.signature(PersonalContext))
        == "(*, home: 'str | Path', im_learning_source: 'ImLearningSource | None' = None) -> 'None'"
    )
    public_methods = {
        name: method
        for name, method in inspect.getmembers(PersonalContext, inspect.isfunction)
        if not name.startswith("_")
    }
    synchronous_host_methods = {
        "remove_fetch_cursor",
        "restore_fetch_cursor",
        "remove_fetch_run_history",
        "shutdown",
        "restore_fetch_run_history",
        "set_distill_corpus",
        "set_distill_runner",
    }
    assert all(
        inspect.iscoroutinefunction(method) == (name not in synchronous_host_methods)
        for name, method in public_methods.items()
    )
    assert {name: _method_shape(method) for name, method in public_methods.items()} == EXPECTED_METHOD_SHAPES


def test_legacy_proactive_harness_package_is_removed() -> None:
    assert not any(LEGACY_PACKAGE.rglob("*.py"))


def _sqlite_exempt(file: Path) -> bool:
    # The im/ subpackage owns the dedicated im_context.db, and
    # distill/sqlite_corpus.py is a read-only corpus adapter over that same
    # database (re-exported from distill/__init__.py).
    rel = file.relative_to(PERSONAL_CONTEXT)
    return rel.parts[0] == "im" or (rel.parts[0] == "distill" and file.name in ("__init__.py", "sqlite_corpus.py"))


def test_embedded_core_has_no_legacy_transport_or_storage_imports() -> None:
    # The im/ learning subpackage owns a dedicated SQLite database by design,
    # and distill/ reads it through a read-only corpus adapter;
    # everything else stays storage-free.
    files: list[Path] = []
    for root in (PERSONAL_CONTEXT, RAIL):
        if root.is_file():
            files.append(root)
            continue
        for file in sorted(root.rglob("*.py")):
            if root.name == "personal_context" and _sqlite_exempt(file):
                continue
            files.append(file)
    source = "\n".join(file.read_text(encoding="utf-8") for file in files)
    for forbidden in (
        "openjiuwen.proactive_harness",
        "FastAPI",
        "uvicorn",
        "sqlite",
        "apscheduler",
        "subprocess_runner",
    ):
        assert forbidden not in source, f"{forbidden} must not appear outside the sqlite allowlist"
