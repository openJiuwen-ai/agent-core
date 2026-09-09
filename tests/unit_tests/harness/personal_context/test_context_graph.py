from __future__ import annotations

import os
from pathlib import Path

import pytest

import openjiuwen.harness.personal_context.context_pipeline as context_pipeline
from openjiuwen.harness.personal_context import PersonalContext
from openjiuwen.harness.personal_context.models import RawChangeItem
from openjiuwen.harness.personal_context.source_metadata import upsert_source_metadata


def _write_pages(home: Path, pages: dict[str, str]) -> Path:
    context = home / "workspace" / "context"
    for relative, markdown in pages.items():
        path = context / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(markdown, encoding="utf-8")
    return context


def _write_deep_context(home: Path) -> None:
    _write_pages(
        home,
        {
            "description.md": "# Context\n\n[A](a/description.md) [B](b/description.md) [Root doc](root.md)\n",
            "a/description.md": "# A\n\n[A1](a1/description.md) [A doc](doc.md)\n",
            "a/doc.md": "# A doc\n",
            "a/a1/description.md": "# A1\n\n[Deep](deep/description.md)\n",
            "a/a1/deep/description.md": "# Deep\n\n[Leaf](leaf.md)\n",
            "a/a1/deep/leaf.md": "# Leaf\n",
            "b/description.md": "# B\n\n[B doc](doc.md)\n",
            "b/doc.md": "# B doc\n",
            "root.md": "# Root doc\n",
        },
    )


def _write_source(home: Path) -> tuple[str, Path]:
    source_root = home / "workspace" / "source-meta"
    source_id = upsert_source_metadata(
        source_root,
        RawChangeItem(
            logical_id="github:pull:42",
            revision_id="revision-1",
            operation="upsert",
            title="GitHub PR 42",
            content="source body must not be persisted",
            original_ref="https://github.com/openjiuwen/agent-core/pull/42",
            metadata={"resource": "pull_request"},
        ),
        provider="github",
        service_id="github-main",
        observed_at="2026-08-12T00:00:00Z",
    )
    return source_id, source_root / f"{source_id}.md"


def _link(page: Path, target: Path, label: str = "Source") -> str:
    relative = os.path.relpath(target, start=page.parent).replace("\\", "/")
    return f"[{label}]({relative})"


@pytest.mark.asyncio
async def test_graph_and_tree_are_empty_without_root_description(tmp_path: Path) -> None:
    context = PersonalContext(home=tmp_path / "personal-context")

    assert await context.get_graph() == {"context_ready": False, "nodes": [], "edges": []}
    assert await context.get_tree() == {"context_ready": False, "nodes": [], "edges": []}


@pytest.mark.asyncio
async def test_initial_slice_is_breadth_first_and_limited_to_requested_depth(tmp_path: Path) -> None:
    home = tmp_path / "personal-context"
    _write_deep_context(home)

    graph = await PersonalContext(home=home).get_graph(root_id=None, depth=3)

    assert [node["path"] for node in graph["nodes"]] == [
        "description.md",
        "a/description.md",
        "b/description.md",
        "root.md",
        "a/a1/description.md",
        "a/doc.md",
        "b/doc.md",
    ]
    assert "a/a1/deep/description.md" not in {node["path"] for node in graph["nodes"]}
    assert graph["nodes"][0] == {
        "id": "page:description.md",
        "kind": "directory",
        "subkind": "directory.0",
        "label": "Context",
        "path": "description.md",
        "service_id": None,
        "has_children": True,
    }
    assert next(node for node in graph["nodes"] if node["path"] == "root.md")["has_children"] is False
    a1 = next(node for node in graph["nodes"] if node["path"] == "a/a1/description.md")
    assert a1["has_children"] is True


@pytest.mark.asyncio
async def test_expanding_directory_returns_only_descendants_and_root_edges(tmp_path: Path) -> None:
    home = tmp_path / "personal-context"
    _write_deep_context(home)

    tree = await PersonalContext(home=home).get_tree(root_id="page:a/description.md", depth=1)

    assert [node["path"] for node in tree["nodes"]] == ["a/a1/description.md", "a/doc.md"]
    assert "page:a/description.md" not in {node["id"] for node in tree["nodes"]}
    assert {(edge["source"], edge["target"], edge["kind"]) for edge in tree["edges"]} == {
        ("page:a/description.md", "page:a/a1/description.md", "contains"),
        ("page:a/description.md", "page:a/doc.md", "contains"),
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("root_id", "depth"),
    [
        ("page:a/doc.md", 1),
        ("page:missing/description.md", 1),
        ("source:src_00000000000000000000000000000000", 1),
        (None, 0),
        (None, 11),
        (None, True),
    ],
)
async def test_slice_rejects_non_directory_unknown_or_invalid_depth(
    tmp_path: Path,
    root_id: str | None,
    depth: object,
) -> None:
    home = tmp_path / "personal-context"
    _write_deep_context(home)

    with pytest.raises(PersonalContext.Error):
        await PersonalContext(home=home).get_graph(root_id=root_id, depth=depth)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_graph_and_tree_share_contains_but_only_graph_has_references(tmp_path: Path) -> None:
    home = tmp_path / "personal-context"
    context = home / "workspace" / "context"
    page = context / "topics" / "page.md"
    source_id, source_path = _write_source(home)
    _write_pages(
        home,
        {
            "description.md": "# Context\n\n[Topics](topics/description.md) [Root](root.md)\n",
            "root.md": "# Root\n\n[Page](topics/page.md)\n",
            "topics/description.md": "# Topics\n\n[Page](page.md) [Root](../root.md)\n",
            "topics/page.md": "# Page\n",
        },
    )
    page.write_text("# Page\n\n" + _link(page, source_path) + "\n", encoding="utf-8")

    graph = await PersonalContext(home=home).get_graph(depth=3)
    tree = await PersonalContext(home=home).get_tree(depth=3)

    assert graph["nodes"] == tree["nodes"]
    assert {node["kind"] for node in graph["nodes"]} <= {"directory", "document"}
    assert all(not str(node["id"]).startswith("source:") for node in graph["nodes"])
    graph_edges = {(edge["source"], edge["target"], edge["kind"]) for edge in graph["edges"]}
    tree_edges = {(edge["source"], edge["target"], edge["kind"]) for edge in tree["edges"]}
    assert {kind for _source, _target, kind in tree_edges} == {"contains"}
    assert {kind for _source, _target, kind in graph_edges} == {"contains", "references"}
    assert tree_edges < graph_edges
    assert ("page:description.md", "page:topics/description.md", "references") not in graph_edges
    assert ("page:topics/description.md", "page:topics/page.md", "references") not in graph_edges
    assert ("page:root.md", "page:topics/page.md", "references") in graph_edges
    assert ("page:topics/description.md", "page:root.md", "references") in graph_edges
    assert all(source_id not in str(edge) for edge in graph["edges"])


@pytest.mark.asyncio
async def test_related_documents_create_only_real_reference_edges(tmp_path: Path) -> None:
    home = tmp_path / "personal-context"
    context_root = home / "workspace" / "context"
    _write_pages(
        home,
        {
            "主题甲/受管页.md": (
                "# 主动上下文目录治理\n\n"
                "<!-- personal-context-managed-source: src_55555555555555555555555555555555 -->\n\n"
                "## 内容\n\nBM25 中文关键词用于主动上下文目录治理。\n"
            ),
            "主题乙/相关页.md": ("# 主动上下文目录治理\n\n## 内容\n\nBM25 中文关键词用于主动上下文目录治理。\n"),
            "运动/跑步.md": "# 跑步训练\n\n## 内容\n\n配速、心率与马拉松训练。\n",
        },
    )
    context_pipeline._render_context_navigation(context_root)
    context_pipeline._refresh_related_documents(context_root)

    graph = await PersonalContext(home=home).get_graph(depth=3)
    references = {(edge["source"], edge["target"]) for edge in graph["edges"] if edge["kind"] == "references"}

    assert references == {("page:主题甲/受管页.md", "page:主题乙/相关页.md")}


@pytest.mark.asyncio
async def test_slice_keeps_stable_order_for_more_than_one_frame(tmp_path: Path) -> None:
    home = tmp_path / "personal-context"
    pages = {"description.md": "# Context\n"}
    pages.update({f"page-{index:03}.md": f"# Page {index}\n" for index in range(201)})
    _write_pages(home, pages)

    tree = await PersonalContext(home=home).get_tree(depth=2)

    assert len(tree["nodes"]) == 202
    assert [node["path"] for node in tree["nodes"]][1:] == [f"page-{index:03}.md" for index in range(201)]
    assert len(tree["edges"]) == 201


@pytest.mark.asyncio
async def test_search_and_node_detail_support_chinese_context_paths(tmp_path: Path) -> None:
    home = tmp_path / "personal-context"
    _write_pages(
        home,
        {
            "description.md": "# Context\n\n[主题](topics/description.md)\n",
            "topics/description.md": "# Topics\n\n[主动上下文](主动上下文/description.md)\n",
            "topics/主动上下文/description.md": ("# 主动上下文\n\n[长期记忆](长期记忆.md)\n"),
            "topics/主动上下文/长期记忆.md": ("# 长期记忆\n\nPersonalContext 会提前整理相关背景。\n"),
            "topics/其他.md": "# 其他\n\n主动上下文。\n",
        },
    )
    context = PersonalContext(home=home)

    graph = await context.get_graph(depth=4)
    tree = await context.get_tree(root_id="page:topics/主动上下文/description.md", depth=1)
    result = await context.search_graph("长期记忆")
    detail = await context.get_graph_page("page:topics/主动上下文/长期记忆.md")

    assert "page:topics/主动上下文/description.md" in {str(node["id"]) for node in graph["nodes"]}
    assert [node["path"] for node in tree["nodes"]] == ["topics/主动上下文/长期记忆.md"]
    assert result["results"][0]["node_id"] == "page:topics/主动上下文/长期记忆.md"
    assert detail["path"] == "topics/主动上下文/长期记忆.md"
    assert str(detail["markdown"]).startswith("# 长期记忆")
    with pytest.raises(PersonalContext.Error):
        await context.get_graph_page("source:src_00000000000000000000000000000000")


@pytest.mark.asyncio
async def test_graph_tree_search_and_page_preserve_deep_semantic_and_fallback_unicode_paths(
    tmp_path: Path,
) -> None:
    home = tmp_path / "personal-context"
    _write_pages(
        home,
        {
            "description.md": (
                "# Context\n\n- [主动上下文](主动上下文/description.md)\n- [待整理](待整理/description.md)\n"
            ),
            "主动上下文/description.md": ("# 主动上下文\n\n- [容量治理](容量治理/description.md)\n"),
            "主动上下文/容量治理/description.md": ("# 容量治理\n\n- [目录统计](目录统计.md)\n"),
            "主动上下文/容量治理/目录统计.md": (
                "# 目录统计\n\n目录容量提示。\n\n[待整理文档](../../待整理/飞书/2026年09月/文档.md)\n"
            ),
            "待整理/description.md": "# 待整理\n\n- [飞书](飞书/description.md)\n",
            "待整理/飞书/description.md": ("# 飞书\n\n- [2026年09月](2026年09月/description.md)\n"),
            "待整理/飞书/2026年09月/description.md": ("# 2026年09月\n\n- [文档](文档.md)\n"),
            "待整理/飞书/2026年09月/文档.md": "# 待整理文档\n\n无法提取可靠主题的来源。\n",
        },
    )
    context = PersonalContext(home=home)

    graph = await context.get_graph(depth=6)
    tree = await context.get_tree(root_id="page:主动上下文/description.md", depth=2)
    search = await context.search_graph("目录统计")
    page = await context.get_graph_page("page:主动上下文/容量治理/目录统计.md")

    graph_paths = {str(node["path"]) for node in graph["nodes"]}
    assert "主动上下文/容量治理/目录统计.md" in graph_paths
    assert "待整理/飞书/2026年09月/文档.md" in graph_paths
    assert [node["path"] for node in tree["nodes"]] == [
        "主动上下文/容量治理/description.md",
        "主动上下文/容量治理/目录统计.md",
    ]
    assert (
        "page:主动上下文/容量治理/目录统计.md",
        "page:待整理/飞书/2026年09月/文档.md",
    ) in {(str(edge["source"]), str(edge["target"])) for edge in graph["edges"] if edge["kind"] == "references"}
    assert search["results"][0]["node_id"] == "page:主动上下文/容量治理/目录统计.md"
    assert page["path"] == "主动上下文/容量治理/目录统计.md"
    assert str(page["markdown"]).startswith("# 目录统计")


@pytest.mark.asyncio
async def test_get_source_returns_structured_detail_separately(tmp_path: Path) -> None:
    home = tmp_path / "personal-context"
    source_id, _source_path = _write_source(home)

    detail = await PersonalContext(home=home).get_source(source_id)

    assert detail["title"] == "GitHub PR 42"
    assert detail["service_id"] == "github-main"
    assert "latest_revision" not in detail
    assert "latest_hash" not in detail
