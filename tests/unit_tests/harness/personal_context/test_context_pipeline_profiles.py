from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, cast

import pytest

import openjiuwen.harness.personal_context.context_pipeline as context_pipeline
from openjiuwen.core.common.exception.errors import BaseError, ExecutionError
from openjiuwen.harness.personal_context.config import PersonalContextConfig
from openjiuwen.harness.personal_context.context_pipeline import (
    ContextPipelineService,
    _bounded_validation_errors,
    _load_agent_json,
    _prepare_agent_candidate,
    _profile_fallback_allowed,
    _validate_agent_candidate,
    _validate_agent_pages,
)
from openjiuwen.harness.personal_context.models import FetchBatch, RawChangeItem
from openjiuwen.harness.personal_context.source_metadata import upsert_source_metadata
from openjiuwen.harness.personal_context.status_codes import StatusCode, build_error


def _config(
    profile: str,
    *,
    max_pages_per_directory: int = 20,
    max_subdirectories_per_directory: int = 20,
) -> PersonalContextConfig:
    model = {"client_provider": "OpenAI", "api_key": "secret", "api_base": "https://example.test"}
    request = {"model": "test"}
    return PersonalContextConfig.from_dict(
        {
            "collection_enabled": True,
            "agent_use_enabled": False,
            "strategy_profile": profile,
            "model_client": model if profile != "rules" else None,
            "model_request": request if profile != "rules" else None,
            "max_pages_per_directory": max_pages_per_directory,
            "max_subdirectories_per_directory": max_subdirectories_per_directory,
            "fetch_services": [],
        }
    )


def _batch(
    *,
    content: str = "First paragraph.",
    raw_snapshot: str | bytes | None = None,
    materialized_source_path: str | None = None,
    materialized_revision: str | None = None,
    metadata: dict[str, object] | None = None,
    original_ref: str = "file:///notes/one",
) -> FetchBatch:
    return FetchBatch(
        batch_id="batch-1",
        items=[
            RawChangeItem(
                logical_id="notes/one",
                revision_id="rev-1",
                operation="upsert",
                title="One",
                content=content,
                original_ref=original_ref,
                metadata=metadata or {},
                raw_snapshot=raw_snapshot,
            )
        ],
        materialized_source_path=materialized_source_path,
        materialized_revision=materialized_revision,
    )


def _top_level_headings(markdown: str) -> list[str]:
    headings: list[str] = []
    in_fence = False
    for line in markdown.splitlines():
        stripped = line.lstrip()
        if stripped.startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if not in_fence and line.startswith("# "):
            headings.append(line)
    return headings


def _processing_batch(item_count: int) -> FetchBatch:
    items = [
        RawChangeItem(
            logical_id=f"notes/{index}",
            revision_id=f"rev-{index}",
            operation="upsert",
            title=f"Note {index}",
            content=f"Source content {index}.",
            original_ref=f"file:///notes/{index}",
            metadata={"index": index},
        )
        for index in range(item_count)
    ]
    return FetchBatch(batch_id="batch-processing", items=items)


async def _submit_run(queue: asyncio.Queue[object], batch: FetchBatch) -> None:
    for tag, payload in (("batch", batch), ("finish", None)):
        completion = asyncio.get_running_loop().create_future()
        await queue.put((tag, "local", "run-1", payload, completion))
        await completion


def _write_filesystem_agent_candidate(sandbox: Path) -> None:
    page = sandbox / "context" / "topics" / "agent.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        "# Agent filesystem result.\n\nAgent-authored knowledge. [[ref:0]]\n",
        encoding="utf-8",
    )
    (page.parent / "description.md").write_text(
        "# Topics\n\n- [Agent](agent.md)\n",
        encoding="utf-8",
    )
    (sandbox / "context" / "description.md").write_text(
        "# Agent root\n\n- [Topics](topics/description.md)\n",
        encoding="utf-8",
    )


def _write_atomic_source(
    source_root: Path,
    *,
    locator: str = "file:///sources/one.md",
    title: str = "Source One",
    provider: str = "local_files",
    observed_at: str = "2026-08-12T00:00:00Z",
) -> str:
    return upsert_source_metadata(
        source_root,
        RawChangeItem(
            logical_id=locator,
            revision_id="rev-1",
            operation="upsert",
            title=title,
            content="source body",
            original_ref=locator,
            metadata={},
        ),
        provider=provider,
        service_id="local",
        observed_at=observed_at,
    )


def _source_link(
    *,
    page_relative: str,
    final_context_root: Path,
    source_root: Path,
    source_id: str,
    label: str | None = None,
) -> str:
    target = os.path.relpath(
        source_root / f"{source_id}.md",
        start=(final_context_root / page_relative).parent,
    ).replace("\\", "/")
    return f"[{label or source_id}]({target})"


def _assert_new_wiki_prompt(prompt: str) -> None:
    assert "[[ref:N]]" in prompt
    assert "already present" in prompt
    assert "origin or evidence" in prompt
    assert "mention or association" in prompt
    assert "does not by itself mean support, proof, agreement, or endorsement" in prompt
    assert "multiple sources" in prompt
    assert "multiple pages" in prompt
    assert "unrelated reference" in prompt
    assert "entities, concepts, claims, and concrete facts" in prompt
    assert "existing Wiki" in prompt
    assert "contradictions, time differences, and uncertainty" in prompt
    assert "smallest coherent change" in prompt
    assert "update or merge existing pages" in prompt
    assert "cross-links" in prompt
    assert "description.md" in prompt
    assert "待核实" in prompt
    assert "per-source summary page" in prompt
    assert "index.md, log.md, or overview.md" in prompt
    assert "personal_context_provenance_manifest.json" not in prompt
    assert "provenance mapping" not in prompt.casefold()
    assert "source-proof" not in prompt.casefold()


@pytest.mark.asyncio
async def test_filesystem_rules_normalizes_legacy_root_page_before_increment(tmp_path: Path) -> None:
    service = ContextPipelineService(home=tmp_path, config=_config("rules"), input_queue=asyncio.Queue())
    context_root = tmp_path / "workspace" / "context"
    source_root = tmp_path / "workspace" / "source-meta"
    source_id = _write_atomic_source(source_root, title="旧根页来源")
    context_root.mkdir(parents=True)
    (context_root / "description.md").write_text(
        "# Context\n\n- [旧根页](旧根页.md)\n",
        encoding="utf-8",
    )
    (context_root / "旧根页.md").write_text(
        f"# 主动上下文迁移\n\n旧内容。\n\n[来源](../source-meta/{source_id}.md)\n",
        encoding="utf-8",
    )
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()

    result = await service._filesystem_with_fallback(
        processed={"documents": [], "blocks": [], "deleted_ids": []},
        sandbox=sandbox,
        batch=_processing_batch(0),
        run_time=datetime(2026, 9, 2, tzinfo=timezone.utc),
    )

    assert result == "rules"
    assert not (sandbox / "context" / "旧根页.md").exists()
    moved = next(
        path
        for path in (sandbox / "context").rglob("*.md")
        if path.name != "description.md" and "旧内容。" in path.read_text(encoding="utf-8")
    )
    assert moved.parent != sandbox / "context"
    assert "../../source-meta/" in moved.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_agent_fallback_does_not_migrate_invalid_legacy_root_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = ContextPipelineService(home=tmp_path, config=_config("agent"), input_queue=asyncio.Queue())
    context_root = tmp_path / "workspace" / "context"
    source_root = tmp_path / "workspace" / "source-meta"
    source_id = _write_atomic_source(source_root, title="迁移来源")
    context_root.mkdir(parents=True)
    (context_root / "description.md").write_text("# Context\n\n- [旧根页](旧根页.md)\n", encoding="utf-8")
    (context_root / "旧根页.md").write_text(
        f"# 迁移主题\n\n旧内容。\n\n[来源](../source-meta/{source_id}.md)\n",
        encoding="utf-8",
    )
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    original_plan = context_pipeline._plan_context_layout_normalization
    planned_from_clean_copy: list[bool] = []

    def plan_spy(candidate_root: Path, **kwargs: object) -> dict[str, str]:
        planned_from_clean_copy.append((candidate_root / "旧根页.md").is_file())
        return original_plan(candidate_root, **kwargs)  # type: ignore[arg-type]

    async def failed_agent(**kwargs: object) -> str:
        del kwargs
        raise build_error(StatusCode.CONTEXT_PROACTIVE_PIPELINE_EXECUTION_ERROR, error_msg="agent failed")

    async def failed_balanced(**kwargs: object) -> tuple[set[str], int]:
        del kwargs
        raise build_error(StatusCode.CONTEXT_PROACTIVE_PIPELINE_EXECUTION_ERROR, error_msg="balanced failed")

    monkeypatch.setattr(context_pipeline, "_plan_context_layout_normalization", plan_spy)
    monkeypatch.setattr(context_pipeline, "run_personal_context_agent", failed_agent)
    monkeypatch.setattr(service, "_filesystem_balanced_model_attempt", failed_balanced)

    with pytest.raises(ExecutionError, match="root may only contain"):
        await service._filesystem_with_fallback(
            processed={"documents": [], "blocks": [], "deleted_ids": []},
            sandbox=sandbox,
            batch=_processing_batch(0),
            run_time=datetime(2026, 9, 2, tzinfo=timezone.utc),
        )

    assert planned_from_clean_copy == [True]
    assert (context_root / "旧根页.md").is_file()
    assert not any(path.is_dir() for path in context_root.iterdir())


def _message_profile(messages: list[object], kwargs: dict[str, object]) -> str:
    configured = kwargs.get("profile")
    if isinstance(configured, str):
        return configured
    content = str(getattr(messages[0], "content", ""))
    payload = json.loads(content if content.lstrip().startswith("{") else content.split("\n", 1)[1])
    return str(payload["profile"])


class _FakeDirectModel:
    instances: list["_FakeDirectModel"] = []
    outputs: list[object] = []

    def __init__(self, *, model_client_config: object, model_config: object) -> None:
        self.model_client_config = model_client_config
        self.model_config = model_config
        self.calls: list[tuple[list[object], dict[str, object]]] = []
        self.__class__.instances.append(self)

    async def invoke(self, messages: list[object], **kwargs: object) -> object:
        self.calls.append((list(messages), dict(kwargs)))
        if not self.__class__.outputs:
            raise AssertionError("fake direct model output queue is empty")
        output = self.__class__.outputs.pop(0)
        if isinstance(output, BaseException):
            raise output
        return output


def _page_model_calls() -> list[tuple[list[object], dict[str, object]]]:
    """Count page batches separately from the final-tree directory requests."""
    return [
        call
        for instance in _FakeDirectModel.instances
        for call in instance.calls
        if "items" in json.loads(str(getattr(call[0][0], "content", "")).split("\n", 1)[1])
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", ["rules", "balanced", "agent"])
async def test_processing_is_deterministic_for_every_total_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    profile: str,
) -> None:
    service = ContextPipelineService(home=tmp_path, config=_config(profile), input_queue=asyncio.Queue())
    batch = _processing_batch(2)

    class UnexpectedProcessingModel:
        def __init__(self, **kwargs: object) -> None:
            del kwargs
            raise AssertionError("Processing must not construct a Model")

    async def unexpected_processing_agent(**kwargs: object) -> str:
        del kwargs
        raise AssertionError("Processing must not call DeepAgent")

    monkeypatch.setattr(context_pipeline, "Model", UnexpectedProcessingModel)
    monkeypatch.setattr(context_pipeline, "run_personal_context_agent", unexpected_processing_agent)

    result = await service._process_deterministic(batch)

    assert result["actual_profile"] == "deterministic"
    documents = result["documents"]
    assert isinstance(documents, list)
    assert [document["logical_id"] for document in documents] == ["notes/0", "notes/1"]
    assert [document["revision_id"] for document in documents] == ["rev-0", "rev-1"]
    assert [document["title"] for document in documents] == ["Note 0", "Note 1"]
    assert [document["markdown"] for document in documents] == ["Source content 0.\n", "Source content 1.\n"]
    assert all(document["actual_profile"] == "deterministic" for document in documents)
    assert result["deleted_ids"] == []
    blocks = result["blocks"]
    assert isinstance(blocks, list)
    assert [block["logical_id"] for block in blocks] == ["notes/0", "notes/1"]
    assert [block["text"] for block in blocks] == ["Source content 0.", "Source content 1."]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("total_profile", "expected_filesystem_model_calls", "expected_agent_calls"),
    [
        ("rules", 0, 0),
        ("balanced", 1, 0),
        ("agent", 0, 1),
    ],
)
async def test_total_profile_maps_processing_and_filesystem_stages_independently(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    total_profile: str,
    expected_filesystem_model_calls: int,
    expected_agent_calls: int,
) -> None:
    queue: asyncio.Queue[object] = asyncio.Queue(maxsize=4)
    service = ContextPipelineService(home=tmp_path, config=_config(total_profile), input_queue=queue)
    agent_prompts: list[str] = []
    published_profiles: list[str] = []
    original_publish = service._publish_processed

    async def agent_spy(*, messages: list[object], sandbox_path: Path, **kwargs: object) -> str:
        del kwargs
        prompt = str(getattr(messages[0], "content", ""))
        agent_prompts.append(prompt)
        _assert_new_wiki_prompt(prompt)
        _write_filesystem_agent_candidate(sandbox_path)
        return "done"

    filesystem_output = json.dumps(
        {
            "items": [
                {
                    "item_index": 0,
                    "summary": "Balanced filesystem summary.",
                    "page_title": "Balanced filesystem page",
                    "keywords": [str("Balanced filesystem page")[:40]],
                }
            ],
        }
    )
    _FakeDirectModel.instances.clear()
    _FakeDirectModel.outputs = {
        "rules": [],
        "balanced": [filesystem_output],
        "agent": [],
    }[total_profile]

    async def publish_spy(**kwargs: object) -> None:
        processed = kwargs["processed"]
        assert isinstance(processed, dict)
        published_profiles.append(str(processed["actual_profile"]))
        await original_publish(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(context_pipeline, "Model", _FakeDirectModel)
    monkeypatch.setattr(context_pipeline, "run_personal_context_agent", agent_spy)
    monkeypatch.setattr(service, "_publish_processed", publish_spy)

    await service.start()
    await _submit_run(queue, _batch())
    await service.stop(timeout_seconds=1)

    model_prompts = [
        str(getattr(messages[0], "content", "")) for model in _FakeDirectModel.instances for messages, _ in model.calls
    ]
    assert all("This is the Processing stage" not in prompt for prompt in model_prompts)
    assert sum("Return JSON with exactly one top-level items array" in prompt for prompt in model_prompts) == (
        expected_filesystem_model_calls
    )
    if total_profile == "balanced":
        balanced_prompt = next(
            prompt for prompt in model_prompts if "Return JSON with exactly one top-level items array" in prompt
        )
        balanced_payload = json.loads(balanced_prompt.split("\n", 1)[1])
        assert set(balanced_payload) == {"items"}
        assert len(balanced_payload["items"]) == 1
        assert set(balanced_payload["items"][0]) == {
            "item_index",
            "title",
            "headings",
            "preview",
            "provider",
            "source_type",
            "service",
        }
        assert "pages" not in balanced_payload
    assert len(agent_prompts) == expected_agent_calls
    assert published_profiles == [total_profile]
    assert all("existing context/description.md" in prompt for prompt in agent_prompts)
    assert all("This is a small run: use the bounded document_previews" in prompt for prompt in agent_prompts)
    assert all("read every bounded source_preview" not in prompt for prompt in agent_prompts)
    assert all(
        "Do not create one page per source merely to satisfy this instruction" in prompt for prompt in agent_prompts
    )
    assert not (tmp_path / "workspace" / "source-proofs").exists()
    context_root = tmp_path / "workspace" / "context"
    published_markdown = "\n".join(path.read_text(encoding="utf-8") for path in context_root.rglob("*.md"))
    assert "[[ref:" not in published_markdown
    assert "../source-meta/src_" in published_markdown
    context_pipeline._validate_reference_graph(
        context_root,
        final_context_root=context_root,
        source_root=tmp_path / "workspace" / "source-meta",
        repairable=False,
    )


@pytest.mark.asyncio
async def test_total_agent_filesystem_starts_after_deterministic_processing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue: asyncio.Queue[object] = asyncio.Queue(maxsize=4)
    service = ContextPipelineService(home=tmp_path, config=_config("agent"), input_queue=queue)
    agent_prompts: list[str] = []

    async def filesystem_agent_spy(*, messages: list[object], sandbox_path: Path, **kwargs: object) -> str:
        del kwargs
        prompt = str(getattr(messages[0], "content", ""))
        agent_prompts.append(prompt)
        if "Use the sandbox filesystem" not in prompt:
            raise build_error(
                StatusCode.CONTEXT_PROACTIVE_PIPELINE_EXECUTION_ERROR,
                error_msg="Processing must not call DeepAgent",
            )
        _write_filesystem_agent_candidate(sandbox_path)
        return "done"

    _FakeDirectModel.instances.clear()
    _FakeDirectModel.outputs = []
    monkeypatch.setattr(context_pipeline, "Model", _FakeDirectModel)
    monkeypatch.setattr(context_pipeline, "run_personal_context_agent", filesystem_agent_spy)

    await service.start()
    await _submit_run(queue, _batch())
    await service.stop(timeout_seconds=1)

    assert _FakeDirectModel.instances == []
    assert len(agent_prompts) == 1
    _assert_new_wiki_prompt(agent_prompts[0])
    assert not (tmp_path / "workspace" / "source-proofs").exists()


def test_changed_context_paths_uses_markdown_file_diff(tmp_path: Path) -> None:
    context = tmp_path / "context"
    page = context / "topics" / "page.md"
    removed = context / "topics" / "removed.md"
    description = context / "description.md"
    page.parent.mkdir(parents=True)
    page.write_text("# Page\n\nOld.\n", encoding="utf-8")
    removed.write_text("# Removed\n", encoding="utf-8")
    description.write_text("# Context\n", encoding="utf-8")
    baseline = context_pipeline._snapshot_managed_files(context)

    page.write_text("# Page\n\nUpdated.\n", encoding="utf-8")
    removed.unlink()
    description.write_text("# Context\n\nUpdated.\n", encoding="utf-8")
    (context / "topics" / "new.md").write_text("# New\n", encoding="utf-8")
    (context / "ignored.txt").write_text("not Markdown", encoding="utf-8")

    assert context_pipeline._changed_context_paths(context, baseline) == {
        "description.md",
        "topics/new.md",
        "topics/page.md",
        "topics/removed.md",
    }


def test_agent_sandbox_rejects_removed_manifest_contract(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    (sandbox / "context").mkdir(parents=True)
    (sandbox / "personal_context_provenance_manifest.json").write_text("{}", encoding="utf-8")

    with pytest.raises(Exception) as raised:
        context_pipeline._validate_agent_sandbox_layout(
            sandbox,
            materialized_baseline=None,
            inputs_baseline=None,
        )
    assert getattr(raised.value, "status", None) == StatusCode.CONTEXT_PROACTIVE_PUBLISH_EXECUTION_ERROR


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("主动上下文", "主动上下文"),
        ("OpenAI Agents SDK 开发指南", "OpenAI Agents SDK"),
        ("RAG/LLM：检索实践", "RAG-LLM-检索实践"),
        ("ＯｐｅｎＡＩ　开发", "OpenAI 开发"),
        ("OpenAI", "OpenAI"),
    ],
)
def test_balanced_topic_name_preserves_safe_unicode(title: str, expected: str) -> None:
    assert context_pipeline._safe_balanced_topic_name(title) == expected


def test_balanced_topic_name_uses_chinese_fallback_and_respects_limits() -> None:
    illegal = '<>:"/\\|?*'
    fallback = context_pipeline._safe_balanced_topic_name(illegal)

    assert fallback == f"主题-{context_pipeline._digest(illegal)[:12]}"
    assert context_pipeline._portable_context_segment_is_safe(fallback)

    reserved = context_pipeline._safe_balanced_topic_name("CON")
    assert reserved == f"主题-{context_pipeline._digest('CON')[:12]}"
    assert context_pipeline._portable_context_segment_is_safe(reserved)

    long_title = "中" * 100
    shortened = context_pipeline._safe_balanced_topic_name(long_title)
    assert len(shortened) == 20
    assert context_pipeline._portable_context_segment_is_safe(shortened)

    byte_heavy = "中" * 79 + "😀"
    byte_shortened = context_pipeline._safe_balanced_topic_name(byte_heavy)
    assert len(byte_shortened) <= 20
    assert len(byte_shortened.encode("utf-8")) <= 240
    assert context_pipeline._portable_context_segment_is_safe(byte_shortened)


def test_semantic_collision_uses_stable_suffix_instead_of_reusing_different_h1(tmp_path: Path) -> None:
    context_root = tmp_path / "context"
    occupied = context_root / "统一主题"
    occupied.mkdir(parents=True)
    (occupied / "description.md").write_text("# 完全不同的旅行主题\n", encoding="utf-8")

    first = context_pipeline._semantic_directory_candidate(
        context_root,
        title="统一主题",
        seed="1234567890abcdef",
    )
    first.mkdir()
    (first / "description.md").write_text("# 统一主题\n", encoding="utf-8")
    second = context_pipeline._semantic_directory_candidate(
        context_root,
        title="统一主题",
        seed="1234567890abcdef",
    )

    assert first == second
    assert first != occupied
    assert first.name.endswith("-12345678")


def test_semantic_collision_reuses_only_matching_h1_across_casefold_and_nfc_equivalent_siblings(
    tmp_path: Path,
) -> None:
    context_root = tmp_path / "context"
    casefold_sibling = context_root / "OPENAI"
    casefold_sibling.mkdir(parents=True)
    (casefold_sibling / "description.md").write_text("# OpenAI\n", encoding="utf-8")
    nfd_sibling = context_root / "Cafe\u0301"
    nfd_sibling.mkdir()
    (nfd_sibling / "description.md").write_text("# Café\n", encoding="utf-8")

    assert (
        context_pipeline._semantic_directory_candidate(
            context_root,
            title="openai",
            seed="aaaaaaaaaaaaaaaa",
        )
        == casefold_sibling
    )
    assert (
        context_pipeline._semantic_directory_candidate(
            context_root,
            title="Café",
            seed="bbbbbbbbbbbbbbbb",
        )
        == nfd_sibling
    )


def test_semantic_collision_uses_suffix_when_equivalent_sibling_has_no_h1(tmp_path: Path) -> None:
    context_root = tmp_path / "context"
    missing_identity = context_root / "OPENAI"
    missing_identity.mkdir(parents=True)

    candidate = context_pipeline._semantic_directory_candidate(
        context_root,
        title="openai",
        seed="cccccccccccccccc",
    )

    assert candidate != missing_identity
    assert candidate.name.endswith("-cccccccc")


def test_semantic_name_prefers_natural_boundary_and_removes_source_extension() -> None:
    name = context_pipeline._safe_semantic_name("EvoSkill Automated Skill Discovery for Multi-Agent Systems.pdf")

    assert name == "EvoSkill Automated"
    assert len(name) <= context_pipeline._MAX_SEMANTIC_NAME_CHARS
    assert not name.casefold().endswith(".pdf")


def test_semantic_name_uses_conventional_commit_subject_without_broken_wrapper() -> None:
    name = context_pipeline._safe_semantic_name("fix(capabilities): prevent tool retries after invalid output.pdf")

    assert name == "prevent tool retries"
    assert "fix(" not in name
    assert len(name) <= 20


def test_semantic_name_keeps_complete_camel_case_token_before_ellipsis() -> None:
    name = context_pipeline._safe_semantic_name("MessageSummaryOffload")

    assert name == "MessageSummary…"
    assert "MessageSummaryOffloa" not in name
    assert len(name) <= 20


def test_semantic_page_stem_reserves_digest_inside_twenty_characters() -> None:
    stem = context_pipeline._semantic_page_stem("超长中文资料标题" * 4, suffix="a31f2c78")

    assert stem.endswith("-a31f2c78")
    assert len(stem) <= 20
    assert context_pipeline._semantic_context_segment_is_safe(f"{stem}.md", markdown_file=True)


def test_semantic_name_hard_truncates_only_without_a_useful_boundary() -> None:
    assert context_pipeline._safe_semantic_name("中" * 21) == ("中" * 19) + "…"
    assert context_pipeline._safe_semantic_name("能力治理（实验版本尚未结束") == "能力治理"


@pytest.mark.parametrize(
    "title",
    [
        "Claude Code CLI 模式与 Terminal 闭环工作流",
        "大模型行业 2026：路线之争、价格战与模应一体",
    ],
)
def test_semantic_name_does_not_leave_a_truncated_chinese_connector(title: str) -> None:
    name = context_pipeline._safe_semantic_name(title)

    assert len(name) <= 20
    assert not name.endswith(("以及", "与", "和", "或", "及", "的"))


def test_semantic_name_suffix_reserves_the_complete_twenty_character_budget() -> None:
    stem = context_pipeline._semantic_page_stem("MessageSummaryOffload", suffix="a31f2c")

    assert stem.endswith("-a31f2c")
    assert len(stem) <= 20
    assert "Offloa" not in stem


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("item_count", "expected_calls", "expected_profile"),
    [
        (0, 0, "rules"),
        (1, 1, "balanced"),
        (5, 1, "balanced"),
        (6, 2, "balanced"),
        (10, 2, "balanced"),
        (20, 4, "balanced"),
    ],
)
async def test_balanced_groups_at_most_five_upserts_without_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    item_count: int,
    expected_calls: int,
    expected_profile: str,
) -> None:
    service = ContextPipelineService(home=tmp_path, config=_config("balanced"), input_queue=asyncio.Queue())
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    documents = [
        {
            "logical_id": f"notes/{index}",
            "revision_id": f"rev-{index}",
            "title": f"Note {index}",
            "markdown": f"Source content {index}.\n",
        }
        for index in range(item_count)
    ]
    outputs: list[str] = []
    for start in range(0, item_count, 5):
        outputs.append(
            json.dumps(
                {
                    "items": [
                        {
                            "item_index": index,
                            "summary": f"模型摘要 {index}。",
                            "page_title": f"笔记 {index}",
                            "keywords": [str(f"笔记 {index}")[:40]],
                        }
                        for index in range(start, min(start + 5, item_count))
                    ]
                }
            )
        )
    _FakeDirectModel.instances.clear()
    _FakeDirectModel.outputs = outputs
    monkeypatch.setattr(context_pipeline, "Model", _FakeDirectModel)

    result = await service._filesystem_with_fallback(
        processed={"documents": documents, "blocks": [], "deleted_ids": []},
        sandbox=sandbox,
        batch=_processing_batch(item_count),
        service_id="local",
    )

    calls = _page_model_calls()
    assert result == expected_profile
    assert len(calls) == expected_calls
    for messages, kwargs in calls:
        assert kwargs == {}
        assert len(messages) == 1
        content = str(getattr(messages[0], "content", ""))
        assert "complete body" not in content
        assert '"pages"' not in content
        payload = json.loads(content.split("\n", 1)[1])
        assert 1 <= len(payload["items"]) <= 5
        assert all(
            set(item) == {"item_index", "title", "headings", "preview", "provider", "source_type", "service"}
            for item in payload["items"]
        )
        assert "item_index, summary, keywords, page_title" in content
        assert "Simplified Chinese" in content
        assert "Retain accurate English names" in content
        assert "Never return directories, paths" in content
    if item_count:
        managed_pages = context_pipeline._managed_pages_by_source(sandbox / "context")
        assert len(managed_pages) == item_count
        source_text = "\n".join(path.read_text(encoding="utf-8") for path in managed_pages.values())
        for index in range(item_count):
            assert f"模型摘要 {index}。" in source_text
            assert f"# 笔记 {index}" in source_text
        assert sorted(path.name for path in (sandbox / "context").iterdir() if path.is_file()) == ["description.md"]
        assert not (sandbox / "context" / "sources").exists()
        assert not (sandbox / "context" / "topics").exists()


@pytest.mark.asyncio
async def test_balanced_uses_shared_configured_capacity_in_prompt_and_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ContextPipelineService(
        home=tmp_path,
        config=_config("balanced", max_pages_per_directory=2, max_subdirectories_per_directory=3),
        input_queue=asyncio.Queue(),
    )
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    documents = [
        {
            "logical_id": f"notes/{index}",
            "revision_id": f"rev-{index}",
            "title": f"检索笔记 {index}",
            "markdown": "BM25 检索排序与索引。\n",
        }
        for index in range(3)
    ]
    _FakeDirectModel.instances.clear()
    _FakeDirectModel.outputs = [
        json.dumps(
            {
                "items": [
                    {
                        "item_index": index,
                        "summary": f"模型摘要 {index}。",
                        "page_title": f"检索页面 {index}",
                        "keywords": [str(f"检索页面 {index}")[:40]],
                    }
                    for index in range(3)
                ]
            },
            ensure_ascii=False,
        )
    ]
    monkeypatch.setattr(context_pipeline, "Model", _FakeDirectModel)

    result = await service._filesystem_with_fallback(
        processed={"documents": documents, "blocks": [], "deleted_ids": []},
        sandbox=sandbox,
        batch=_processing_batch(3),
        service_id="local",
    )

    assert result == "balanced"
    calls = _page_model_calls()
    assert len(calls) == 1
    payload = json.loads(str(getattr(calls[0][0][0], "content", "")).split("\n", 1)[1])
    assert all("candidates" not in item for item in payload["items"])
    directory_payloads = [
        json.loads(str(getattr(messages[0], "content", "")).split("\n", 1)[1])
        for instance in _FakeDirectModel.instances
        for messages, _kwargs in instance.calls
        if "directory_id" in json.loads(str(getattr(messages[0], "content", "")).split("\n", 1)[1])
    ]
    assert directory_payloads
    assert all(len(payload["files"]) <= 2 and len(payload["subdirectories"]) <= 3 for payload in directory_payloads)
    context_pipeline._validate_context_capacities(
        sandbox / "context",
        max_pages_per_directory=2,
        max_subdirectories_per_directory=3,
    )


@pytest.mark.asyncio
async def test_balanced_enriches_pages_without_rewriting_existing_directory_body(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context_root = tmp_path / "workspace" / "context"
    existing = context_root / "openjiuwen"
    existing.mkdir(parents=True)
    root_body = "根语义正文保持不变。"
    existing_body = "OpenJiuWen 目录正文保持不变。"
    (context_root / "description.md").write_text(
        f"# Agent 门户\n\n- [OpenJiuWen](openjiuwen/description.md)\n\n{root_body}\n",
        encoding="utf-8",
    )
    existing_description = existing / "description.md"
    existing_description.write_text(f"# OpenJiuWen\n\n{existing_body}\n", encoding="utf-8")
    documents = [
        {
            "logical_id": "notes/openjiuwen",
            "revision_id": "rev-0",
            "title": "OpenJiuWen Rail 接入",
            "markdown": "Rail integration details.\n",
        },
        {
            "logical_id": "notes/proactive",
            "revision_id": "rev-1",
            "title": "主动上下文设计",
            "markdown": "PersonalContext design.\n",
        },
    ]
    _FakeDirectModel.instances.clear()
    _FakeDirectModel.outputs = [
        json.dumps(
            {
                "items": [
                    {
                        "item_index": 0,
                        "summary": "OpenJiuWen Rail 的有界摘要。",
                        "page_title": "OpenJiuWen Rail 接入说明",
                        "keywords": [str("OpenJiuWen Rail 接入说明")[:40]],
                    },
                    {
                        "item_index": 1,
                        "summary": "主动上下文的有界摘要。",
                        "page_title": "主动上下文设计说明",
                        "keywords": [str("主动上下文设计说明")[:40]],
                    },
                ]
            },
            ensure_ascii=False,
        )
    ]
    monkeypatch.setattr(context_pipeline, "Model", _FakeDirectModel)
    service = ContextPipelineService(home=tmp_path, config=_config("balanced"), input_queue=asyncio.Queue())
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()

    result = await service._filesystem_with_fallback(
        processed={"documents": documents, "blocks": [], "deleted_ids": []},
        sandbox=sandbox,
        batch=_processing_batch(2),
        service_id="local",
    )

    candidate = sandbox / "context"
    assert result == "balanced"
    assert root_body in (candidate / "description.md").read_text(encoding="utf-8")
    existing_text = (candidate / "openjiuwen" / "description.md").read_text(encoding="utf-8")
    assert existing_body in existing_text
    pages = context_pipeline._managed_pages_by_source(candidate)
    assert len(pages) == 2
    texts = {page.name: page.read_text(encoding="utf-8") for page in pages.values()}
    assert "OpenJiuWen Rail 的有界摘要。" in texts["OpenJiuWen Rail 接入说明.md"]
    assert "主动上下文的有界摘要。" in texts["主动上下文设计说明.md"]
    assert not (candidate / "sources").exists()
    assert not (candidate / "topics").exists()


@pytest.mark.asyncio
async def test_balanced_long_display_titles_keep_full_h1_with_one_model_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    long_page_title = "这是模型生成的完整页面显示标题而且明显超过二十个字符"
    _FakeDirectModel.instances.clear()
    _FakeDirectModel.outputs = [
        json.dumps(
            {
                "items": [
                    {
                        "item_index": 0,
                        "summary": "有界中文摘要。",
                        "page_title": long_page_title,
                        "keywords": [str(long_page_title)[:40]],
                    }
                ]
            },
            ensure_ascii=False,
        )
    ]
    monkeypatch.setattr(context_pipeline, "Model", _FakeDirectModel)
    service = ContextPipelineService(home=tmp_path, config=_config("balanced"), input_queue=asyncio.Queue())
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()

    result = await service._filesystem_with_fallback(
        processed={
            "documents": [
                {
                    "logical_id": "notes/long-title",
                    "revision_id": "rev-1",
                    "title": "来源标题",
                    "markdown": "确定性正文。\n",
                }
            ],
            "blocks": [],
            "deleted_ids": [],
        },
        sandbox=sandbox,
        batch=_processing_batch(1),
        service_id="local",
    )

    candidate = sandbox / "context"
    assert result == "balanced"
    assert len(_FakeDirectModel.instances) == 1
    assert len(_page_model_calls()) == 1
    page = next(path for path in candidate.rglob("*.md") if path.name != "description.md")
    assert len(page.stem) <= 20
    assert all(len(part) <= 20 for part in page.relative_to(candidate).parts[:-1])
    assert page.read_text(encoding="utf-8").startswith(f"# {long_page_title}\n")


@pytest.mark.asyncio
async def test_balanced_preexisting_managed_source_updates_title_and_summary_without_moving(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logical_id = "notes/existing"
    source_id = f"src_{context_pipeline._digest(logical_id)}"
    context_root = tmp_path / "workspace" / "context"
    assert _write_atomic_source(context_root.parent / "source-meta", locator=logical_id) == source_id
    existing_directory = context_root / "既有主题"
    existing_directory.mkdir(parents=True)
    existing_page = existing_directory / "稳定路径.md"
    existing_page.write_text(
        f"# 旧标题\n\n<!-- personal-context-managed-source: {source_id} -->\n\n## 摘要\n\n旧摘要。\n",
        encoding="utf-8",
    )
    (existing_directory / "description.md").write_text("# 既有主题\n\n- [旧标题](稳定路径.md)\n", encoding="utf-8")
    (context_root / "description.md").write_text(
        "# Context\n\n- [既有主题](既有主题/description.md)\n",
        encoding="utf-8",
    )
    _FakeDirectModel.instances.clear()
    _FakeDirectModel.outputs = [
        json.dumps(
            {
                "items": [
                    {
                        "item_index": 0,
                        "summary": "更新后的中文摘要。",
                        "page_title": "更新后的中文标题",
                        "keywords": [str("更新后的中文标题")[:40]],
                    }
                ]
            },
            ensure_ascii=False,
        )
    ]
    monkeypatch.setattr(context_pipeline, "Model", _FakeDirectModel)
    service = ContextPipelineService(home=tmp_path, config=_config("balanced"), input_queue=asyncio.Queue())
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()

    result = await service._filesystem_with_fallback(
        processed={
            "documents": [
                {
                    "logical_id": logical_id,
                    "revision_id": "rev-2",
                    "title": "来源的新标题",
                    "markdown": "更新后的确定性正文。\n",
                }
            ],
            "blocks": [],
            "deleted_ids": [],
        },
        sandbox=sandbox,
        batch=_processing_batch(1),
        service_id="local",
    )

    assert result == "balanced"
    candidate_page = context_pipeline._managed_pages_by_source(sandbox / "context")[source_id]
    assert candidate_page.relative_to(sandbox / "context").as_posix() == "既有主题/稳定路径.md"
    candidate_text = candidate_page.read_text(encoding="utf-8")
    assert candidate_text.startswith("# 更新后的中文标题\n")
    assert "更新后的中文摘要。" in candidate_text
    assert not (sandbox / "context" / "不应创建的新主题").exists()


@pytest.mark.asyncio
async def test_balanced_invalid_items_fall_back_individually_and_later_groups_continue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ContextPipelineService(home=tmp_path, config=_config("balanced"), input_queue=asyncio.Queue())
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    documents = [
        {
            "logical_id": f"notes/{index}",
            "revision_id": f"rev-{index}",
            "title": f"Note {index}",
            "markdown": f"Deterministic content {index}.\n",
        }
        for index in range(7)
    ]
    _FakeDirectModel.instances.clear()
    _FakeDirectModel.outputs = [
        json.dumps(
            {
                "items": [
                    {
                        "item_index": 0,
                        "summary": "唯一被采用的摘要。",
                        "page_title": "采用的页面标题",
                        "keywords": [str("采用的页面标题")[:40]],
                    },
                    {
                        "item_index": 1,
                        "summary": "[非法链接](https://example.test)",
                        "page_title": "不采用的页面标题",
                        "keywords": [str("不采用的页面标题")[:40]],
                    },
                ]
            },
            ensure_ascii=False,
        ),
        RuntimeError("model unavailable"),
    ]
    monkeypatch.setattr(context_pipeline, "Model", _FakeDirectModel)

    result = await service._filesystem_with_fallback(
        processed={"documents": documents, "blocks": [], "deleted_ids": []},
        sandbox=sandbox,
        batch=_processing_batch(7),
        service_id="local",
    )

    assert result == "balanced"
    assert len(_page_model_calls()) == 2
    pages = {
        path.read_text(encoding="utf-8")
        for path in context_pipeline._managed_pages_by_source(sandbox / "context").values()
    }
    assert len(pages) == 7
    assert any("唯一被采用的摘要。" in page for page in pages)
    assert any("Deterministic content 1." in page and "非法链接" not in page for page in pages)
    assert all("model unavailable" not in page for page in pages)


@pytest.mark.asyncio
async def test_balanced_zero_accepted_items_returns_publishable_rules_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ContextPipelineService(home=tmp_path, config=_config("balanced"), input_queue=asyncio.Queue())
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    processed = {
        "documents": [
            {
                "logical_id": "notes/one",
                "revision_id": "rev-1",
                "title": "One",
                "markdown": "Deterministic fallback.\n",
            }
        ],
        "blocks": [],
        "deleted_ids": [],
    }
    _FakeDirectModel.instances.clear()
    _FakeDirectModel.outputs = ["not-json"]
    monkeypatch.setattr(context_pipeline, "Model", _FakeDirectModel)

    result = await service._filesystem_with_fallback(
        processed=processed,
        sandbox=sandbox,
        batch=_processing_batch(1),
        service_id="local",
    )

    assert result == "rules"
    assert processed["_filesystem_candidate_prepared"] is True
    assert processed["_balanced_accepted_count"] == 0
    source_page = next(iter(context_pipeline._managed_pages_by_source(sandbox / "context").values()))
    assert "Deterministic fallback." in source_page.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_agent_publication_keeps_unmapped_aggregate_page_without_manifest(tmp_path: Path) -> None:
    service = ContextPipelineService(home=tmp_path, config=_config("rules"), input_queue=asyncio.Queue())
    sandbox = tmp_path / "sandbox"
    context = sandbox / "context"
    topic = context / "topics"
    topic.mkdir(parents=True)
    (context / "description.md").write_text("# Context\n\n- [Summary](topics/summary.md)\n", encoding="utf-8")
    (topic / "summary.md").write_text("# Summary\n\nCombined knowledge without direct mapping.\n", encoding="utf-8")

    await service._publish_processed(
        service_id="local",
        run_id="run-unmapped",
        batch=_batch(),
        processed={
            "documents": [
                {
                    "logical_id": "notes/one",
                    "revision_id": "rev-1",
                    "title": "One",
                    "markdown": "Processed text.\n",
                    "original_ref": "file:///notes/one",
                    "metadata": {},
                    "raw_snapshot": None,
                    "actual_profile": "agent",
                }
            ],
            "blocks": [],
            "deleted_ids": [],
            "actual_profile": "agent",
            "_agent_changed_context_paths": set(),
            "_agent_candidate_prepared": True,
            "_filesystem_candidate_profile": "agent",
        },
        sandbox=sandbox,
    )

    published = tmp_path / "workspace" / "context" / "topics" / "summary.md"
    assert published.read_text(encoding="utf-8") == "# Summary\n\nCombined knowledge without direct mapping.\n"
    assert not (tmp_path / "workspace" / "personal_context_provenance_manifest.json").exists()


@pytest.mark.asyncio
async def test_agent_publication_does_not_prepend_source_title_to_agent_page_h1(tmp_path: Path) -> None:
    service = ContextPipelineService(home=tmp_path, config=_config("rules"), input_queue=asyncio.Queue())
    sandbox = tmp_path / "sandbox"
    context = sandbox / "context"
    topic = context / "topics"
    topic.mkdir(parents=True)
    (context / "description.md").write_text(
        "# Context\n\n- [Summary](topics/summary.md)\n",
        encoding="utf-8",
    )
    (topic / "summary.md").write_text(
        "# Consolidated summary\n\nAgent-authored knowledge.\n",
        encoding="utf-8",
    )

    await service._publish_processed(
        service_id="local",
        run_id="run-single-h1",
        batch=_batch(),
        processed={
            "documents": [
                {
                    "logical_id": "notes/one",
                    "revision_id": "rev-1",
                    "title": "Raw source title",
                    "markdown": "Processed text.\n",
                    "original_ref": "file:///notes/one",
                    "metadata": {},
                    "raw_snapshot": None,
                    "actual_profile": "agent",
                }
            ],
            "blocks": [],
            "deleted_ids": [],
            "actual_profile": "agent",
            "_agent_changed_context_paths": {"topics/summary.md"},
            "_agent_candidate_prepared": True,
            "_filesystem_candidate_profile": "agent",
        },
        sandbox=sandbox,
    )

    published = tmp_path / "workspace" / "context" / "topics" / "summary.md"
    assert _top_level_headings(published.read_text(encoding="utf-8")) == ["# Consolidated summary"]


@pytest.mark.asyncio
async def test_agent_run_without_manifest_publishes_only_aggregate_and_source_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue: asyncio.Queue[object] = asyncio.Queue(maxsize=4)
    service = ContextPipelineService(home=tmp_path, config=_config("agent"), input_queue=queue)

    async def agent_without_manifest(*, sandbox_path: Path, **kwargs: object) -> str:
        del kwargs
        context = sandbox_path / "context"
        topic = context / "topics"
        topic.mkdir(parents=True, exist_ok=True)
        (context / "description.md").write_text(
            "# Context\n\n- [Topics](topics/description.md)\n",
            encoding="utf-8",
        )
        (topic / "description.md").write_text("# Topics\n\n- [Summary](summary.md)\n", encoding="utf-8")
        (topic / "summary.md").write_text(
            "# Summary\n\nCombined knowledge without direct mapping. [[ref:0]]\n",
            encoding="utf-8",
        )
        return "done"

    _FakeDirectModel.instances.clear()
    _FakeDirectModel.outputs = []
    monkeypatch.setattr(context_pipeline, "Model", _FakeDirectModel)
    monkeypatch.setattr(context_pipeline, "run_personal_context_agent", agent_without_manifest)

    await service.start()
    await _submit_run(queue, _batch())
    await service.stop(timeout_seconds=1)

    context = tmp_path / "workspace" / "context"
    assert (context / "topics" / "summary.md").is_file()
    source_pages = list((context / "sources").rglob("*.md")) if (context / "sources").exists() else []
    assert source_pages == []
    assert not (tmp_path / "workspace" / "source-proofs").exists()
    assert len(list((tmp_path / "workspace" / "source-meta").glob("src_*.md"))) == 1


@pytest.mark.asyncio
async def test_filesystem_agent_noop_for_non_empty_run_is_repairable_and_falls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ContextPipelineService(home=tmp_path, config=_config("agent"), input_queue=asyncio.Queue())
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    context = tmp_path / "workspace" / "context"
    (context / "topics").mkdir(parents=True)
    (context / "description.md").write_text(
        "# Context\n\n- [Existing](topics/existing.md)\n",
        encoding="utf-8",
    )
    (context / "topics" / "existing.md").write_text(
        "# Existing\n\nExisting knowledge.\n",
        encoding="utf-8",
    )
    context_pipeline._render_context_navigation(context)
    context_pipeline._validate_candidate(context)
    validation_errors: list[str] = []

    async def noop_agent(*, sandbox_path: Path, validate_result: Any, **kwargs: object) -> str:
        del kwargs
        errors = validate_result("done", sandbox_path)
        validation_errors.extend(errors)
        if errors:
            raise build_error(StatusCode.CONTEXT_PROACTIVE_PIPELINE_EXECUTION_ERROR, error_msg=errors[0])
        return "done"

    monkeypatch.setattr(context_pipeline, "run_personal_context_agent", noop_agent)
    _FakeDirectModel.instances.clear()
    _FakeDirectModel.outputs = [
        json.dumps(
            {
                "items": [
                    {
                        "item_index": 0,
                        "summary": "Balanced filesystem summary.",
                        "page_title": "Balanced filesystem page",
                        "keywords": [str("Balanced filesystem page")[:40]],
                    }
                ],
            }
        )
    ]
    monkeypatch.setattr(context_pipeline, "Model", _FakeDirectModel)

    result = await service._filesystem_with_fallback(
        processed={
            "documents": [
                {
                    "logical_id": "notes/one",
                    "revision_id": "rev-1",
                    "title": "One",
                    "markdown": "Processed text.\n",
                    "original_ref": "file:///notes/one",
                    "metadata": {},
                    "raw_snapshot": None,
                    "actual_profile": "balanced",
                }
            ],
            "blocks": [],
            "deleted_ids": [],
            "actual_profile": "balanced",
        },
        sandbox=sandbox,
        batch=_batch(),
    )

    assert result == "balanced"
    assert len(validation_errors) == 1
    assert validation_errors[0].endswith("agent did not add or update any Context knowledge page")
    source_page = next(iter(context_pipeline._managed_pages_by_source(sandbox / "context").values()))
    assert "Balanced filesystem summary." in source_page.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_context_write_failure_keeps_old_page_and_root_description(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = ContextPipelineService(home=tmp_path, config=_config("rules"), input_queue=asyncio.Queue())
    first_sandbox = tmp_path / "sandbox-first"
    old_candidate = first_sandbox / "context" / "topics" / "old.md"
    old_candidate.parent.mkdir(parents=True)
    old_candidate.write_text("# Old\n\nOld body.\n", encoding="utf-8")
    (first_sandbox / "context" / "description.md").write_text(
        "# Context\n\n- [Old](topics/old.md)\n",
        encoding="utf-8",
    )
    first_document = {
        "logical_id": "notes/one",
        "revision_id": "rev-1",
        "title": "One",
        "markdown": "Old processed body.\n",
        "original_ref": "file:///notes/one",
        "metadata": {},
        "raw_snapshot": None,
        "actual_profile": "agent",
    }
    await service._publish_processed(
        service_id="local",
        run_id="run-1",
        batch=_batch(),
        processed={
            "documents": [first_document],
            "blocks": [],
            "deleted_ids": [],
            "actual_profile": "agent",
            "_agent_changed_context_paths": {"topics/old.md"},
            "_agent_candidate_prepared": True,
            "_filesystem_candidate_profile": "agent",
        },
        sandbox=first_sandbox,
    )

    context_root = tmp_path / "workspace" / "context"
    old_page = context_root / "topics" / "old.md"
    old_page_bytes = old_page.read_bytes()
    old_description_bytes = (context_root / "description.md").read_bytes()
    second_sandbox = tmp_path / "sandbox-second"
    _prepare_agent_candidate(context_root, second_sandbox)
    new_candidate = second_sandbox / "context" / "topics" / "new.md"
    new_candidate.write_text("# New\n\nNew body.\n", encoding="utf-8")
    (second_sandbox / "context" / "description.md").write_text(
        "# Context\n\n- [New](topics/new.md)\n",
        encoding="utf-8",
    )
    original_atomic_write = context_pipeline._atomic_write

    def fail_new_context_page(path: Path, data: bytes) -> None:
        if path == context_root / "topics" / "new.md":
            raise OSError("injected candidate page write failure")
        original_atomic_write(path, data)

    monkeypatch.setattr(context_pipeline, "_atomic_write", fail_new_context_page)
    with pytest.raises(OSError):
        await service._publish_processed(
            service_id="local",
            run_id="run-2",
            batch=_batch(),
            processed={
                "documents": [dict(first_document, revision_id="rev-2", markdown="New processed body.\n")],
                "blocks": [],
                "deleted_ids": [],
                "actual_profile": "agent",
                "_agent_changed_context_paths": {"topics/new.md"},
                "_agent_candidate_prepared": True,
                "_filesystem_candidate_profile": "agent",
            },
            sandbox=second_sandbox,
        )

    assert old_page.read_bytes() == old_page_bytes
    assert (context_root / "description.md").read_bytes() == old_description_bytes


@pytest.mark.asyncio
async def test_context_write_failure_keeps_old_nested_description_and_page(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ContextPipelineService(home=tmp_path, config=_config("rules"), input_queue=asyncio.Queue())
    first_sandbox = tmp_path / "sandbox-first"
    old_candidate = first_sandbox / "context" / "topics" / "old.md"
    old_candidate.parent.mkdir(parents=True)
    old_candidate.write_text("# Old\n\nOld body.\n", encoding="utf-8")
    (old_candidate.parent / "description.md").write_text(
        "# Topics\n\n- [Old](old.md)\n",
        encoding="utf-8",
    )
    (first_sandbox / "context" / "description.md").write_text(
        "# Context\n\n- [Topics](topics/description.md)\n",
        encoding="utf-8",
    )
    first_document = {
        "logical_id": "notes/one",
        "revision_id": "rev-1",
        "title": "One",
        "markdown": "Old processed body.\n",
        "original_ref": "file:///notes/one",
        "metadata": {},
        "raw_snapshot": None,
        "actual_profile": "agent",
    }
    await service._publish_processed(
        service_id="local",
        run_id="run-1",
        batch=_batch(),
        processed={
            "documents": [first_document],
            "blocks": [],
            "deleted_ids": [],
            "actual_profile": "agent",
            "_agent_changed_context_paths": {"topics/old.md"},
            "_agent_candidate_prepared": True,
            "_filesystem_candidate_profile": "agent",
        },
        sandbox=first_sandbox,
    )

    context_root = tmp_path / "workspace" / "context"
    old_page = context_root / "topics" / "old.md"
    nested_description = context_root / "topics" / "description.md"
    old_page_bytes = old_page.read_bytes()
    old_nested_description_bytes = nested_description.read_bytes()
    old_root_description_bytes = (context_root / "description.md").read_bytes()
    second_sandbox = tmp_path / "sandbox-second"
    _prepare_agent_candidate(context_root, second_sandbox)
    new_candidate = second_sandbox / "context" / "topics" / "new.md"
    new_candidate.write_text("# New\n\nNew body.\n", encoding="utf-8")
    (new_candidate.parent / "description.md").write_text(
        "# Topics\n\n- [New](new.md)\n",
        encoding="utf-8",
    )
    original_atomic_write = context_pipeline._atomic_write

    def fail_new_context_page(path: Path, data: bytes) -> None:
        if path == context_root / "topics" / "new.md":
            raise OSError("injected candidate page write failure")
        original_atomic_write(path, data)

    monkeypatch.setattr(context_pipeline, "_atomic_write", fail_new_context_page)
    with pytest.raises(OSError):
        await service._publish_processed(
            service_id="local",
            run_id="run-2",
            batch=_batch(),
            processed={
                "documents": [dict(first_document, revision_id="rev-2", markdown="New processed body.\n")],
                "blocks": [],
                "deleted_ids": [],
                "actual_profile": "agent",
                "_agent_changed_context_paths": {"topics/new.md"},
                "_agent_candidate_prepared": True,
                "_filesystem_candidate_profile": "agent",
            },
            sandbox=second_sandbox,
        )

    assert old_page.read_bytes() == old_page_bytes
    assert nested_description.read_bytes() == old_nested_description_bytes
    assert (context_root / "description.md").read_bytes() == old_root_description_bytes


def test_agent_removed_materialized_source_is_non_fallback(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    with pytest.raises(Exception) as raised:
        context_pipeline._validate_agent_sandbox_layout(
            sandbox,
            materialized_baseline={"README.md": (1, "digest")},
        )
    assert getattr(raised.value, "status", None) == StatusCode.CONTEXT_PROACTIVE_PUBLISH_EXECUTION_ERROR


def test_agent_core_operation_history_is_allowed_in_sandbox(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    history = sandbox / ".agent_history"
    history.mkdir()
    (history / "file_ops.json").write_text("{}", encoding="utf-8")

    context_pipeline._validate_agent_sandbox_layout(
        sandbox,
        materialized_baseline=None,
        inputs_baseline=None,
    )


def test_agent_inputs_preserve_full_source_and_redact_metadata(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    full_content = "start-" + ("x" * 9_000) + "-end"

    baseline = context_pipeline._prepare_agent_inputs(
        _batch(
            content=full_content,
            raw_snapshot=b"complete raw snapshot",
            original_ref="https://user:pass@example.test/private?token=secret",
            metadata={"access_token": "secret", "kind": "note"},
        ),
        sandbox=sandbox,
    )

    record = next((sandbox / "inputs" / "records").iterdir())
    assert record.joinpath("content.md").read_text(encoding="utf-8") == full_content
    assert record.joinpath("raw-snapshot.bin").read_bytes() == b"complete raw snapshot"
    metadata = record.joinpath("metadata.json").read_text(encoding="utf-8")
    assert "secret" not in metadata
    assert "user:pass" not in metadata
    assert '"kind": "note"' in metadata
    assert baseline == context_pipeline._snapshot_managed_files(sandbox / "inputs")
    assert all(path.stat().st_mode & 0o222 == 0 for path in (sandbox / "inputs").rglob("*") if path.is_file())


def test_agent_inputs_are_immutable_but_tmp_is_writable(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    baseline = context_pipeline._prepare_agent_inputs(_batch(), sandbox=sandbox)
    content = next((sandbox / "inputs" / "records").glob("*/content.md"))
    content.chmod(content.stat().st_mode | 0o200)
    content.write_text("tampered", encoding="utf-8")

    with pytest.raises(Exception) as raised:
        context_pipeline._validate_agent_sandbox_layout(
            sandbox,
            materialized_baseline=None,
            inputs_baseline=baseline,
        )

    assert getattr(raised.value, "status", None) == StatusCode.CONTEXT_PROACTIVE_PUBLISH_EXECUTION_ERROR
    scratch = sandbox / "tmp" / "notes.md"
    scratch.write_text("scratch", encoding="utf-8")
    assert scratch.read_text(encoding="utf-8") == "scratch"


@pytest.mark.parametrize(
    "key",
    [
        "auth",
        "Authorization",
        "api_key",
        "api-key",
        "apiKey",
        "access_token",
        "access-token",
        "accessToken",
        "refresh_token",
        "refresh-token",
        "refreshToken",
        "token",
        "password",
        "passwd",
        "secret",
        "client_secret",
        "clientSecret",
        "credential",
        "credentials",
        "private_key",
        "privateKey",
        "cookie",
    ],
)
def test_agent_metadata_removes_explicit_sensitive_key_variants(key: str) -> None:
    assert context_pipeline._agent_metadata({key: "sensitive"}) == {}


@pytest.mark.parametrize("key", ["keyboard_layout", "monkey", "turnkey", "keynote"])
def test_agent_metadata_preserves_normal_keys_containing_key(key: str) -> None:
    assert context_pipeline._agent_metadata({key: "ordinary"}) == {key: "ordinary"}


@pytest.mark.parametrize(
    ("document_lengths", "expected"),
    [
        ([1] * 10, False),
        ([1] * 11, True),
        ([30_000, 30_000], False),
        ([30_000, 30_001], True),
        ([40_000], False),
        ([40_001], True),
    ],
)
def test_large_run_thresholds_are_strict(document_lengths: list[int], expected: bool) -> None:
    processed = {
        "documents": [
            {"logical_id": f"notes/{index}", "markdown": "x" * length} for index, length in enumerate(document_lengths)
        ]
    }

    assert context_pipeline._is_large_run(processed) is expected


def test_run_finish_rewrites_preview_and_writes_complete_briefing_without_truncating_artifacts(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    config = PersonalContextConfig.from_dict(
        {
            "collection_enabled": True,
            "agent_use_enabled": False,
            "strategy_profile": "rules",
            "model_client": None,
            "model_request": None,
            "fetch_services": [
                {
                    "service_id": "local",
                    "provider": "local_files",
                    "enabled": True,
                    "interval_seconds": 60,
                    "time_range": {"mode": "all"},
                    "source": {"root_dir": str(source_root)},
                    "credentials": {},
                }
            ],
        }
    )
    queue: asyncio.Queue[object] = asyncio.Queue()
    service = ContextPipelineService(home=tmp_path / "home", config=config, input_queue=queue)
    sandbox = tmp_path / "home" / "workspace" / "sandboxes" / "local" / "run-1"
    sandbox.mkdir(parents=True)
    full_content = "source-start-" + ("x" * 15_000) + "-source-end"
    full_processed = "processed-start-" + ("y" * 15_000) + "-processed-end"
    batch = _batch(
        content=full_content,
        raw_snapshot="raw-start-" + ("z" * 15_000) + "-raw-end",
        original_ref="https://user:pass@example.test/private?token=secret",
    )
    record_paths = service._write_batch_records(sandbox, batch)
    service._write_processed_batch(
        sandbox,
        batch,
        {
            "documents": [
                {
                    "logical_id": "notes/one",
                    "revision_id": "rev-1",
                    "title": "One",
                    "markdown": full_processed,
                    "original_ref": "https://user:pass@example.test/private?token=secret",
                    "metadata": {},
                    "actual_profile": "rules",
                }
            ],
            "blocks": [
                {
                    "block_id": "block-1",
                    "logical_id": "notes/one",
                    "order": 0,
                    "text": full_processed,
                }
            ],
            "deleted_ids": [],
            "actual_profile": "rules",
        },
        record_paths,
        {"notes/one": "[[ref:0]]"},
    )
    source_id = _write_atomic_source(
        service._source_meta_root,
        locator=str(batch.items[0].original_ref),
        title="One",
        observed_at="2026-08-12T00:00:00Z",
    )

    processed = service._prepare_run_finish_io(
        sandbox,
        {
            "sandbox": sandbox,
            "batch_ids": ["batch-1"],
            "provider": "local_files",
            "source_alias_by_id": {source_id: "[[ref:0]]"},
            "source_id_by_logical_id": {"notes/one": source_id},
        },
    )

    record_root = next((sandbox / "inputs" / "records" / "batch-1").iterdir())
    processed_root = next((sandbox / "inputs" / "processed" / "batch-1").iterdir())
    assert record_root.joinpath("content.md").read_text(encoding="utf-8") == full_content
    assert record_root.joinpath("context.md").read_text(encoding="utf-8") == full_content[:12_000]
    assert processed_root.joinpath("context-document.md").read_text(encoding="utf-8") == (
        "[[ref:0]]\n\n" + full_processed
    )
    assert full_processed in processed_root.joinpath("blocks.jsonl").read_text(encoding="utf-8")
    assert processed["_large_run"] is False
    prompt_documents = context_pipeline._agent_documents_payload(processed, large_run=False)
    assert len(prompt_documents) == 1
    assert len(str(prompt_documents[0]["summary"])) == 1_200
    assert "markdown" not in prompt_documents[0] and "blocks" not in prompt_documents[0]

    briefing = json.loads((sandbox / "inputs" / "briefing.json").read_text(encoding="utf-8"))
    assert briefing["source_count"] == 1
    source = briefing["sources"][0]
    assert source["logical_id"] == "notes/one"
    assert source["revision_id"] == "rev-1"
    assert source["provider"] == "local_files"
    assert source["title"] == "One"
    assert source["original_ref"] == "https://example.test/private"
    assert source["source_ref"] == "[[ref:0]]"
    assert len(source["summary"]) == 450
    assert set(source["artifacts"]) == {
        "blocks",
        "processed_document",
        "processed_record",
        "source_content",
        "source_metadata",
        "source_preview",
        "source_raw",
    }
    for relative_path in source["artifacts"].values():
        assert isinstance(relative_path, str)
        assert sandbox.joinpath(relative_path).is_file()


@pytest.mark.asyncio
async def test_deterministic_briefing_extracts_outline_first_paragraph_and_counts(tmp_path: Path) -> None:
    service = ContextPipelineService(home=tmp_path / "home", config=_config("rules"), input_queue=asyncio.Queue())
    sandbox = tmp_path / "home" / "workspace" / "sandboxes" / "local" / "run-briefing"
    sandbox.mkdir(parents=True)
    content = (
        "---\n"
        "kind: note\n"
        "---\n"
        "# 主标题\n\n"
        "第一个有效正文段落包含关键事实。\n"
        "同一段的下一行。\n\n"
        "## 细节\n\n"
        "第二个段落。\n"
    )
    batch = _batch(content=content, raw_snapshot="raw text")
    record_paths = service._write_batch_records(sandbox, batch)
    processed = await service._process_deterministic(batch)
    service._write_processed_batch(
        sandbox,
        batch,
        processed,
        record_paths,
        {"notes/one": "[[ref:0]]"},
    )
    source_id = _write_atomic_source(
        service._source_meta_root,
        locator=str(batch.items[0].original_ref),
        title="One",
        observed_at="2026-08-12T00:00:00Z",
    )

    service._prepare_run_finish_io(
        sandbox,
        {
            "sandbox": sandbox,
            "batch_ids": ["batch-1"],
            "provider": "local_files",
            "source_alias_by_id": {source_id: "[[ref:0]]"},
            "source_id_by_logical_id": {"notes/one": source_id},
        },
    )

    briefing = json.loads((sandbox / "inputs" / "briefing.json").read_text(encoding="utf-8"))
    source = briefing["sources"][0]
    assert source["headings"] == [
        {"level": 1, "text": "主标题"},
        {"level": 2, "text": "细节"},
    ]
    assert source["summary"] == "第一个有效正文段落包含关键事实。 同一段的下一行。"
    assert len(source["summary"]) <= 450
    assert source["content_chars"] == len(context_pipeline._normalize_markdown(content))
    assert source["raw_snapshot_type"] == "text"
    briefing_markdown = (sandbox / "inputs" / "briefing.md").read_text(encoding="utf-8")
    assert "- content_chars:" in briefing_markdown
    assert "- raw_snapshot_type: `text`" in briefing_markdown
    assert "- outline: `H1 主标题 | H2 细节`" in briefing_markdown


@pytest.mark.asyncio
async def test_run_inputs_and_source_metadata_never_persist_source_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue: asyncio.Queue[object] = asyncio.Queue(maxsize=4)
    service = ContextPipelineService(home=tmp_path, config=_config("rules"), input_queue=queue)
    original_publish = service._publish_processed
    original_ref = "https://user:pass@example.test/private/note?token=query-secret&safe=ignored"
    secrets = {
        "user:pass",
        "query-secret",
        "bearer-auth-secret",
        "top-secret-api-key",
        "camel-api-secret",
        "nested-secret-token",
        "list-password",
    }
    content = "full source body remains available"
    batch = _batch(
        content=content,
        original_ref=original_ref,
        metadata={
            "auth": "Bearer bearer-auth-secret",
            "api_key": "top-secret-api-key",
            "apiKey": "camel-api-secret",
            "kind": "note",
            "nested": {"token": "nested-secret-token", "visible": "kept"},
            "items": [{"password": "list-password", "label": "safe-label"}],
            "keyboard_layout": "US",
            "monkey": "animal",
            "turnkey": "delivery",
            "keynote": "slides",
        },
    )

    def read_text_inputs(root: Path) -> str:
        return "\n".join(
            path.read_text(encoding="utf-8")
            for path in root.rglob("*")
            if path.is_file() and path.suffix in {".json", ".jsonl", ".md", ".txt"}
        )

    async def inspect_publish(**kwargs: object) -> None:
        sandbox = kwargs["sandbox"]
        assert isinstance(sandbox, Path)
        briefing = json.loads((sandbox / "inputs" / "briefing.json").read_text(encoding="utf-8"))
        source = briefing["sources"][0]
        assert source["original_ref"] == "https://example.test/private/note"
        assert source["provider"] == "local"
        for relative_path in source["artifacts"].values():
            assert sandbox.joinpath(relative_path).is_file()
        briefing_text = read_text_inputs(sandbox / "inputs")
        assert all(secret not in briefing_text for secret in secrets)
        assert "example.test/private/note" in briefing_text
        assert '"kind": "note"' in briefing_text
        assert '"visible": "kept"' in briefing_text
        assert '"label": "safe-label"' in briefing_text
        assert '"keyboard_layout": "US"' in briefing_text
        assert '"monkey": "animal"' in briefing_text
        assert '"turnkey": "delivery"' in briefing_text
        assert '"keynote": "slides"' in briefing_text

        await original_publish(**kwargs)  # type: ignore[arg-type]
        assert not (tmp_path / "workspace" / "source-proofs").exists()
        metadata_path = next((tmp_path / "workspace" / "source-meta").glob("src_*.md"))
        metadata_text = metadata_path.read_text(encoding="utf-8")
        assert '"https://example.test/private/note"' in metadata_text
        assert all(secret not in metadata_text for secret in secrets)

    monkeypatch.setattr(service, "_publish_processed", inspect_publish)
    await service.start()
    batch_completion = asyncio.get_running_loop().create_future()
    try:
        await queue.put(("batch", "local", "run-secret-redaction", batch, batch_completion))
        await asyncio.wait_for(asyncio.shield(batch_completion), timeout=2)

        run_inputs = tmp_path / "workspace" / "sandboxes" / "local" / "run-secret-redaction" / "inputs"
        inputs_text = read_text_inputs(run_inputs)
        assert all(secret not in inputs_text for secret in secrets)
        assert "example.test/private/note" in inputs_text
        assert '"kind": "note"' in inputs_text
        assert '"visible": "kept"' in inputs_text
        assert '"label": "safe-label"' in inputs_text
        assert '"keyboard_layout": "US"' in inputs_text
        assert '"monkey": "animal"' in inputs_text
        assert '"turnkey": "delivery"' in inputs_text
        assert '"keynote": "slides"' in inputs_text
        assert next(run_inputs.glob("records/*/*/content.md")).read_text(encoding="utf-8") == content

        finish_completion = asyncio.get_running_loop().create_future()
        await queue.put(("finish", "local", "run-secret-redaction", None, finish_completion))
        await asyncio.wait_for(asyncio.shield(finish_completion), timeout=2)
    finally:
        if not batch_completion.done():
            batch_completion.cancel()
        await service.stop(timeout_seconds=1)


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", ["agent", "balanced"])
async def test_filesystem_production_prompt_bounds_deleted_ids_documents_and_titles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    profile: str,
) -> None:
    queue: asyncio.Queue[object] = asyncio.Queue()
    service = ContextPipelineService(home=tmp_path, config=_config(profile), input_queue=queue)
    sandbox = tmp_path / "workspace" / "sandboxes" / "local" / "run-bounded-prompt"
    deleted_ids = [f"deleted/private-credential-{index:04d}-" + ("d" * 200) for index in range(200)]
    documents = [
        {
            "logical_id": f"notes/{index}",
            "revision_id": f"rev-{index}",
            "title": f"Title {index} " + ("t" * 2_000),
            "markdown": "m" * 1_000,
            "original_ref": f"https://example.test/{index}",
            "metadata": {},
            "actual_profile": "balanced",
        }
        for index in range(13)
    ]
    processed: dict[str, object] = {
        "documents": documents,
        "blocks": [
            {"block_id": f"block-{index}", "logical_id": f"notes/{index}", "order": 0, "text": "b" * 1_000}
            for index in range(13)
        ],
        "deleted_ids": deleted_ids,
        "actual_profile": "balanced",
        "_large_run": True,
    }
    deleted_root = sandbox / "inputs" / "deleted"
    deleted_root.mkdir(parents=True)
    deleted_path = deleted_root / "batch-1.json"
    deleted_path.write_text(json.dumps(deleted_ids, ensure_ascii=False), encoding="utf-8")
    (sandbox / "inputs" / "briefing.md").write_text("bounded briefing", encoding="utf-8")
    prompts: list[str] = []

    async def agent_capture(*, messages: list[object], **kwargs: object) -> str:
        del kwargs
        prompts.append(str(getattr(messages[0], "content", "")))
        raise build_error(StatusCode.CONTEXT_PROACTIVE_CONFIG_INVALID, error_msg="stop after prompt capture")

    class FailingModel:
        def __init__(self, **kwargs: object) -> None:
            del kwargs

        async def invoke(self, messages: list[object]) -> object:
            prompts.append(str(getattr(messages[0], "content", "")))
            raise build_error(StatusCode.CONTEXT_PROACTIVE_CONFIG_INVALID, error_msg="stop after prompt capture")

    monkeypatch.setattr(context_pipeline, "run_personal_context_agent", agent_capture)
    monkeypatch.setattr(context_pipeline, "Model", FailingModel)

    if profile == "agent":
        with pytest.raises(Exception) as raised:
            await service._filesystem_with_fallback(
                processed=processed,
                sandbox=sandbox,
                batch=FetchBatch(batch_id="finish-run", items=[]),
            )
        assert getattr(raised.value, "status", None) == StatusCode.CONTEXT_PROACTIVE_CONFIG_INVALID
    else:
        assert (
            await service._filesystem_with_fallback(
                processed=processed,
                sandbox=sandbox,
                batch=FetchBatch(batch_id="finish-run", items=[]),
            )
            == "rules"
        )

    if profile == "agent":
        assert len(prompts) == 1
        prompt = prompts[0]
    else:
        page_prompts = [prompt for prompt in prompts if "items" in json.loads(prompt.split("\n", 1)[1])]
        assert len(page_prompts) == 3
        prompt = page_prompts[0]
    payload = json.loads(prompt.split("\n", 1)[1])
    if profile == "agent":
        prompt_documents = payload["document_previews"]
        assert payload["deleted_count"] == len(deleted_ids)
        assert "deleted_ids" not in payload
        assert payload["deleted_input_root"] == "inputs/deleted"
        assert "This is a large run: use the complete briefing first" in prompt
        assert "Do not eagerly read every source_preview or source_content" in prompt
        assert "Write a concise complete page in one write_file call" in prompt
        assert "Never draft multiple complete pages in one model response" not in prompt
        assert "At most one complete page may be submitted per model response" not in prompt
        assert "Continue with later tool calls until every planned topic page" in prompt
        assert "Every upsert source with distinct, non-duplicative key facts" in prompt
        assert "no more than 4000 characters" in prompt
        assert "Update only pages and directory descriptions affected by this run" in prompt
        assert "Update each affected description.md once" in prompt
        assert "delete temporary files directly" not in prompt
        assert "shell" not in prompt.casefold()
        assert "relative to the Markdown file that contains the link" in prompt
        assert "../B/description.md" in prompt
        assert "../../B/description.md escapes Context" in prompt
        assert "Do not leave links to planned pages that you did not create" in prompt
        assert "perform one lightweight check of the internal Context links" in prompt
        assert "exactly one top-level # heading outside fenced code blocks" in prompt
        assert "Use only read_file, write_file, edit_file, glob, list_files, grep, and move_path" in prompt
        assert "Only description.md and directories may exist directly under context/" in prompt
        assert "Before choosing or creating a page path, use list_files" in prompt
        assert "Organize knowledge by topic across providers" in prompt
        assert "待整理" not in prompt
        assert "fallback_route" not in prompt
        assert "If a directory has 16 to 19 ordinary Markdown pages" in prompt
        assert "If it has 20 or more ordinary Markdown pages" in prompt
        assert "manually update every affected relative link and description.md navigation" in prompt
        assert "personal-context-managed-source" in prompt
        assert len(prompt_documents) == 12
        assert all(len(str(document["title"])) <= 512 for document in prompt_documents)
        assert all("markdown" not in document and "blocks" not in document for document in prompt_documents)
    else:
        assert set(payload) == {"items"}
        prompt_documents = payload["items"]
        assert len(prompt_documents) == 5
        assert all(
            set(document) == {"item_index", "title", "headings", "preview", "provider", "source_type", "service"}
            for document in prompt_documents
        )
        assert all(len(str(document["title"])) <= 512 for document in prompt_documents)
        assert all(len(str(document["preview"])) <= 2800 for document in prompt_documents)
        assert "deleted_count" not in payload
        assert "deleted_ids" not in payload
        assert "deleted_input_root" not in payload
    assert deleted_ids[0] not in prompt and deleted_ids[-1] not in prompt
    assert len(prompt) < 30_000
    assert json.loads(deleted_path.read_text(encoding="utf-8")) == deleted_ids


def test_large_run_preview_and_initial_prompt_are_bounded_while_disk_documents_stay_complete(
    tmp_path: Path,
) -> None:
    queue: asyncio.Queue[object] = asyncio.Queue()
    service = ContextPipelineService(home=tmp_path / "home", config=_config("rules"), input_queue=queue)
    sandbox = tmp_path / "home" / "workspace" / "sandboxes" / "local" / "run-1"
    sandbox.mkdir(parents=True)
    items = [
        RawChangeItem(
            logical_id=f"notes/{index}",
            revision_id=f"rev-{index}",
            operation="upsert",
            title=f"Title {index}",
            content=(f"source-{index}-" + ("x" * 4_500)),
            original_ref=f"https://example.test/{index}",
            metadata={},
        )
        for index in range(11)
    ]
    batch = FetchBatch(batch_id="batch-1", items=items)
    record_paths = service._write_batch_records(sandbox, batch)
    documents = [
        {
            "logical_id": item.logical_id,
            "revision_id": item.revision_id,
            "title": item.title,
            "markdown": f"processed-{index}-" + ("y" * 4_500),
            "original_ref": item.original_ref,
            "metadata": {},
            "actual_profile": "rules",
        }
        for index, item in enumerate(items)
    ]
    service._write_processed_batch(
        sandbox,
        batch,
        {"documents": documents, "blocks": [], "deleted_ids": [], "actual_profile": "rules"},
        record_paths,
        {item.logical_id: f"[[ref:{index}]]" for index, item in enumerate(items)},
    )
    source_ids = {
        item.logical_id: _write_atomic_source(
            service._source_meta_root,
            locator=str(item.original_ref),
            title=str(item.title),
            observed_at="2026-08-12T00:00:00Z",
        )
        for item in items
    }

    processed = service._prepare_run_finish_io(
        sandbox,
        {
            "sandbox": sandbox,
            "batch_ids": ["batch-1"],
            "provider": "local",
            "source_alias_by_id": {source_ids[item.logical_id]: f"[[ref:{index}]]" for index, item in enumerate(items)},
            "source_id_by_logical_id": source_ids,
        },
    )
    prompt_documents = context_pipeline._agent_documents_payload(processed, large_run=True)

    assert processed["_large_run"] is True
    assert len(prompt_documents) == 11
    assert all(len(str(document["summary"])) <= 600 for document in prompt_documents)
    assert all("markdown" not in document and "blocks" not in document for document in prompt_documents)
    briefing = json.loads((sandbox / "inputs" / "briefing.json").read_text(encoding="utf-8"))
    assert briefing["source_count"] == 11
    assert {source["logical_id"] for source in briefing["sources"]} == {f"notes/{index}" for index in range(11)}
    for record_root in (sandbox / "inputs" / "records" / "batch-1").iterdir():
        assert len(record_root.joinpath("context.md").read_text(encoding="utf-8")) == 2_800
        assert len(record_root.joinpath("content.md").read_text(encoding="utf-8")) > 4_500
    for processed_root in (sandbox / "inputs" / "processed" / "batch-1").iterdir():
        assert len(processed_root.joinpath("context-document.md").read_text(encoding="utf-8")) > 4_500


def test_bounded_initial_prompt_lists_at_most_twelve_documents() -> None:
    processed = {
        "documents": [
            {
                "logical_id": f"notes/{index}",
                "revision_id": f"rev-{index}",
                "title": f"Title {index}",
                "markdown": "x" * 1_000,
            }
            for index in range(13)
        ]
    }

    prompt_documents = context_pipeline._agent_documents_payload(processed, large_run=True)

    assert len(prompt_documents) == 12
    assert all(len(str(document["summary"])) == 600 for document in prompt_documents)


@pytest.mark.parametrize(
    ("segment", "expected"),
    [
        ("知识管理", True),
        ("OpenAI Agents SDK", True),
        ("研究.v2", True),
        ("中" * 80, True),
        ("", False),
        (".", False),
        ("..", False),
        ("e\u0301", False),
        ("\ud800", False),
        ("主题\u200b页", False),
        ("主题\x01页", False),
        ("name.", False),
        ("name ", False),
        ("CON", False),
        ("con.txt", False),
        ("LPT9.md", False),
        ("a/b", False),
        ("a\\b", False),
        ("a:name", False),
        ("中" * 81, False),
        ("中" * 79 + "😀", False),
    ],
)
def test_portable_context_segment_contract(segment: str, expected: bool) -> None:
    assert context_pipeline._portable_context_segment_is_safe(segment) is expected


def _write_named_candidate(context: Path, *, directory: str, page_name: str) -> str:
    page = context / directory / page_name
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text("# 长期记忆\n\n候选正文。\n", encoding="utf-8")
    (page.parent / "description.md").write_text(
        f"# 知识管理\n\n- [长期记忆]({page_name})\n",
        encoding="utf-8",
    )
    (context / "description.md").write_text(
        f"# Context\n\n- [知识管理]({directory}/description.md)\n",
        encoding="utf-8",
    )
    return f"{directory}/{page_name}"


def test_agent_candidate_rejects_new_non_nfc_path_as_repairable(tmp_path: Path) -> None:
    context = tmp_path / "context"
    relative = _write_named_candidate(
        context,
        directory="e\u0301",
        page_name="长期记忆.md",
    )

    with pytest.raises(BaseError) as raised:
        _validate_agent_candidate(
            context,
            baseline={},
            changed_paths={relative, "description.md", "e\u0301/description.md"},
            require_single_h1=True,
        )

    assert raised.value.status == StatusCode.CONTEXT_PROACTIVE_PIPELINE_EXECUTION_ERROR
    assert "portable" in str(raised.value)


def test_description_navigation_error_identifies_safe_relative_source_and_target(tmp_path: Path) -> None:
    root = tmp_path / "context"
    topic = root / "Agent框架工程"
    topic.mkdir(parents=True)
    (root / "description.md").write_text("# Context\n", encoding="utf-8")
    description = topic / "description.md"
    description.write_text("# Agent框架工程\n\n[错误同级](../../Agent技能工程/description.md)\n", encoding="utf-8")

    with pytest.raises(Exception) as captured:
        context_pipeline._validate_description_navigation(root, repairable=True)

    message = str(captured.value)
    assert "Agent框架工程/description.md -> ../../Agent技能工程/description.md" in message
    assert str(tmp_path) not in message


def test_agent_new_semantic_path_over_twenty_chars_is_repairable() -> None:
    relative = f"{'新' * 21}/{'页' * 21}.md"

    with pytest.raises(BaseError, match="at most 20 Unicode characters") as raised:
        context_pipeline._validate_new_context_path_segments(
            {relative},
            baseline_paths=set(),
        )

    assert raised.value.status == StatusCode.CONTEXT_PROACTIVE_PIPELINE_EXECUTION_ERROR


def test_agent_existing_long_semantic_path_is_not_rejected_as_new() -> None:
    relative = f"{'旧' * 40}/{'页' * 40}.md"

    context_pipeline._validate_new_context_path_segments(
        {relative},
        baseline_paths={relative},
    )


def test_agent_candidate_keeps_legacy_path_and_accepts_safe_chinese_and_english_names(
    tmp_path: Path,
) -> None:
    context = tmp_path / "context"
    legacy_directory = "e\u0301"
    legacy_page = _write_named_candidate(
        context,
        directory=legacy_directory,
        page_name="legacy.md",
    )
    baseline = context_pipeline._snapshot_managed_files(context)

    legacy_root = context / legacy_directory
    (legacy_root / "legacy.md").write_text("# Legacy\n\n更新后的旧页面。\n", encoding="utf-8")
    (legacy_root / "长期记忆.md").write_text("# 长期记忆\n\n中文新页面。\n", encoding="utf-8")
    (legacy_root / "OpenAI.md").write_text("# OpenAI\n\n安全英文专有名词页面。\n", encoding="utf-8")
    (legacy_root / "description.md").write_text(
        "# 知识管理\n\n- [Legacy](legacy.md)\n- [长期记忆](长期记忆.md)\n- [OpenAI](OpenAI.md)\n",
        encoding="utf-8",
    )

    _validate_agent_candidate(
        context,
        baseline=baseline,
        changed_paths={
            legacy_page,
            f"{legacy_directory}/description.md",
            f"{legacy_directory}/长期记忆.md",
            f"{legacy_directory}/OpenAI.md",
        },
        require_single_h1=True,
    )


def test_agent_root_layout_rejects_ordinary_markdown_as_repairable(tmp_path: Path) -> None:
    context = tmp_path / "context"
    context.mkdir()
    (context / "description.md").write_text(
        "# Context\n\n- [Root page](Root page.md)\n",
        encoding="utf-8",
    )
    (context / "Root page.md").write_text("# Root page\n\nContent.\n", encoding="utf-8")

    with pytest.raises(BaseError) as raised:
        _validate_agent_candidate(
            context,
            baseline={},
            changed_paths={"description.md", "Root page.md"},
            require_description=True,
            require_single_h1=True,
        )

    assert raised.value.status == StatusCode.CONTEXT_PROACTIVE_PIPELINE_EXECUTION_ERROR
    assert "root" in str(raised.value).casefold()


def test_agent_description_coverage_rejects_unlisted_page_as_repairable(tmp_path: Path) -> None:
    context = tmp_path / "context"
    topic = context / "主动上下文"
    topic.mkdir(parents=True)
    (context / "description.md").write_text(
        "# Context\n\n- [主动上下文](主动上下文/description.md)\n",
        encoding="utf-8",
    )
    (topic / "description.md").write_text("# 主动上下文\n", encoding="utf-8")
    (topic / "目录治理.md").write_text("# 目录治理\n\n正文。\n", encoding="utf-8")

    with pytest.raises(BaseError) as raised:
        _validate_agent_candidate(
            context,
            baseline={},
            changed_paths={
                "description.md",
                "主动上下文/description.md",
                "主动上下文/目录治理.md",
            },
            require_description=True,
            require_single_h1=True,
        )

    assert raised.value.status == StatusCode.CONTEXT_PROACTIVE_PIPELINE_EXECUTION_ERROR
    assert "coverage" in str(raised.value).casefold()


def _managed_agent_candidate_fixture(
    tmp_path: Path,
) -> tuple[Path, Path, Path, dict[str, tuple[int, str]], dict[str, str], str, str]:
    final_context_root = tmp_path / "workspace" / "context"
    source_root = tmp_path / "workspace" / "source-meta"
    source_id = _write_atomic_source(source_root, locator="file:///sources/managed-one.md")
    second_source_id = _write_atomic_source(source_root, locator="file:///sources/managed-two.md")
    managed_link = _source_link(
        page_relative="旧主题/来源页.md",
        final_context_root=final_context_root,
        source_root=source_root,
        source_id=source_id,
        label="原子来源",
    )
    _write_context_pages(
        final_context_root,
        {
            "description.md": "# Context\n\n- [旧主题](旧主题/description.md)\n",
            "旧主题/description.md": "# 旧主题\n\n- [来源页](来源页.md)\n- [关联页](关联页.md)\n",
            "旧主题/来源页.md": (
                f"# 来源页\n\n<!-- personal-context-managed-source: {source_id} -->\n\n{managed_link}\n"
            ),
            "旧主题/关联页.md": "# 关联页\n\n[来源页](来源页.md)\n",
        },
    )
    baseline = context_pipeline._snapshot_managed_files(final_context_root)
    baseline_managed_pages = {
        managed_id: path.relative_to(final_context_root).as_posix()
        for managed_id, path in context_pipeline._managed_pages_by_source(final_context_root).items()
    }
    sandbox = tmp_path / "sandbox"
    _prepare_agent_candidate(final_context_root, sandbox)
    return (
        sandbox / "context",
        final_context_root,
        source_root,
        baseline,
        baseline_managed_pages,
        source_id,
        second_source_id,
    )


def test_agent_managed_source_marker_can_move_with_page_and_updated_links(tmp_path: Path) -> None:
    (
        candidate,
        final_context_root,
        source_root,
        baseline,
        baseline_managed_pages,
        source_id,
        _second_source_id,
    ) = _managed_agent_candidate_fixture(tmp_path)
    old_page = candidate / "旧主题" / "来源页.md"
    new_directory = candidate / "新主题"
    new_directory.mkdir()
    new_page = new_directory / "来源页.md"
    old_page.replace(new_page)
    new_page.write_text(
        "# 来源页\n\n"
        f"<!-- personal-context-managed-source: {source_id} -->\n\n"
        f"{_source_link(page_relative='新主题/来源页.md', final_context_root=final_context_root, source_root=source_root, source_id=source_id)}\n\n"
        "[关联页](../旧主题/关联页.md)\n",
        encoding="utf-8",
    )
    (candidate / "旧主题" / "关联页.md").write_text(
        "# 关联页\n\n[来源页](../新主题/来源页.md)\n",
        encoding="utf-8",
    )
    (candidate / "旧主题" / "description.md").write_text(
        "# 旧主题\n\n- [关联页](关联页.md)\n",
        encoding="utf-8",
    )
    (new_directory / "description.md").write_text(
        "# 新主题\n\n- [来源页](来源页.md)\n",
        encoding="utf-8",
    )
    (candidate / "description.md").write_text(
        "# Context\n\n- [旧主题](旧主题/description.md)\n- [新主题](新主题/description.md)\n",
        encoding="utf-8",
    )

    _validate_agent_candidate(
        candidate,
        baseline=baseline,
        changed_paths=context_pipeline._changed_context_paths(candidate, baseline),
        baseline_root=final_context_root,
        final_context_root=final_context_root,
        source_root=source_root,
        baseline_managed_pages_by_source=baseline_managed_pages,
        require_description=True,
        require_single_h1=True,
    )
    context_pipeline._validate_reference_graph(
        candidate,
        final_context_root=final_context_root,
        source_root=source_root,
        alias_targets={},
        repairable=True,
    )
    inputs = candidate.parent / "inputs"
    inputs.mkdir()
    assert (
        context_pipeline._validate_filesystem_agent_result(
            "done",
            candidate.parent,
            {},
            context_baseline=baseline,
            materialized_baseline=None,
            inputs_baseline={},
            baseline_root=final_context_root,
            baseline_path_by_candidate=None,
            final_context_root=final_context_root,
            source_root=source_root,
            alias_targets={},
            deleted_source_ids=set(),
            baseline_managed_pages_by_source=baseline_managed_pages,
        )
        == []
    )


def test_agent_candidate_tracks_unmanaged_aggregate_move_by_stable_page_identity(tmp_path: Path) -> None:
    final_context_root = tmp_path / "workspace" / "context"
    source_root = tmp_path / "workspace" / "source-meta"
    source_id = _write_atomic_source(
        source_root,
        locator="file:///sources/retrieval.md",
        title="BM25 检索实践",
    )
    source_link = _source_link(
        page_relative="主题甲/检索综合.md",
        final_context_root=final_context_root,
        source_root=source_root,
        source_id=source_id,
        label="检索来源",
    )
    _write_context_pages(
        final_context_root,
        {
            "description.md": "# Context\n\n- [主题甲](主题甲/description.md)\n- [主题乙](主题乙/description.md)\n",
            "主题甲/description.md": "# 主题甲\n\n- [检索综合](检索综合.md)\n- [保留页](保留页.md)\n",
            "主题甲/检索综合.md": f"# 检索综合\n\nBM25 与向量召回的综合说明。\n\n{source_link}\n",
            "主题甲/保留页.md": f"# 保留页\n\n主题甲的稳定内容。\n\n{source_link}\n",
            "主题乙/description.md": "# 主题乙\n",
        },
    )
    baseline = context_pipeline._snapshot_managed_files(final_context_root)
    baseline_paths_by_identity = context_pipeline._context_page_paths_by_identity(final_context_root)
    sandbox = tmp_path / "sandbox-aggregate-move"
    _prepare_agent_candidate(final_context_root, sandbox)
    candidate = sandbox / "context"
    moved = candidate / "主题乙" / "检索综合.md"
    (candidate / "主题甲" / "检索综合.md").replace(moved)
    moved.write_text(
        "# 检索综合\n\nBM25 与向量召回的综合说明。\n\n"
        + _source_link(
            page_relative="主题乙/检索综合.md",
            final_context_root=final_context_root,
            source_root=source_root,
            source_id=source_id,
            label="检索来源",
        )
        + "\n",
        encoding="utf-8",
    )
    (candidate / "主题甲" / "description.md").write_text(
        "# 主题甲\n\n- [保留页](保留页.md)\n",
        encoding="utf-8",
    )
    (candidate / "主题乙" / "description.md").write_text(
        "# 主题乙\n\n- [检索综合](检索综合.md)\n",
        encoding="utf-8",
    )
    (sandbox / "inputs").mkdir()

    assert (
        context_pipeline._validate_filesystem_agent_result(
            "done",
            sandbox,
            {},
            context_baseline=baseline,
            materialized_baseline=None,
            inputs_baseline={},
            baseline_root=final_context_root,
            baseline_path_by_candidate=None,
            final_context_root=final_context_root,
            source_root=source_root,
            alias_targets={},
            deleted_source_ids=set(),
            baseline_managed_pages_by_source={},
            baseline_partition_path_by_identity=baseline_paths_by_identity,
        )
        == []
    )

    pending_page = candidate / "待整理" / "本地来源" / "2026年08月" / "检索综合.md"
    pending_page.parent.mkdir(parents=True)
    moved.replace(pending_page)
    with pytest.raises(BaseError, match="normal_page_in_fallback"):
        context_pipeline._validate_context_partition_integrity(
            candidate,
            source_root=source_root,
            baseline_root=final_context_root,
            baseline_path_by_identity=baseline_paths_by_identity,
            alias_targets={},
            repairable=True,
        )


def test_agent_candidate_allows_complete_normal_directory_move_but_partition_gate_rejects_pending(
    tmp_path: Path,
) -> None:
    final_context_root = tmp_path / "workspace" / "context"
    source_root = tmp_path / "workspace" / "source-meta"
    source_id = _write_atomic_source(
        source_root,
        locator="file:///sources/directory-move.md",
        title="目录移动来源",
    )

    def source_link(page_relative: str) -> str:
        return _source_link(
            page_relative=page_relative,
            final_context_root=final_context_root,
            source_root=source_root,
            source_id=source_id,
            label="原子来源",
        )

    _write_context_pages(
        final_context_root,
        {
            "description.md": "# Context\n\n- [旧父级](旧父级/description.md)\n- [新父级](新父级/description.md)\n",
            "旧父级/description.md": "# 旧父级\n\n- [稳定页](稳定页.md)\n- [检索主题](检索主题/description.md)\n",
            "旧父级/稳定页.md": f"# 稳定页\n\n旧父级保留内容。\n\n{source_link('旧父级/稳定页.md')}\n",
            "旧父级/检索主题/description.md": "# 检索主题\n\n- [来源页](来源页.md)\n",
            "旧父级/检索主题/来源页.md": (
                "# BM25 检索来源\n\n"
                f"<!-- personal-context-managed-source: {source_id} -->\n\n"
                f"{source_link('旧父级/检索主题/来源页.md')}\n"
            ),
            "新父级/description.md": "# 新父级\n\n- [稳定页](稳定页.md)\n",
            "新父级/稳定页.md": f"# 稳定页\n\n新父级保留内容。\n\n{source_link('新父级/稳定页.md')}\n",
        },
    )
    baseline = context_pipeline._snapshot_managed_files(final_context_root)
    baseline_paths_by_identity = context_pipeline._context_page_paths_by_identity(final_context_root)
    baseline_managed_pages = {
        managed_id: page.relative_to(final_context_root).as_posix()
        for managed_id, page in context_pipeline._managed_pages_by_source(final_context_root).items()
    }
    sandbox = tmp_path / "sandbox-directory-move"
    _prepare_agent_candidate(final_context_root, sandbox)
    candidate = sandbox / "context"
    moved_directory = candidate / "新父级" / "检索主题"
    (candidate / "旧父级" / "检索主题").replace(moved_directory)
    moved_page = moved_directory / "来源页.md"
    moved_page.write_text(
        "# BM25 检索来源\n\n"
        f"<!-- personal-context-managed-source: {source_id} -->\n\n"
        f"{source_link('新父级/检索主题/来源页.md')}\n",
        encoding="utf-8",
    )
    (candidate / "旧父级" / "description.md").write_text(
        "# 旧父级\n\n- [稳定页](稳定页.md)\n",
        encoding="utf-8",
    )
    (candidate / "新父级" / "description.md").write_text(
        "# 新父级\n\n- [稳定页](稳定页.md)\n- [检索主题](检索主题/description.md)\n",
        encoding="utf-8",
    )
    (sandbox / "inputs").mkdir()

    assert (
        context_pipeline._validate_filesystem_agent_result(
            "done",
            sandbox,
            {},
            context_baseline=baseline,
            materialized_baseline=None,
            inputs_baseline={},
            baseline_root=final_context_root,
            baseline_path_by_candidate=None,
            final_context_root=final_context_root,
            source_root=source_root,
            alias_targets={},
            deleted_source_ids=set(),
            baseline_managed_pages_by_source=baseline_managed_pages,
            baseline_partition_path_by_identity=baseline_paths_by_identity,
        )
        == []
    )

    pending_directory = candidate / "待整理" / "本地来源" / "2026年08月" / "检索主题"
    pending_directory.parent.mkdir(parents=True)
    moved_directory.replace(pending_directory)
    with pytest.raises(BaseError, match="normal_page_in_fallback"):
        context_pipeline._validate_context_partition_integrity(
            candidate,
            source_root=source_root,
            baseline_root=final_context_root,
            baseline_path_by_identity=baseline_paths_by_identity,
            alias_targets={},
            repairable=True,
        )


@pytest.mark.parametrize("mode", ["delete", "duplicate", "change", "forge", "malformed"])
def test_agent_managed_source_marker_rejects_identity_changes(tmp_path: Path, mode: str) -> None:
    (
        candidate,
        final_context_root,
        source_root,
        baseline,
        baseline_managed_pages,
        source_id,
        second_source_id,
    ) = _managed_agent_candidate_fixture(tmp_path)
    managed_page = candidate / "旧主题" / "来源页.md"
    original = managed_page.read_text(encoding="utf-8")
    marker = f"<!-- personal-context-managed-source: {source_id} -->"

    if mode == "delete":
        managed_page.write_text(original.replace(marker + "\n\n", ""), encoding="utf-8")
    elif mode == "change":
        managed_page.write_text(original.replace(source_id, second_source_id), encoding="utf-8")
    elif mode == "malformed":
        managed_page.write_text(
            original.replace(marker, "<!-- personal-context-managed-source: forged -->"),
            encoding="utf-8",
        )
    else:
        new_page = candidate / "旧主题" / ("复制页.md" if mode == "duplicate" else "伪造页.md")
        forged_id = source_id if mode == "duplicate" else second_source_id
        new_page.write_text(
            f"# 新页面\n\n<!-- personal-context-managed-source: {forged_id} -->\n",
            encoding="utf-8",
        )
        description = candidate / "旧主题" / "description.md"
        description.write_text(
            description.read_text(encoding="utf-8") + f"- [新页面]({new_page.name})\n",
            encoding="utf-8",
        )

    with pytest.raises(BaseError) as raised:
        _validate_agent_candidate(
            candidate,
            baseline=baseline,
            changed_paths=context_pipeline._changed_context_paths(candidate, baseline),
            baseline_root=final_context_root,
            final_context_root=final_context_root,
            source_root=source_root,
            baseline_managed_pages_by_source=baseline_managed_pages,
            require_description=True,
            require_single_h1=True,
        )

    assert raised.value.status == StatusCode.CONTEXT_PROACTIVE_PIPELINE_EXECUTION_ERROR
    assert "managed source" in str(raised.value).casefold()


def _write_partition_page(
    context_root: Path,
    source_root: Path,
    *,
    relative: str,
    source_id: str | None,
    title: str,
    body: str,
) -> Path:
    page = context_root / relative
    page.parent.mkdir(parents=True, exist_ok=True)
    source_link = ""
    if source_id is not None:
        source_link = "\n\n" + _source_link(
            page_relative=relative,
            final_context_root=context_root,
            source_root=source_root,
            source_id=source_id,
        )
    page.write_text(f"# {title}\n\n{body}{source_link}\n", encoding="utf-8")
    return page


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("VLAct：表征中心的 VLA 持续预训练", "VLAct:表征中心的 VLA 持续预训练"),
        ("RFC：传输规范", "RFC:传输规范"),
    ],
)
def test_human_semantic_title_with_colon_is_not_treated_as_uri(value: str, expected: str) -> None:
    assert context_pipeline._page_label_candidate(value) == (expected, "readable")
    assert context_pipeline._source_title_label({"title": value, "locator": "file:///source.md"}) == value


@pytest.mark.parametrize(
    "value",
    [
        "https://example.com/topic",
        "mailto:user@example.com",
        "tel:+8613800000000",
        "urn:isbn:9780000000000",
        r"C:\Users\mega\topic.md",
        r"\\server\share\topic.md",
    ],
)
def test_human_semantic_title_rejects_real_uri_and_absolute_path(value: str) -> None:
    assert context_pipeline._page_label_candidate(value)[0] is None
    assert context_pipeline._source_title_label({"title": value, "locator": "file:///source.md"}) == ""


@pytest.mark.parametrize("value", [r"C:\Users\mega\topic.md", "C:/Users/mega/topic.md", "/home/user/topic.md"])
def test_human_semantic_title_rejects_cross_platform_absolute_paths(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    from pathlib import PurePosixPath

    monkeypatch.setattr(context_pipeline, "Path", PurePosixPath)
    assert context_pipeline._page_label_candidate(value)[0] is None
    assert context_pipeline._source_title_label({"title": value, "locator": "file:///source.md"}) == ""


def test_candidate_source_reference_uses_final_context_projection(tmp_path: Path) -> None:
    final_context = tmp_path / "workspace" / "context"
    source_root = tmp_path / "workspace" / "source-meta"
    source_id = _write_atomic_source(
        source_root,
        locator="file:///sources/vlact.md",
        title="VLAct：表征中心的 VLA 持续预训练",
    )
    relative = "视觉语言模型/VLAct.md"
    source_link = _source_link(
        page_relative=relative,
        final_context_root=final_context,
        source_root=source_root,
        source_id=source_id,
    )
    final_page = final_context / relative
    final_page.parent.mkdir(parents=True)
    final_page.write_text(f"# VLAct：表征中心的 VLA 持续预训练\n\n{source_link}\n", encoding="utf-8")

    candidate_context = tmp_path / "workspace" / "sandboxes" / "service" / "run" / "context"
    candidate_page = candidate_context / relative
    candidate_page.parent.mkdir(parents=True)
    candidate_page.write_text(final_page.read_text(encoding="utf-8"), encoding="utf-8")

    expected = {source_id}
    assert (
        context_pipeline._source_ids_reachable_from_page(
            final_context,
            source_root=source_root,
            page_relative=relative,
        )
        == expected
    )
    assert (
        context_pipeline._source_ids_reachable_from_page(
            candidate_context,
            final_context_root=final_context,
            source_root=source_root,
            page_relative=relative,
        )
        == expected
    )
    assert {
        str(item["source_id"])
        for item in context_pipeline._page_source_metadata(
            candidate_page,
            context_root=candidate_context,
            final_context_root=final_context,
            source_root=source_root,
            alias_targets=None,
        )
    } == expected


def test_agent_authored_pending_directory_is_validated_as_an_ordinary_directory(tmp_path: Path) -> None:
    final_context = tmp_path / "workspace" / "context"
    final_context.mkdir(parents=True)
    source_root = tmp_path / "workspace" / "source-meta"
    source_id = _write_atomic_source(
        source_root,
        locator="file:///sources/search.md",
        title="BM25 检索实践",
    )
    sandbox = tmp_path / "workspace" / "sandboxes" / "service" / "run"
    candidate = sandbox / "context"
    pending = candidate / "待整理"
    pending.mkdir(parents=True)
    relative = "待整理/检索实践.md"
    source_link = _source_link(
        page_relative=relative,
        final_context_root=final_context,
        source_root=source_root,
        source_id=source_id,
    )
    (candidate / "description.md").write_text(
        "# 个人上下文\n\n- [待整理](待整理/description.md)\n",
        encoding="utf-8",
    )
    (pending / "description.md").write_text(
        "# 待整理\n\n- [检索实践](检索实践.md)\n",
        encoding="utf-8",
    )
    (pending / "检索实践.md").write_text(
        f"# BM25 检索实践\n\n语义清晰但由 Agent 自主选择目录。\n\n{source_link}\n",
        encoding="utf-8",
    )
    inputs = sandbox / "inputs"
    inputs.mkdir()

    assert (
        context_pipeline._validate_filesystem_agent_result(
            "done",
            sandbox,
            {},
            context_baseline={},
            materialized_baseline=None,
            inputs_baseline={},
            baseline_root=final_context,
            baseline_path_by_candidate=None,
            final_context_root=final_context,
            source_root=source_root,
            alias_targets={},
            deleted_source_ids=set(),
            baseline_managed_pages_by_source={},
            baseline_partition_path_by_identity={},
        )
        == []
    )


def test_second_agent_service_accepts_formal_source_links_from_existing_context(tmp_path: Path) -> None:
    final_context = tmp_path / "workspace" / "context"
    source_root = tmp_path / "workspace" / "source-meta"
    existing_relative = "视觉语言模型/VLAct.md"
    source_ids = [
        _write_atomic_source(
            source_root,
            locator=f"file:///sources/vlact-{index}.md",
            title=f"VLAct 来源 {index}",
        )
        for index in range(4)
    ]
    existing_links = "\n".join(
        _source_link(
            page_relative=existing_relative,
            final_context_root=final_context,
            source_root=source_root,
            source_id=source_id,
        )
        for source_id in source_ids
    )
    _write_context_pages(
        final_context,
        {
            "description.md": "# 个人上下文\n\n- [视觉语言模型](视觉语言模型/description.md)\n",
            "视觉语言模型/description.md": "# 视觉语言模型\n\n- [VLAct](VLAct.md)\n",
            existing_relative: f"# VLAct：表征中心的 VLA 持续预训练\n\n{existing_links}\n",
        },
    )
    baseline = context_pipeline._snapshot_managed_files(final_context)
    baseline_paths = context_pipeline._context_page_paths_by_identity(final_context)

    sandbox = tmp_path / "workspace" / "sandboxes" / "second-service" / "second-run"
    _prepare_agent_candidate(final_context, sandbox)
    candidate = sandbox / "context"
    incoming_source_id = _write_atomic_source(
        source_root,
        locator="file:///sources/vision-action.md",
        title="视觉动作模型实践",
    )
    incoming_relative = "视觉语言模型/视觉动作模型.md"
    incoming_link = _source_link(
        page_relative=incoming_relative,
        final_context_root=final_context,
        source_root=source_root,
        source_id=incoming_source_id,
    )
    (candidate / incoming_relative).write_text(
        f"# 视觉动作模型实践\n\n第二个服务新增的知识。\n\n{incoming_link}\n",
        encoding="utf-8",
    )
    (candidate / "视觉语言模型" / "description.md").write_text(
        "# 视觉语言模型\n\n- [VLAct](VLAct.md)\n- [视觉动作模型](视觉动作模型.md)\n",
        encoding="utf-8",
    )
    inputs = sandbox / "inputs"
    inputs.mkdir()

    assert (
        context_pipeline._validate_filesystem_agent_result(
            "done",
            sandbox,
            {
                "documents": [
                    {
                        "logical_id": "file:///sources/vision-action.md",
                        "revision_id": "rev-1",
                        "title": "视觉动作模型实践",
                        "markdown": "第二个服务新增的知识。",
                    }
                ]
            },
            context_baseline=baseline,
            materialized_baseline=None,
            inputs_baseline={},
            baseline_root=final_context,
            baseline_path_by_candidate=None,
            final_context_root=final_context,
            source_root=source_root,
            alias_targets={},
            deleted_source_ids=set(),
            baseline_managed_pages_by_source={},
            baseline_partition_path_by_identity=baseline_paths,
        )
        == []
    )
    assert context_pipeline._source_ids_reachable_from_page(
        candidate,
        final_context_root=final_context,
        source_root=source_root,
        page_relative=existing_relative,
    ) == set(source_ids)


def test_rules_balanced_partition_rejects_new_readable_page_in_pending_with_sanitized_error(tmp_path: Path) -> None:
    context_root = tmp_path / "candidate" / "context"
    source_root = tmp_path / "workspace" / "source-meta"
    locator = "https://user:TOP_SECRET@example.test/private?token=HIDDEN"
    source_id = _write_atomic_source(
        source_root,
        locator=locator,
        title="BM25 检索实践",
        provider="feishu",
        observed_at="2026-08-12T00:00:00Z",
    )
    relative = "待整理/飞书/2026年08月/检索实践.md"
    _write_partition_page(
        context_root,
        source_root,
        relative=relative,
        source_id=source_id,
        title="BM25 检索实践",
        body="TOP_SECRET 正文不应出现在错误中。",
    )

    with pytest.raises(BaseError) as raised:
        context_pipeline._validate_context_partition_integrity(
            context_root,
            source_root=source_root,
            baseline_root=None,
            alias_targets=None,
            repairable=True,
        )

    rendered = str(raised.value)
    assert relative in rendered
    assert "normal_page_in_fallback" in rendered
    assert "TOP_SECRET" not in rendered
    assert "HIDDEN" not in rendered
    assert locator not in rendered


def test_rules_balanced_partition_rejects_normal_baseline_page_moved_into_pending(tmp_path: Path) -> None:
    baseline_root = tmp_path / "formal" / "context"
    source_root = tmp_path / "workspace" / "source-meta"
    _write_partition_page(
        baseline_root,
        source_root,
        relative="正常主题/稳定页面.md",
        source_id=None,
        title="稳定语义页面",
        body="稳定内容。",
    )
    context_root = tmp_path / "candidate" / "context"
    _write_partition_page(
        context_root,
        source_root,
        relative="待整理/未归属/2026年08月/稳定页面.md",
        source_id=None,
        title="稳定语义页面",
        body="稳定内容。",
    )

    with pytest.raises(BaseError, match="normal_page_in_fallback"):
        context_pipeline._validate_context_partition_integrity(
            context_root,
            source_root=source_root,
            baseline_root=baseline_root,
            alias_targets=None,
            repairable=True,
        )


def test_rules_balanced_partition_rejects_empty_pending_tree(tmp_path: Path) -> None:
    context_root = tmp_path / "candidate" / "context"
    pending = context_root / "待整理"
    pending.mkdir(parents=True)
    (pending / "description.md").write_text("# 待整理\n", encoding="utf-8")

    with pytest.raises(BaseError, match="empty_fallback_tree"):
        context_pipeline._validate_context_partition_integrity(
            context_root,
            source_root=tmp_path / "workspace" / "source-meta",
            baseline_root=None,
            alias_targets=None,
            repairable=True,
        )


def test_rules_balanced_partition_rejects_empty_nested_pending_navigation(tmp_path: Path) -> None:
    context_root = tmp_path / "candidate" / "context"
    source_root = tmp_path / "workspace" / "source-meta"
    source_id = _write_atomic_source(
        source_root,
        locator="file:///sources/2026.md",
        title="2026",
        provider="feishu",
        observed_at="2026-08-12T00:00:00Z",
    )
    _write_partition_page(
        context_root,
        source_root,
        relative="待整理/飞书/2026年08月/2026.md",
        source_id=source_id,
        title="2026",
        body="123 2026-08-12",
    )
    fake_navigation = context_root / "待整理" / "飞书" / "2026年08月" / "待整理导航"
    fake_navigation.mkdir(parents=True)
    (fake_navigation / "description.md").write_text("# 待整理导航\n", encoding="utf-8")

    with pytest.raises(BaseError, match="empty_fallback_navigation"):
        context_pipeline._validate_context_partition_integrity(
            context_root,
            source_root=source_root,
            baseline_root=None,
            alias_targets=None,
            repairable=True,
        )


def test_rules_balanced_partition_rejects_pending_marker_hidden_in_normal_forest(tmp_path: Path) -> None:
    context_root = tmp_path / "candidate" / "context"
    disguised_pending = context_root / "正常主题" / "待整理"
    disguised_pending.mkdir(parents=True)
    (disguised_pending / "description.md").write_text("# 待整理\n", encoding="utf-8")

    with pytest.raises(BaseError, match=r"正常主题/待整理 \[fallback_root_misplaced\]"):
        context_pipeline._validate_context_partition_integrity(
            context_root,
            source_root=tmp_path / "workspace" / "source-meta",
            baseline_root=None,
            alias_targets=None,
            repairable=True,
        )


@pytest.mark.parametrize(
    ("relative", "with_source", "expected_error"),
    [
        ("待整理/飞书/2026年08月/2026.md", False, "fallback_source_metadata_missing"),
        ("待整理/本地文件/2026年09月/2026.md", True, "fallback_route_mismatch"),
    ],
)
def test_rules_balanced_partition_requires_page_provenance_and_exact_pending_route(
    tmp_path: Path,
    relative: str,
    with_source: bool,
    expected_error: str,
) -> None:
    context_root = tmp_path / "candidate" / "context"
    source_root = tmp_path / "workspace" / "source-meta"
    source_id = _write_atomic_source(
        source_root,
        locator="file:///sources/2026.md",
        title="2026",
        provider="feishu",
        observed_at="2026-08-12T00:00:00Z",
    )
    _write_partition_page(
        context_root,
        source_root,
        relative=relative,
        source_id=source_id if with_source else None,
        title="2026",
        body="123 2026-08-12",
    )

    with pytest.raises(BaseError, match=expected_error):
        context_pipeline._validate_context_partition_integrity(
            context_root,
            source_root=source_root,
            baseline_root=None,
            alias_targets=None,
            repairable=True,
        )


def test_rules_balanced_partition_accepts_only_genuine_page_provenance_pending_route(tmp_path: Path) -> None:
    context_root = tmp_path / "candidate" / "context"
    source_root = tmp_path / "workspace" / "source-meta"
    source_id = _write_atomic_source(
        source_root,
        locator="file:///sources/2026.md",
        title="2026",
        provider="feishu",
        observed_at="2026-08-12T00:00:00Z",
    )
    _write_partition_page(
        context_root,
        source_root,
        relative="待整理/飞书/2026年08月/2026.md",
        source_id=source_id,
        title="2026",
        body="123 2026-08-12",
    )

    context_pipeline._validate_context_partition_integrity(
        context_root,
        source_root=source_root,
        baseline_root=None,
        alias_targets=None,
        repairable=True,
    )


def test_page_partition_ignores_fenced_headings_and_code_text_as_semantic_evidence(tmp_path: Path) -> None:
    context_root = tmp_path / "candidate" / "context"
    source_root = tmp_path / "workspace" / "source-meta"
    source_id = _write_atomic_source(
        source_root,
        locator="file:///sources/2026.md",
        title="Page Content",
        provider="github",
        observed_at="2026-06-12T00:00:00Z",
    )
    page = _write_partition_page(
        context_root,
        source_root,
        relative="待整理/GitHub/2026年06月/Page Content.md",
        source_id=source_id,
        title="Page Content",
        body="```markdown\n# BM25 检索高级实践\n向量检索与稀疏排序优化。\n```\n\n123 2026",
    )

    partition, label, reason = context_pipeline._context_page_partition(
        page,
        context_root=context_root,
        source_root=source_root,
        baseline_relative=None,
        allow_existing_fallback_promotion=False,
    )

    assert partition == "fallback"
    assert label is None
    assert reason == "generic_or_numeric_only"


def test_prospective_rules_partition_ignores_generated_empty_preview_placeholder(tmp_path: Path) -> None:
    source_root = tmp_path / "workspace" / "source-meta"
    source_id = _write_atomic_source(
        source_root,
        locator="file:///sources/Page Content.md",
        title="Page Content",
        provider="github",
        observed_at="2026-06-12T00:00:00Z",
    )

    partition, label, reason = context_pipeline._prospective_rules_page_partition(
        {
            "logical_id": "file:///sources/Page Content.md",
            "revision_id": "rev-empty",
            "title": "Page Content",
            "markdown": "",
        },
        source_root=source_root,
        source_id=source_id,
    )

    assert partition == "fallback"
    assert label is None
    assert reason == "generic_or_numeric_only"


def test_page_partition_ignores_related_managed_block_as_semantic_evidence(tmp_path: Path) -> None:
    context_root = tmp_path / "candidate" / "context"
    source_root = tmp_path / "workspace" / "source-meta"
    source_id = _write_atomic_source(
        source_root,
        locator="file:///sources/2026.md",
        title="Page Content",
        provider="github",
        observed_at="2026-06-12T00:00:00Z",
    )
    page = _write_partition_page(
        context_root,
        source_root,
        relative="待整理/GitHub/2026年06月/Page Content.md",
        source_id=source_id,
        title="Page Content",
        body=(
            "123 2026\n\n"
            f"{context_pipeline._RELATED_START}\n"
            "## 相关文档\n\n- [BM25 检索高级实践](../../检索/BM25.md)\n"
            f"{context_pipeline._RELATED_END}"
        ),
    )

    partition, label, reason = context_pipeline._context_page_partition(
        page,
        context_root=context_root,
        source_root=source_root,
        baseline_relative=None,
        allow_existing_fallback_promotion=False,
    )

    assert partition == "fallback"
    assert label is None
    assert reason == "generic_or_numeric_only"


def test_page_partition_ignores_inline_code_as_semantic_evidence(tmp_path: Path) -> None:
    context_root = tmp_path / "candidate" / "context"
    source_root = tmp_path / "workspace" / "source-meta"
    source_id = _write_atomic_source(
        source_root,
        locator="file:///sources/2026.md",
        title="Page Content",
        provider="github",
        observed_at="2026-06-12T00:00:00Z",
    )
    page = _write_partition_page(
        context_root,
        source_root,
        relative="待整理/GitHub/2026年06月/Page Content.md",
        source_id=source_id,
        title="Page Content",
        body="`BM25 检索高级实践与向量召回优化`\n\n123 2026",
    )

    partition, label, reason = context_pipeline._context_page_partition(
        page,
        context_root=context_root,
        source_root=source_root,
        baseline_relative=None,
        allow_existing_fallback_promotion=False,
    )

    assert partition == "fallback"
    assert label is None
    assert reason == "generic_or_numeric_only"


@pytest.mark.parametrize(
    ("locator", "expected_label"),
    [
        ("file:///private/source/BM25检索实践.md", "BM25检索实践"),
        (
            "https://user:TOP_SECRET@example.test/private/BM25%E6%A3%80%E7%B4%A2%E5%AE%9E%E8%B7%B5.md?token=HIDDEN",
            "BM25检索实践",
        ),
    ],
    ids=["file", "percent-encoded-http"],
)
def test_page_partition_uses_only_safe_source_locator_basename_when_title_defaults_to_locator(
    tmp_path: Path,
    locator: str,
    expected_label: str,
) -> None:
    context_root = tmp_path / "candidate" / "context"
    source_root = tmp_path / "workspace" / "source-meta"
    source_id = _write_atomic_source(
        source_root,
        locator=locator,
        title="",
        provider="github",
        observed_at="2026-06-12T00:00:00Z",
    )
    page = _write_partition_page(
        context_root,
        source_root,
        relative="正常主题/Page Content.md",
        source_id=source_id,
        title="Page Content",
        body="123 2026",
    )

    partition, label, reason = context_pipeline._context_page_partition(
        page,
        context_root=context_root,
        source_root=source_root,
        baseline_relative=None,
        allow_existing_fallback_promotion=False,
    )

    assert partition == "normal"
    assert label == expected_label
    assert reason is None


def test_page_partition_rejects_generic_source_locator_basename_when_title_defaults_to_locator(tmp_path: Path) -> None:
    context_root = tmp_path / "candidate" / "context"
    source_root = tmp_path / "workspace" / "source-meta"
    source_id = _write_atomic_source(
        source_root,
        locator="file:///private/source/2026.md",
        title="",
        provider="github",
        observed_at="2026-06-12T00:00:00Z",
    )
    page = _write_partition_page(
        context_root,
        source_root,
        relative="待整理/GitHub/2026年06月/Page Content.md",
        source_id=source_id,
        title="Page Content",
        body="123 2026",
    )

    partition, label, reason = context_pipeline._context_page_partition(
        page,
        context_root=context_root,
        source_root=source_root,
        baseline_relative=None,
        allow_existing_fallback_promotion=False,
    )

    assert partition == "fallback"
    assert label is None
    assert reason == "generic_or_numeric_only"


def test_rules_balanced_partition_keeps_legacy_baseline_pending_route_as_authoritative(tmp_path: Path) -> None:
    baseline_root = tmp_path / "formal" / "context"
    context_root = tmp_path / "candidate" / "context"
    source_root = tmp_path / "workspace" / "source-meta"
    source_id = _write_atomic_source(
        source_root,
        locator="file:///sources/legacy-low-confidence.md",
        title="2025",
        provider="github",
        observed_at="2026-06-12T00:00:00Z",
    )
    legacy_relative = "待整理/历史Git平台/2026年06月/2025.md"
    for root in (baseline_root, context_root):
        _write_partition_page(
            root,
            source_root,
            relative=legacy_relative,
            source_id=source_id,
            title="2025",
            body="123 2025-06-12",
        )

    context_pipeline._validate_context_partition_integrity(
        context_root,
        source_root=source_root,
        baseline_root=baseline_root,
        baseline_path_by_identity=context_pipeline._context_page_paths_by_identity(baseline_root),
        alias_targets=None,
        repairable=True,
    )


@pytest.mark.asyncio
async def test_agent_fallback_uses_available_navigation_when_source_month_leaf_is_full(tmp_path: Path) -> None:
    context_root = tmp_path / "candidate" / "context"
    source_root = tmp_path / "workspace" / "source-meta"
    old_locator = "file:///sources/2025.md"
    old_source_id = _write_atomic_source(
        source_root,
        locator=old_locator,
        title="2025",
        provider="github",
        observed_at="2026-06-12T00:00:00Z",
    )
    old_relative = "待整理/GitHub/2026年06月/2025.md"
    _write_partition_page(
        context_root,
        source_root,
        relative=old_relative,
        source_id=old_source_id,
        title="2025",
        body="123 2025-06-12",
    )
    new_locator = "file:///sources/2026.md"
    new_source_id = _write_atomic_source(
        source_root,
        locator=new_locator,
        title="2026",
        provider="github",
        observed_at="2026-06-03T00:00:00Z",
    )

    await context_pipeline._apply_rules_increment(
        context_root,
        source_root=source_root,
        provider="feishu",
        processed={
            "documents": [
                {
                    "logical_id": new_locator,
                    "revision_id": "rev-new",
                    "title": "2026",
                    "markdown": "123 2026-07-03\n",
                }
            ],
            "blocks": [],
            "deleted_ids": [],
        },
        source_ids_by_logical_id={new_locator: new_source_id},
        deleted_source_ids=set(),
        run_time=datetime(2026, 9, 20, tzinfo=timezone.utc),
        max_pages_per_directory=1,
        max_subdirectories_per_directory=2,
        preserve_existing_paths=True,
    )

    assert (context_root / old_relative).is_file()
    new_page = context_pipeline._managed_pages_by_source(context_root)[new_source_id]
    new_relative = new_page.relative_to(context_root).as_posix()
    assert new_relative.startswith("待整理/GitHub/2026年06月/")
    assert new_page.parent != (context_root / "待整理" / "GitHub" / "2026年06月")
    assert "飞书" not in new_relative
    assert "2026年09月" not in new_relative
    context_pipeline._validate_context_capacities(
        context_root,
        max_pages_per_directory=1,
        max_subdirectories_per_directory=2,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("with_embedding", [False, True], ids=["sparse", "hybrid"])
async def test_agent_fallback_routes_new_readable_page_away_from_full_normal_leaf(
    tmp_path: Path,
    with_embedding: bool,
) -> None:
    context_root = tmp_path / "candidate" / "context"
    source_root = tmp_path / "workspace" / "source-meta"
    full_topic = context_root / "BM25检索高级实践"
    full_topic.mkdir(parents=True)
    existing_page = full_topic / "既有检索实践.md"
    existing_page.write_text(
        "# BM25 检索高级实践\n\nBM25 稀疏检索、相关性排序与召回优化。\n",
        encoding="utf-8",
    )
    context_pipeline._render_context_navigation(context_root)
    baseline_relative = existing_page.relative_to(context_root).as_posix()
    locator = "file:///sources/bm25-advanced.md"
    source_id = _write_atomic_source(
        source_root,
        locator=locator,
        title="BM25 检索高级实践",
        provider="github",
        observed_at="2026-06-12T00:00:00Z",
    )
    embedding_calls = 0

    async def embed_texts(texts: list[str]) -> list[list[float]]:
        nonlocal embedding_calls
        embedding_calls += 1
        return [[1.0, 0.0] for _ in texts]

    await context_pipeline._apply_rules_increment(
        context_root,
        source_root=source_root,
        provider="feishu",
        processed={
            "documents": [
                {
                    "logical_id": locator,
                    "revision_id": "rev-new",
                    "title": "BM25 检索高级实践",
                    "markdown": "BM25 稀疏检索、相关性排序与召回优化。\n",
                }
            ],
            "blocks": [],
            "deleted_ids": [],
        },
        source_ids_by_logical_id={locator: source_id},
        deleted_source_ids=set(),
        run_time=datetime(2026, 9, 20, tzinfo=timezone.utc),
        max_pages_per_directory=1,
        max_subdirectories_per_directory=1,
        embed_texts=embed_texts if with_embedding else None,
        preserve_existing_paths=True,
    )

    assert (context_root / baseline_relative).is_file()
    new_page = context_pipeline._managed_pages_by_source(context_root)[source_id]
    assert new_page.parent != full_topic
    assert new_page.is_relative_to(full_topic)
    assert new_page.parent.parent == full_topic
    assert "检索" in new_page.parent.name or "BM25" in new_page.parent.name
    assert new_page.parent.name.endswith("导航")
    assert embedding_calls > 0 if with_embedding else embedding_calls == 0
    context_pipeline._validate_context_capacities(
        context_root,
        max_pages_per_directory=1,
        max_subdirectories_per_directory=1,
    )


@pytest.mark.asyncio
async def test_hybrid_capacity_route_uses_dense_only_full_leaf_match_in_one_embedding_call(tmp_path: Path) -> None:
    context_root = tmp_path / "candidate" / "context"
    source_root = tmp_path / "workspace" / "source-meta"
    full_topic = context_root / "历史向量召回记录"
    full_topic.mkdir(parents=True)
    (full_topic / "既有记录.md").write_text(
        "# 历史向量召回记录\n\n面团发酵、烤箱温度与历史评估记录。\n",
        encoding="utf-8",
    )
    context_pipeline._render_context_navigation(context_root)
    locator = "file:///sources/vector-ranking.md"
    source_id = _write_atomic_source(
        source_root,
        locator=locator,
        title="向量召回实验",
        provider="github",
        observed_at="2026-06-12T00:00:00Z",
    )
    document: Mapping[str, object] = {
        "logical_id": locator,
        "revision_id": "rev-new",
        "title": "向量召回实验",
        "markdown": "向量索引、召回排序与近邻搜索评估。\n",
    }
    title, headings, preview = context_pipeline._document_semantic_parts(document)
    sparse_query = context_pipeline._semantic_fields(title, headings, preview)
    sparse_ranked = context_pipeline._rank_semantic_candidates(
        sparse_query,
        [context_pipeline._directory_semantic_fields(full_topic)],
    )
    assert sparse_ranked[0][1] > 0.22
    assert context_pipeline._accepted_semantic_directory(sparse_query, [full_topic]) is None
    embedding_calls = 0

    async def embed_texts(texts: list[str]) -> list[list[float]]:
        nonlocal embedding_calls
        embedding_calls += 1
        assert len(texts) == 2
        return [[1.0, 0.0], [1.0, 0.0]]

    target = await context_pipeline._select_rules_directory_hybrid(
        context_root,
        provider="feishu",
        document=document,
        source_id=source_id,
        run_time=datetime(2026, 9, 20, tzinfo=timezone.utc),
        embed_texts=embed_texts,
        max_pages=1,
        max_subdirectories=1,
        source_root=source_root,
        provider_neutral_fallback=True,
    )

    assert embedding_calls == 1
    assert target.parent == full_topic
    assert target.name.endswith("导航")


@pytest.mark.asyncio
@pytest.mark.parametrize("with_embedding", [False, True], ids=["sparse", "hybrid"])
@pytest.mark.parametrize("title_kind", ["generic", "locator"])
async def test_provider_neutral_rules_uses_partition_label_for_directory_page_and_heading(
    tmp_path: Path,
    with_embedding: bool,
    title_kind: str,
) -> None:
    context_root = tmp_path / "candidate" / "context"
    source_root = tmp_path / "workspace" / "source-meta"
    misleading_topic = context_root / "Page Content"
    misleading_topic.mkdir(parents=True)
    (misleading_topic / "旧页面.md").write_text("# Page Content\n\n123 2026\n", encoding="utf-8")
    fenced_topic = context_root / "烘焙温度控制"
    fenced_topic.mkdir(parents=True)
    (fenced_topic / "旧页面.md").write_text("# 烘焙温度控制\n\n烤箱与面团。\n", encoding="utf-8")
    existing_locator = "file:///sources/existing.md"
    existing_source_id = _write_atomic_source(
        source_root,
        locator=existing_locator,
        title="既有稳定主题",
        provider="github",
        observed_at="2026-06-01T00:00:00Z",
    )
    existing_relative = "既有稳定主题/固定路径.md"
    existing_page = context_root / existing_relative
    existing_page.parent.mkdir(parents=True)
    existing_page.write_text(
        f"# 既有稳定主题\n\n<!-- personal-context-managed-source: {existing_source_id} -->\n",
        encoding="utf-8",
    )
    context_pipeline._render_context_navigation(context_root)

    locator = "file:///sources/2026.md"
    semantic_label = "BM25 检索与向量召回排序工程深度实践指南"
    source_id = _write_atomic_source(
        source_root,
        locator=locator,
        title=semantic_label,
        provider="github",
        observed_at="2026-06-12T00:00:00Z",
    )
    document_title = locator if title_kind == "locator" else "Page Content"
    document: Mapping[str, object] = {
        "logical_id": locator,
        "revision_id": "rev-new",
        "title": document_title,
        "markdown": "```markdown\n# 烘焙温度控制\n代码中的伪语义。\n```\n\n123 2026\n",
    }
    partition, label, reason = context_pipeline._prospective_rules_page_partition(
        document,
        source_root=source_root,
        source_id=source_id,
    )
    assert (partition, label, reason) == ("normal", semantic_label, None)
    embedding_calls = 0

    async def embed_texts(texts: list[str]) -> list[list[float]]:
        nonlocal embedding_calls
        embedding_calls += 1
        if embedding_calls == 1:
            assert texts[0] == semantic_label
            return [[1.0, 0.0], *([[0.0, 1.0]] * (len(texts) - 1))]
        return [[1.0, 0.0] for _ in texts]

    await context_pipeline._apply_rules_increment(
        context_root,
        source_root=source_root,
        provider="feishu",
        processed={"documents": [document], "blocks": [], "deleted_ids": []},
        source_ids_by_logical_id={locator: source_id},
        deleted_source_ids=set(),
        run_time=datetime(2026, 9, 20, tzinfo=timezone.utc),
        max_pages_per_directory=5,
        max_subdirectories_per_directory=8,
        embed_texts=embed_texts if with_embedding else None,
        preserve_existing_paths=True,
    )

    assert existing_page.is_file()
    assert context_pipeline._managed_pages_by_source(context_root)[existing_source_id] == existing_page
    new_page = context_pipeline._managed_pages_by_source(context_root)[source_id]
    assert new_page.parent.name == context_pipeline._safe_semantic_name(semantic_label)
    assert new_page.name == f"{context_pipeline._semantic_page_stem(semantic_label)}.md"
    assert new_page.read_text(encoding="utf-8").splitlines()[0] == f"# {semantic_label}"
    assert new_page.parent not in {misleading_topic, fenced_topic}
    assert embedding_calls > 0 if with_embedding else embedding_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("with_embedding", [False, True], ids=["sparse", "hybrid"])
async def test_provider_neutral_rules_keeps_sanitized_h2_and_preview_for_directory_matching(
    tmp_path: Path,
    with_embedding: bool,
) -> None:
    context_root = tmp_path / "candidate" / "context"
    source_root = tmp_path / "workspace" / "source-meta"
    database_topic = context_root / "PostgreSQL索引"
    database_topic.mkdir(parents=True)
    (database_topic / "既有索引.md").write_text(
        "# PostgreSQL 索引策略\n\nBTree 查询优化、执行计划与数据库索引维护。\n",
        encoding="utf-8",
    )
    context_pipeline._render_context_navigation(context_root)
    locator = "file:///sources/2026.md"
    source_id = _write_atomic_source(
        source_root,
        locator=locator,
        title="工程周报",
        provider="github",
        observed_at="2026-06-12T00:00:00Z",
    )
    document: Mapping[str, object] = {
        "logical_id": locator,
        "revision_id": "rev-new",
        "title": "Page Content",
        "markdown": (
            "```markdown\n# 家庭烘焙伪主题\n烤箱与面团。\n```\n\n"
            "## PostgreSQL 索引策略\n\nBTree 查询优化、执行计划与数据库索引维护。\n\n"
            f"{context_pipeline._RELATED_START}\n## 相关文档\n"
            "- [家庭烘焙](../家庭烘焙.md)\n"
            f"{context_pipeline._RELATED_END}\n"
        ),
    }
    assert context_pipeline._prospective_rules_page_partition(
        document,
        source_root=source_root,
        source_id=source_id,
    ) == ("normal", "工程周报", None)
    label_only = context_pipeline._semantic_fields("工程周报", (), "")
    expected_query = context_pipeline._semantic_fields(
        "工程周报",
        ("PostgreSQL 索引策略",),
        "BTree 查询优化、执行计划与数据库索引维护。",
    )
    assert context_pipeline._accepted_semantic_directory(label_only, [database_topic]) is None
    assert context_pipeline._accepted_semantic_directory(expected_query, [database_topic]) == database_topic
    embedding_calls = 0

    async def embed_texts(texts: list[str]) -> list[list[float]]:
        nonlocal embedding_calls
        embedding_calls += 1
        assert "工程周报" in texts[0]
        assert "PostgreSQL 索引策略" in texts[0]
        assert "BTree 查询优化" in texts[0]
        assert "Page Content" not in texts[0]
        assert "家庭烘焙" not in texts[0]
        return [[1.0, 0.0] for _ in texts]

    if with_embedding:
        target = await context_pipeline._select_rules_directory_hybrid(
            context_root,
            provider="feishu",
            document=document,
            source_id=source_id,
            run_time=datetime(2026, 9, 20, tzinfo=timezone.utc),
            embed_texts=embed_texts,
            source_root=source_root,
            provider_neutral_fallback=True,
        )
    else:
        target = context_pipeline._select_rules_directory(
            context_root,
            provider="feishu",
            document=document,
            source_id=source_id,
            run_time=datetime(2026, 9, 20, tzinfo=timezone.utc),
            source_root=source_root,
            provider_neutral_fallback=True,
        )

    assert target == database_topic
    assert embedding_calls == (1 if with_embedding else 0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model_target", "new_topic_title"),
    [
        ("directory_1", None),
        ("new_topic", "BM25检索实践"),
        ("new_topic", "家庭烘焙技巧"),
    ],
    ids=["existing-full-leaf", "new-topic-existing-full-leaf", "new-topic-overflow-root"],
)
async def test_agent_to_balanced_cannot_move_new_readable_page_back_into_full_leaf(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    model_target: str,
    new_topic_title: str | None,
) -> None:
    config = _config("agent", max_pages_per_directory=1, max_subdirectories_per_directory=2)
    service = ContextPipelineService(
        home=tmp_path,
        config=config,
        input_queue=asyncio.Queue(),
    )
    formal_context = tmp_path / "workspace" / "context"
    source_root = tmp_path / "workspace" / "source-meta"
    full_topic = formal_context / "BM25检索实践"
    full_topic.mkdir(parents=True)
    existing_page = full_topic / "既有检索.md"
    existing_page.write_text("# BM25 检索实践\n\n稀疏召回与相关性排序。\n", encoding="utf-8")
    other_topic = formal_context / "数据库索引"
    other_topic.mkdir(parents=True)
    (other_topic / "既有索引.md").write_text("# 数据库索引\n\nBTree 与查询计划。\n", encoding="utf-8")
    context_pipeline._render_context_navigation(formal_context)
    baseline_root_directories = {path.name for path in formal_context.iterdir() if path.is_dir()}
    locator = "file:///sources/bm25-capacity.md"
    source_id = _write_atomic_source(
        source_root,
        locator=locator,
        title="BM25 检索实践",
        provider="github",
        observed_at="2026-06-12T00:00:00Z",
    )

    async def failed_agent(**kwargs: object) -> str:
        del kwargs
        raise build_error(StatusCode.CONTEXT_PROACTIVE_PIPELINE_EXECUTION_ERROR, error_msg="invalid output")

    monkeypatch.setattr(context_pipeline, "run_personal_context_agent", failed_agent)
    _FakeDirectModel.instances.clear()
    _FakeDirectModel.outputs = [
        json.dumps(
            {
                "items": [
                    {
                        "item_index": 0,
                        "summary": "BM25 检索实践摘要。",
                        "page_title": "BM25 检索实践",
                        "keywords": [str("BM25 检索实践")[:40]],
                    }
                ]
            },
            ensure_ascii=False,
        )
    ]
    monkeypatch.setattr(context_pipeline, "Model", _FakeDirectModel)
    processed: dict[str, object] = {
        "documents": [
            {
                "logical_id": locator,
                "revision_id": "rev-new",
                "title": "BM25 检索实践",
                "markdown": "BM25 稀疏召回、相关性排序与检索优化。\n",
            }
        ],
        "blocks": [],
        "deleted_ids": [],
    }
    sandbox = tmp_path / "sandbox-agent-balanced-capacity"
    sandbox.mkdir()

    result = await service._filesystem_with_fallback(
        processed=processed,
        sandbox=sandbox,
        batch=_batch(),
        provider="feishu",
        run_time=datetime(2026, 9, 20, tzinfo=timezone.utc),
        source_ids_by_logical_id={locator: source_id},
    )

    assert result == "balanced"
    candidate = sandbox / "context"
    assert (candidate / existing_page.relative_to(formal_context)).is_file()
    new_page = context_pipeline._managed_pages_by_source(candidate)[source_id]
    assert new_page.parent != candidate / full_topic.relative_to(formal_context)
    assert new_page.is_relative_to(candidate / full_topic.relative_to(formal_context))
    assert {path.name for path in candidate.iterdir() if path.is_dir()} == baseline_root_directories
    assert all(
        context_pipeline._directory_ordinary_markdown_count(directory) > 0
        or context_pipeline._directory_direct_subdirectory_count(directory) > 0
        for directory in context_pipeline._context_directories(candidate)[1:]
    )
    context_pipeline._validate_context_capacities(
        candidate,
        max_pages_per_directory=1,
        max_subdirectories_per_directory=2,
    )
    assert len(_FakeDirectModel.instances) == 1


@pytest.mark.asyncio
async def test_agent_to_balanced_keeps_legal_rules_route_instead_of_leaving_empty_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ContextPipelineService(
        home=tmp_path,
        config=_config("agent", max_pages_per_directory=2, max_subdirectories_per_directory=4),
        input_queue=asyncio.Queue(),
    )
    formal_context = tmp_path / "workspace" / "context"
    source_root = tmp_path / "workspace" / "source-meta"
    existing_target = formal_context / "BM25历史资料"
    existing_target.mkdir(parents=True)
    existing_page = existing_target / "既有会议.md"
    existing_page.write_text("# BM25 团队会议\n\n历史决策与会议记录。\n", encoding="utf-8")
    context_pipeline._render_context_navigation(formal_context)
    locator = "file:///sources/bm25-routing.md"
    source_id = _write_atomic_source(
        source_root,
        locator=locator,
        title="BM25 检索实践",
        provider="github",
        observed_at="2026-06-12T00:00:00Z",
    )
    document: Mapping[str, object] = {
        "logical_id": locator,
        "revision_id": "rev-new",
        "title": "BM25 检索实践",
        "markdown": "稀疏召回、相关性排序与查询优化。\n",
    }
    title, headings, preview = context_pipeline._document_semantic_parts(document)
    ranked = context_pipeline._rank_semantic_candidates(
        context_pipeline._semantic_fields(title, headings, preview),
        [context_pipeline._directory_semantic_fields(existing_target)],
    )
    assert 0.0 < ranked[0][1] < context_pipeline._DIRECTORY_ACCEPT_SCORE

    async def failed_agent(**kwargs: object) -> str:
        del kwargs
        raise build_error(StatusCode.CONTEXT_PROACTIVE_PIPELINE_EXECUTION_ERROR, error_msg="invalid output")

    monkeypatch.setattr(context_pipeline, "run_personal_context_agent", failed_agent)
    _FakeDirectModel.instances.clear()
    _FakeDirectModel.outputs = [
        json.dumps(
            {
                "items": [
                    {
                        "item_index": 0,
                        "summary": "BM25 检索实践的有界摘要。",
                        "page_title": "BM25 检索实践",
                        "keywords": [str("BM25 检索实践")[:40]],
                    }
                ]
            },
            ensure_ascii=False,
        )
    ]
    monkeypatch.setattr(context_pipeline, "Model", _FakeDirectModel)
    sandbox = tmp_path / "sandbox-agent-balanced-legal-route"
    sandbox.mkdir()

    result = await service._filesystem_with_fallback(
        processed={"documents": [document], "blocks": [], "deleted_ids": []},
        sandbox=sandbox,
        batch=_batch(),
        provider="feishu",
        run_time=datetime(2026, 9, 20, tzinfo=timezone.utc),
        source_ids_by_logical_id={locator: source_id},
    )

    assert result == "balanced"
    candidate = sandbox / "context"
    assert (candidate / existing_page.relative_to(formal_context)).is_file()
    incoming_page = context_pipeline._managed_pages_by_source(candidate)[source_id]
    rules_directory = candidate / context_pipeline._safe_semantic_name("BM25 检索实践")
    assert incoming_page.parent == rules_directory
    assert incoming_page.parent != candidate / existing_target.relative_to(formal_context)
    assert all(
        context_pipeline._directory_ordinary_markdown_count(directory) > 0
        or context_pipeline._directory_direct_subdirectory_count(directory) > 0
        for directory in context_pipeline._context_directories(candidate)[1:]
    )
    assert len(_FakeDirectModel.instances) == 1


@pytest.mark.asyncio
async def test_agent_authored_pending_path_does_not_trigger_profile_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ContextPipelineService(home=tmp_path, config=_config("agent"), input_queue=asyncio.Queue())
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    validation_errors: list[str] = []
    secret_sentinels = (
        "TOKEN_SENTINEL_8f20",
        "https://user:password@example.test/private?token=hidden",
        "D:\\private\\source\\secret.md",
    )

    async def misplaced_agent(*, sandbox_path: Path, validate_result: Any, **kwargs: object) -> str:
        del kwargs
        page = sandbox_path / "context" / "待整理" / "飞书" / "2026年08月" / "检索实践.md"
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text(
            "# BM25 检索实践\n\n这是清晰可读的新主题。\n\n" + "\n".join(secret_sentinels) + "\n",
            encoding="utf-8",
        )
        context_pipeline._render_context_navigation(sandbox_path / "context")
        errors = validate_result("done", sandbox_path)
        validation_errors.extend(errors)
        if errors:
            raise build_error(StatusCode.CONTEXT_PROACTIVE_PIPELINE_EXECUTION_ERROR, error_msg=errors[0])
        return "done"

    monkeypatch.setattr(context_pipeline, "run_personal_context_agent", misplaced_agent)
    _FakeDirectModel.instances.clear()
    _FakeDirectModel.outputs = [
        json.dumps(
            {
                "items": [
                    {
                        "item_index": 0,
                        "summary": "回退摘要。",
                        "page_title": "检索实践",
                        "keywords": [str("检索实践")[:40]],
                    }
                ]
            },
            ensure_ascii=False,
        )
    ]
    monkeypatch.setattr(context_pipeline, "Model", _FakeDirectModel)

    result = await service._filesystem_with_fallback(
        processed={
            "documents": [
                {
                    "logical_id": "notes/search",
                    "revision_id": "rev-1",
                    "title": "BM25 检索实践",
                    "markdown": "清晰的检索实践内容。\n",
                }
            ],
            "blocks": [],
            "deleted_ids": [],
        },
        sandbox=sandbox,
        batch=_batch(),
    )

    assert result == "agent"
    assert validation_errors == []
    assert (sandbox / "context" / "待整理" / "飞书" / "2026年08月" / "检索实践.md").is_file()


def test_agent_twenty_first_page_is_a_hard_validation_failure(tmp_path: Path) -> None:
    context = tmp_path / "context"
    topic = context / "容量治理"
    topic.mkdir(parents=True)
    links: list[str] = []
    changed_paths = {"description.md", "容量治理/description.md"}
    for index in range(21):
        name = f"页面-{index:02d}.md"
        (topic / name).write_text(f"# 页面 {index}\n\n正文。\n", encoding="utf-8")
        links.append(f"- [页面 {index}]({name})")
        changed_paths.add(f"容量治理/{name}")
    (topic / "description.md").write_text("# 容量治理\n\n" + "\n".join(links) + "\n", encoding="utf-8")
    (context / "description.md").write_text(
        "# Context\n\n- [容量治理](容量治理/description.md)\n",
        encoding="utf-8",
    )

    with pytest.raises(BaseError, match="page capacity"):
        _validate_agent_candidate(
            context,
            baseline={},
            changed_paths=changed_paths,
            baseline_managed_pages_by_source={},
            max_pages_per_directory=20,
            max_subdirectories_per_directory=20,
            require_description=True,
            require_single_h1=True,
        )


def test_agent_fallback_capacity_exemption_does_not_bypass_root_safety(tmp_path: Path) -> None:
    context = tmp_path / "context"
    context.mkdir()
    (context / "越界页面.md").write_text("# 越界页面\n\n正文。\n", encoding="utf-8")
    (context / "description.md").write_text(
        "# Context\n\n- [越界页面](越界页面.md)\n",
        encoding="utf-8",
    )

    with pytest.raises(BaseError, match="root may only contain"):
        _validate_agent_candidate(
            context,
            baseline={},
            changed_paths={"description.md", "越界页面.md"},
            baseline_managed_pages_by_source={},
            capacity_exempt=True,
        )


def test_agent_nested_description_must_be_utf8(tmp_path: Path) -> None:
    context = tmp_path / "context"
    context.mkdir()
    (context / "description.md").write_text("root", encoding="utf-8")
    nested = context / "topics" / "description.md"
    nested.parent.mkdir()
    nested.write_bytes(b"\xff\xfe")
    with pytest.raises(Exception) as raised:
        _validate_agent_candidate(context, baseline={}, changed_paths=set(), require_description=True)
    assert getattr(raised.value, "status", None) == StatusCode.CONTEXT_PROACTIVE_PIPELINE_EXECUTION_ERROR


def test_agent_page_allows_credential_shaped_domain_text(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    page = sandbox / "context" / "topics" / "page.md"
    page.parent.mkdir(parents=True)
    page.write_text(
        "A configuration model may declare `api_key: str`; this is a type annotation, not a credential value.",
        encoding="utf-8",
    )
    _validate_agent_pages(sandbox / "context", ["topics/page.md"])


@pytest.mark.parametrize(
    "body",
    [
        "Page body without a title.\n",
        "# First title\n\nContent.\n\n# Second title\n",
    ],
    ids=["missing-h1", "multiple-h1"],
)
def test_agent_changed_page_requires_exactly_one_top_level_heading(tmp_path: Path, body: str) -> None:
    context = tmp_path / "context"
    page = context / "topics" / "page.md"
    page.parent.mkdir(parents=True)
    page.write_text(body, encoding="utf-8")

    with pytest.raises(Exception) as raised:
        _validate_agent_candidate(
            context,
            baseline={},
            changed_paths={"topics/page.md"},
            require_description=False,
            require_single_h1=True,
        )

    assert getattr(raised.value, "status", None) == StatusCode.CONTEXT_PROACTIVE_PIPELINE_EXECUTION_ERROR
    assert "exactly one top-level heading" in str(raised.value)


def test_agent_page_heading_check_ignores_fenced_hash_lines(tmp_path: Path) -> None:
    context = tmp_path / "context"
    page = context / "topics" / "page.md"
    page.parent.mkdir(parents=True)
    page.write_text(
        "# Page title\n\n```python\n# example comment\n```not-a-closing-fence\n# still code\n```\n",
        encoding="utf-8",
    )

    _validate_agent_candidate(
        context,
        baseline={},
        changed_paths={"topics/page.md"},
        require_description=False,
        require_single_h1=True,
    )


def test_agent_candidate_does_not_recheck_unchanged_historical_page_headings(tmp_path: Path) -> None:
    context = tmp_path / "context"
    page = context / "topics" / "legacy.md"
    page.parent.mkdir(parents=True)
    (context / "description.md").write_text(
        "# Context\n\n- [Topics](topics/description.md)\n",
        encoding="utf-8",
    )
    (page.parent / "description.md").write_text(
        "# Topics\n\n- [Legacy](legacy.md)\n",
        encoding="utf-8",
    )
    page.write_text("# Legacy title\n\n# Historical second title\n", encoding="utf-8")
    baseline = context_pipeline._snapshot_managed_files(context)

    _validate_agent_candidate(
        context,
        baseline=baseline,
        changed_paths=set(),
        require_single_h1=True,
    )


@pytest.mark.parametrize("payload", [b"\xff", "x" * 2_000_001], ids=["invalid-utf8", "oversized"])
def test_agent_page_artifact_errors_are_repairable(tmp_path: Path, payload: bytes | str) -> None:
    context = tmp_path / "context"
    context.mkdir()
    page = context / "topics" / "page.md"
    page.parent.mkdir()
    if isinstance(payload, bytes):
        page.write_bytes(payload)
    else:
        page.write_text(payload, encoding="utf-8")
    with pytest.raises(Exception) as raised:
        _validate_agent_pages(context, ["topics/page.md"])
    assert getattr(raised.value, "status", None) == StatusCode.CONTEXT_PROACTIVE_PIPELINE_EXECUTION_ERROR


@pytest.mark.parametrize(
    ("limit_name", "limit", "relative"),
    [
        ("_MAX_AGENT_CONTEXT_FILES", 0, "page.md"),
        ("_MAX_AGENT_CONTEXT_PATH_CHARS", 3, "long-page.md"),
        ("_MAX_AGENT_CONTEXT_FILE_BYTES", 3, "page.md"),
    ],
)
def test_agent_candidate_artifact_limits_are_repairable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    limit_name: str,
    limit: int,
    relative: str,
) -> None:
    context = tmp_path / "context"
    context.mkdir()
    monkeypatch.setattr(context_pipeline, limit_name, limit)
    (context / relative).write_text("page", encoding="utf-8")
    with pytest.raises(Exception) as raised:
        _validate_agent_candidate(
            context,
            baseline={},
            changed_paths={relative},
            require_description=False,
        )
    assert getattr(raised.value, "status", None) == StatusCode.CONTEXT_PROACTIVE_PIPELINE_EXECUTION_ERROR


def test_agent_can_delete_page_linked_only_to_current_deleted_source(tmp_path: Path) -> None:
    baseline_root = tmp_path / "baseline"
    candidate = tmp_path / "candidate"
    source_root = tmp_path / "workspace" / "source-meta"
    source_id = _write_atomic_source(source_root)
    source_link = _source_link(
        page_relative="page.md",
        final_context_root=baseline_root,
        source_root=source_root,
        source_id=source_id,
    )
    _write_context_pages(
        baseline_root,
        {
            "description.md": "# Context\n\n- [Page](page.md)\n",
            "page.md": f"# Page\n\n{source_link}\n",
        },
    )
    _prepare_agent_candidate(baseline_root, candidate)
    (candidate / "context" / "page.md").unlink()

    _validate_agent_candidate(
        candidate / "context",
        baseline=context_pipeline._snapshot_managed_files(baseline_root),
        changed_paths=set(),
        baseline_root=baseline_root,
        source_root=source_root,
        deleted_source_ids={source_id},
    )


@pytest.mark.parametrize("mode", ["surviving_source", "no_source"])
def test_agent_cannot_delete_page_not_exclusively_linked_to_current_deleted_sources(
    tmp_path: Path,
    mode: str,
) -> None:
    baseline_root = tmp_path / "baseline"
    candidate = tmp_path / "candidate"
    source_root = tmp_path / "workspace" / "source-meta"
    deleted_id = _write_atomic_source(source_root)
    links = [
        _source_link(
            page_relative="page.md",
            final_context_root=baseline_root,
            source_root=source_root,
            source_id=deleted_id,
        )
    ]
    if mode == "surviving_source":
        surviving_id = _write_atomic_source(source_root, locator="https://example.test/surviving")
        links.append(
            _source_link(
                page_relative="page.md",
                final_context_root=baseline_root,
                source_root=source_root,
                source_id=surviving_id,
            )
        )
    else:
        links = ["[External](https://example.test/only)"]
    _write_context_pages(
        baseline_root,
        {
            "description.md": "# Context\n\n- [Page](page.md)\n",
            "page.md": "# Page\n\n" + " and ".join(links) + "\n",
        },
    )
    _prepare_agent_candidate(baseline_root, candidate)
    (candidate / "context" / "page.md").unlink()

    with pytest.raises(BaseError) as raised:
        _validate_agent_candidate(
            candidate / "context",
            baseline=context_pipeline._snapshot_managed_files(baseline_root),
            changed_paths=set(),
            baseline_root=baseline_root,
            source_root=source_root,
            deleted_source_ids={deleted_id},
        )

    assert raised.value.status == StatusCode.CONTEXT_PROACTIVE_PIPELINE_EXECUTION_ERROR


def test_agent_description_rejects_frontmatter(tmp_path: Path) -> None:
    context = tmp_path / "context"
    context.mkdir()
    (context / "description.md").write_text("root", encoding="utf-8")
    nested = context / "topics" / "description.md"
    nested.parent.mkdir()
    nested.write_text("---\npc_sentinel: true\n---\nmanaged", encoding="utf-8")
    with pytest.raises(Exception) as raised:
        _validate_agent_candidate(context, baseline={}, changed_paths=set(), require_description=True)
    assert getattr(raised.value, "status", None) == StatusCode.CONTEXT_PROACTIVE_PIPELINE_EXECUTION_ERROR


def test_candidate_allows_missing_relative_source_link(tmp_path: Path) -> None:
    """Provider Markdown may contain a relative link not materialized in Context."""

    context = tmp_path / "context"
    context.mkdir()
    page = context / "topics" / "page.md"
    page.parent.mkdir()
    page.write_text("See [the related note](missing.md).\n", encoding="utf-8")

    context_pipeline._validate_description_navigation(context)


def test_candidate_allows_relative_source_link_outside_context(tmp_path: Path) -> None:
    """Provider Markdown may link to a source file outside the managed Context."""

    context = tmp_path / "context"
    context.mkdir()
    page = context / "sources" / "feishu" / "page.md"
    page.parent.mkdir(parents=True)
    page.write_text("See [the source](../../../../workdir/source.py).\n", encoding="utf-8")

    context_pipeline._validate_description_navigation(context)


def test_agent_candidate_allows_unresolved_links_outside_sandbox(tmp_path: Path) -> None:
    context = tmp_path / "sandbox" / "context"
    page = context / "sources" / "feishu" / "page.md"
    page.parent.mkdir(parents=True)
    page.write_text("See [the original](../../../../external/source.md).\n", encoding="utf-8")

    _validate_agent_candidate(
        context,
        baseline={},
        changed_paths={"sources/feishu/page.md"},
        require_description=False,
    )


def test_candidate_allows_drive_relative_source_link(tmp_path: Path) -> None:
    """A provider code snippet may contain a drive-relative Markdown target."""

    context = tmp_path / "context"
    context.mkdir()
    page = context / "topics" / "page.md"
    page.parent.mkdir()
    page.write_text(r"See [the code](D:workdir\agent-core\module.py).\n", encoding="utf-8")

    context_pipeline._validate_description_navigation(context)


def test_candidate_allows_absolute_link_in_ordinary_page(tmp_path: Path) -> None:
    context = tmp_path / "context"
    page = context / "topics" / "page.md"
    page.parent.mkdir(parents=True)
    page.write_text("See [the source](C:/outside/source.md).\n", encoding="utf-8")

    context_pipeline._validate_description_navigation(context)


@pytest.mark.parametrize("target", ["missing.md", "../../outside.md"])
def test_description_navigation_must_resolve_inside_context(tmp_path: Path, target: str) -> None:
    context = tmp_path / "context"
    context.mkdir()
    (context / "description.md").write_text(f"See [topic]({target}).\n", encoding="utf-8")

    with pytest.raises(Exception) as raised:
        context_pipeline._validate_description_navigation(context)

    assert getattr(raised.value, "status", None) == StatusCode.CONTEXT_PROACTIVE_PUBLISH_EXECUTION_ERROR


def test_description_navigation_accepts_verified_source_metadata_links(tmp_path: Path) -> None:
    candidate, final_context_root, source_root, source_id = _reference_graph_roots(tmp_path)
    root_link = _source_link(
        page_relative="description.md",
        final_context_root=final_context_root,
        source_root=source_root,
        source_id=source_id,
    )
    nested_link = _source_link(
        page_relative="topics/description.md",
        final_context_root=final_context_root,
        source_root=source_root,
        source_id=source_id,
    )
    _write_context_pages(
        candidate,
        {
            "description.md": f"# Context\n\n- [Evidence]({root_link.split('](', 1)[1][:-1]})\n",
            "topics/description.md": f"# Topics\n\n- [Evidence]({nested_link.split('](', 1)[1][:-1]})\n",
        },
    )

    context_pipeline._validate_description_navigation(
        candidate,
        final_context_root=final_context_root,
        source_root=source_root,
    )


def test_agent_candidate_accepts_verified_source_metadata_in_descriptions(tmp_path: Path) -> None:
    candidate, final_context_root, source_root, source_id = _reference_graph_roots(tmp_path)
    root_link = _source_link(
        page_relative="description.md",
        final_context_root=final_context_root,
        source_root=source_root,
        source_id=source_id,
    )
    _write_context_pages(candidate, {"description.md": f"# Context\n\n{root_link}\n"})

    _validate_agent_candidate(
        candidate,
        baseline=context_pipeline._snapshot_managed_files(candidate),
        changed_paths=set(),
        baseline_root=final_context_root,
        final_context_root=final_context_root,
        source_root=source_root,
    )


@pytest.mark.parametrize("target", ["../source-meta/src_missing.md", "../other/metadata.md"])
def test_description_navigation_rejects_unverified_source_metadata_links(
    tmp_path: Path,
    target: str,
) -> None:
    candidate, final_context_root, source_root, _ = _reference_graph_roots(tmp_path)
    _write_context_pages(candidate, {"description.md": f"# Context\n\n- [Evidence]({target})\n"})

    with pytest.raises(Exception) as raised:
        context_pipeline._validate_description_navigation(
            candidate,
            final_context_root=final_context_root,
            source_root=source_root,
        )

    assert getattr(raised.value, "status", None) == StatusCode.CONTEXT_PROACTIVE_PUBLISH_EXECUTION_ERROR


def test_agent_json_parser_accepts_fenced_and_double_encoded_json() -> None:
    expected = {"pages": {"topics/page.md": "# Page"}}
    assert (
        _load_agent_json(
            '```json\n{"pages": {"topics/page.md": "# Page"}}\n```',
            error_message="invalid",
        )
        == expected
    )
    assert _load_agent_json(json.dumps(json.dumps(expected)), error_message="invalid") == expected


def test_balanced_validation_details_redact_unix_unc_and_url_secrets() -> None:
    error = build_error(
        StatusCode.CONTEXT_PROACTIVE_PIPELINE_EXECUTION_ERROR,
        error_msg=(
            "token=secret url=https://user:pass@example.test/path?access_token=hidden&x=1 "
            "unix=/tmp/private.txt unc=\\\\server\\share\\private.txt"
        ),
    )

    details = _bounded_validation_errors(error)
    rendered = " ".join(details)
    assert "secret" not in rendered
    assert "user:pass" not in rendered
    assert "hidden" not in rendered
    assert "/tmp/private.txt" not in rendered
    assert "\\\\server\\share\\private.txt" not in rendered


@pytest.mark.asyncio
async def test_balanced_agent_failure_falls_back_to_rules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    queue: asyncio.Queue[object] = asyncio.Queue(maxsize=4)
    service = ContextPipelineService(home=tmp_path, config=_config("balanced"), input_queue=queue)

    _FakeDirectModel.instances.clear()
    _FakeDirectModel.outputs = ["not-json", "still-not-json", "also-not-json"]
    monkeypatch.setattr("openjiuwen.harness.personal_context.context_pipeline.Model", _FakeDirectModel)
    await service.start()
    await _submit_run(queue, _batch())

    assert not (tmp_path / "workspace" / "source-proofs").exists()
    assert list((tmp_path / "workspace" / "context").rglob("*.md"))
    await service.stop(timeout_seconds=1)


@pytest.mark.asyncio
async def test_deterministic_processing_does_not_prevent_filesystem_agent_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue: asyncio.Queue[object] = asyncio.Queue(maxsize=4)
    service = ContextPipelineService(home=tmp_path, config=_config("agent"), input_queue=queue)
    profiles: list[str] = []

    async def fail_agent(**kwargs: object) -> str:
        profiles.append("agent")
        raise RuntimeError("model unavailable")

    monkeypatch.setattr("openjiuwen.harness.personal_context.context_pipeline.run_personal_context_agent", fail_agent)
    _FakeDirectModel.instances.clear()
    _FakeDirectModel.outputs = ["bad-1"]
    monkeypatch.setattr("openjiuwen.harness.personal_context.context_pipeline.Model", _FakeDirectModel)
    await service.start()
    await _submit_run(queue, _batch())

    assert not (tmp_path / "workspace" / "source-proofs").exists()
    assert profiles == ["agent"]
    assert len(_page_model_calls()) == 1
    await service.stop(timeout_seconds=1)


@pytest.mark.asyncio
async def test_direct_agent_prompt_and_inputs_do_not_expose_prescribed_fallback_route(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ContextPipelineService(home=tmp_path, config=_config("agent"), input_queue=asyncio.Queue())
    batch = _batch(
        content="BODY_SENTINEL should remain source content only.",
        original_ref="https://user:TOP_SECRET@example.test/private/route-note.md?token=HIDDEN",
    )
    source_id = upsert_source_metadata(
        tmp_path / "workspace" / "source-meta",
        batch.items[0],
        provider="github",
        service_id="github-service",
        observed_at="2026-06-12T00:00:00Z",
    )
    captured: dict[str, object] = {}

    async def failed_agent(*, messages: list[object], sandbox_path: Path, **kwargs: object) -> str:
        del kwargs
        content = str(getattr(messages[0], "content", ""))
        captured["payload"] = json.loads(content.rsplit("\n", maxsplit=1)[-1])
        captured["briefing"] = (sandbox_path / "inputs" / "briefing.md").read_text(encoding="utf-8")
        metadata_path = next((sandbox_path / "inputs" / "records").rglob("metadata.json"))
        captured["metadata"] = json.loads(metadata_path.read_text(encoding="utf-8"))
        raise build_error(StatusCode.CONTEXT_PROACTIVE_PIPELINE_EXECUTION_ERROR, error_msg="invalid output")

    async def failed_balanced(**kwargs: object) -> tuple[set[str], int]:
        del kwargs
        raise build_error(StatusCode.CONTEXT_PROACTIVE_PIPELINE_EXECUTION_ERROR, error_msg="invalid output")

    monkeypatch.setattr(context_pipeline, "run_personal_context_agent", failed_agent)
    monkeypatch.setattr(service, "_filesystem_balanced_model_attempt", failed_balanced)
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    processed: dict[str, object] = {
        "documents": [
            {
                "logical_id": "notes/one",
                "revision_id": "rev-1",
                "title": "BM25 检索实践",
                "markdown": "BM25 检索与排序。",
            }
        ],
        "blocks": [],
        "deleted_ids": [],
    }

    result = await service._filesystem_with_fallback(
        processed=processed,
        sandbox=sandbox,
        batch=batch,
        provider="feishu",
        run_time=datetime(2026, 9, 20, tzinfo=timezone.utc),
        source_ids_by_logical_id={"notes/one": source_id},
    )

    assert result == "rules"
    payload = cast(dict[str, object], captured["payload"])
    previews = cast(list[dict[str, object]], payload["document_previews"])
    assert "fallback_route" not in previews[0]
    metadata = cast(dict[str, object], captured["metadata"])
    assert "fallback_route" not in metadata
    assert "fallback_route" not in str(captured["briefing"])
    route_projection = json.dumps({"payload": previews[0], "metadata": metadata}, ensure_ascii=False)
    for forbidden in ("TOP_SECRET", "HIDDEN", "BODY_SENTINEL", "飞书", "2026年09月"):
        assert forbidden not in route_projection


@pytest.mark.asyncio
async def test_agent_success_validates_pages_and_does_not_serialize_raw_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue: asyncio.Queue[object] = asyncio.Queue(maxsize=4)
    service = ContextPipelineService(home=tmp_path, config=_config("agent"), input_queue=queue)
    calls: list[tuple[str, str]] = []
    route_projections: list[tuple[bool, bool]] = []

    async def successful_agent(*, messages: list[object], **kwargs: object) -> str:
        content = str(getattr(messages[0], "content", ""))
        assert set(kwargs) == {
            "model_client",
            "model_request",
            "sandbox_path",
            "validate_result",
            "max_pages_per_directory",
            "max_subdirectories_per_directory",
        }
        profile = _message_profile(messages, kwargs)
        calls.append((profile, content))
        _assert_new_wiki_prompt(content)
        sandbox_path = Path(str(kwargs["sandbox_path"]))
        assert "summary-first semantic portal" in content
        assert "Simplified Chinese" in content
        assert "short ASCII slug" not in content
        assert "short, clear Simplified Chinese semantic names whenever possible" in content
        assert "safe English-only name remains valid" in content
        assert "Do not mechanically rename existing Context paths" in content
        assert "待整理" not in content
        assert "readable but isolated topic in its own semantic directory" in content
        assert "content-derived navigation directories" in content
        assert "fallback_route" not in content
        assert "at most 20 Unicode characters" in content
        assert "The final .md extension does not count" in content
        assert "Keep the complete display title in the Markdown H1" in content
        assert "240 UTF-8 bytes" in content
        assert "This is a small run: use the bounded document_previews" in content
        assert "read every bounded source_preview" not in content
        prompt_payload = json.loads(content.rsplit("\n", maxsplit=1)[-1])
        prompt_has_route = "fallback_route" in prompt_payload["document_previews"][0]
        briefing_payload = json.loads((sandbox_path / "inputs" / "briefing.json").read_text(encoding="utf-8"))
        briefing_has_route = "fallback_route" in briefing_payload["sources"][0]
        route_projections.append((prompt_has_route, briefing_has_route))
        source_content = next((sandbox_path / "inputs" / "records").rglob("content.md"))
        assert source_content.read_text(encoding="utf-8") == "First paragraph."
        processed_document = next((sandbox_path / "inputs" / "processed").rglob("context-document.md"))
        assert processed_document.read_text(encoding="utf-8").strip() == "[[ref:0]]\n\nFirst paragraph."
        blocks = next((sandbox_path / "inputs" / "processed").rglob("blocks.jsonl"))
        assert '"text": "First paragraph."' in blocks.read_text(encoding="utf-8")
        (sandbox_path / "tmp" / "filesystem-notes.md").write_text("scratch", encoding="utf-8")
        page = sandbox_path / "context" / "知识管理" / "长期记忆.md"
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text(
            "# 长期记忆\n\nAgent 整理后的知识。[[ref:0]]\n",
            encoding="utf-8",
        )
        (page.parent / "description.md").write_text(
            "# 知识管理\n\n- [长期记忆](长期记忆.md)\n",
            encoding="utf-8",
        )
        (sandbox_path / "context" / "description.md").write_text(
            "# 个人上下文\n\n- [知识管理](知识管理/description.md)\n",
            encoding="utf-8",
        )
        return "done"

    monkeypatch.setattr(
        "openjiuwen.harness.personal_context.context_pipeline.run_personal_context_agent", successful_agent
    )
    _FakeDirectModel.instances.clear()
    _FakeDirectModel.outputs = []
    monkeypatch.setattr(context_pipeline, "Model", _FakeDirectModel)
    await service.start()
    await _submit_run(
        queue,
        _batch(
            raw_snapshot=b"binary source",
            original_ref="https://user:pass@example.test/notes/one?token=hidden-secret",
            metadata={"access-token": "hidden-secret", "kind": "note"},
        ),
    )

    assert [profile for profile, _ in calls] == ["agent"]
    assert all(
        "binary source" not in content and "hidden-secret" not in content and "user:pass" not in content
        for _, content in calls
    )
    assert len(route_projections) == 1
    assert route_projections[0] == (False, False)
    assert not (tmp_path / "workspace" / "source-proofs").exists()
    published_page = tmp_path / "workspace" / "context" / "知识管理" / "长期记忆.md"
    assert "Agent 整理后的知识。" in published_page.read_text(encoding="utf-8")
    assert not (tmp_path / "workspace" / "context" / "topics" / "agent.md").exists()
    assert not (tmp_path / "workspace" / "inputs").exists()
    assert not (tmp_path / "workspace" / "tmp").exists()
    assert not (tmp_path / "workspace" / "personal_context_provenance_manifest.json").exists()
    await service.stop(timeout_seconds=1)


@pytest.mark.asyncio
async def test_filesystem_agent_undeclared_root_is_a_non_fallback_security_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = ContextPipelineService(home=tmp_path, config=_config("agent"), input_queue=asyncio.Queue())
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    processed = {
        "documents": [
            {
                "logical_id": "notes/one",
                "revision_id": "rev-1",
                "title": "One",
                "markdown": "Processed text.\n",
            }
        ],
        "deleted_ids": [],
    }

    async def unsafe_filesystem_agent(*, sandbox_path: Path, validate_result: Any, **kwargs: object) -> str:
        del kwargs
        (sandbox_path / "context" / "description.md").parent.mkdir(parents=True, exist_ok=True)
        (sandbox_path / "context" / "description.md").write_text("# Context\n", encoding="utf-8")
        (sandbox_path / "context" / "page.md").write_text("# Page\n\n[[ref:0]]\n", encoding="utf-8")
        (sandbox_path / "undeclared-output").mkdir()
        validate_result("done", sandbox_path)
        return "done"

    monkeypatch.setattr(
        "openjiuwen.harness.personal_context.context_pipeline.run_personal_context_agent", unsafe_filesystem_agent
    )
    with pytest.raises(Exception) as raised:
        await service._filesystem_with_fallback(
            processed=processed,
            sandbox=sandbox,
            batch=_batch(),
        )
    assert getattr(raised.value, "status", None) == StatusCode.CONTEXT_PROACTIVE_PUBLISH_EXECUTION_ERROR


@pytest.mark.asyncio
async def test_filesystem_agent_content_validation_can_fallback_to_balanced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = ContextPipelineService(home=tmp_path, config=_config("agent"), input_queue=asyncio.Queue())
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    existing_context = tmp_path / "workspace" / "context"
    existing_topic = existing_context / "既有主题"
    existing_topic.mkdir(parents=True)
    (existing_context / "description.md").write_text(
        "# Context\n\n- [既有主题](既有主题/description.md)\n", encoding="utf-8"
    )
    (existing_topic / "description.md").write_text("# 既有主题\n\n- [既有页面](既有页面.md)\n", encoding="utf-8")
    existing_page = existing_topic / "既有页面.md"
    existing_page.write_text("# 既有页面\n\n既有内容。\n", encoding="utf-8")
    processed = {
        "documents": [
            {
                "logical_id": "notes/one",
                "revision_id": "rev-1",
                "title": "One",
                "markdown": "Processed text.\n",
            }
        ],
        "blocks": [],
        "deleted_ids": [],
    }

    async def failed_agent(*, sandbox_path: Path, validate_result: Any, **kwargs: object) -> str:
        del kwargs
        page = sandbox_path / "context" / "topics" / "agent.md"
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text("This invalid page has no heading or reference.", encoding="utf-8")
        (sandbox_path / "context" / "description.md").write_text("description", encoding="utf-8")
        errors = validate_result("done", sandbox_path)
        if errors:
            raise build_error(StatusCode.CONTEXT_PROACTIVE_PIPELINE_EXECUTION_ERROR, error_msg=errors[0])
        return "done"

    monkeypatch.setattr("openjiuwen.harness.personal_context.context_pipeline.run_personal_context_agent", failed_agent)
    _FakeDirectModel.instances.clear()
    _FakeDirectModel.outputs = [
        json.dumps(
            {
                "items": [
                    {
                        "item_index": 0,
                        "summary": "Balanced filesystem summary.",
                        "page_title": "Balanced filesystem page",
                        "keywords": [str("Balanced filesystem page")[:40]],
                    }
                ],
            }
        )
    ]
    monkeypatch.setattr("openjiuwen.harness.personal_context.context_pipeline.Model", _FakeDirectModel)
    balanced_options: dict[str, object] = {}
    original_balanced_attempt = service._filesystem_balanced_model_attempt

    async def capture_balanced_attempt(**kwargs: object) -> tuple[set[str], int]:
        balanced_options.update(kwargs)
        return await original_balanced_attempt(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(service, "_filesystem_balanced_model_attempt", capture_balanced_attempt)

    result = await service._filesystem_with_fallback(
        processed=processed,
        sandbox=sandbox,
        batch=_batch(),
        provider="feishu",
        run_time=datetime(2026, 9, 4, tzinfo=timezone.utc),
    )

    assert result == "balanced"
    assert len(_FakeDirectModel.instances) == 1
    assert balanced_options["preserve_existing_paths"] is True
    assert balanced_options["provider"] == "feishu"
    assert balanced_options["run_time"] == datetime(2026, 9, 4, tzinfo=timezone.utc)
    assert processed["_filesystem_capacity_exempt"] is True
    assert (sandbox / "context" / "既有主题" / "既有页面.md").is_file()


@pytest.mark.asyncio
async def test_agent_originated_fallback_preserves_over_capacity_existing_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ContextPipelineService(
        home=tmp_path,
        config=_config("agent", max_pages_per_directory=1, max_subdirectories_per_directory=2),
        input_queue=asyncio.Queue(),
    )
    formal_context = tmp_path / "workspace" / "context"
    source_root = tmp_path / "workspace" / "source-meta"
    legacy_source_id = _write_atomic_source(source_root, locator="file:///sources/legacy.md")
    incoming_source_id = _write_atomic_source(
        source_root,
        locator="notes/fallback",
        title="降级输入",
        provider="local_files",
        observed_at="2026-09-05T00:00:00Z",
    )
    existing_topic = formal_context / "原路径"
    legacy_directory = formal_context / "既有来源" / "本地资料"
    existing_topic.mkdir(parents=True)
    legacy_directory.mkdir(parents=True)
    legacy_filename = "旧来源页.md"
    for name in ("页面一.md", "页面二.md"):
        (existing_topic / name).write_text(f"# {name[:-3]}\n\n既有内容。\n", encoding="utf-8")
    legacy_page = legacy_directory / legacy_filename
    legacy_page.write_text(
        context_pipeline._rules_source_page(
            {
                "logical_id": "file:///sources/legacy.md",
                "title": "旧来源页",
                "markdown": "旧正文。",
            },
            source_id=legacy_source_id,
            summary_override="旧摘要。",
        ),
        encoding="utf-8",
    )
    context_pipeline._render_context_navigation(formal_context)
    context_pipeline._validate_candidate(
        formal_context,
        final_context_root=formal_context,
        source_root=source_root,
        max_pages_per_directory=1,
        max_subdirectories_per_directory=2,
        capacity_exempt=True,
    )

    async def failed_agent(*, sandbox_path: Path, **kwargs: object) -> str:
        del kwargs
        rogue = sandbox_path / "context" / "rogue.md"
        rogue.write_text("# 失败候选\n", encoding="utf-8")
        raise build_error(StatusCode.CONTEXT_PROACTIVE_PIPELINE_EXECUTION_ERROR, error_msg="invalid output")

    monkeypatch.setattr(context_pipeline, "run_personal_context_agent", failed_agent)
    _FakeDirectModel.instances.clear()
    _FakeDirectModel.outputs = [
        json.dumps(
            {
                "items": [
                    {
                        "item_index": 0,
                        "summary": "降级摘要。",
                        "page_title": "降级页面",
                        "keywords": [str("降级页面")[:40]],
                    }
                ]
            },
            ensure_ascii=False,
        )
    ]
    monkeypatch.setattr(context_pipeline, "Model", _FakeDirectModel)
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    processed: dict[str, object] = {
        "documents": [
            {
                "logical_id": "notes/fallback",
                "revision_id": "rev-1",
                "title": "降级输入",
                "markdown": "降级输入内容。\n",
            }
        ],
        "blocks": [],
        "deleted_ids": [],
    }

    result = await service._filesystem_with_fallback(
        processed=processed,
        sandbox=sandbox,
        batch=_batch(),
        source_ids_by_logical_id={"notes/fallback": incoming_source_id},
    )

    assert result == "balanced"
    assert (sandbox / "context" / "原路径" / "页面一.md").is_file()
    assert (sandbox / "context" / "原路径" / "页面二.md").is_file()
    assert (sandbox / "context" / "既有来源" / "本地资料" / legacy_filename).is_file()
    assert processed["_filesystem_preserve_existing_paths"] is True
    assert processed["_filesystem_capacity_exempt"] is True
    assert not (sandbox / "context" / "rogue.md").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback_profile", ["balanced", "rules"])
async def test_agent_fallback_preserves_every_baseline_path_and_uses_page_metadata_pending_route(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fallback_profile: str,
) -> None:
    service = ContextPipelineService(
        home=tmp_path,
        config=_config("agent", max_pages_per_directory=1, max_subdirectories_per_directory=2),
        input_queue=asyncio.Queue(),
    )
    formal_context = tmp_path / "workspace" / "context"
    source_root = tmp_path / "workspace" / "source-meta"
    crowded = formal_context / "稳定原路径"
    crowded.mkdir(parents=True)
    for name in ("既有一.md", "既有二.md"):
        (crowded / name).write_text(f"# {name[:-3]}\n\n既有语义内容。\n", encoding="utf-8")
    legacy_source_id = _write_atomic_source(
        source_root,
        locator="file:///sources/legacy-low.md",
        title="2025",
        provider="local_files",
        observed_at="2026-06-12T00:00:00Z",
    )
    existing_pending_relative = "待整理/本地文件/2026年06月/2025.md"
    _write_partition_page(
        formal_context,
        source_root,
        relative=existing_pending_relative,
        source_id=legacy_source_id,
        title="2025",
        body="123 2025-06-12",
    )
    context_pipeline._render_context_navigation(formal_context)
    context_pipeline._validate_candidate(
        formal_context,
        final_context_root=formal_context,
        source_root=source_root,
        max_pages_per_directory=1,
        max_subdirectories_per_directory=2,
        capacity_exempt=True,
    )
    baseline_entries = {
        path.relative_to(formal_context).as_posix(): path.is_dir()
        for path in [formal_context, *formal_context.rglob("*")]
    }
    partition_validation_calls: list[tuple[Path, Mapping[str, object]]] = []
    validate_partition = context_pipeline._validate_context_partition_integrity

    def track_partition_validation(context_root: Path, **kwargs: object) -> None:
        partition_validation_calls.append((context_root, kwargs))
        validate_partition(context_root, **kwargs)

    monkeypatch.setattr(context_pipeline, "_validate_context_partition_integrity", track_partition_validation)

    async def failed_agent(*, sandbox_path: Path, **kwargs: object) -> str:
        del kwargs
        rogue = sandbox_path / "context" / "Agent失败残留.md"
        rogue.write_text("# Agent失败残留\n", encoding="utf-8")
        raise build_error(StatusCode.CONTEXT_PROACTIVE_PIPELINE_EXECUTION_ERROR, error_msg="invalid output")

    monkeypatch.setattr(context_pipeline, "run_personal_context_agent", failed_agent)
    _FakeDirectModel.instances.clear()
    _FakeDirectModel.outputs = [
        json.dumps(
            {
                "items": [
                    {
                        "item_index": 0,
                        "summary": "低置信来源的有界摘要。",
                        "page_title": "模型生成可读标题",
                        "keywords": [str("模型生成可读标题")[:40]],
                    }
                ]
            },
            ensure_ascii=False,
        )
    ]
    monkeypatch.setattr(context_pipeline, "Model", _FakeDirectModel)
    if fallback_profile == "rules":

        async def failed_balanced(**kwargs: object) -> tuple[set[str], int]:
            del kwargs
            raise build_error(StatusCode.CONTEXT_PROACTIVE_PIPELINE_EXECUTION_ERROR, error_msg="invalid output")

        monkeypatch.setattr(service, "_filesystem_balanced_model_attempt", failed_balanced)
    incoming_locator = "file:///sources/2026.md"
    incoming_source_id = _write_atomic_source(
        source_root,
        locator=incoming_locator,
        title="Page Content",
        provider="github",
        observed_at="2026-06-03T00:00:00Z",
    )
    processed: dict[str, object] = {
        "documents": [
            {
                "logical_id": incoming_locator,
                "revision_id": "rev-new",
                "title": "Page Content",
                "markdown": "123 2026\n",
            }
        ],
        "blocks": [],
        "deleted_ids": [],
    }
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()

    result = await service._filesystem_with_fallback(
        processed=processed,
        sandbox=sandbox,
        batch=_batch(),
        provider="feishu",
        run_time=datetime(2026, 9, 20, tzinfo=timezone.utc),
        source_ids_by_logical_id={incoming_locator: incoming_source_id},
    )

    # No legal model result was applied to this deterministic low-confidence page.
    assert result == "rules"
    candidate = sandbox / "context"
    for relative, is_directory in baseline_entries.items():
        path = candidate / relative
        assert path.is_dir() if is_directory else path.is_file()
    assert (candidate / existing_pending_relative).is_file()
    incoming_page = context_pipeline._managed_pages_by_source(candidate)[incoming_source_id]
    incoming_relative = incoming_page.relative_to(candidate).as_posix()
    assert incoming_relative.startswith("待整理/GitHub/2026年06月/")
    assert "飞书" not in incoming_relative
    assert "2026年09月" not in incoming_relative
    assert not (candidate / "Agent失败残留.md").exists()
    assert processed["_filesystem_capacity_exempt"] is True
    assert len(_FakeDirectModel.instances) == (1 if fallback_profile == "balanced" else 0)
    assert len(partition_validation_calls) == 1
    validated_root, validation_kwargs = partition_validation_calls[0]
    assert validated_root == candidate
    assert validation_kwargs["repairable"] is True
    assert validation_kwargs["baseline_path_by_identity"]


@pytest.mark.asyncio
async def test_direct_balanced_cannot_launder_new_deterministic_fallback_with_model_title(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ContextPipelineService(
        home=tmp_path,
        config=_config("balanced"),
        input_queue=asyncio.Queue(),
    )
    source_root = tmp_path / "workspace" / "source-meta"
    locator = "file:///sources/2026.md"
    source_id = _write_atomic_source(
        source_root,
        locator=locator,
        title="Page Content",
        provider="github",
        observed_at="2026-06-03T00:00:00Z",
    )
    _FakeDirectModel.instances.clear()
    _FakeDirectModel.outputs = [
        json.dumps(
            {
                "items": [
                    {
                        "item_index": 0,
                        "summary": "模型生成的检索摘要。",
                        "page_title": "BM25 检索实践",
                        "keywords": [str("BM25 检索实践")[:40]],
                    }
                ]
            },
            ensure_ascii=False,
        )
    ]
    monkeypatch.setattr(context_pipeline, "Model", _FakeDirectModel)
    processed: dict[str, object] = {
        "documents": [
            {
                "logical_id": locator,
                "revision_id": "rev-new",
                "title": "Page Content",
                "markdown": "123 2026\n",
            }
        ],
        "blocks": [],
        "deleted_ids": [],
    }
    assert (
        context_pipeline._prospective_rules_page_partition(
            cast(Mapping[str, object], cast(list[object], processed["documents"])[0]),
            source_root=source_root,
            source_id=source_id,
        )[0]
        == "fallback"
    )
    sandbox = tmp_path / "sandbox-direct-balanced-low"
    sandbox.mkdir()

    result = await service._filesystem_with_fallback(
        processed=processed,
        sandbox=sandbox,
        batch=_batch(),
        provider="feishu",
        run_time=datetime(2026, 9, 20, tzinfo=timezone.utc),
        source_ids_by_logical_id={locator: source_id},
    )

    assert result == "rules"
    incoming_page = context_pipeline._managed_pages_by_source(sandbox / "context")[source_id]
    incoming_relative = incoming_page.relative_to(sandbox / "context").as_posix()
    assert incoming_relative.startswith("待整理/GitHub/2026年06月/")
    assert "BM25" not in incoming_page.read_text(encoding="utf-8")
    assert len(_FakeDirectModel.instances) == 1


@pytest.mark.asyncio
async def test_direct_balanced_fallback_to_rules_reclusters_over_capacity_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ContextPipelineService(
        home=tmp_path,
        config=_config("balanced", max_pages_per_directory=2, max_subdirectories_per_directory=2),
        input_queue=asyncio.Queue(),
    )
    formal_context = tmp_path / "workspace" / "context"
    crowded_topic = formal_context / "拥挤主题"
    crowded_topic.mkdir(parents=True)
    (formal_context / "description.md").write_text(
        "# Context\n\n- [拥挤主题](拥挤主题/description.md)\n", encoding="utf-8"
    )
    (crowded_topic / "description.md").write_text(
        "# 拥挤主题\n\n- [页面1](页面1.md)\n- [页面2](页面2.md)\n- [页面3](页面3.md)\n",
        encoding="utf-8",
    )
    for index in range(1, 4):
        (crowded_topic / f"页面{index}.md").write_text(
            f"# 页面{index}\n\n关于主题 {index} 的既有内容。\n", encoding="utf-8"
        )
    context_pipeline._render_context_navigation(formal_context)

    class FailingModel:
        def __init__(self, **kwargs: object) -> None:
            del kwargs

        async def invoke(self, messages: list[object], **kwargs: object) -> object:
            del messages, kwargs
            raise RuntimeError("balanced model unavailable")

    monkeypatch.setattr(context_pipeline, "Model", FailingModel)
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    processed: dict[str, object] = {
        "documents": [
            {
                "logical_id": "notes/new",
                "revision_id": "rev-new",
                "title": "新增主题页面",
                "markdown": "新增主题内容。\n",
            }
        ],
        "blocks": [],
        "deleted_ids": [],
    }

    result = await service._filesystem_with_fallback(
        processed=processed,
        sandbox=sandbox,
        batch=_batch(),
    )

    assert result == "rules"
    assert processed["_filesystem_capacity_exempt"] is False
    candidate_context = sandbox / "context"
    for directory in [candidate_context, *[path for path in candidate_context.rglob("*") if path.is_dir()]]:
        ordinary_pages = [path for path in directory.glob("*.md") if path.name != "description.md" and path.is_file()]
        child_directories = [path for path in directory.iterdir() if path.is_dir()]
        assert len(ordinary_pages) <= 2
        assert len(child_directories) <= 2


@pytest.mark.asyncio
async def test_filesystem_agent_root_layout_failure_falls_back_from_clean_baseline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ContextPipelineService(home=tmp_path, config=_config("agent"), input_queue=asyncio.Queue())
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    processed = {
        "documents": [
            {
                "logical_id": "notes/one",
                "revision_id": "rev-1",
                "title": "One",
                "markdown": "Processed text.\n",
            }
        ],
        "blocks": [],
        "deleted_ids": [],
    }

    async def invalid_root_agent(*, sandbox_path: Path, validate_result: Any, **kwargs: object) -> str:
        del kwargs
        root_page = sandbox_path / "context" / "Agent 根层残留.md"
        root_page.parent.mkdir(parents=True, exist_ok=True)
        root_page.write_text("# Agent 根层残留\n\n[[ref:0]]\n", encoding="utf-8")
        (sandbox_path / "context" / "description.md").write_text(
            "# Context\n\n- [Agent 根层残留](<Agent 根层残留.md>)\n",
            encoding="utf-8",
        )
        errors = validate_result("done", sandbox_path)
        if errors:
            raise build_error(StatusCode.CONTEXT_PROACTIVE_PIPELINE_EXECUTION_ERROR, error_msg=errors[0])
        return "done"

    monkeypatch.setattr(context_pipeline, "run_personal_context_agent", invalid_root_agent)
    _FakeDirectModel.instances.clear()
    _FakeDirectModel.outputs = [
        json.dumps(
            {
                "items": [
                    {
                        "item_index": 0,
                        "summary": "Balanced 重新整理后的摘要。",
                        "page_title": "Balanced 重新整理",
                        "keywords": [str("Balanced 重新整理")[:40]],
                    }
                ]
            },
            ensure_ascii=False,
        )
    ]
    monkeypatch.setattr(context_pipeline, "Model", _FakeDirectModel)

    result = await service._filesystem_with_fallback(
        processed=processed,
        sandbox=sandbox,
        batch=_batch(),
    )

    assert result == "balanced"
    assert not (sandbox / "context" / "Agent 根层残留.md").exists()
    assert all(entry.name == "description.md" or entry.is_dir() for entry in (sandbox / "context").iterdir())


@pytest.mark.asyncio
async def test_filesystem_rules_fallback_discards_failed_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("INFO", logger=context_pipeline.__name__)
    service = ContextPipelineService(home=tmp_path, config=_config("agent"), input_queue=asyncio.Queue())
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    processed = {
        "documents": [
            {
                "logical_id": "notes/one",
                "revision_id": "rev-1",
                "title": "One",
                "markdown": "Processed text.\n",
            }
        ],
        "blocks": [],
        "deleted_ids": [],
    }

    async def failed_agent(*, sandbox_path: Path, **kwargs: object) -> str:
        del kwargs
        rogue = sandbox_path / "context" / "rogue.md"
        rogue.parent.mkdir(parents=True, exist_ok=True)
        rogue.write_text("failed Agent candidate", encoding="utf-8")
        raise build_error(StatusCode.CONTEXT_PROACTIVE_PIPELINE_EXECUTION_ERROR, error_msg="invalid output")

    async def failed_balanced(**kwargs: object) -> tuple[set[str], int]:
        del kwargs
        rogue = sandbox / "context" / "balanced-rogue.md"
        rogue.parent.mkdir(parents=True, exist_ok=True)
        rogue.write_text("failed balanced candidate", encoding="utf-8")
        raise build_error(StatusCode.CONTEXT_PROACTIVE_PIPELINE_EXECUTION_ERROR, error_msg="invalid output")

    monkeypatch.setattr(context_pipeline, "run_personal_context_agent", failed_agent)
    monkeypatch.setattr(service, "_filesystem_balanced_model_attempt", failed_balanced)

    result = await service._filesystem_with_fallback(
        processed=processed,
        sandbox=sandbox,
        batch=_batch(),
    )

    assert result == "rules"
    assert sorted(entry.name for entry in sandbox.iterdir()) == ["context", "inputs"]
    assert not (sandbox / "context" / "rogue.md").exists()
    assert not (sandbox / "context" / "balanced-rogue.md").exists()
    source_page = next(iter(context_pipeline._managed_pages_by_source(sandbox / "context").values()))
    assert source_page.is_file()
    assert processed["_filesystem_candidate_prepared"] is True
    assert "capacity_exempt_due_to_agent_fallback=true actual_profile=rules" in caplog.text


@pytest.mark.asyncio
async def test_filesystem_agent_missing_markdown_link_does_not_force_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = ContextPipelineService(home=tmp_path, config=_config("agent"), input_queue=asyncio.Queue())
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    processed = {
        "documents": [
            {
                "logical_id": "notes/one",
                "revision_id": "rev-1",
                "title": "One",
                "markdown": "Processed text.\n",
            }
        ],
        "blocks": [],
        "deleted_ids": [],
    }

    async def failed_agent(*, sandbox_path: Path, validate_result: Any, **kwargs: object) -> str:
        del kwargs
        page = sandbox_path / "context" / "topics" / "agent.md"
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text("# Agent page\n\nSee [missing](missing.md). [[ref:0]]\n", encoding="utf-8")
        (page.parent / "description.md").write_text(
            "# Topics\n\n[Agent](agent.md)\n",
            encoding="utf-8",
        )
        (sandbox_path / "context" / "description.md").write_text(
            "# Context\n\n[Topics](topics/description.md)\n",
            encoding="utf-8",
        )
        errors = validate_result("done", sandbox_path)
        if errors:
            raise build_error(StatusCode.CONTEXT_PROACTIVE_PIPELINE_EXECUTION_ERROR, error_msg=errors[0])
        return "done"

    monkeypatch.setattr("openjiuwen.harness.personal_context.context_pipeline.run_personal_context_agent", failed_agent)
    _FakeDirectModel.instances.clear()
    _FakeDirectModel.outputs = [
        json.dumps(
            {
                "pages": {"topics/balanced.md": "# Balanced\n\nBalanced filesystem page. [[ref:0]]"},
            }
        )
    ]
    monkeypatch.setattr("openjiuwen.harness.personal_context.context_pipeline.Model", _FakeDirectModel)

    result = await service._filesystem_with_fallback(
        processed=processed,
        sandbox=sandbox,
        batch=_batch(),
    )

    assert result == "agent"
    assert _FakeDirectModel.instances == []


@pytest.mark.asyncio
async def test_balanced_invalid_output_does_not_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = ContextPipelineService(home=tmp_path, config=_config("balanced"), input_queue=asyncio.Queue())
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    _FakeDirectModel.instances.clear()
    _FakeDirectModel.outputs = [
        "bad-1",
        json.dumps(
            {
                "items": [
                    {
                        "item_index": 0,
                        "summary": "This output must remain unused.",
                        "page_title": "Unused page title",
                        "keywords": [str("Unused page title")[:40]],
                    }
                ],
            }
        ),
    ]
    monkeypatch.setattr("openjiuwen.harness.personal_context.context_pipeline.Model", _FakeDirectModel)
    processed = {
        "documents": [
            {
                "logical_id": "notes/one",
                "revision_id": "rev-1",
                "title": "One",
                "markdown": "Processed text.\n",
            }
        ],
        "blocks": [],
        "deleted_ids": [],
        "actual_profile": "balanced",
    }
    result = await service._filesystem_with_fallback(
        processed=processed,
        sandbox=sandbox,
        batch=_batch(),
    )
    assert result == "rules"
    calls = _page_model_calls()
    assert len(calls) == 1
    assert len(calls[0][0]) == 1
    assert len(_FakeDirectModel.outputs) == 0  # Subsequent calls are directory requests, not page retries.


@pytest.mark.asyncio
async def test_balanced_delete_only_skips_page_model_and_does_not_restore_page(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ContextPipelineService(home=tmp_path, config=_config("balanced"), input_queue=asyncio.Queue())
    context_root = tmp_path / "workspace" / "context"
    source_root = tmp_path / "workspace" / "source-meta"
    source_id = _write_atomic_source(source_root)
    await context_pipeline._apply_rules_increment(
        context_root,
        provider="local",
        processed={
            "documents": [
                {
                    "logical_id": "notes/one",
                    "revision_id": "rev-1",
                    "title": "One",
                    "markdown": "Old processed body. [[ref:0]]\n",
                }
            ],
            "deleted_ids": [],
        },
        source_ids_by_logical_id={"notes/one": source_id},
        deleted_source_ids=set(),
        run_time=datetime(2026, 9, 2, tzinfo=timezone.utc),
        fallback_references=("[[ref:0]]",),
    )
    page_relative = (
        context_pipeline._managed_pages_by_source(context_root)[source_id].relative_to(context_root).as_posix()
    )

    _FakeDirectModel.instances.clear()
    _FakeDirectModel.outputs = []
    monkeypatch.setattr("openjiuwen.harness.personal_context.context_pipeline.Model", _FakeDirectModel)
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    processed = {
        "documents": [],
        "blocks": [],
        "deleted_ids": ["notes/one"],
        "actual_profile": "balanced",
    }
    aliases = {"[[ref:0]]": source_id}

    assert (
        await service._filesystem_with_fallback(
            processed=processed,
            sandbox=sandbox,
            batch=_batch(),
            alias_targets=aliases,
            deleted_source_ids={source_id},
            service_id="local",
        )
        == "rules"
    )
    await service._publish_processed(
        service_id="local",
        run_id="run-delete",
        batch=_batch(),
        processed=processed,
        sandbox=sandbox,
        alias_targets=aliases,
    )

    assert not (context_root / page_relative).exists()
    assert page_relative not in (context_root / "description.md").read_text(encoding="utf-8")
    assert _page_model_calls() == []


@pytest.mark.asyncio
async def test_source_ref_alias_survives_balanced_enrichment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue: asyncio.Queue[object] = asyncio.Queue(maxsize=8)
    service = ContextPipelineService(home=tmp_path, config=_config("balanced"), input_queue=queue)
    _FakeDirectModel.instances.clear()
    _FakeDirectModel.outputs = [
        json.dumps(
            {
                "items": [
                    {
                        "item_index": 0,
                        "summary": "Balanced source summary.",
                        "page_title": "Balanced source page",
                        "keywords": [str("Balanced source page")[:40]],
                    }
                ],
            }
        ),
    ]
    monkeypatch.setattr(context_pipeline, "Model", _FakeDirectModel)

    await service.start()
    try:
        await _submit_run(queue, _batch())

        assert len(_FakeDirectModel.instances) == 1
        calls = _page_model_calls()
        assert len(calls) == 1
        for messages, _kwargs in calls:
            prompt = "\n".join(str(getattr(message, "content", "")) for message in messages)
            assert "[[ref:0]]" not in prompt
            assert "src_" not in prompt
            assert "source-meta" not in prompt
        source_page = next(iter(context_pipeline._managed_pages_by_source(tmp_path / "workspace" / "context").values()))
        source_text = source_page.read_text(encoding="utf-8")
        assert "Balanced source summary." in source_text
        assert "[[ref:" not in source_text
        assert "source-meta/src_" in source_text
        assert service._run_states == {}
    finally:
        await service.stop(timeout_seconds=1)


@pytest.mark.asyncio
async def test_balanced_model_error_publishes_rules_candidate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = ContextPipelineService(home=tmp_path, config=_config("balanced"), input_queue=asyncio.Queue())
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    _FakeDirectModel.instances.clear()
    _FakeDirectModel.outputs = [OSError("disk failure")]
    monkeypatch.setattr("openjiuwen.harness.personal_context.context_pipeline.Model", _FakeDirectModel)
    processed = {
        "documents": [
            {
                "logical_id": "notes/one",
                "revision_id": "rev-1",
                "title": "One",
                "markdown": "Processed text.\n",
            }
        ],
        "blocks": [],
        "deleted_ids": [],
        "actual_profile": "balanced",
    }
    assert (
        await service._filesystem_with_fallback(
            processed=processed,
            sandbox=sandbox,
            batch=_batch(),
        )
        == "rules"
    )
    assert len(_page_model_calls()) == 1
    source_page = next(iter(context_pipeline._managed_pages_by_source(sandbox / "context").values()))
    assert "Processed text." in source_page.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_filesystem_candidate_prepare_disk_error_is_non_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = ContextPipelineService(home=tmp_path, config=_config("balanced"), input_queue=asyncio.Queue())
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()

    def fail_prepare(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise OSError("permission denied")

    monkeypatch.setattr("openjiuwen.harness.personal_context.context_pipeline._prepare_agent_candidate", fail_prepare)
    processed = {
        "documents": [
            {
                "logical_id": "notes/one",
                "revision_id": "rev-1",
                "title": "One",
                "markdown": "Processed text.\n",
            }
        ],
        "blocks": [],
        "deleted_ids": [],
        "actual_profile": "balanced",
    }
    with pytest.raises(Exception) as raised:
        await service._filesystem_with_fallback(
            processed=processed,
            sandbox=sandbox,
            batch=_batch(),
        )
    assert getattr(raised.value, "status", None) == StatusCode.CONTEXT_PROACTIVE_PUBLISH_EXECUTION_ERROR


@pytest.mark.asyncio
async def test_agent_publication_updates_aggregate_page_without_dropping_older_source(tmp_path: Path) -> None:
    service = ContextPipelineService(home=tmp_path, config=_config("rules"), input_queue=asyncio.Queue())

    def item(logical_id: str, revision_id: str, title: str) -> RawChangeItem:
        return RawChangeItem(
            logical_id=logical_id,
            revision_id=revision_id,
            operation="upsert",
            title=title,
            content=f"{title} content.",
            original_ref=f"file:///{logical_id}",
            metadata={},
        )

    first_batch = FetchBatch(
        batch_id="batch-aggregate-1",
        items=[item("notes/one", "rev-1", "One"), item("notes/two", "rev-1", "Two")],
    )
    first_sandbox = tmp_path / "sandbox-aggregate-first"
    first_topic = first_sandbox / "context" / "topics"
    first_topic.mkdir(parents=True)
    (first_sandbox / "context" / "description.md").write_text(
        "# Portal\n\n- [Topics](topics/description.md)\n", encoding="utf-8"
    )
    (first_topic / "description.md").write_text("# Topics\n\n- [Combined](combined.md)\n", encoding="utf-8")
    (first_topic / "combined.md").write_text("# Combined\n\nInitial Agent synthesis.\n", encoding="utf-8")
    first_documents = [
        {
            "logical_id": logical_id,
            "revision_id": "rev-1",
            "title": title,
            "markdown": f"{title} processed.\n",
            "original_ref": f"file:///{logical_id}",
            "metadata": {},
            "raw_snapshot": None,
            "actual_profile": "agent",
        }
        for logical_id, title in (("notes/one", "One"), ("notes/two", "Two"))
    ]
    await service._publish_processed(
        service_id="local",
        run_id="run-aggregate-1",
        batch=first_batch,
        processed={
            "documents": first_documents,
            "blocks": [],
            "deleted_ids": [],
            "actual_profile": "agent",
            "_agent_changed_context_paths": {"topics/combined.md"},
            "_agent_candidate_prepared": True,
            "_filesystem_candidate_profile": "agent",
        },
        sandbox=first_sandbox,
    )

    second_batch = FetchBatch(
        batch_id="batch-aggregate-2",
        items=[item("notes/one", "rev-2", "One updated")],
    )
    second_sandbox = tmp_path / "sandbox-aggregate-second"
    _prepare_agent_candidate(tmp_path / "workspace" / "context", second_sandbox)
    (second_sandbox / "context" / "topics" / "combined.md").write_text(
        "# Combined\n\nAgent merged the updated source with the existing topic.\n",
        encoding="utf-8",
    )
    await service._publish_processed(
        service_id="local",
        run_id="run-aggregate-2",
        batch=second_batch,
        processed={
            "documents": [
                {
                    "logical_id": "notes/one",
                    "revision_id": "rev-2",
                    "title": "One updated",
                    "markdown": "Updated processed text.\n",
                    "original_ref": "file:///notes/one",
                    "metadata": {},
                    "raw_snapshot": None,
                    "actual_profile": "agent",
                }
            ],
            "blocks": [],
            "deleted_ids": [],
            "actual_profile": "agent",
            "_agent_changed_context_paths": {"topics/combined.md"},
            "_agent_candidate_prepared": True,
            "_filesystem_candidate_profile": "agent",
        },
        sandbox=second_sandbox,
    )

    page = tmp_path / "workspace" / "context" / "topics" / "combined.md"
    text = page.read_text(encoding="utf-8")
    assert "Agent merged the updated source with the existing topic." in text
    assert "personal_context_logical_ids:" not in text


@pytest.mark.asyncio
async def test_filesystem_agent_can_update_an_existing_page_without_repair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ContextPipelineService(home=tmp_path, config=_config("agent"), input_queue=asyncio.Queue())

    old_batch = FetchBatch(
        batch_id="batch-old",
        items=[
            RawChangeItem(
                logical_id="notes/old",
                revision_id="rev-old",
                operation="upsert",
                title="Old",
                content="Old source.",
                original_ref="file:///notes/old",
                metadata={},
            )
        ],
    )
    first_sandbox = tmp_path / "sandbox-first"
    topic = first_sandbox / "context" / "topics"
    topic.mkdir(parents=True)
    (first_sandbox / "context" / "description.md").write_text(
        "# Context\n\n- [Combined](topics/combined.md)\n",
        encoding="utf-8",
    )
    (topic / "combined.md").write_text("# Combined\n\nOld knowledge.\n", encoding="utf-8")
    await service._publish_processed(
        service_id="local",
        run_id="run-old",
        batch=old_batch,
        processed={
            "documents": [
                {
                    "logical_id": "notes/old",
                    "revision_id": "rev-old",
                    "title": "Old",
                    "markdown": "Old processed knowledge.\n",
                    "original_ref": "file:///notes/old",
                    "metadata": {},
                    "raw_snapshot": None,
                    "actual_profile": "agent",
                }
            ],
            "blocks": [],
            "deleted_ids": [],
            "actual_profile": "agent",
            "_agent_changed_context_paths": {"topics/combined.md"},
            "_agent_candidate_prepared": True,
            "_filesystem_candidate_profile": "agent",
        },
        sandbox=first_sandbox,
    )

    new_batch = FetchBatch(
        batch_id="batch-new",
        items=[
            RawChangeItem(
                logical_id="notes/new",
                revision_id="rev-new",
                operation="upsert",
                title="New",
                content="New source.",
                original_ref="file:///notes/new",
                metadata={},
            )
        ],
    )
    processed = {
        "documents": [
            {
                "logical_id": "notes/new",
                "revision_id": "rev-new",
                "title": "New",
                "markdown": "New processed knowledge.\n",
                "original_ref": "file:///notes/new",
                "metadata": {},
                "raw_snapshot": None,
                "actual_profile": "balanced",
            }
        ],
        "blocks": [],
        "deleted_ids": [],
        "actual_profile": "balanced",
    }
    inherited_pages: list[str] = []

    async def agent_spy(*, sandbox_path: Path, validate_result: Any, **kwargs: object) -> str:
        del kwargs
        page = sandbox_path / "context" / "topics" / "combined.md"
        inherited_pages.append(page.read_text(encoding="utf-8"))
        page.write_text(
            "# Combined\n\nOld knowledge plus new knowledge. [[ref:0]]\n",
            encoding="utf-8",
        )
        assert validate_result("done", sandbox_path) == []
        return "done"

    monkeypatch.setattr(context_pipeline, "run_personal_context_agent", agent_spy)
    second_sandbox = tmp_path / "sandbox-second"
    second_sandbox.mkdir()

    assert (
        await service._filesystem_with_fallback(
            processed=processed,
            sandbox=second_sandbox,
            batch=new_batch,
        )
        == "agent"
    )
    assert inherited_pages and not inherited_pages[0].lstrip().startswith("---")
    assert "## Source / Evidence" not in inherited_pages[0]

    await service._publish_processed(
        service_id="local",
        run_id="run-new",
        batch=new_batch,
        processed=processed,
        sandbox=second_sandbox,
    )
    published = tmp_path / "workspace" / "context" / "topics" / "combined.md"
    text = published.read_text(encoding="utf-8")
    assert not text.lstrip().startswith("---")
    assert "Old knowledge plus new knowledge. [[ref:0]]" in text
    assert "personal_context_logical_ids:" not in text
    assert "## Source / Evidence" not in text


@pytest.mark.asyncio
async def test_delete_event_does_not_infer_edits_to_an_ordinary_aggregate(tmp_path: Path) -> None:
    service = ContextPipelineService(home=tmp_path, config=_config("rules"), input_queue=asyncio.Queue())
    first_sandbox = tmp_path / "sandbox-aggregate"
    topic = first_sandbox / "context" / "topics"
    topic.mkdir(parents=True)
    (first_sandbox / "context" / "description.md").write_text(
        "# Context\n\n- [Combined](topics/combined.md)\n",
        encoding="utf-8",
    )
    (topic / "combined.md").write_text("# Combined\n\nTwo sources.\n", encoding="utf-8")
    documents = [
        {
            "logical_id": logical_id,
            "revision_id": "rev-1",
            "title": title,
            "markdown": f"{title} source.\n",
            "original_ref": f"file:///{logical_id}",
            "metadata": {},
            "raw_snapshot": None,
            "actual_profile": "agent",
        }
        for logical_id, title in (("notes/one", "One"), ("notes/two", "Two"))
    ]
    await service._publish_processed(
        service_id="local",
        run_id="run-aggregate",
        batch=_batch(),
        processed={
            "documents": documents,
            "blocks": [],
            "deleted_ids": [],
            "actual_profile": "agent",
            "_agent_changed_context_paths": {"topics/combined.md"},
            "_agent_candidate_prepared": True,
            "_filesystem_candidate_profile": "agent",
        },
        sandbox=first_sandbox,
    )

    second_sandbox = tmp_path / "sandbox-delete"
    _prepare_agent_candidate(tmp_path / "workspace" / "context", second_sandbox)
    (second_sandbox / "context" / "topics" / "combined.md").write_text(
        "# Combined\n\nAgent edited the ordinary aggregate body.\n",
        encoding="utf-8",
    )
    await service._publish_processed(
        service_id="local",
        run_id="run-delete",
        batch=_batch(),
        processed={
            "documents": [],
            "blocks": [],
            "deleted_ids": ["notes/one"],
            "actual_profile": "agent",
            "_agent_changed_context_paths": set(),
            "_agent_candidate_prepared": True,
            "_filesystem_candidate_profile": "agent",
        },
        sandbox=second_sandbox,
    )

    aggregate = tmp_path / "workspace" / "context" / "topics" / "combined.md"
    aggregate_text = aggregate.read_text(encoding="utf-8")
    assert not aggregate_text.lstrip().startswith("---")
    assert "Agent edited the ordinary aggregate body." in aggregate_text
    assert (tmp_path / "workspace" / "context" / "description.md").is_file()


@pytest.mark.asyncio
async def test_publish_rejects_stale_description_after_page_deletion(tmp_path: Path) -> None:
    service = ContextPipelineService(home=tmp_path, config=_config("rules"), input_queue=asyncio.Queue())
    first_sandbox = tmp_path / "sandbox-single"
    page = first_sandbox / "context" / "topics" / "single.md"
    page.parent.mkdir(parents=True)
    (first_sandbox / "context" / "description.md").write_text(
        "# Context\n\n- [Topics](topics/description.md)\n- [Single](topics/single.md) and "
        "[Keep](topics/keep.md) remain available.\n",
        encoding="utf-8",
    )
    (page.parent / "description.md").write_text(
        "# Topics\n\n- [Single](single.md)\n",
        encoding="utf-8",
    )
    page.write_text("# Single\n\nOnly source.\n", encoding="utf-8")
    (page.parent / "keep.md").write_text("# Keep\n\nUnrelated page.\n", encoding="utf-8")
    await service._publish_processed(
        service_id="local",
        run_id="run-single",
        batch=_batch(),
        processed={
            "documents": [
                {
                    "logical_id": "notes/one",
                    "revision_id": "rev-1",
                    "title": "One",
                    "markdown": "Only source.\n",
                    "original_ref": "file:///notes/one",
                    "metadata": {},
                    "raw_snapshot": None,
                    "actual_profile": "agent",
                }
            ],
            "blocks": [],
            "deleted_ids": [],
            "actual_profile": "agent",
            "_agent_changed_context_paths": {"topics/single.md"},
            "_agent_candidate_prepared": True,
            "_filesystem_candidate_profile": "agent",
        },
        sandbox=first_sandbox,
    )

    second_sandbox = tmp_path / "sandbox-delete-single"
    _prepare_agent_candidate(tmp_path / "workspace" / "context", second_sandbox)
    (second_sandbox / "context" / "topics" / "single.md").unlink()
    with pytest.raises(Exception) as raised:
        await service._publish_processed(
            service_id="local",
            run_id="run-delete-single",
            batch=_batch(),
            processed={
                "documents": [],
                "blocks": [],
                "deleted_ids": ["notes/one"],
                "actual_profile": "agent",
                "_agent_changed_context_paths": set(),
                "_agent_candidate_prepared": True,
                "_filesystem_candidate_profile": "agent",
            },
            sandbox=second_sandbox,
        )
    assert getattr(raised.value, "status", None) == StatusCode.CONTEXT_PROACTIVE_PUBLISH_EXECUTION_ERROR


@pytest.mark.asyncio
async def test_agent_publication_keeps_multiple_changed_pages(tmp_path: Path) -> None:
    service = ContextPipelineService(home=tmp_path, config=_config("rules"), input_queue=asyncio.Queue())
    sandbox = tmp_path / "sandbox"
    context = sandbox / "context"
    topic = context / "skillforge"
    topic.mkdir(parents=True)
    (context / "description.md").write_text(
        "# Portal\n\n- [SkillForge](skillforge/description.md)\n",
        encoding="utf-8",
    )
    (topic / "description.md").write_text(
        "# SkillForge\n\n- [Overview](overview.md)\n- [Details](details.md)\n",
        encoding="utf-8",
    )
    (topic / "overview.md").write_text("# Overview\n\nAgent overview.\n", encoding="utf-8")
    (topic / "details.md").write_text("# Details\n\nAgent details.\n", encoding="utf-8")
    document = {
        "logical_id": "notes/one",
        "revision_id": "rev-1",
        "title": "One",
        "markdown": "Processing text.\n",
        "original_ref": "file:///notes/one",
        "metadata": {},
        "raw_snapshot": None,
        "actual_profile": "agent",
    }
    await service._publish_processed(
        service_id="local",
        run_id="run-1",
        batch=_batch(),
        processed={
            "documents": [document],
            "blocks": [{"block_id": "one", "logical_id": "notes/one", "order": 0, "text": "Processing text."}],
            "deleted_ids": [],
            "actual_profile": "agent",
            "_agent_changed_context_paths": {"skillforge/overview.md", "skillforge/details.md"},
            "_agent_candidate_prepared": True,
            "_filesystem_candidate_profile": "agent",
        },
        sandbox=sandbox,
    )

    published = tmp_path / "workspace" / "context" / "skillforge"
    assert "Agent overview." in (published / "overview.md").read_text(encoding="utf-8")
    assert "Agent details." in (published / "details.md").read_text(encoding="utf-8")
    assert not (published / "overview.md").read_text(encoding="utf-8").lstrip().startswith("---")
    assert not (published / "details.md").read_text(encoding="utf-8").lstrip().startswith("---")


@pytest.mark.asyncio
async def test_filesystem_fallback_downshifts_after_deterministic_processing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue: asyncio.Queue[object] = asyncio.Queue(maxsize=4)
    service = ContextPipelineService(home=tmp_path, config=_config("agent"), input_queue=queue)
    profiles: list[str] = []

    async def mixed_agent(*, messages: list[object], **kwargs: object) -> str:
        profile = _message_profile(messages, kwargs)
        profiles.append(profile)
        content = str(getattr(messages[0], "content", ""))
        _assert_new_wiki_prompt(content)
        raise RuntimeError("filesystem agent unavailable")

    monkeypatch.setattr("openjiuwen.harness.personal_context.context_pipeline.run_personal_context_agent", mixed_agent)
    _FakeDirectModel.instances.clear()
    _FakeDirectModel.outputs = [
        json.dumps(
            {
                "items": [
                    {
                        "item_index": 0,
                        "summary": "Balanced filesystem result.",
                        "page_title": "Balanced filesystem page",
                        "keywords": [str("Balanced filesystem page")[:40]],
                    }
                ],
            }
        ),
    ]
    monkeypatch.setattr("openjiuwen.harness.personal_context.context_pipeline.Model", _FakeDirectModel)
    await service.start()
    await _submit_run(queue, _batch())

    assert not (tmp_path / "workspace" / "source-proofs").exists()
    assert profiles == ["agent"]
    source_page = next(iter(context_pipeline._managed_pages_by_source(tmp_path / "workspace" / "context").values()))
    assert "Balanced filesystem result." in source_page.read_text(encoding="utf-8")
    await service.stop(timeout_seconds=1)


@pytest.mark.asyncio
async def test_filesystem_agent_without_page_output_falls_back_before_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue: asyncio.Queue[object] = asyncio.Queue(maxsize=4)
    service = ContextPipelineService(home=tmp_path, config=_config("agent"), input_queue=queue)

    async def long_path_agent(*, messages: list[object], **kwargs: object) -> str:
        del kwargs
        content = str(getattr(messages[0], "content", ""))
        _assert_new_wiki_prompt(content)
        return "done"

    monkeypatch.setattr(
        "openjiuwen.harness.personal_context.context_pipeline.run_personal_context_agent", long_path_agent
    )
    _FakeDirectModel.instances.clear()
    _FakeDirectModel.outputs = ["not-json"]
    monkeypatch.setattr(context_pipeline, "Model", _FakeDirectModel)
    await service.start()
    await _submit_run(queue, _batch())

    assert not (tmp_path / "workspace" / "source-proofs").exists()
    await service.stop(timeout_seconds=1)


@pytest.mark.asyncio
async def test_filesystem_agent_rejects_undeclared_context_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue: asyncio.Queue[object] = asyncio.Queue(maxsize=4)
    service = ContextPipelineService(home=tmp_path, config=_config("agent"), input_queue=queue)

    async def unsafe_agent(*, messages: list[object], **kwargs: object) -> str:
        content = str(getattr(messages[0], "content", ""))
        _assert_new_wiki_prompt(content)
        sandbox_path = Path(str(kwargs["sandbox_path"]))
        extra = sandbox_path / "context" / "not-declared.txt"
        extra.parent.mkdir(parents=True, exist_ok=True)
        extra.write_text("unexpected", encoding="utf-8")
        page = sandbox_path / "context" / "topics" / "agent.md"
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text("# Agent\n\nAgent filesystem result. [[ref:0]]\n", encoding="utf-8")
        (sandbox_path / "context" / "description.md").write_text("# Agent root\n", encoding="utf-8")
        return "done"

    monkeypatch.setattr("openjiuwen.harness.personal_context.context_pipeline.run_personal_context_agent", unsafe_agent)
    _FakeDirectModel.instances.clear()
    _FakeDirectModel.outputs = ["not-json"]
    monkeypatch.setattr("openjiuwen.harness.personal_context.context_pipeline.Model", _FakeDirectModel)
    await service.start()
    await _submit_run(queue, _batch())
    assert not (tmp_path / "workspace" / "source-proofs").exists()
    await service.stop(timeout_seconds=1)


@pytest.mark.asyncio
async def test_agent_reads_materialized_candidate_copy_inside_read_only_sandbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue: asyncio.Queue[object] = asyncio.Queue(maxsize=4)
    service = ContextPipelineService(home=tmp_path, config=_config("agent"), input_queue=queue)
    candidate = tmp_path / "workspace" / "materialized-sources" / "github" / "service" / "candidate"
    candidate.mkdir(parents=True)
    source_file = candidate / "README.md"
    source_file.write_text("candidate source", encoding="utf-8")

    async def successful_agent(*, messages: list[object], **kwargs: object) -> str:
        content = str(getattr(messages[0], "content", ""))
        if "Use the sandbox filesystem" in content:
            sandbox_path = Path(str(kwargs["sandbox_path"]))
            copied = sandbox_path / "materialized-source" / "README.md"
            assert copied.read_text(encoding="utf-8") == "candidate source"
            assert copied.stat().st_mode & 0o222 == 0
            page = sandbox_path / "context" / "sources" / "local" / "agent.md"
            page.parent.mkdir(parents=True, exist_ok=True)
            page.write_text("# Agent\n\nAgent filesystem result. [[ref:0]]\n", encoding="utf-8")
            (sandbox_path / "context" / "description.md").write_text("# Agent root\n", encoding="utf-8")
            return "done"
        raise AssertionError("Processing must not call DeepAgent")

    monkeypatch.setattr(
        "openjiuwen.harness.personal_context.context_pipeline.run_personal_context_agent", successful_agent
    )
    _FakeDirectModel.instances.clear()
    _FakeDirectModel.outputs = []
    monkeypatch.setattr(context_pipeline, "Model", _FakeDirectModel)
    await service.start()
    await _submit_run(
        queue,
        _batch(materialized_source_path=str(candidate), materialized_revision="rev-1"),
    )
    assert not (tmp_path / "workspace" / "sandboxes" / "local" / "run-1").exists()
    await service.stop(timeout_seconds=1)


def test_filesystem_reset_preserves_run_inputs_and_materialized_source(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    input_file = sandbox / "inputs" / "records" / "batch-1" / "source.md"
    materialized_file = sandbox / "materialized-source" / "README.md"
    context_file = sandbox / "context" / "old.md"
    temporary_file = sandbox / "tmp" / "attempt.txt"
    unexpected_root = sandbox / "unexpected.json"
    for path, content in (
        (input_file, "run input"),
        (materialized_file, "candidate source"),
        (context_file, "candidate output"),
        (temporary_file, "scratch"),
        (unexpected_root, "{}"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    input_bytes = input_file.read_bytes()
    materialized_bytes = materialized_file.read_bytes()

    context_pipeline._reset_filesystem_sandbox(sandbox)

    assert input_file.read_bytes() == input_bytes
    assert materialized_file.read_bytes() == materialized_bytes
    assert not context_file.exists()
    assert not temporary_file.exists()
    assert not unexpected_root.exists()


@pytest.mark.asyncio
async def test_materialized_source_is_copied_once_and_not_exposed_to_balanced_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ContextPipelineService(home=tmp_path, config=_config("agent"), input_queue=asyncio.Queue())
    sandbox = tmp_path / "workspace" / "sandboxes" / "local" / "run-1"
    sandbox.mkdir(parents=True)
    candidate = tmp_path / "workspace" / "materialized-sources" / "github" / "service" / "candidate"
    candidate.mkdir(parents=True)
    (candidate / "README.md").write_text("candidate source", encoding="utf-8")
    processed = {
        "documents": [
            {
                "logical_id": "notes/one",
                "revision_id": "rev-1",
                "title": "One",
                "markdown": "Processed text.\n",
                "original_ref": "file:///notes/one",
                "metadata": {},
                "raw_snapshot": None,
                "actual_profile": "agent",
            }
        ],
        "blocks": [],
        "deleted_ids": [],
        "actual_profile": "agent",
    }
    batch = _batch(materialized_source_path=str(candidate), materialized_revision="rev-1")
    real_materialize = context_pipeline._materialize_candidate_source
    copy_calls = 0
    observed_contents: list[bytes] = []

    def materialize_once(source_value: str | None, *, sandbox: Path, home: Path) -> str | None:
        nonlocal copy_calls
        copy_calls += 1
        return real_materialize(source_value, sandbox=sandbox, home=home)

    async def fail_agent_with_two_validations(**kwargs: object) -> str:
        sandbox_path = Path(str(kwargs["sandbox_path"]))
        validate_result = kwargs["validate_result"]
        assert callable(validate_result)
        for _ in range(2):
            observed_contents.append((sandbox_path / "materialized-source" / "README.md").read_bytes())
            validate_result("invalid", sandbox_path)
        raise RuntimeError("model output remained invalid")

    original_balanced = service._filesystem_balanced_model_attempt
    _FakeDirectModel.instances.clear()
    _FakeDirectModel.outputs = [
        json.dumps(
            {
                "items": [
                    {
                        "item_index": 0,
                        "summary": "Processed source summary.",
                        "page_title": "Processed source",
                        "keywords": ["source processing"],
                    }
                ]
            }
        )
    ]
    monkeypatch.setattr(context_pipeline, "Model", _FakeDirectModel)

    async def balanced_success(**kwargs: object) -> tuple[set[str], int]:
        assert "batch" not in kwargs
        assert "materialized_baseline" not in kwargs
        assert "materialized_path" not in kwargs
        assert "payload" not in kwargs
        assert kwargs["deleted_source_ids"] == set()
        return await original_balanced(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(context_pipeline, "_materialize_candidate_source", materialize_once)
    monkeypatch.setattr(context_pipeline, "run_personal_context_agent", fail_agent_with_two_validations)
    monkeypatch.setattr(service, "_filesystem_balanced_model_attempt", balanced_success)

    profile = await service._filesystem_with_fallback(
        processed=processed,
        sandbox=sandbox,
        batch=batch,
    )

    assert profile == "balanced"
    assert copy_calls == 1
    assert observed_contents == [b"candidate source"] * 2
    assert (sandbox / "materialized-source" / "README.md").read_bytes() == b"candidate source"


@pytest.mark.asyncio
async def test_non_model_agent_error_does_not_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    queue: asyncio.Queue[object] = asyncio.Queue(maxsize=4)
    service = ContextPipelineService(home=tmp_path, config=_config("agent"), input_queue=queue)
    candidate = tmp_path / "workspace" / "materialized-sources" / "github" / "service" / "candidate"
    candidate.mkdir(parents=True)
    (candidate / "README.md").write_text("candidate source", encoding="utf-8")

    async def fail_path(**kwargs: object) -> str:
        raise build_error(
            StatusCode.CONTEXT_PROACTIVE_PIPELINE_EXECUTION_ERROR,
            error_msg="sandbox path is invalid",
            details={"fallback_allowed": False},
        )

    monkeypatch.setattr("openjiuwen.harness.personal_context.context_pipeline.run_personal_context_agent", fail_path)
    _FakeDirectModel.instances.clear()
    _FakeDirectModel.outputs = []
    monkeypatch.setattr(context_pipeline, "Model", _FakeDirectModel)
    await service.start()
    with pytest.raises(Exception):
        await _submit_run(
            queue,
            _batch(materialized_source_path=str(candidate), materialized_revision="rev-1"),
        )
    assert not (tmp_path / "workspace" / "context" / "description.md").exists()
    assert not (tmp_path / "workspace" / "sandboxes" / "local" / "run-1").exists()
    await service.stop(timeout_seconds=1)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [
        StatusCode.DEEPAGENT_RUNTIME_ERROR,
        StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR,
        StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR,
        StatusCode.AGENT_CONTROLLER_RUNTIME_ERROR,
        StatusCode.AGENT_TOOL_NOT_FOUND,
    ],
)
async def test_filesystem_non_model_agent_statuses_do_not_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: StatusCode
) -> None:
    service = ContextPipelineService(home=tmp_path, config=_config("agent"), input_queue=asyncio.Queue())
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    balanced_calls = 0

    async def fail_agent(**kwargs: object) -> str:
        del kwargs
        raise build_error(status, error_msg="agent runtime failure")

    async def fail_balanced(**kwargs: object) -> dict[str, list[str]]:
        nonlocal balanced_calls
        del kwargs
        balanced_calls += 1
        raise AssertionError("non-model agent errors must not enter balanced fallback")

    monkeypatch.setattr(context_pipeline, "run_personal_context_agent", fail_agent)
    monkeypatch.setattr(service, "_filesystem_balanced_model_attempt", fail_balanced)

    with pytest.raises(Exception) as raised:
        await service._filesystem_with_fallback(
            processed={
                "documents": [],
                "blocks": [],
                "deleted_ids": [],
            },
            sandbox=sandbox,
            batch=_batch(),
        )

    assert getattr(raised.value, "status", None) is status
    assert balanced_calls == 0


@pytest.mark.asyncio
async def test_deterministic_processing_error_fails_without_model_fallback_or_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue: asyncio.Queue[object] = asyncio.Queue(maxsize=4)
    service = ContextPipelineService(home=tmp_path, config=_config("agent"), input_queue=queue)
    model_constructions = 0

    class UnexpectedProcessingModel:
        def __init__(self, **kwargs: object) -> None:
            nonlocal model_constructions
            del kwargs
            model_constructions += 1

        async def invoke(self, messages: list[object], **kwargs: object) -> object:
            del messages, kwargs
            raise RuntimeError("Processing must not invoke a model")

    def fail_normalization(content: str) -> str:
        del content
        raise ValueError("deterministic normalization failed")

    monkeypatch.setattr(context_pipeline, "Model", UnexpectedProcessingModel)
    monkeypatch.setattr(context_pipeline, "_normalize_markdown", fail_normalization)
    await service.start()
    completion = asyncio.get_running_loop().create_future()
    try:
        await queue.put(("batch", "local", "run-processing-error", _batch(), completion))
        with pytest.raises(Exception) as raised:
            await asyncio.wait_for(asyncio.shield(completion), timeout=2)

        assert getattr(raised.value, "status", None) == StatusCode.CONTEXT_PROACTIVE_PIPELINE_EXECUTION_ERROR
        assert isinstance(raised.value.__cause__, ValueError)
        assert model_constructions == 0
        assert not (tmp_path / "workspace" / "context" / "description.md").exists()
        assert not (tmp_path / "workspace" / "sandboxes" / "local" / "run-processing-error").exists()
    finally:
        if not completion.done():
            completion.cancel()
        await service.stop(timeout_seconds=1)


def test_plain_value_error_remains_eligible_for_filesystem_model_output_repair() -> None:
    assert _profile_fallback_allowed(ValueError("filesystem model output is invalid"))


def test_short_references_resolve_at_each_context_depth(tmp_path: Path) -> None:
    final_context_root = tmp_path / "workspace" / "context"
    source_root = tmp_path / "workspace" / "source-meta"
    candidate = tmp_path / "sandbox" / "context"
    source_id = _write_atomic_source(source_root)
    pages = {
        "root.md": "# Root\n\n[[ref:0]] and [existing](level/page.md).\n",
        "level/page.md": "# Level one\n\n[[ref:0]]\n",
        "level/deep/page.md": "# Level two\n\n[[ref:0]]\n",
    }
    for relative, markdown in pages.items():
        page = candidate / relative
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text(markdown, encoding="utf-8")

    context_pipeline._resolve_short_references(
        candidate,
        final_context_root=final_context_root,
        source_root=source_root,
        alias_targets={"[[ref:0]]": source_id},
    )

    assert f"[来源1](../source-meta/{source_id}.md)" in (candidate / "root.md").read_text(encoding="utf-8")
    assert f"[来源1](../../source-meta/{source_id}.md)" in (candidate / "level/page.md").read_text(encoding="utf-8")
    assert f"[来源1](../../../source-meta/{source_id}.md)" in (candidate / "level/deep/page.md").read_text(
        encoding="utf-8"
    )
    root_text = (candidate / "root.md").read_text(encoding="utf-8")
    assert "[existing](level/page.md)" in root_text
    assert "[[ref:" not in root_text
    assert "[[" not in root_text
    assert "## Source" not in root_text
    assert not root_text.startswith("---")


def test_short_references_resolve_multiple_and_repeated_tokens(tmp_path: Path) -> None:
    final_context_root = tmp_path / "workspace" / "context"
    source_root = tmp_path / "workspace" / "source-meta"
    candidate = tmp_path / "sandbox" / "context"
    first_id = _write_atomic_source(source_root)
    second_id = _write_atomic_source(source_root, locator="https://example.test/pr/2", title="Source Two")
    page = candidate / "topics" / "page.md"
    page.parent.mkdir(parents=True)
    page.write_text("# Page\n\n[[ref:0]] then [[ref:1]] and [[ref:0]].\n", encoding="utf-8")

    context_pipeline._resolve_short_references(
        candidate,
        final_context_root=final_context_root,
        source_root=source_root,
        alias_targets={"[[ref:0]]": first_id, "[[ref:1]]": second_id},
    )

    text = page.read_text(encoding="utf-8")
    assert text.count(f"[来源1](../../source-meta/{first_id}.md)") == 2
    assert text.count(f"[来源2](../../source-meta/{second_id}.md)") == 1
    assert first_id not in text.replace(f"../../source-meta/{first_id}.md", "")
    assert second_id not in text.replace(f"../../source-meta/{second_id}.md", "")
    assert "[[ref:" not in text


def test_short_reference_numbers_restart_for_each_page(tmp_path: Path) -> None:
    final_context_root = tmp_path / "workspace" / "context"
    source_root = tmp_path / "workspace" / "source-meta"
    candidate = tmp_path / "sandbox" / "context"
    first_id = _write_atomic_source(source_root)
    second_id = _write_atomic_source(source_root, locator="https://example.test/pr/2", title="Source Two")
    _write_context_pages(
        candidate,
        {
            "first.md": "# First\n\n[[ref:0]] then [[ref:1]].\n",
            "second.md": "# Second\n\n[[ref:1]].\n",
        },
    )

    context_pipeline._resolve_short_references(
        candidate,
        final_context_root=final_context_root,
        source_root=source_root,
        alias_targets={"[[ref:0]]": first_id, "[[ref:1]]": second_id},
    )

    assert "[来源1]" in (candidate / "first.md").read_text(encoding="utf-8")
    assert "[来源2]" in (candidate / "first.md").read_text(encoding="utf-8")
    second_text = (candidate / "second.md").read_text(encoding="utf-8")
    assert "[来源1]" in second_text
    assert "[来源2]" not in second_text


@pytest.mark.parametrize(
    ("markdown", "mapping_kind"),
    [
        ("# Page\n\n[[ref:9]]\n", "known"),
        ("# Page\n\n[[ref:01]]\n", "known"),
        ("# Page\n\n[[ref:x]]\n", "known"),
        ("# Page\n\n[[ref:0]]\n", "missing"),
    ],
)
def test_short_reference_resolution_rejects_unknown_residual_or_missing_source(
    tmp_path: Path,
    markdown: str,
    mapping_kind: str,
) -> None:
    final_context_root = tmp_path / "workspace" / "context"
    source_root = tmp_path / "workspace" / "source-meta"
    candidate = tmp_path / "sandbox" / "context"
    source_id = _write_atomic_source(source_root)
    page = candidate / "page.md"
    page.parent.mkdir(parents=True)
    page.write_text(markdown, encoding="utf-8")
    target = source_id if mapping_kind == "known" else "src_0123456789abcdef0123456789abcdef"

    with pytest.raises(BaseError) as raised:
        context_pipeline._resolve_short_references(
            candidate,
            final_context_root=final_context_root,
            source_root=source_root,
            alias_targets={"[[ref:0]]": target},
        )

    assert raised.value.status == StatusCode.CONTEXT_PROACTIVE_PIPELINE_EXECUTION_ERROR
    assert page.read_text(encoding="utf-8") == markdown


def _reference_graph_roots(tmp_path: Path) -> tuple[Path, Path, Path, str]:
    final_context_root = tmp_path / "workspace" / "context"
    source_root = tmp_path / "workspace" / "source-meta"
    candidate = tmp_path / "sandbox" / "context"
    source_id = _write_atomic_source(source_root)
    return candidate, final_context_root, source_root, source_id


def _write_context_pages(context_root: Path, pages: dict[str, str]) -> None:
    for relative, markdown in pages.items():
        page = context_root / relative
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text(markdown, encoding="utf-8")


def test_reference_graph_accepts_direct_and_transitive_atomic_sources(tmp_path: Path) -> None:
    candidate, final_context_root, source_root, source_id = _reference_graph_roots(tmp_path)
    source_link = _source_link(
        page_relative="b.md",
        final_context_root=final_context_root,
        source_root=source_root,
        source_id=source_id,
    )
    _write_context_pages(
        candidate,
        {
            "description.md": "# Context\n\n- [A](a.md)\n- [B](b.md)\n",
            "a.md": "# A\n\nSee [B](b.md).\n",
            "b.md": f"# B\n\nReferenced object: {source_link}.\n",
        },
    )

    context_pipeline._validate_reference_graph(
        candidate,
        final_context_root=final_context_root,
        source_root=source_root,
        repairable=False,
    )


def test_reference_graph_accepts_nested_descriptions_and_virtual_alias(tmp_path: Path) -> None:
    candidate, final_context_root, source_root, source_id = _reference_graph_roots(tmp_path)
    _write_context_pages(
        candidate,
        {
            "description.md": "# Context\n\n- [Topics](topics/description.md)\n",
            "topics/description.md": "# Topics\n\n- [Page](page.md)\n",
            "topics/page.md": "# Page\n\nThis page mentions [[ref:0]].\n",
        },
    )

    context_pipeline._validate_reference_graph(
        candidate,
        final_context_root=final_context_root,
        source_root=source_root,
        alias_targets={"[[ref:0]]": source_id},
        repairable=True,
    )


def test_reference_graph_ignores_unlinked_source_and_parses_fragment_and_title(tmp_path: Path) -> None:
    candidate, final_context_root, source_root, source_id = _reference_graph_roots(tmp_path)
    _write_atomic_source(source_root, locator="https://example.test/unlinked", title="Unlinked")
    source_link = _source_link(
        page_relative="b.md",
        final_context_root=final_context_root,
        source_root=source_root,
        source_id=source_id,
    )
    _write_context_pages(
        candidate,
        {
            "description.md": '# Context\n\n- [A](a.md "entry")\n- [B](b.md#details)\n',
            "a.md": '# A\n\nSee [B](b.md#details "section").\n',
            "b.md": f"# B\n\n{source_link}\n\n## Details\n",
        },
    )

    context_pipeline._validate_reference_graph(
        candidate,
        final_context_root=final_context_root,
        source_root=source_root,
        repairable=False,
    )


@pytest.mark.parametrize("case", ["external", "missing", "self", "rootless", "orphan", "navigation", "escape"])
def test_reference_graph_rejects_invalid_or_unrooted_context(
    tmp_path: Path,
    case: str,
) -> None:
    candidate, final_context_root, source_root, source_id = _reference_graph_roots(tmp_path)
    direct_source = _source_link(
        page_relative="page.md",
        final_context_root=final_context_root,
        source_root=source_root,
        source_id=source_id,
    )
    pages: dict[str, str]
    if case == "external":
        pages = {
            "description.md": "# Context\n\n- [Page](page.md)\n",
            "page.md": "# Page\n\n[External](https://example.test/article)\n",
        }
    elif case == "missing":
        pages = {
            "description.md": "# Context\n\n- [Page](page.md)\n",
            "page.md": "# Page\n\n[Missing](missing.md)\n",
        }
    elif case == "self":
        pages = {
            "description.md": "# Context\n\n- [Page](page.md)\n",
            "page.md": f"# Page\n\n[Self](page.md) and {direct_source}\n",
        }
    elif case == "rootless":
        pages = {
            "description.md": "# Context\n\n- [A](a.md)\n- [B](b.md)\n",
            "a.md": "# A\n\n[B](b.md)\n",
            "b.md": "# B\n\n[A](a.md)\n",
        }
    elif case in {"orphan", "navigation"}:
        pages = {
            "description.md": "# Context\n\n- [Page](page.md)\n",
            "page.md": f"# Page\n\n{direct_source}\n",
            "orphan.md": "# Orphan\n\n[Page](page.md)\n",
        }
    else:
        outside = tmp_path / "outside.md"
        outside.write_text("# Outside\n", encoding="utf-8")
        pages = {
            "description.md": "# Context\n\n- [Page](page.md)\n",
            "page.md": f"# Page\n\n{direct_source} and [Outside](../../outside.md)\n",
        }
    _write_context_pages(candidate, pages)

    with pytest.raises(BaseError) as raised:
        context_pipeline._validate_reference_graph(
            candidate,
            final_context_root=final_context_root,
            source_root=source_root,
            repairable=True,
        )

    assert raised.value.status == StatusCode.CONTEXT_PROACTIVE_PIPELINE_EXECUTION_ERROR


@pytest.mark.parametrize("literal", ["`[[ref:01]]`", "```text\n[[ref:01]]\n```"])
def test_reference_graph_rejects_malformed_short_reference_inside_code(
    tmp_path: Path,
    literal: str,
) -> None:
    candidate, final_context_root, source_root, source_id = _reference_graph_roots(tmp_path)
    direct_source = _source_link(
        page_relative="page.md",
        final_context_root=final_context_root,
        source_root=source_root,
        source_id=source_id,
    )
    _write_context_pages(
        candidate,
        {
            "description.md": "# Context\n\n- [Page](page.md)\n",
            "page.md": f"# Page\n\n{direct_source}\n\n{literal}\n",
        },
    )

    with pytest.raises(BaseError) as raised:
        context_pipeline._validate_reference_graph(
            candidate,
            final_context_root=final_context_root,
            source_root=source_root,
            repairable=True,
        )

    assert raised.value.status == StatusCode.CONTEXT_PROACTIVE_PIPELINE_EXECUTION_ERROR
