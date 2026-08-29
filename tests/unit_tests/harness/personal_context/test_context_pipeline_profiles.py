from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import pytest

import openjiuwen.harness.personal_context.context_pipeline as context_pipeline
from openjiuwen.core.common.exception.errors import BaseError
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


def _config(profile: str) -> PersonalContextConfig:
    model = {"client_provider": "OpenAI", "api_key": "secret", "api_base": "https://example.test"}
    request = {"model": "test"}
    return PersonalContextConfig.from_dict(
        {
            "collection_enabled": True,
            "agent_use_enabled": False,
            "strategy_profile": profile,
            "model_client": model if profile != "rules" else None,
            "model_request": request if profile != "rules" else None,
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
        provider="local_files",
        service_id="local",
        observed_at="2026-08-12T00:00:00Z",
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
                    "target": "sources",
                    "new_topic_title": None,
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
        assert set(balanced_payload["items"][0]) == {"item_index", "title", "preview", "candidates"}
        assert "pages" not in balanced_payload
    assert len(agent_prompts) == expected_agent_calls
    assert published_profiles == [total_profile]
    assert all("existing context/description.md" in prompt for prompt in agent_prompts)
    assert all("This is a small run: read every bounded source_preview" in prompt for prompt in agent_prompts)
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
    description = context / "description.md"
    page.parent.mkdir(parents=True)
    page.write_text("# Page\n\nOld.\n", encoding="utf-8")
    description.write_text("# Context\n", encoding="utf-8")
    baseline = context_pipeline._snapshot_managed_files(context)

    page.write_text("# Page\n\nUpdated.\n", encoding="utf-8")
    description.write_text("# Context\n\nUpdated.\n", encoding="utf-8")
    (context / "topics" / "new.md").write_text("# New\n", encoding="utf-8")
    (context / "ignored.txt").write_text("not Markdown", encoding="utf-8")

    assert context_pipeline._changed_context_paths(context, baseline) == {
        "description.md",
        "topics/new.md",
        "topics/page.md",
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


def test_balanced_directory_candidates_are_local_bounded_and_opaque(tmp_path: Path) -> None:
    context_root = tmp_path / "context"
    for index in range(100):
        description = context_root / "topics" / f"topic-{index:03d}" / "description.md"
        description.parent.mkdir(parents=True)
        description.write_text(
            f"# Topic {index:03d}\n\nSemantic preview {index:03d}. " + ("x" * 2_000),
            encoding="utf-8",
        )

    public_candidates, target_paths = context_pipeline._balanced_directory_candidates(
        context_root,
        {
            "title": "Topic 042 release notes",
            "markdown": "Topic 042 contains a focused update.",
        },
    )

    assert 1 <= len(public_candidates) <= 5
    assert [candidate["id"] for candidate in public_candidates] == [
        f"directory_{index}" for index in range(1, len(public_candidates) + 1)
    ]
    assert public_candidates[0]["title"] == "Topic 042"
    assert all(set(candidate) == {"id", "title", "preview"} for candidate in public_candidates)
    assert all(len(candidate["preview"]) <= 240 for candidate in public_candidates)
    serialized = json.dumps(public_candidates, ensure_ascii=False)
    assert str(context_root) not in serialized
    assert "topic-042" not in serialized
    assert set(target_paths) == {candidate["id"] for candidate in public_candidates}
    assert target_paths["directory_1"] == context_root / "topics" / "topic-042"


def test_balanced_enrichment_parser_accepts_items_independently() -> None:
    allowed_targets = {index: {"sources", "new_topic", "directory_1"} for index in range(8)}
    payload = {
        "items": [
            {
                "item_index": 0,
                "summary": "合法目录摘要。",
                "target": "directory_1",
                "new_topic_title": None,
            },
            {
                "item_index": 1,
                "summary": "合法来源摘要。",
                "target": "sources",
                "new_topic_title": None,
            },
            {
                "item_index": 2,
                "summary": "合法新主题摘要。",
                "target": "new_topic",
                "new_topic_title": "主动上下文",
            },
            {
                "item_index": 3,
                "summary": "带额外字段。",
                "target": "sources",
                "new_topic_title": None,
                "extra": True,
            },
            {
                "item_index": 4,
                "summary": "[非法链接](https://example.test)",
                "target": "sources",
                "new_topic_title": None,
            },
            {
                "item_index": 5,
                "summary": "非法候选。",
                "target": "directory_5",
                "new_topic_title": None,
            },
            {
                "item_index": 6,
                "summary": "重复一。",
                "target": "sources",
                "new_topic_title": None,
            },
            {
                "item_index": 6,
                "summary": "重复二。",
                "target": "sources",
                "new_topic_title": None,
            },
        ]
    }

    accepted = context_pipeline._parse_balanced_enrichments(
        json.dumps(payload, ensure_ascii=False),
        allowed_targets=allowed_targets,
    )

    assert set(accepted) == {0, 1, 2}
    assert accepted[0] == {
        "summary": "合法目录摘要。",
        "target": "directory_1",
        "new_topic_title": None,
    }
    assert accepted[2]["new_topic_title"] == "主动上下文"


@pytest.mark.parametrize(
    "text",
    [
        "not-json",
        json.dumps([]),
        json.dumps("invalid"),
        json.dumps({"items": [], "extra": True}),
        json.dumps({"items": {}}),
    ],
)
def test_balanced_enrichment_parser_returns_no_items_for_invalid_top_level(text: str) -> None:
    assert context_pipeline._parse_balanced_enrichments(text, allowed_targets={0: {"sources"}}) == {}


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
                            "summary": f"LLM summary {index}.",
                            "target": "sources",
                            "new_topic_title": None,
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

    calls = [call for instance in _FakeDirectModel.instances for call in instance.calls]
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
        assert all(len(item["candidates"]) <= 5 for item in payload["items"])
        assert all("path" not in candidate for item in payload["items"] for candidate in item["candidates"])
    if item_count:
        source_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (sandbox / "context" / "sources" / "local").glob("*.md")
            if path.name != "description.md"
        )
        for index in range(item_count):
            assert f"LLM summary {index}." in source_text


@pytest.mark.asyncio
async def test_balanced_applies_existing_directory_and_controlled_new_topic_without_rewriting_body(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context_root = tmp_path / "workspace" / "context"
    existing = context_root / "topics" / "openjiuwen"
    existing.mkdir(parents=True)
    root_body = "根语义正文保持不变。"
    topics_body = "主题导航正文保持不变。"
    existing_body = "OpenJiuWen 目录正文保持不变。"
    (context_root / "description.md").write_text(
        f"# Agent 门户\n\n- [主题](topics/description.md)\n\n{root_body}\n",
        encoding="utf-8",
    )
    (context_root / "topics" / "description.md").write_text(
        f"# Topics\n\n- [OpenJiuWen](openjiuwen/description.md)\n\n{topics_body}\n",
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
                        "target": "directory_1",
                        "new_topic_title": None,
                    },
                    {
                        "item_index": 1,
                        "summary": "主动上下文的有界摘要。",
                        "target": "new_topic",
                        "new_topic_title": "主动上下文",
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
    assert topics_body in (candidate / "topics" / "description.md").read_text(encoding="utf-8")
    existing_text = (candidate / "topics" / "openjiuwen" / "description.md").read_text(encoding="utf-8")
    assert existing_body in existing_text
    assert existing_text.count("<!-- personal-context:source-links:start -->") == 1
    controlled_topics = [
        path for path in (candidate / "topics").iterdir() if path.is_dir() and path.name != "openjiuwen"
    ]
    assert len(controlled_topics) == 1
    controlled_description = (controlled_topics[0] / "description.md").read_text(encoding="utf-8")
    assert "<!-- personal-context:managed-topic -->" in controlled_description
    assert "<!-- personal-context:source-links:start -->" in controlled_description
    topics_text = (candidate / "topics" / "description.md").read_text(encoding="utf-8")
    assert "<!-- personal-context:topic-links:start -->" in topics_text
    assert controlled_topics[0].name in topics_text


@pytest.mark.asyncio
async def test_balanced_invalid_items_fall_back_individually_and_model_error_stops_later_groups(
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
                        "target": "sources",
                        "new_topic_title": None,
                    },
                    {
                        "item_index": 1,
                        "summary": "[非法链接](https://example.test)",
                        "target": "sources",
                        "new_topic_title": None,
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
    assert len(_FakeDirectModel.instances[0].calls) == 2
    pages = {
        path.read_text(encoding="utf-8")
        for path in (sandbox / "context" / "sources" / "local").glob("*.md")
        if path.name != "description.md"
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
    source_page = next(
        path for path in (sandbox / "context" / "sources" / "local").glob("*.md") if path.name != "description.md"
    )
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
                        "target": "sources",
                        "new_topic_title": None,
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
    source_page = sandbox / "context" / "sources" / "local" / f"{context_pipeline._digest('notes/one')}.md"
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

    processed = service._prepare_run_finish_io(
        sandbox,
        {
            "sandbox": sandbox,
            "batch_ids": ["batch-1"],
            "provider": "local_files",
            "source_alias_by_id": {"src_test": "[[ref:0]]"},
            "source_id_by_logical_id": {"notes/one": "src_test"},
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
    assert len(str(prompt_documents[0]["summary"])) == 700
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

    service._prepare_run_finish_io(
        sandbox,
        {
            "sandbox": sandbox,
            "batch_ids": ["batch-1"],
            "provider": "local_files",
            "source_alias_by_id": {"src_test": "[[ref:0]]"},
            "source_id_by_logical_id": {"notes/one": "src_test"},
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

    assert len(prompts) == 1
    prompt = prompts[0]
    payload = json.loads(prompt.split("\n", 1)[1])
    if profile == "agent":
        prompt_documents = payload["document_previews"]
        assert payload["deleted_count"] == len(deleted_ids)
        assert "deleted_ids" not in payload
        assert payload["deleted_input_root"] == "inputs/deleted"
        assert "This is a large run: use the complete briefing first" in prompt
        assert "Do not eagerly read every source_preview or source_content" in prompt
        assert "write that topic page before expanding the next topic" in prompt
        assert "Never draft multiple complete pages in one model response" in prompt
        assert "At most one complete page may be submitted per model response" in prompt
        assert "continue with later tool calls until every planned topic page" in prompt
        assert "Every upsert source with distinct, non-duplicative key facts" in prompt
        assert "no more than 2000 characters" in prompt
        assert "delete temporary files directly" not in prompt
        assert "shell" not in prompt.casefold()
        assert "relative to the Markdown file that contains the link" in prompt
        assert "Do not leave links to planned pages that you did not create" in prompt
        assert "perform one lightweight check of the internal Context links" in prompt
        assert "exactly one top-level # heading outside fenced code blocks" in prompt
        assert len(prompt_documents) == 12
        assert all(len(str(document["title"])) <= 512 for document in prompt_documents)
        assert all("markdown" not in document and "blocks" not in document for document in prompt_documents)
    else:
        assert set(payload) == {"items"}
        prompt_documents = payload["items"]
        assert len(prompt_documents) == 5
        assert all(set(document) == {"item_index", "title", "preview", "candidates"} for document in prompt_documents)
        assert all(len(str(document["title"])) <= 512 for document in prompt_documents)
        assert all(len(str(document["preview"])) <= 240 for document in prompt_documents)
        assert all(len(document["candidates"]) <= 5 for document in prompt_documents)
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

    processed = service._prepare_run_finish_io(
        sandbox,
        {
            "sandbox": sandbox,
            "batch_ids": ["batch-1"],
            "provider": "local",
            "source_alias_by_id": {f"src_{index}": f"[[ref:{index}]]" for index in range(11)},
            "source_id_by_logical_id": {item.logical_id: f"src_{index}" for index, item in enumerate(items)},
        },
    )
    prompt_documents = context_pipeline._agent_documents_payload(processed, large_run=True)

    assert processed["_large_run"] is True
    assert len(prompt_documents) == 11
    assert all(len(str(document["summary"])) <= 320 for document in prompt_documents)
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
    assert all(len(str(document["summary"])) == 320 for document in prompt_documents)


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
    (context / "description.md").write_text("# Context\n", encoding="utf-8")
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
    assert len(_FakeDirectModel.instances[0].calls) == 1
    await service.stop(timeout_seconds=1)


@pytest.mark.asyncio
async def test_agent_success_validates_pages_and_does_not_serialize_raw_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue: asyncio.Queue[object] = asyncio.Queue(maxsize=4)
    service = ContextPipelineService(home=tmp_path, config=_config("agent"), input_queue=queue)
    calls: list[tuple[str, str]] = []

    async def successful_agent(*, messages: list[object], **kwargs: object) -> str:
        content = str(getattr(messages[0], "content", ""))
        assert set(kwargs) == {
            "model_client",
            "model_request",
            "sandbox_path",
            "validate_result",
        }
        profile = _message_profile(messages, kwargs)
        calls.append((profile, content))
        _assert_new_wiki_prompt(content)
        sandbox_path = Path(str(kwargs["sandbox_path"]))
        assert "summary-first semantic portal" in content
        assert "Simplified Chinese" in content
        assert "short ASCII slug" in content
        assert "This is a small run: read every bounded source_preview" in content
        source_content = next((sandbox_path / "inputs" / "records").rglob("content.md"))
        assert source_content.read_text(encoding="utf-8") == "First paragraph."
        processed_document = next((sandbox_path / "inputs" / "processed").rglob("context-document.md"))
        assert processed_document.read_text(encoding="utf-8").strip() == "[[ref:0]]\n\nFirst paragraph."
        blocks = next((sandbox_path / "inputs" / "processed").rglob("blocks.jsonl"))
        assert '"text": "First paragraph."' in blocks.read_text(encoding="utf-8")
        (sandbox_path / "tmp" / "filesystem-notes.md").write_text("scratch", encoding="utf-8")
        page = sandbox_path / "context" / "topics" / "agent.md"
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text(
            "# Agent filesystem result.\n\nAgent-authored knowledge. [[ref:0]]\n",
            encoding="utf-8",
        )
        (page.parent / "description.md").write_text(
            "# Topics\n\n- [Agent](agent.md)\n",
            encoding="utf-8",
        )
        (sandbox_path / "context" / "description.md").write_text(
            "# Agent root\n\n- [Topics](topics/description.md)\n",
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
    assert not (tmp_path / "workspace" / "source-proofs").exists()
    published_page = tmp_path / "workspace" / "context" / "topics" / "agent.md"
    assert "Agent filesystem result." in published_page.read_text(encoding="utf-8")
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
                        "target": "sources",
                        "new_topic_title": None,
                    }
                ],
            }
        )
    ]
    monkeypatch.setattr("openjiuwen.harness.personal_context.context_pipeline.Model", _FakeDirectModel)

    result = await service._filesystem_with_fallback(
        processed=processed,
        sandbox=sandbox,
        batch=_batch(),
    )

    assert result == "balanced"
    assert len(_FakeDirectModel.instances) == 1


@pytest.mark.asyncio
async def test_filesystem_rules_fallback_discards_failed_candidate(
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
    source_page = sandbox / "context" / "sources" / "local" / f"{context_pipeline._digest('notes/one')}.md"
    assert source_page.is_file()
    assert processed["_filesystem_candidate_prepared"] is True


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
        (sandbox_path / "context" / "description.md").write_text(
            "# Context\n\n[Agent](topics/agent.md)\n",
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
                        "target": "sources",
                        "new_topic_title": None,
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
    calls = _FakeDirectModel.instances[0].calls
    assert len(calls) == 1
    assert len(calls[0][0]) == 1
    assert len(_FakeDirectModel.outputs) == 1


@pytest.mark.asyncio
async def test_balanced_delete_only_uses_rules_without_model_and_does_not_restore_page(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ContextPipelineService(home=tmp_path, config=_config("balanced"), input_queue=asyncio.Queue())
    context_root = tmp_path / "workspace" / "context"
    source_root = tmp_path / "workspace" / "source-meta"
    source_id = _write_atomic_source(source_root)
    page_relative = f"sources/local/{context_pipeline._digest('notes/one')}.md"
    context_pipeline._apply_rules_increment(
        context_root,
        service_id="local",
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
        fallback_references=("[[ref:0]]",),
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
    assert _FakeDirectModel.instances == []


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
                        "target": "sources",
                        "new_topic_title": None,
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
        calls = _FakeDirectModel.instances[0].calls
        assert len(calls) == 1
        for messages, _kwargs in calls:
            prompt = "\n".join(str(getattr(message, "content", "")) for message in messages)
            assert "[[ref:0]]" not in prompt
            assert "src_" not in prompt
            assert "source-meta" not in prompt
        source_page = next(
            path
            for path in (tmp_path / "workspace" / "context" / "sources" / "local").glob("*.md")
            if path.name != "description.md"
        )
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
    assert len(_FakeDirectModel.instances[0].calls) == 1
    source_page = sandbox / "context" / "sources" / "local" / f"{context_pipeline._digest('notes/one')}.md"
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
                        "target": "sources",
                        "new_topic_title": None,
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
    source_page = next(
        path
        for path in (tmp_path / "workspace" / "context" / "sources" / "local").glob("*.md")
        if path.name != "description.md"
    )
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

    async def balanced_success(**kwargs: object) -> tuple[set[str], int]:
        assert "batch" not in kwargs
        assert "materialized_baseline" not in kwargs
        assert "materialized_path" not in kwargs
        assert "payload" not in kwargs
        assert "deleted_source_ids" not in kwargs
        baseline = kwargs["context_baseline"]
        assert isinstance(baseline, dict)
        return context_pipeline._changed_context_paths(sandbox / "context", baseline), 1

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
