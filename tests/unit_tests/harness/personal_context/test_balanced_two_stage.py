"""Contracts for Balanced's run-level semantics and final-tree presentation."""

import asyncio
import json

import pytest

from openjiuwen.harness.personal_context import context_pipeline as pipeline
from openjiuwen.harness.personal_context.config import PersonalContextConfig
from openjiuwen.harness.personal_context.models import FetchBatch, RawChangeItem


def _item(index=0, **overrides):
    return dict(
        item_index=index, summary="介绍语义检索方法。", keywords=["语义检索"], page_title="检索方法", **overrides
    )


def test_page_protocol_accepts_semantics_only():
    item = _item()
    assert pipeline._parse_balanced_page_semantics(json.dumps({"items": [item]}), allowed_indices={0}) == {0: item}


@pytest.mark.parametrize(
    "change",
    [
        {"target": "keep_rules"},
        {"keywords": []},
        {"keywords": ["检索", "检索"]},
        {"keywords": ["1"]},
        {"summary": "https://secret.test/path"},
        {"keywords": ["2026年9月7日"]},
        {"keywords": ["文档", "内容"]},
        {"summary": "x" * 451},
        {"page_title": "../unsafe"},
        {"item_index": True},
    ],
)
def test_page_protocol_rejects_bad_item_without_discarding_valid_sibling(change):
    item = _item()
    item.update(change)
    sibling = _item(1)
    assert pipeline._parse_balanced_page_semantics(json.dumps({"items": [item, sibling]}), allowed_indices={0, 1}) == {
        1: sibling
    }


def test_page_protocol_duplicate_index_rejected():
    assert pipeline._parse_balanced_page_semantics(json.dumps({"items": [_item(), _item()]}), allowed_indices={0}) == {}


def test_page_protocol_rejects_duplicate_json_keys_and_source_ids():
    text = json.dumps({"items": [_item()]})
    text = text.replace('"item_index": 0', '"item_index": 5, "item_index": 0')
    assert pipeline._parse_balanced_page_semantics(text, allowed_indices={0}) == {}
    item = _item()
    item["summary"] = "source src_" + "a" * 32
    assert pipeline._parse_balanced_page_semantics(json.dumps({"items": [item]}), allowed_indices={0}) == {}


@pytest.mark.asyncio
async def test_multi_batch_repeated_source_is_summarized_once(tmp_path, monkeypatch):
    groups = []

    class Model:
        def __init__(self, **kwargs):
            pass

        async def invoke(self, messages):
            payload = json.loads(messages[0].content.split("\n", 1)[1])
            if "items" in payload:
                groups.append(payload["items"])
                return json.dumps({"items": [_item(item["item_index"]) for item in payload["items"]]})
            return "{}"

    monkeypatch.setattr(pipeline, "Model", Model)
    service = _service(tmp_path)
    batch = _batch()
    await service._process_batch_event("local", "run", batch)
    await service._process_batch_event("local", "run", batch.model_copy(update={"batch_id": "next"}))
    await service._finish_run_event("local", "run")
    assert [len(group) for group in groups] == [1]
    assert len(pipeline._managed_pages_by_source(service._context_root)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("operations", [("upsert", "upsert"), ("delete", "upsert"), ("upsert", "delete")])
async def test_run_aggregation_keeps_only_final_source_operation_and_blocks(tmp_path, operations):
    service = _service(tmp_path)
    batch = _batch()
    for index, operation in enumerate(operations):
        item = batch.items[0].model_copy(
            update={
                "revision_id": f"r{index}",
                "operation": operation,
                "content": f"语义检索最终内容{index}",
            }
        )
        await service._process_batch_event(
            "local", "run", batch.model_copy(update={"batch_id": f"batch-{index}", "items": [item]})
        )
    processed = service._load_run_processed(service._run_states[("local", "run")])
    if operations[-1] == "delete":
        assert processed["documents"] == []
        assert processed["blocks"] == []
        assert processed["deleted_ids"] == [batch.items[0].logical_id]
    else:
        assert [document["revision_id"] for document in processed["documents"]] == ["r1"]
        assert processed["deleted_ids"] == []
        assert len(processed["blocks"]) == 1
        assert "最终内容1" in str(processed["blocks"])
        assert "最终内容0" not in str(processed["blocks"])
    await service._cleanup_run_state(("local", "run"))


@pytest.mark.asyncio
async def test_failed_publication_does_not_turn_redelivered_revision_into_unchanged(tmp_path, monkeypatch):
    groups = []

    class Model:
        def __init__(self, **kwargs):
            pass

        async def invoke(self, messages):
            payload = json.loads(messages[0].content.split("\n", 1)[1])
            if "items" in payload:
                groups.append(payload["items"])
                return json.dumps({"items": [_item(item["item_index"]) for item in payload["items"]]})
            return "{}"

    monkeypatch.setattr(pipeline, "Model", Model)
    service = _service(tmp_path)
    batch = _batch()
    await service._process_batch_event("local", "first", batch)
    await service._finish_run_event("local", "first")
    revised = batch.model_copy(
        update={"items": [batch.items[0].model_copy(update={"revision_id": "r2", "content": "语义检索新增倒排索引。"})]}
    )
    before = {
        page.relative_to(service._context_root): page.read_bytes() for page in service._context_root.rglob("*.md")
    }

    async def fail_publish(**kwargs):
        raise pipeline._publish_error("test publication failure")

    with monkeypatch.context() as scoped:
        scoped.setattr(service, "_publish_processed", fail_publish)
        await service._process_batch_event("local", "failed", revised)
        with pytest.raises(Exception, match="test publication failure"):
            await service._finish_run_event("local", "failed")
    assert {
        page.relative_to(service._context_root): page.read_bytes() for page in service._context_root.rglob("*.md")
    } == before
    groups.clear()
    await service._process_batch_event("local", "retry", revised)
    await service._finish_run_event("local", "retry")
    assert [len(group) for group in groups] == [1]
    assert "新增倒排索引" in next(iter(pipeline._managed_pages_by_source(service._context_root).values())).read_text(
        encoding="utf-8"
    )


def test_page_payload_cleans_machine_blocks_and_bounds_content():
    document = {
        "title": "检索",
        "markdown": "# 检索\n"
        + "<!-- personal-context:navigation:start -->\n导航污染\n<!-- personal-context:navigation:end -->\n"
        + "正文内容" * 1000
        + " [[ref:0]] https://secret.test/value",
    }
    payload = pipeline._balanced_page_payload(
        document, item_index=0, provider="github", source_type="issue", service="service-1", limit=200
    )
    assert len(payload["preview"]) <= 200
    assert "导航污染" not in payload["preview"]
    assert "[[ref:" not in str(payload)
    assert "https://" not in str(payload)
    assert set(payload) == {"item_index", "title", "headings", "preview", "provider", "source_type", "service"}


def test_page_payload_preserves_code_but_redacts_credentials_and_source_ids():
    payload = pipeline._balanced_page_payload(
        {"title": "向量检索", "markdown": "```python\nsearch(vector)\n```\napi_key=private_value src_" + "a" * 32},
        item_index=0,
        provider="github",
        source_type="code",
        service="service-1",
        limit=12000,
    )
    assert "search(vector)" in payload["preview"]
    assert "private_value" not in payload["preview"]
    assert "src_" not in payload["preview"]


def test_semantic_enrichment_does_not_replace_body():
    document = {"title": "原题", "markdown": "# 原题\n原始内容。", "_balanced_semantics": _item()}
    title, headings, summary = pipeline._document_semantic_parts(document)
    assert title == "检索方法"
    assert "语义检索" in headings
    assert summary == "介绍语义检索方法。"
    assert document["markdown"] == "# 原题\n原始内容。"


def test_source_prior_uses_normalized_member_distributions_and_semantic_gate():
    first = {"provider": "github", "source_type": "issue", "service": "a-b"}
    second = {"provider": "github", "source_type": "pr", "service": "a-c"}
    left = pipeline._source_distribution([first])
    right = pipeline._source_distribution([first, second])
    assert pipeline._source_overlap(left, right) == pytest.approx(0.7)
    assert pipeline._source_aware_score(0, left, right) == 0
    assert pipeline._source_aware_score(0.3, left, right) == pytest.approx(0.44)
    assert pipeline._source_aware_score(0.9, left, right) == 1
    assert pipeline._source_overlap(left, {}) == 0


def test_source_prior_cannot_connect_disjoint_semantic_islands():
    source = pipeline._source_distribution([{"provider": "github", "source_type": "issue", "service": "same"}])
    assert pipeline._capacity_constrained_clusters(
        {"a": {"检索": 1.0}, "b": {"烹饪": 1.0}},
        max_members=20,
        target_members=12,
        source_distributions_by_id={"a": source, "b": source},
    ) == [("a",), ("b",)]


def test_source_prior_connects_semantically_close_members():
    source = pipeline._source_distribution([{"provider": "github", "source_type": "issue", "service": "same"}])
    vectors = {"a": {"shared": 0.6, "a": 0.8}, "b": {"shared": 0.6, "b": 0.8}}
    assert pipeline._capacity_constrained_clusters(vectors, max_members=20, target_members=12) == [("a",), ("b",)]
    assert pipeline._capacity_constrained_clusters(
        vectors, max_members=20, target_members=12, source_distributions_by_id={"a": source, "b": source}
    ) == [("a", "b")]


def test_directory_protocol_requires_matching_identity_and_safe_prose():
    record = {"directory_id": "directory-0", "directory_title": "检索资料", "directory_description": "介绍检索技术。"}
    assert pipeline._parse_balanced_directory_presentation(json.dumps(record), directory_id="directory-0") == record
    assert pipeline._parse_balanced_directory_presentation(json.dumps(record), directory_id="directory-1") is None
    record["directory_description"] = "[来源](../../source-meta/secret.md)"
    assert pipeline._parse_balanced_directory_presentation(json.dumps(record), directory_id="directory-0") is None


def test_directory_signature_ignores_generated_prose_and_model_invisible_member_tail(tmp_path):
    root = tmp_path / "context"
    leaf = root / "检索"
    leaf.mkdir(parents=True)
    description = leaf / "description.md"
    description.write_text("# 检索\n用户说明\n", encoding="utf-8")
    page = leaf / "内容.md"
    page.write_text("# 内容\n" + "内容" * 201, encoding="utf-8")
    original = pipeline._directory_presentation_signature(leaf, context_root=root)
    updated = pipeline._write_directory_presentation_text(
        description.read_text(encoding="utf-8"), title="新标题", description="模型简介", signature=original
    )
    description.write_text(updated, encoding="utf-8")
    assert pipeline._directory_presentation_signature(leaf, context_root=root) == original
    assert "用户说明" in updated
    assert "模型简介" not in pipeline._unmanaged_description_body(updated)
    assert "模型简介" not in pipeline._sanitized_semantic_markdown(updated)
    page.write_text(page.read_text(encoding="utf-8") + "新内容", encoding="utf-8")
    assert pipeline._directory_presentation_signature(leaf, context_root=root) == original


def test_directory_payload_contains_all_direct_members_and_current_child_intro(tmp_path):
    root = tmp_path / "context"
    child = root / "子目录"
    child.mkdir(parents=True)
    (root / "description.md").write_text("# PersonalContext\n", encoding="utf-8")
    (root / "直属.md").write_text("# 直属\n" + "正文" * 201, encoding="utf-8")
    (child / "孙页面.md").write_text("# 不要递归展开\n孙内容", encoding="utf-8")
    (child / "description.md").write_text(
        pipeline._write_directory_presentation_text(
            "# 子目录\n", title="子主题", description="本轮子目录简介", signature="a" * 64
        ),
        encoding="utf-8",
    )
    payload = pipeline._balanced_directory_payload(
        root,
        context_root=root,
        source_root=tmp_path / "source-meta",
        directory_ids={root: "directory-0", child: "directory-1"},
    )
    assert len(payload["files"]) == 1
    assert len(payload["files"][0]["content_preview"]) == 200
    assert payload["subdirectories"][0]["description_preview"] == "本轮子目录简介"
    assert "孙内容" not in str(payload)
    assert str(tmp_path) not in str(payload)


def _service(tmp_path):
    config = PersonalContextConfig.from_dict(
        {
            "collection_enabled": True,
            "agent_use_enabled": False,
            "strategy_profile": "balanced",
            "model_client": {"client_provider": "OpenAI", "api_key": "test", "api_base": "https://example.test"},
            "model_request": {"model": "test"},
            "fetch_services": [],
        }
    )
    return pipeline.ContextPipelineService(home=tmp_path, config=config, input_queue=asyncio.Queue())


def _batch(count=1):
    return FetchBatch(
        batch_id="batch",
        items=[
            RawChangeItem(
                logical_id=f"item-{index}",
                revision_id="r1",
                operation="upsert",
                title=f"语义检索 {index}",
                content=f"语义检索使用向量索引查找相关知识，正文 {index}。",
                original_ref=f"https://example.test/{index}",
            )
            for index in range(count)
        ],
    )


@pytest.fixture
def semantic_model(monkeypatch):
    calls = []

    class Model:
        def __init__(self, **kwargs):
            pass

        async def invoke(self, messages):
            payload = json.loads(messages[0].content.split("\n", 1)[1])
            calls.append(payload)
            if "items" in payload:
                return json.dumps({"items": [_item(item["item_index"]) for item in payload["items"]]})
            return json.dumps(
                {
                    "directory_id": payload["directory_id"],
                    "directory_title": "检索资料",
                    "directory_description": "介绍语义检索资料。",
                }
            )

    monkeypatch.setattr(pipeline, "Model", Model)
    return calls


@pytest.mark.asyncio
async def test_legacy_directory_schema_migrates_once_without_resummarizing_pages(tmp_path, semantic_model):
    service = _service(tmp_path)
    await service._process_batch_event("local", "first", _batch())
    await service._finish_run_event("local", "first")
    pages_before = {
        path: path.read_bytes() for path in pipeline._managed_pages_by_source(service._context_root).values()
    }
    for description in service._context_root.rglob("description.md"):
        text = description.read_text(encoding="utf-8")
        description.write_text(text.replace("- 呈现版本：1", "- 呈现版本：legacy"), encoding="utf-8")
    semantic_model.clear()
    await service._process_batch_event("local", "migration", _batch())
    await service._finish_run_event("local", "migration")
    assert semantic_model and all("directory_id" in call for call in semantic_model)
    assert all(path.read_bytes() == data for path, data in pages_before.items())
    before = {path: path.read_bytes() for path in service._context_root.rglob("*.md")}
    semantic_model.clear()
    await service._process_batch_event("local", "unchanged", _batch())
    await service._finish_run_event("local", "unchanged")
    assert semantic_model == []
    assert all(path.read_bytes() == data for path, data in before.items())


@pytest.mark.asyncio
async def test_incremental_delete_and_fresh_build_have_same_final_source_members(tmp_path, semantic_model):
    incremental = _service(tmp_path / "incremental")
    batch = _batch(2)
    await incremental._process_batch_event("local", "first", batch)
    await incremental._finish_run_event("local", "first")
    deleted = batch.model_copy(
        update={"items": [batch.items[0].model_copy(update={"operation": "delete", "revision_id": "deleted"})]}
    )
    semantic_model.clear()
    await incremental._process_batch_event("local", "delete", deleted)
    await incremental._finish_run_event("local", "delete")
    assert all("items" not in call for call in semantic_model)
    assert any(call.get("role") == "semantic" for call in semantic_model)
    assert all(call.get("role") != "root" for call in semantic_model)
    fresh = _service(tmp_path / "fresh")
    await fresh._process_batch_event("local", "first", batch.model_copy(update={"items": [batch.items[1]]}))
    await fresh._finish_run_event("local", "first")
    incremental_pages = pipeline._managed_pages_by_source(incremental._context_root)
    fresh_pages = pipeline._managed_pages_by_source(fresh._context_root)
    assert set(incremental_pages) == set(fresh_pages)
    assert len(incremental_pages) == 1
    for pages in (incremental_pages, fresh_pages):
        assert "正文 1" in next(iter(pages.values())).read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_balanced_fresh_then_identical_increment_has_zero_calls_and_identical_bytes(tmp_path, monkeypatch):
    calls = []

    class Model:
        def __init__(self, **kwargs):
            pass

        async def invoke(self, messages):
            payload = json.loads(messages[0].content.split("\n", 1)[1])
            calls.append(payload)
            if "items" in payload:
                return json.dumps({"items": [_item(item["item_index"]) for item in payload["items"]]})
            return json.dumps(
                {
                    "directory_id": payload["directory_id"],
                    "directory_title": "知识检索资料",
                    "directory_description": "归纳语义检索的相关知识。",
                }
            )

    monkeypatch.setattr(pipeline, "Model", Model)
    service = _service(tmp_path)
    await service._process_batch_event("local", "first", _batch())
    await service._finish_run_event("local", "first")
    root = service._context_root
    pages = pipeline._managed_pages_by_source(root)
    assert len(pages) == 1
    assert "介绍语义检索方法。" in next(iter(pages.values())).read_text(encoding="utf-8")
    assert "items" in calls[0] and "candidates" not in str(calls[0])
    assert "directory_id" in calls[-1]
    assert calls[-1]["role"] == "root"
    assert calls[-1]["subdirectories"][0]["description_preview"] == "归纳语义检索的相关知识。"
    assert next(iter(pages.values())).parent.name == "知识检索资料"
    before = {page.relative_to(root).as_posix(): page.read_bytes() for page in root.rglob("*.md")}
    calls.clear()
    await service._process_batch_event("local", "second", _batch())
    await service._finish_run_event("local", "second")
    assert calls == []
    assert {page.relative_to(root).as_posix(): page.read_bytes() for page in root.rglob("*.md")} == before


@pytest.mark.asyncio
async def test_balanced_failed_page_group_does_not_stop_later_groups(tmp_path, monkeypatch):
    groups = []

    class Model:
        def __init__(self, **kwargs):
            pass

        async def invoke(self, messages):
            payload = json.loads(messages[0].content.split("\n", 1)[1])
            if "items" not in payload:
                return "{}"
            groups.append(payload["items"])
            if len(groups) == 1:
                raise RuntimeError("model unavailable")
            return json.dumps({"items": [_item(item["item_index"]) for item in payload["items"]]})

    monkeypatch.setattr(pipeline, "Model", Model)
    service = _service(tmp_path)
    await service._process_batch_event("local", "run", _batch(6))
    await service._finish_run_event("local", "run")
    assert [len(group) for group in groups] == [5, 1]
    text = "\n".join(
        page.read_text(encoding="utf-8") for page in pipeline._managed_pages_by_source(service._context_root).values()
    )
    assert text.count("介绍语义检索方法。") == 1


@pytest.mark.asyncio
async def test_balanced_page_groups_use_bounded_model_concurrency(tmp_path, monkeypatch):
    active = 0
    peak = 0

    class Model:
        def __init__(self, **kwargs):
            pass

        async def invoke(self, messages):
            nonlocal active, peak
            payload = json.loads(messages[0].content.split("\n", 1)[1])
            if "items" not in payload:
                return json.dumps(
                    {
                        "directory_id": payload["directory_id"],
                        "directory_title": "检索资料",
                        "directory_description": "介绍语义检索资料。",
                    }
                )
            active += 1
            peak = max(peak, active)
            try:
                await asyncio.sleep(0.05)
                return json.dumps({"items": [_item(item["item_index"]) for item in payload["items"]]})
            finally:
                active -= 1

    monkeypatch.setattr(pipeline, "Model", Model)
    service = _service(tmp_path)
    await service._process_batch_event("local", "run", _batch(12))
    await service._finish_run_event("local", "run")

    assert 2 <= peak <= 6


@pytest.mark.asyncio
async def test_balanced_directory_presentations_run_same_depth_concurrently(tmp_path, monkeypatch):
    active = 0
    peak = 0
    group_sizes = []

    class Model:
        def __init__(self, **kwargs):
            pass

        async def invoke(self, messages):
            nonlocal active, peak
            payload = json.loads(messages[0].content.split("\n", 1)[1])
            directories = payload.get("directories", [payload])
            group_sizes.append(len(directories))
            active += 1
            peak = max(peak, active)
            try:
                await asyncio.sleep(0.05)
                return json.dumps(
                    {
                        "items": [
                            {
                                "directory_id": directory["directory_id"],
                                "directory_title": directory["current_title"],
                                "directory_description": "保持稳定的目录简介。",
                            }
                            for directory in directories
                        ]
                    }
                )
            finally:
                active -= 1

    monkeypatch.setattr(pipeline, "Model", Model)
    service = _service(tmp_path)
    sandbox = tmp_path / "workspace" / "sandboxes" / "local" / "run"
    root = sandbox / "context"
    root.mkdir(parents=True)
    (root / "description.md").write_text("# PersonalContext\n", encoding="utf-8")
    for index in range(11):
        directory = root / f"主题{index}"
        directory.mkdir()
        (directory / "description.md").write_text(f"# 主题{index}\n", encoding="utf-8")
        (directory / f"资料{index}.md").write_text(f"# 资料{index}\n\n保持目录用于并发呈现测试。\n", encoding="utf-8")
    pipeline._render_context_navigation(root)

    await service._filesystem_balanced_model_attempt(
        processed={"documents": [], "changed_source_ids": set()},
        sandbox=sandbox,
        context_baseline={},
        alias_targets=None,
        source_ids_by_logical_id={},
        preexisting_managed_source_ids=frozenset(),
    )

    assert 2 <= peak <= 6
    assert group_sizes[:3] == [5, 5, 1]


def test_directory_rename_rewrites_only_link_targets_and_preserves_existing_dirs(tmp_path):
    root = tmp_path / "context"
    old = root / "旧目录"
    new = root / "新目录"
    old.mkdir(parents=True)
    new.mkdir()
    (root / "description.md").write_text(
        "# PersonalContext\n[新目录](新目录/description.md)\n[旧目录](旧目录/description.md)", encoding="utf-8"
    )
    (old / "description.md").write_text("# 旧目录\n[新目录](../新目录/description.md)", encoding="utf-8")
    (new / "description.md").write_text("# 新目录\n[旧目录](../旧目录/description.md)", encoding="utf-8")
    pipeline._rename_balanced_directories(
        root,
        source_root=tmp_path / "source-meta",
        titles={old: "不该改", new: "准确主题"},
        existing_directories={"旧目录"},
    )
    assert old.is_dir()
    assert (root / "准确主题" / "description.md").is_file()
    assert "[新目录](../准确主题/description.md)" in (old / "description.md").read_text(encoding="utf-8")
    assert "[旧目录](../旧目录/description.md)" in (root / "准确主题" / "description.md").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_content_revision_can_migrate_but_title_only_change_cannot(tmp_path):
    root = tmp_path / "context"
    old = root / "烹饪"
    target = root / "语义检索"
    old.mkdir(parents=True)
    target.mkdir()
    (old / "description.md").write_text("# 烹饪\n食材菜谱烹饪", encoding="utf-8")
    (target / "description.md").write_text("# 语义检索\n向量索引语义检索", encoding="utf-8")
    (target / "知识.md").write_text("# 语义检索\n向量索引语义检索", encoding="utf-8")
    page = old / "笔记.md"
    page.write_text("# 烹饪\n## 摘要\n烹饪\n## 正文\n食材菜谱烹饪", encoding="utf-8")
    assert (
        await pipeline._rules_update_target_directory(
            root,
            page=page,
            document={"title": "语义检索", "markdown": "食材菜谱烹饪"},
            source_root=tmp_path / "source-meta",
            source_id="src_" + "0" * 32,
            embed_texts=None,
        )
        is None
    )
    assert (
        await pipeline._rules_update_target_directory(
            root,
            page=page,
            document={"title": "语义检索", "markdown": "向量索引语义检索"},
            source_root=tmp_path / "source-meta",
            source_id="src_" + "0" * 32,
            embed_texts=None,
        )
        == target
    )
