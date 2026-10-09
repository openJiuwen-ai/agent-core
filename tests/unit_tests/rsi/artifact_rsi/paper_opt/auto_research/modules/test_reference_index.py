"""Index, containment, and source-exploration traces for OpenJiuwen references."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from openjiuwen.core.foundation.tool.base import Tool
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.extensions.rails.openjiuwen_reference_rail import (
    ReferencePathError,
    _RefSearchTool,
    normalize_reference_path,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.code_implementation.agent import (
    CodeImplementationAgent,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.code_implementation.grounding import (
    reference_trace_ok,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.code_implementation.reference_index import (
    ReferencePathError as IndexPathError,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.code_implementation.reference_index import (
    ReferenceRoots,
    clear_index_cache,
    resolve_reference_path,
    search_reference,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.experiment_design.schemas import ExperimentPlan

_MAX_FILE_BYTES = 200_000
_BANNED_PROMPT_TEXT = (
    "recipes/",
    "direct_model",
    "react_agent",
    "browser_agent",
    "custom_tool",
    "runner_lifecycle",
)


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_index_cache()
    yield
    clear_index_cache()


def _roots(tmp_path: Path) -> ReferenceRoots:
    examples = tmp_path / "examples"
    source = tmp_path / "source"
    docs = tmp_path / "docs"
    for path in (examples, source, docs):
        path.mkdir()
    return ReferenceRoots(examples=examples, source=source, docs=docs)


def _assert_explore_prompt(text: str) -> None:
    assert "When the instruction names a symbol, search that symbol" in text
    assert "infer one short query" in text
    assert "smaller reusable" in text
    assert "plain Python" in text
    for banned in _BANNED_PROMPT_TEXT:
        assert banned not in text


def _plan() -> ExperimentPlan:
    now = datetime.now(UTC)
    return ExperimentPlan(
        run_id="r1",
        design_session_id="ds",
        design_path="",
        code_agent_instruction_path="",
        created_at=now,
        updated_at=now,
        setup="few-shot prompt baseline",
        metrics=["accuracy"],
        primary_metric="accuracy",
    )


def test_default_scope_ranks_public_export_ahead_of_private(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    (roots.source / "pkg.py").write_text(
        '__all__ = ["Widget"]\n\ndef Widget():\n    """visible symbol"""\n    return 1\n\n'
        "def _Widget():\n    return 0\n",
        encoding="utf-8",
    )
    (roots.docs / "note.md").write_text("Widget is mentioned only in docs\n", encoding="utf-8")
    hits = search_reference("Widget", roots)
    assert hits
    assert hits[0].label == "public-export"
    assert hits[0].symbol == "Widget"
    assert hits[0].virtual_path == "source/openjiuwen/pkg.py"
    assert hits[0].start_line >= 3
    labels = [hit.label for hit in hits]
    assert labels.index("public-export") < labels.index("implementation-detail")
    assert "docs" not in labels


def test_docs_only_hit_requires_docs_scope(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    (roots.docs / "note.md").write_text("uniquedocstoken appears once\n", encoding="utf-8")
    assert search_reference("uniquedocstoken", roots) == []
    hits = search_reference("uniquedocstoken", roots, scopes=("docs",))
    assert hits[0].label == "docs"
    assert hits[0].virtual_path.startswith("docs/")


def test_public_export_outranks_example(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    (roots.examples / "demo.py").write_text("def Widget():\n    return 'example'\n", encoding="utf-8")
    (roots.source / "api.py").write_text(
        '__all__ = ["Widget"]\n\ndef Widget():\n    return "source"\n',
        encoding="utf-8",
    )
    hits = search_reference("Widget", roots, scopes=("source", "examples"))
    assert hits[0].label == "public-export"
    assert hits[0].virtual_path.startswith("source/openjiuwen/")


def test_search_respects_result_cap_and_snippet_length(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    for index in range(6):
        (roots.docs / f"note{index}.md").write_text(f"alpha token {index}\n", encoding="utf-8")
    hits = search_reference("alpha", roots, scopes=("docs",), max_results=2)
    assert len(hits) == 2
    assert all(len(hit.snippet) <= 240 for hit in hits)


def test_cache_invalidates_when_file_changes(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    path = roots.docs / "note.md"
    path.write_text("first marker\n", encoding="utf-8")
    assert search_reference("first", roots, scopes=("docs",))
    path.write_text("second marker\n", encoding="utf-8")
    os.utime(path, None)
    assert search_reference("second", roots, scopes=("docs",))
    assert search_reference("first", roots, scopes=("docs",)) == []


def test_malformed_python_and_odd_encoding_do_not_raise(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    (roots.source / "broken.py").write_text("def (\n# keep-broken-token\n", encoding="utf-8")
    (roots.docs / "odd.md").write_bytes(b"readable-token \xff\n")
    assert search_reference("keep-broken-token", roots, scopes=("source",))
    assert search_reference("readable-token", roots, scopes=("docs",))


def test_large_file_is_excluded(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    (roots.docs / "huge.md").write_text("x" * (_MAX_FILE_BYTES + 1) + "\nuniquelargetoken\n", encoding="utf-8")
    assert search_reference("uniquelargetoken", roots, scopes=("docs",)) == []


def test_regex_search_is_explicit(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    (roots.docs / "note.md").write_text("WidgetOne\n", encoding="utf-8")
    assert search_reference(r"W.dgetOne", roots, scopes=("docs",)) == []
    assert search_reference(r"W.dgetOne", roots, scopes=("docs",), use_regex=True)


def test_resolve_rejects_traversal_rsi_and_symlink_escape(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    with pytest.raises(IndexPathError):
        resolve_reference_path("docs/../../outside.txt", roots)
    (roots.source / "rsi").mkdir()
    (roots.source / "rsi" / "secret.py").write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(IndexPathError):
        resolve_reference_path("source/openjiuwen/rsi/secret.py", roots)
    with pytest.raises(IndexPathError):
        resolve_reference_path("recipes/direct_model/recipe.py", roots)
    link = roots.docs / "escape.md"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is not permitted")
    with pytest.raises(IndexPathError):
        resolve_reference_path("docs/escape.md", roots)


def test_docs_relative_path_still_resolves(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    target = roots.docs / "en" / "SUMMARY.md"
    target.parent.mkdir()
    target.write_text("# toc\n", encoding="utf-8")
    resolved = normalize_reference_path("en/SUMMARY.md", roots.docs, roots=roots)
    assert resolved == target.resolve()
    legacy = normalize_reference_path("docs/en/SUMMARY.md", roots.docs, roots=roots)
    assert legacy == target.resolve()


def test_rail_normalize_rejects_escape_without_roots(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    with pytest.raises(ReferencePathError):
        normalize_reference_path("../secret.md", docs)


def test_prompts_describe_source_exploration() -> None:
    class _Self:
        def _living_design_note(self, plan: ExperimentPlan) -> str:
            del plan
            return ""

    task = CodeImplementationAgent._build_task_prompt(_Self(), _plan(), "compare two methods")
    system = CodeImplementationAgent._render_system_prompt()
    _assert_explore_prompt(task)
    _assert_explore_prompt(system)
    assert "openjiuwen_ref_search" in task
    assert "openjiuwen_ref_search" in system
    assert "extensions/registry.py" not in system


def test_trace_accepts_definition_and_import_reads() -> None:
    assert reference_trace_ok(
        [
            {"name": "openjiuwen_ref_search", "query": "Model"},
            {"name": "openjiuwen_ref_read_file", "file_path": "source/openjiuwen/core/model.py"},
            {"name": "openjiuwen_ref_read_file", "file_path": "source/openjiuwen/core/client.py"},
        ]
    )
    assert not reference_trace_ok(
        [{"name": "openjiuwen_ref_read_file", "file_path": "en/SUMMARY.md"}]
    )
    assert not reference_trace_ok(
        [
            {"name": "openjiuwen_ref_search", "query": "Model"},
            {"name": "openjiuwen_ref_read_file", "file_path": "source/openjiuwen/core/model.py"},
        ]
    )


def test_search_tool_is_a_tool_and_returns_source_hit(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    (roots.source / "pkg.py").write_text(
        '__all__ = ["Widget"]\n\ndef Widget():\n    return 1\n',
        encoding="utf-8",
    )
    tool = _RefSearchTool("en", "agent", roots)
    assert isinstance(tool, Tool)
    assert tool.card.id
    result = asyncio.run(tool.invoke({"query": "Widget"}))
    assert result.success
    hits = result.data["hits"]
    assert hits[0]["virtual_path"] == "source/openjiuwen/pkg.py"
    assert hits[0]["label"] == "public-export"
    assert hits[0]["symbol"] == "Widget"


def test_trace_accepts_two_searches_when_nothing_reusable() -> None:
    searches = [
        {"name": "openjiuwen_ref_search", "query": "Widget"},
        {"name": "openjiuwen_ref_search", "scopes": ["source"], "query": "Model"},
    ]
    note = "source search found nothing reusable"
    assert reference_trace_ok(searches, assumptions=note)
    assert not reference_trace_ok(searches, assumptions="")
    docs_only = [
        {"name": "openjiuwen_ref_search", "scopes": ["docs"], "query": "Widget"},
        {"name": "openjiuwen_ref_search", "scopes": ["docs"], "query": "Model"},
    ]
    assert not reference_trace_ok(docs_only, assumptions=note)
