"""独立离线审查：仅合成数据，失败断言表达 SPEC 合同。"""

import asyncio
import json

import pytest

from openjiuwen.harness.personal_context import context_pipeline as p
from openjiuwen.harness.personal_context.config import PersonalContextConfig
from openjiuwen.harness.personal_context.models import FetchBatch, RawChangeItem


def service_at(tmp_path):
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
    return p.ContextPipelineService(home=tmp_path, config=config, input_queue=asyncio.Queue())


def record(tmp_path, *, provider="github", service="service-a", index=0):
    item = RawChangeItem(
        logical_id=f"item-{index}",
        revision_id="r1",
        operation="upsert",
        title="向量检索",
        content="向量检索索引方法",
        original_ref=f"https://example.test/{index}",
        metadata={"resource": "issue"},
    )
    sid = p.upsert_source_metadata(
        tmp_path / "source-meta", item, provider=provider, service_id=service, observed_at="2026-09-07T00:00:00Z"
    )
    return sid


def page_in(directory, sid, body="向量检索索引方法"):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "description.md").write_text("# 向量检索\n", encoding="utf-8")
    page = directory / "资料.md"
    page.write_text(f"# 向量检索\n<!-- personal-context-managed-source: {sid} -->\n{body}\n", encoding="utf-8")
    return page


def test_signature_captures_readable_link_label_changes(tmp_path):
    root = tmp_path / "context"
    sid = record(tmp_path)
    page = page_in(root / "主题", sid, "参考[向量索引](https://example.test/reference)")
    before = p._directory_presentation_signature(page.parent, context_root=root)
    page.write_text(page.read_text(encoding="utf-8").replace("[向量索引]", "[关系数据库]"), encoding="utf-8")
    assert p._directory_presentation_signature(page.parent, context_root=root) != before


def test_directory_parser_rejects_unencodable_description():
    payload = {"directory_id": "directory-0", "directory_title": "检索资料", "directory_description": "说明\ud800"}
    assert p._parse_balanced_directory_presentation(json.dumps(payload), directory_id="directory-0") is None


def test_directory_batch_parser_keeps_valid_sibling_when_one_item_is_invalid():
    valid = {
        "directory_id": "directory-0",
        "directory_title": "检索资料",
        "directory_description": "介绍检索技术。",
    }
    invalid = {
        "directory_id": "directory-1",
        "directory_title": "非法标题",
        "directory_description": "说明 api_key=SYNTHETIC_TEST_VALUE",
    }

    assert p._parse_balanced_directory_presentations(
        json.dumps({"items": [valid, invalid]}),
        allowed_ids={"directory-0", "directory-1"},
    ) == {"directory-0": valid}


def test_directory_presentation_signature_tracks_only_model_visible_payload():
    payload = {
        "directory_id": "directory-0",
        "role": "semantic",
        "current_title": "检索资料",
        "files": [
            {
                "page_id": "page-0",
                "title": "向量检索",
                "content_preview": "前缀",
                "provider": [],
                "fetch": [],
            }
        ],
        "subdirectories": [],
    }
    before = p._balanced_directory_presentation_signature(payload)
    assert p._balanced_directory_presentation_signature(dict(payload)) == before
    protocol_only_change = {
        **payload,
        "directory_id": "directory-99",
        "current_title": "模型上轮生成的标题",
        "files": [{**payload["files"][0], "page_id": "page-88"}],
    }
    assert p._balanced_directory_presentation_signature(protocol_only_change) == before
    changed = {**payload, "files": [{**payload["files"][0], "content_preview": "可见变化"}]}
    assert p._balanced_directory_presentation_signature(changed) != before


def test_directory_batching_is_stable_and_bounded_to_five_items():
    values = list(range(11))
    assert p._balanced_directory_batches(values) == [values[:5], values[5:10], values[10:]]


@pytest.mark.asyncio
async def test_bad_directory_unicode_isolated_and_parent_still_called(tmp_path, monkeypatch):
    calls = []

    class Model:
        def __init__(self, **kwargs):
            pass

        async def invoke(self, messages):
            payload = json.loads(messages[0].content.split("\n", 1)[1])
            calls.append(payload)
            if "items" in payload:
                return json.dumps(
                    {
                        "items": [
                            {
                                "item_index": row["item_index"],
                                "page_title": "向量检索",
                                "summary": "模型增强的索引摘要。",
                                "keywords": ["向量索引"],
                            }
                            for row in payload["items"]
                        ]
                    }
                )
            return json.dumps(
                {
                    "directory_id": payload["directory_id"],
                    "directory_title": "索引资料",
                    "directory_description": "非法简介\ud800" if payload["role"] != "root" else "根目录说明",
                }
            )

    monkeypatch.setattr(p, "Model", Model)
    service = service_at(tmp_path)
    batch = FetchBatch(
        batch_id="batch",
        items=[
            RawChangeItem(
                logical_id="i",
                revision_id="r1",
                operation="upsert",
                title="向量检索",
                content="向量检索索引方法",
                original_ref="https://example.test/i",
            )
        ],
    )
    await service._process_batch_event("local", "run", batch)
    await service._finish_run_event("local", "run")
    assert any(call.get("role") == "root" for call in calls), "单个目录非法 Unicode 不应中止父目录调用"
    assert "模型增强的索引摘要。" in next(iter(p._managed_pages_by_source(service._context_root).values())).read_text(
        encoding="utf-8"
    )


def test_directory_parser_rejects_credential_shaped_output():
    payload = {
        "directory_id": "directory-0",
        "directory_title": "检索资料",
        "directory_description": "说明 api_key=SYNTHETIC_TEST_VALUE",
    }
    assert p._parse_balanced_directory_presentation(json.dumps(payload), directory_id="directory-0") is None


def test_fragmentation_gate_uses_source_prior(tmp_path, monkeypatch):
    root = tmp_path / "context"
    for index in range(8):
        page_in(root / f"主题{index}", record(tmp_path, index=index))
    # 确定性精确向量：不同页 cosine=.36，相同来源加 .20 后达 .56 > .42。
    monkeypatch.setattr(
        p, "_clustering_sparse_vectors", lambda records: {name: {"shared": 0.6, name: 0.8} for name in records}
    )
    vectors = {str(index): {"shared": 0.6, str(index): 0.8} for index in range(8)}
    source = p._source_distribution([{"provider": "github", "source_type": "issue", "service": "service-a"}])
    with_prior = p._capacity_constrained_clusters(
        vectors, max_members=20, target_members=12, source_distributions_by_id={key: source for key in vectors}
    )
    assert len(with_prior) == 1
    assert p._fragmentation_would_improve(
        root, source_root=tmp_path / "source-meta", max_pages_per_directory=20, max_subdirectories_per_directory=20
    )


def test_managed_page_uses_own_source_not_related_neighbor(tmp_path):
    root = tmp_path / "context"
    own = record(tmp_path, provider="github", index=1)
    other = record(tmp_path, provider="gitcode", service="other", index=2)
    page = page_in(root / "主题", own)
    neighbor = page.parent / "邻居.md"
    neighbor.write_text(f"# 邻居\n<!-- personal-context-managed-source: {other} -->\n", encoding="utf-8")
    page.write_text(page.read_text(encoding="utf-8") + "[邻居](邻居.md)", encoding="utf-8")
    distribution = p._page_source_distribution(page, context_root=root, source_root=tmp_path / "source-meta")
    assert distribution[("provider", "github")] == 1
    assert ("provider", "gitcode") not in distribution


def test_metadata_identity_validation_not_bypassed(tmp_path):
    sid = record(tmp_path)
    path = tmp_path / "source-meta" / f"{sid}.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace('"provider":', '"unused":') + "unexpected", encoding="utf-8"
    )
    with pytest.raises(Exception):
        p._source_distribution_for_id(tmp_path / "source-meta", sid)


def test_existing_intro_is_cleaned_before_parent_payload(tmp_path):
    root = tmp_path / "context"
    child = root / "主题"
    child.mkdir(parents=True)
    (root / "description.md").write_text("# PersonalContext\n", encoding="utf-8")
    (child / "description.md").write_text(
        p._write_directory_presentation_text(
            "# 主题\n",
            title="主题",
            description="合成说明 api_key=SYNTHETIC_TEST_VALUE https://example.test/private",
            signature="a" * 64,
        ),
        encoding="utf-8",
    )
    payload = p._balanced_directory_payload(
        root, context_root=root, source_root=tmp_path / "source-meta", directory_ids={root: "r", child: "c"}
    )
    preview = payload["subdirectories"][0]["description_preview"]
    assert "SYNTHETIC_TEST_VALUE" not in preview and "https://" not in preview


def test_source_distribution_breaks_directory_tie_using_real_metadata(tmp_path):
    root = tmp_path / "context"
    a = root / "甲"
    b = root / "乙"
    first = record(tmp_path, index=1)
    other = record(tmp_path, provider="gitcode", service="service-b", index=2)
    page_in(a, first)
    page_in(b, other)
    source = p._source_distribution_for_id(tmp_path / "source-meta", other)
    ranked = p._rank_with_source_prior(
        [(0, 0.55), (1, 0.55)], [a, b], query_source=source, context_root=root, source_root=tmp_path / "source-meta"
    )
    assert ranked[0][0] == 1
    assert ranked[0][1] == pytest.approx(0.75)
    assert p._accepted_directory_rank(ranked) == 1


def test_signature_ignores_generated_prose_and_model_invisible_member_tail_change(tmp_path):
    root = tmp_path / "context"
    leaf = root / "父目录" / "叶目录"
    first = record(tmp_path, index=1)
    page = page_in(leaf, first)
    before = p._directory_presentation_signature(root, context_root=root)
    desc = leaf / "description.md"
    desc.write_text(
        p._write_directory_presentation_text(
            desc.read_text(encoding="utf-8"), title="模型显示新标题", description="模型自己的简介", signature="a" * 64
        ),
        encoding="utf-8",
    )
    assert p._directory_presentation_signature(root, context_root=root) == before
    page.write_text(page.read_text(encoding="utf-8") + "真正的新内容", encoding="utf-8")
    assert p._directory_presentation_signature(root, context_root=root) == before


def test_collision_mapping_and_nested_links_safe(tmp_path):
    root = tmp_path / "context"
    existing = root / "稳定目录"
    a, b = root / "甲", root / "乙"
    nested = a / "下层"
    for path in (root, existing, a, b, nested):
        path.mkdir(parents=True, exist_ok=True)
        (path / "description.md").write_text(f"# {path.name}\n", encoding="utf-8")
    (nested / "资料.md").write_text(
        "# 资料\n[乙](../../乙/description.md)\n[稳定](../../稳定目录/description.md)", encoding="utf-8"
    )
    p._rename_balanced_directories(
        root,
        source_root=tmp_path / "source-meta",
        titles={a: "稳定目录", b: "稳定目录", nested: "CON", existing: "不得改名"},
        existing_directories={"稳定目录"},
    )
    assert existing.is_dir()
    assert len(list(root.iterdir())) == 4
    pages = list(root.rglob("资料.md"))
    assert len(pages) == 1
    for path in root.rglob("*"):
        if path.is_dir():
            assert len(path.name) <= 20
            assert p._portable_context_segment_is_safe(path.name)
    text = pages[0].read_text(encoding="utf-8")
    for target in p._MARKDOWN_LINK.findall(text):
        assert (pages[0].parent / target).resolve().is_file()


@pytest.mark.asyncio
async def test_existing_intro_preserved_on_model_failure_and_bottom_up_order(tmp_path, monkeypatch):
    service = service_at(tmp_path)
    sandbox = tmp_path / "sandbox"
    root = sandbox / "context"
    parent, leaf = root / "父主题", root / "父主题" / "子主题"
    for directory in (root, parent, leaf):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "description.md").write_text(
            p._write_directory_presentation_text(
                f"# {'PersonalContext' if directory == root else directory.name}\n用户手写原文\n",
                title="PersonalContext" if directory == root else directory.name,
                description="原来合法的简介",
                signature="a" * 64,
            ),
            encoding="utf-8",
        )
    (leaf / "页.md").write_text("# 向量检索\n" + "检索内容" * 100, encoding="utf-8")
    calls = []

    class Model:
        def __init__(self, **kwargs):
            pass

        async def invoke(self, messages):
            payload = json.loads(messages[0].content.split("\n", 1)[1])
            calls.append(payload)
            raise RuntimeError("synthetic model failure")

    async def keep_structure(*args, **kwargs):
        pass

    monkeypatch.setattr(p, "Model", Model)
    monkeypatch.setattr(p, "_apply_rules_increment", keep_structure)
    await service._filesystem_balanced_model_attempt(
        sandbox=sandbox,
        processed={"documents": []},
        context_baseline=p._snapshot_managed_files(root),
        alias_targets=None,
        source_ids_by_logical_id={},
        preexisting_managed_source_ids=frozenset(),
    )
    assert [call["current_title"] for call in calls] == ["子主题", "父主题", "PersonalContext"]
    assert len(calls[0]["files"][0]["content_preview"]) == 200
    assert calls[1]["subdirectories"][0]["description_preview"] == "原来合法的简介"
    for directory in (root, parent, leaf):
        text = (directory / "description.md").read_text(encoding="utf-8")
        assert "用户手写原文\n" in text
        assert p._directory_intro(text) == "原来合法的简介"
