from __future__ import annotations

import os
from pathlib import Path

import pytest

from openjiuwen.harness.personal_context import PersonalContext
from openjiuwen.harness.personal_context.models import RawChangeItem
from openjiuwen.harness.personal_context.source_metadata import upsert_source_metadata


def _write_source(
    home: Path,
    *,
    locator: str,
    title: str,
    service_id: str = "github-main",
) -> tuple[str, Path]:
    source_root = home / "workspace" / "source-meta"
    source_id = upsert_source_metadata(
        source_root,
        RawChangeItem(
            logical_id=locator,
            revision_id="revision-1",
            operation="upsert",
            title=title,
            content="source body must not be persisted",
            original_ref=locator,
            metadata={"resource": "pull_request"},
        ),
        provider="github",
        service_id=service_id,
        observed_at="2026-08-12T00:00:00Z",
    )
    return source_id, source_root / f"{source_id}.md"


def _link(page: Path, target: Path, label: str) -> str:
    relative = os.path.relpath(target, start=page.parent).replace("\\", "/")
    return f"[{label}]({relative})"


@pytest.mark.asyncio
async def test_graph_is_empty_without_root_description(tmp_path: Path) -> None:
    assert await PersonalContext(home=tmp_path / "personal_context").get_graph() == {
        "context_ready": False,
        "nodes": [],
        "edges": [],
    }


@pytest.mark.asyncio
async def test_graph_classifies_description_document_and_linked_source_nodes(tmp_path: Path) -> None:
    home = tmp_path / "personal_context"
    context = home / "workspace" / "context"
    page = context / "topics" / "agent.md"
    page.parent.mkdir(parents=True)
    source_id, source_path = _write_source(
        home,
        locator="https://github.com/openjiuwen/agent-core/pull/42",
        title="GitHub PR 42",
    )
    (context / "description.md").write_text(
        "# Context\n\n- [Topics](topics/description.md)\n",
        encoding="utf-8",
    )
    (context / "topics" / "description.md").write_text(
        "# Topics\n\n- [Agent](agent.md)\n",
        encoding="utf-8",
    )
    page.write_text(
        "# Agent\n\nSee [root](../description.md) and " + _link(page, source_path, "PR 42") + ".\n",
        encoding="utf-8",
    )

    graph = await PersonalContext(home=home).get_graph()

    assert graph["context_ready"] is True
    assert {node["id"]: (node["kind"], node["subkind"]) for node in graph["nodes"]} == {
        "page:description.md": ("directory", "directory.0"),
        "page:topics/description.md": ("directory", "directory.1"),
        "page:topics/agent.md": ("document", "document.0"),
        f"source:{source_id}": ("source", "source.0"),
    }
    node_ids = {node["id"] for node in graph["nodes"]}
    assert "context:." not in node_ids
    assert "directory:topics" not in node_ids
    assert all("source-meta" not in node_id for node_id in node_ids)
    assert {(edge["source"], edge["target"], edge["kind"]) for edge in graph["edges"]} >= {
        ("page:description.md", "page:topics/description.md", "contains"),
        ("page:topics/description.md", "page:topics/agent.md", "contains"),
        ("page:description.md", "page:topics/description.md", "navigates_to"),
        ("page:topics/description.md", "page:topics/agent.md", "navigates_to"),
        ("page:topics/agent.md", "page:description.md", "links_to"),
        ("page:topics/agent.md", f"source:{source_id}", "links_to"),
    }


@pytest.mark.asyncio
async def test_graph_directory_subkind_tracks_description_depth(tmp_path: Path) -> None:
    context = tmp_path / "personal_context" / "workspace" / "context"
    nested = context / "topics" / "deep"
    nested.mkdir(parents=True)
    (context / "description.md").write_text("# Root\n", encoding="utf-8")
    (context / "topics" / "description.md").write_text("# Topics\n", encoding="utf-8")
    (nested / "description.md").write_text("# Deep\n", encoding="utf-8")

    graph = await PersonalContext(home=tmp_path / "personal_context").get_graph()

    assert {node["id"]: node["subkind"] for node in graph["nodes"] if node["kind"] == "directory"} == {
        "page:description.md": "directory.0",
        "page:topics/description.md": "directory.1",
        "page:topics/deep/description.md": "directory.2",
    }


@pytest.mark.asyncio
async def test_graph_uses_ordinary_link_edges_and_only_reads_linked_sources(tmp_path: Path) -> None:
    home = tmp_path / "personal_context"
    context = home / "workspace" / "context"
    topics = context / "topics"
    topics.mkdir(parents=True)
    root_source_id, root_source = _write_source(home, locator="https://example.test/root", title="Root source")
    shared_id, shared_source = _write_source(home, locator="https://example.test/shared", title="Shared source")
    extra_id, extra_source = _write_source(home, locator="https://example.test/extra", title="Extra source")
    unlinked_id, _unlinked_source = _write_source(
        home,
        locator="https://example.test/unlinked",
        title="Unlinked source",
    )
    corrupt_unlinked = home / "workspace" / "source-meta" / f"src_{'f' * 32}.md"
    corrupt_unlinked.write_text("{broken", encoding="utf-8")
    root = context / "description.md"
    description = topics / "description.md"
    first = topics / "first.md"
    second = topics / "second.md"
    root.write_text(
        "# Context\n\n- [Topics](topics/description.md)\n- " + _link(root, root_source, "Root source") + "\n",
        encoding="utf-8",
    )
    description.write_text(
        "# Topics\n\n- [First](first.md)\n- [Second](second.md)\n",
        encoding="utf-8",
    )
    first.write_text(
        "# First\n\n" + _link(first, shared_source, "Shared") + " and " + _link(first, extra_source, "Extra") + "\n",
        encoding="utf-8",
    )
    second.write_text(
        "# Second\n\n" + _link(second, shared_source, "Shared") + " [web](https://example.com) [asset](asset.pdf)\n",
        encoding="utf-8",
    )

    graph = await PersonalContext(home=home).get_graph()
    nodes = {node["id"]: node for node in graph["nodes"]}
    edges = {(edge["source"], edge["target"], edge["kind"]) for edge in graph["edges"]}

    assert {node_id for node_id in nodes if node_id.startswith("source:")} == {
        f"source:{root_source_id}",
        f"source:{shared_id}",
        f"source:{extra_id}",
    }
    assert f"source:{unlinked_id}" not in nodes
    assert ("page:description.md", f"source:{root_source_id}", "navigates_to") in edges
    assert ("page:topics/first.md", f"source:{shared_id}", "links_to") in edges
    assert ("page:topics/second.md", f"source:{shared_id}", "links_to") in edges
    assert ("page:topics/first.md", f"source:{extra_id}", "links_to") in edges
    assert all(edge[2] not in {"derived_from", "references_source"} for edge in edges)
    assert len([edge for edge in edges if edge[1] == f"source:{shared_id}"]) == 2


@pytest.mark.asyncio
async def test_graph_ignores_external_missing_and_non_markdown_links(tmp_path: Path) -> None:
    context = tmp_path / "personal_context" / "workspace" / "context"
    context.mkdir(parents=True)
    (context / "description.md").write_text(
        "[web](https://example.com) [missing](missing.md) [asset](asset.pdf)",
        encoding="utf-8",
    )

    graph = await PersonalContext(home=tmp_path / "personal_context").get_graph()

    assert graph["context_ready"] is True
    assert graph["nodes"] == [
        {
            "id": "page:description.md",
            "kind": "directory",
            "subkind": "directory.0",
            "label": "description.md",
            "path": "description.md",
            "service_id": None,
        }
    ]
    assert graph["edges"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["corrupt", "mismatch", "oversized"])
async def test_graph_rejects_linked_invalid_source_metadata(tmp_path: Path, failure: str) -> None:
    home = tmp_path / "personal_context"
    context = home / "workspace" / "context"
    source_root = home / "workspace" / "source-meta"
    context.mkdir(parents=True)
    source_root.mkdir(parents=True)
    if failure == "corrupt":
        source_path = source_root / f"src_{'a' * 32}.md"
        source_path.write_text("# broken\n", encoding="utf-8")
    elif failure == "mismatch":
        _source_id, valid = _write_source(home, locator="https://example.test/valid", title="Valid")
        source_path = source_root / f"src_{'b' * 32}.md"
        source_path.write_bytes(valid.read_bytes())
    else:
        source_path = source_root / f"src_{'c' * 32}.md"
        source_path.write_bytes(b"x" * (64 * 1024 + 1))
    root = context / "description.md"
    root.write_text("# Context\n\n" + _link(root, source_path, "Source") + "\n", encoding="utf-8")

    with pytest.raises(PersonalContext.Error) as caught:
        await PersonalContext(home=home).get_graph()

    assert caught.value.status.name == "CONTEXT_PROACTIVE_FILE_EXECUTION_ERROR"


@pytest.mark.asyncio
async def test_graph_rejects_linked_source_symlink(tmp_path: Path) -> None:
    home = tmp_path / "personal_context"
    context = home / "workspace" / "context"
    source_root = home / "workspace" / "source-meta"
    context.mkdir(parents=True)
    source_root.mkdir(parents=True)
    outside = tmp_path / "outside.md"
    outside.write_text("# Outside\n", encoding="utf-8")
    source_path = source_root / f"src_{'d' * 32}.md"
    try:
        source_path.symlink_to(outside)
    except (NotImplementedError, OSError):
        pytest.skip("symlink creation is unavailable")
    root = context / "description.md"
    root.write_text("# Context\n\n" + _link(root, source_path, "Source") + "\n", encoding="utf-8")

    with pytest.raises(PersonalContext.Error) as caught:
        await PersonalContext(home=home).get_graph()

    assert caught.value.status.name == "CONTEXT_PROACTIVE_FILE_EXECUTION_ERROR"


@pytest.mark.asyncio
async def test_search_ranks_title_above_body_and_returns_graph_page_id(tmp_path: Path) -> None:
    home = tmp_path / "personal_context"
    context = home / "workspace" / "context"
    topics = context / "topics"
    topics.mkdir(parents=True)
    (context / "description.md").write_text("# Context\n", encoding="utf-8")
    (topics / "personal_context.md").write_text(
        "# 主动上下文\n\nPersonalContext 会提前整理相关背景。\n",
        encoding="utf-8",
    )
    (topics / "other.md").write_text(
        "# 其他主题\n\n正文偶然提到主动上下文、主动上下文和主动上下文。\n",
        encoding="utf-8",
    )
    (topics / "unrelated.md").write_text("# 无关页面\n\n没有匹配内容。\n", encoding="utf-8")
    personal_context = PersonalContext(home=home)

    result = await personal_context.search_graph("主动上下文")
    graph = await personal_context.get_graph()

    assert list(result) == ["results"]
    assert result["results"][0] == {
        "node_id": "page:topics/personal_context.md",
        "title": "主动上下文",
        "path": "topics/personal_context.md",
        "snippet": "# 主动上下文 PersonalContext 会提前整理相关背景。",
    }
    assert all(item["path"] != "topics/unrelated.md" for item in result["results"])
    page_ids = {node["id"] for node in graph["nodes"] if node["kind"] in {"directory", "document"}}
    assert {item["node_id"] for item in result["results"]} <= page_ids


@pytest.mark.asyncio
async def test_search_returns_at_most_ten_pages(tmp_path: Path) -> None:
    home = tmp_path / "personal_context"
    context = home / "workspace" / "context"
    context.mkdir(parents=True)
    (context / "description.md").write_text("# Context\n", encoding="utf-8")
    for index in range(12):
        (context / f"page-{index:02d}.md").write_text(
            f"# 共享主题 {index}\n",
            encoding="utf-8",
        )

    result = await PersonalContext(home=home).search_graph("共享主题")

    assert len(result["results"]) == 10
    assert [item["path"] for item in result["results"]] == [f"page-{index:02d}.md" for index in range(10)]


@pytest.mark.asyncio
async def test_search_returns_empty_results_without_published_context(tmp_path: Path) -> None:
    assert await PersonalContext(home=tmp_path / "personal_context").search_graph("主动上下文") == {"results": []}


@pytest.mark.asyncio
async def test_search_does_not_include_source_metadata(tmp_path: Path) -> None:
    home = tmp_path / "personal_context"
    context = home / "workspace" / "context"
    context.mkdir(parents=True)
    source_id, source_path = _write_source(
        home,
        locator="https://example.test/source-only-term",
        title="OnlyInSourceMetadata",
    )
    root = context / "description.md"
    root.write_text("# Context\n\n" + _link(root, source_path, "Source") + "\n", encoding="utf-8")

    assert await PersonalContext(home=home).search_graph("OnlyInSourceMetadata") == {"results": []}
    assert f"source:{source_id}" in {node["id"] for node in (await PersonalContext(home=home).get_graph())["nodes"]}


@pytest.mark.asyncio
async def test_search_rejects_blank_query(tmp_path: Path) -> None:
    with pytest.raises(PersonalContext.Error) as caught:
        await PersonalContext(home=tmp_path / "personal_context").search_graph("   ")
    assert caught.value.status.name == "CONTEXT_PROACTIVE_STATE_INVALID"


@pytest.mark.asyncio
async def test_get_graph_page_returns_existing_context_markdown(tmp_path: Path) -> None:
    home = tmp_path / "personal_context"
    context = home / "workspace" / "context"
    page = context / "topics" / "personal_context.md"
    page.parent.mkdir(parents=True)
    (context / "description.md").write_text("# Context\n", encoding="utf-8")
    page.write_bytes("# 主动上下文\n\n完整正文。\n".encode())

    assert await PersonalContext(home=home).get_graph_page("page:topics/personal_context.md") == {
        "node_id": "page:topics/personal_context.md",
        "title": "主动上下文",
        "path": "topics/personal_context.md",
        "markdown": "# 主动上下文\n\n完整正文。\n",
    }


@pytest.mark.asyncio
async def test_get_graph_page_returns_source_metadata_markdown(tmp_path: Path) -> None:
    home = tmp_path / "personal_context"
    source_id, source_path = _write_source(
        home,
        locator="https://github.com/openjiuwen/agent-core/pull/42",
        title="GitHub PR 42",
    )
    source_markdown = source_path.read_text(encoding="utf-8")

    assert await PersonalContext(home=home).get_graph_page(f"source:{source_id}") == {
        "node_id": f"source:{source_id}",
        "title": "GitHub PR 42",
        "path": f"{source_id}.md",
        "markdown": source_markdown,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "node_id",
    [
        "context:.",
        "directory:topics",
        "page:",
        "page:../outside.md",
        "page:/outside.md",
        r"page:topics\personal_context.md",
        "page:topics/missing.md",
        "source:",
        "source:github-main",
        "source:../outside",
        f"source:src_{'0' * 32}",
    ],
)
async def test_get_graph_page_rejects_invalid_or_missing_node(tmp_path: Path, node_id: str) -> None:
    home = tmp_path / "personal_context"
    context = home / "workspace" / "context"
    context.mkdir(parents=True)
    (context / "description.md").write_text("# Context\n", encoding="utf-8")

    with pytest.raises(PersonalContext.Error) as caught:
        await PersonalContext(home=home).get_graph_page(node_id)

    assert caught.value.status.name == "CONTEXT_PROACTIVE_FILE_EXECUTION_ERROR"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("prefix", "filename", "payload", "oversized"),
    [
        ("page", "invalid.md", b"\xff", False),
        ("page", "large.md", b"", True),
        ("source", f"src_{'1' * 32}.md", b"\xff", False),
        ("source", f"src_{'2' * 32}.md", b"", True),
    ],
)
async def test_get_graph_page_rejects_invalid_utf8_or_oversized_file(
    tmp_path: Path,
    prefix: str,
    filename: str,
    payload: bytes,
    oversized: bool,
) -> None:
    home = tmp_path / "personal_context"
    context = home / "workspace" / "context"
    context.mkdir(parents=True)
    (context / "description.md").write_text("# Context\n", encoding="utf-8")
    root = context if prefix == "page" else home / "workspace" / "source-meta"
    root.mkdir(parents=True, exist_ok=True)
    root.joinpath(filename).write_bytes(b"x" * (2 * 1024 * 1024 + 1) if oversized else payload)

    with pytest.raises(PersonalContext.Error) as caught:
        await PersonalContext(home=home).get_graph_page(
            f"{prefix}:{filename.removesuffix('.md') if prefix == 'source' else filename}"
        )

    assert caught.value.status.name == "CONTEXT_PROACTIVE_FILE_EXECUTION_ERROR"


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", ["page", "source"])
async def test_get_graph_page_rejects_symlink(tmp_path: Path, prefix: str) -> None:
    home = tmp_path / "personal_context"
    context = home / "workspace" / "context"
    context.mkdir(parents=True)
    (context / "description.md").write_text("# Context\n", encoding="utf-8")
    outside = tmp_path / "outside.md"
    outside.write_text("# Outside\n", encoding="utf-8")
    if prefix == "page":
        link = context / "linked.md"
        node_id = "page:linked.md"
    else:
        source_root = home / "workspace" / "source-meta"
        source_root.mkdir(parents=True)
        source_id = f"src_{'3' * 32}"
        link = source_root / f"{source_id}.md"
        node_id = f"source:{source_id}"
    try:
        link.symlink_to(outside)
    except (NotImplementedError, OSError):
        pytest.skip("symlink creation is unavailable")

    with pytest.raises(PersonalContext.Error) as caught:
        await PersonalContext(home=home).get_graph_page(node_id)

    assert caught.value.status.name == "CONTEXT_PROACTIVE_FILE_EXECUTION_ERROR"
