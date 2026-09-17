"""Source navigation must not become generated Context navigation."""

from __future__ import annotations

import asyncio
import importlib
from pathlib import Path, PurePosixPath

import pytest

from openjiuwen.harness.personal_context import context_pipeline
from openjiuwen.harness.personal_context.config import PersonalContextConfig
from openjiuwen.harness.personal_context.context_pipeline import ContextPipelineService
from openjiuwen.harness.personal_context.models import FetchBatch, RawChangeItem


def normalize(markdown: str) -> str:
    module = importlib.import_module("openjiuwen.harness.personal_context.source_markdown")
    return module.normalize_source_links(markdown)


@pytest.mark.parametrize(
    "target",
    [
        "../README.md",
        "a.md",
        "../../a.md#part",
        "#part",
        "/tmp/a.md",
        "C:\\notes\\a.md",
        "file:///tmp/a.md",
        "//server/a.md",
        "javascript:alert(1)",
    ],
)
def test_source_navigation_becomes_literal_text(target: str) -> None:
    result = normalize(f"See [guide]({target}).")
    assert "[guide](" not in result
    assert "guide（原文链接：" in result
    assert normalize(result) == result


@pytest.mark.parametrize(
    "markdown",
    [
        '[guide](https://example.test/a.md "title")',
        "![image](https://example.test/a.png)",
        "`[example](../a.md)`",
        "``[example](../a.md) ` x``",
        "```md\n[example](../a.md)\n```\n",
        "~~~\n[example](../a.md)\n~~~\n",
        "    [example](../a.md)\n",
        r"\[example](../a.md)",
    ],
)
def test_web_links_and_code_are_preserved(markdown: str) -> None:
    assert normalize(markdown) == markdown


@pytest.mark.parametrize(
    "markdown",
    [
        '[guide](<../space name.md> "title")',
        "![diagram](../image.png)",
        "[**nested [label]**](../a_(b).md)",
        "[guide][ref]\n\n[ref]: ../README.md\n",
        "[guide][]\n\n[guide]: ../README.md\n",
        "[guide]\n\n[guide]: ../README.md\n",
        '<a href="../README.md">guide</a>',
        '<img src="../image.png" alt="diagram">',
    ],
)
def test_other_source_link_forms_are_not_active(markdown: str) -> None:
    result = normalize(markdown)
    assert result != markdown
    assert normalize(result) == result
    assert "原文链接" in result


@pytest.mark.parametrize(
    "markdown",
    [
        "[guide](<../a)b.md>)",
        '[guide](../a.md "title (draft)")',
        "[guide][ref]\n\n[ref]:\n  ../README.md\n",
        "[![local image](../a.png)](https://example.test)",
        "<file:///tmp/a.md>",
    ],
)
def test_nested_and_multiline_destinations_are_literal(markdown: str) -> None:
    result = normalize(markdown)
    assert "原文链接" in result
    assert "![local image](../a.png)" not in result
    assert normalize(result) == result


def test_context_navigation_validation_is_not_relaxed(tmp_path: Path) -> None:
    with pytest.raises(Exception, match="candidate Markdown reference target is missing"):
        context_pipeline._classify_reference_target(
            "missing.md",
            page_relative=PurePosixPath("page.md"),
            context_root=tmp_path,
            final_context_root=tmp_path,
            source_root=tmp_path.parent / "source-meta",
            error=context_pipeline._pipeline_error,
        )


@pytest.mark.parametrize("markdown", ["- item\n\n    [bad](missing.md)\n", "Paragraph\n    [bad](missing.md)\n"])
def test_indented_prose_is_not_mistaken_for_code(markdown: str) -> None:
    module = importlib.import_module("openjiuwen.harness.personal_context.source_markdown")
    assert "[bad](missing.md)" in module.markdown_reference_text(markdown)
    assert "[bad](missing.md)" not in normalize(markdown)


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", ["rules", "balanced", "agent"])
async def test_all_profiles_normalize_before_creating_documents_and_blocks(tmp_path: Path, profile: str) -> None:
    config = PersonalContextConfig.from_dict(
        {
            "strategy_profile": profile,
            "collection_enabled": True,
            "agent_use_enabled": False,
            "model_client": {"client_provider": "OpenAI", "api_key": "test", "api_base": "https://example.test"}
            if profile != "rules"
            else None,
            "model_request": {"model": "test"} if profile != "rules" else None,
            "fetch_services": [],
        }
    )
    pipeline = ContextPipelineService(home=tmp_path, config=config, input_queue=asyncio.Queue())
    original = "# Guide\n\nSee [other](../other.md).\n"
    item = RawChangeItem(
        logical_id="guide",
        revision_id="r1",
        operation="upsert",
        title="Guide",
        content=original,
        original_ref="C:/docs/zh/guide.md",
        metadata={},
    )
    result = await pipeline._process_deterministic(FetchBatch(batch_id="batch", items=[item]))
    assert "[other](../other.md)" not in result["documents"][0]["markdown"]
    assert "原文链接" in result["documents"][0]["markdown"]
    assert all("[other](../other.md)" not in block["text"] for block in result["blocks"])
    assert item.content == original
