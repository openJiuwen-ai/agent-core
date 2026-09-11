"""Contracts for PersonalContext semantic directory governance."""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import inspect
import logging
import math
import os
import re
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from types import ModuleType

import pytest

import openjiuwen.harness.personal_context.context_pipeline as context_pipeline
from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.foundation.store.base_embedding import EmbeddingConfig
from openjiuwen.harness.personal_context.config import PersonalContextConfig
from openjiuwen.harness.personal_context.models import FetchBatch, RawChangeItem
from openjiuwen.harness.personal_context.source_metadata import upsert_source_metadata


def _path_safety() -> ModuleType:
    return importlib.import_module("openjiuwen.harness.personal_context.path_safety")


@pytest.mark.parametrize(
    ("segment", "expected"),
    [
        ("知识管理", True),
        ("OpenAI Agents SDK", True),
        ("e\u0301", False),
        ("CON", False),
        ("con.md", False),
        ("主题.", False),
        ("主题\u200b", False),
        ("中" * 81, False),
        ("a/b", False),
    ],
)
def test_shared_portable_context_segment(segment: str, expected: bool) -> None:
    assert _path_safety().portable_context_segment_is_safe(segment) is expected


def test_semantic_name_contract_counts_directory_and_markdown_stem() -> None:
    path_safety = _path_safety()

    assert path_safety.semantic_context_segment_is_safe("中" * 20, markdown_file=False)
    assert not path_safety.semantic_context_segment_is_safe("中" * 21, markdown_file=False)
    assert path_safety.semantic_context_segment_is_safe(f"{'中' * 20}.md", markdown_file=True)
    assert not path_safety.semantic_context_segment_is_safe(f"{'中' * 21}.md", markdown_file=True)
    assert path_safety.portable_context_segment_is_safe("机" * 21)


def test_semantic_name_contract_keeps_fixed_description_outside_product_limit() -> None:
    path_safety = _path_safety()

    assert path_safety.SEMANTIC_NAME_MAX_CHARS == 20
    assert path_safety.portable_context_segment_is_safe("description.md")


def test_context_relative_path_rejects_root_absolute_parent_and_backslash(tmp_path: Path) -> None:
    context = tmp_path / "context"
    context.mkdir()

    for value in ("", ".", "..", "../x.md", "/x.md", "C:/x.md", "a\\b.md"):
        with pytest.raises(ValueError):
            _path_safety().resolve_context_relative_path(context, value, must_exist=False)


def test_context_relative_path_accepts_safe_unicode_child(tmp_path: Path) -> None:
    context = tmp_path / "context"
    target = context / "主动上下文" / "目录治理.md"
    target.parent.mkdir(parents=True)
    target.write_text("# 目录治理\n", encoding="utf-8")

    assert (
        _path_safety().resolve_context_relative_path(
            context,
            "主动上下文/目录治理.md",
            must_exist=True,
        )
        == target.resolve()
    )


def test_context_relative_path_rejects_symlink_traversal(tmp_path: Path) -> None:
    context = tmp_path / "context"
    outside = tmp_path / "outside"
    context.mkdir()
    outside.mkdir()
    link = context / "link"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc.__class__.__name__}")

    with pytest.raises(ValueError, match="link or reparse point"):
        _path_safety().resolve_context_relative_path(context, "link/page.md", must_exist=False)


def test_context_relative_path_rejects_dangling_symlink_traversal(tmp_path: Path) -> None:
    context = tmp_path / "context"
    context.mkdir()
    link = context / "link"
    try:
        os.symlink(tmp_path / "missing", link, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc.__class__.__name__}")

    with pytest.raises(ValueError, match="link or reparse point"):
        _path_safety().resolve_context_relative_path(context, "link/page.md", must_exist=False)


def test_context_relative_path_rejects_reported_reparse_point(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = tmp_path / "context"
    child = context / "child"
    child.mkdir(parents=True)
    path_safety = _path_safety()
    monkeypatch.setattr(path_safety, "is_reparse_point", lambda path: path == child)

    with pytest.raises(ValueError, match="link or reparse point"):
        path_safety.resolve_context_relative_path(context, "child/page.md", must_exist=False)


@pytest.mark.skipif(os.name != "nt", reason="Windows extended-path regression")
def test_shared_path_resolution_supports_deep_existing_windows_path(tmp_path: Path) -> None:
    context = tmp_path / "context"
    context.mkdir()
    long_parts = tuple(f"安全层{index}-" + ("x" * 60) for index in range(4))
    page = context.joinpath(*long_parts, "页面.md")
    context_pipeline._atomic_write(page, "# 页面\n".encode())
    relative = page.relative_to(context).as_posix()
    assert len(str(page.parent)) > 260

    resolved = _path_safety().resolve_context_relative_path(context, relative, must_exist=True)

    assert resolved.relative_to(context).as_posix() == relative


def test_sparse_semantics_match_cross_provider_chinese_topic() -> None:
    query = context_pipeline._semantic_fields(
        title="PersonalContext 目录治理",
        headings=["容量控制", "相关文档"],
        preview="用 BM25 与中文关键词整理主动上下文知识库。",
    )
    matching = context_pipeline._semantic_fields(
        title="主动上下文",
        headings=["语义目录治理"],
        preview="跨来源整理目录并限制单目录页面数量。",
    )
    unrelated = context_pipeline._semantic_fields(
        title="健身训练",
        headings=["配速"],
        preview="跑步与心率记录。",
    )

    scores = context_pipeline._sparse_semantic_scores(query, [matching, unrelated])

    assert scores[0] >= context_pipeline._DIRECTORY_ACCEPT_SCORE
    assert scores[0] - scores[1] >= context_pipeline._DIRECTORY_MARGIN


def test_sparse_semantics_keep_english_technical_terms_and_reject_tie() -> None:
    query = context_pipeline._semantic_fields(
        title="MCP transport",
        headings=["SSE"],
        preview="JSON-RPC transport",
    )
    candidates = [
        context_pipeline._semantic_fields(
            title="MCP",
            headings=["transport"],
            preview="SSE protocol",
        ),
        context_pipeline._semantic_fields(
            title="模型上下文",
            headings=["协议"],
            preview="通用协议介绍",
        ),
    ]

    ranked = context_pipeline._rank_semantic_candidates(query, candidates)

    assert ranked[0][0] == 0
    assert context_pipeline._accepted_directory_rank(ranked) == 0
    tied = context_pipeline._rank_semantic_candidates(query, [candidates[0], candidates[0]])
    assert context_pipeline._accepted_directory_rank(tied) is None


def test_sparse_semantics_are_stable_for_the_same_input() -> None:
    query = context_pipeline._semantic_fields("目录治理", ["容量"], "相关文档")
    corpus = [
        context_pipeline._semantic_fields("主动上下文", ["目录治理"], "容量限制"),
        context_pipeline._semantic_fields("运动", ["跑步"], "心率"),
    ]

    assert [context_pipeline._rank_semantic_candidates(query, corpus) for _ in range(5)] == [
        context_pipeline._rank_semantic_candidates(query, corpus)
    ] * 5


def test_bm25_sparse_vectors_are_l2_normalized_and_provider_neutral() -> None:
    fields = {
        "feishu-page": context_pipeline._semantic_fields("目录治理", ["容量"], "主动上下文 BM25"),
        "github-page": context_pipeline._semantic_fields("PersonalContext 目录", ["容量治理"], "BM25 聚类"),
        "sport-page": context_pipeline._semantic_fields("跑步训练", ["心率"], "马拉松配速"),
    }

    vectors = context_pipeline._bm25_sparse_vectors(fields)

    for vector in vectors.values():
        assert math.isclose(sum(value * value for value in vector.values()), 1.0)
    assert context_pipeline._sparse_vector_cosine(vectors["feishu-page"], vectors["github-page"]) > (
        context_pipeline._sparse_vector_cosine(vectors["feishu-page"], vectors["sport-page"])
    )


def test_clustering_title_anchor_skips_generic_how_to_prefixes() -> None:
    assert context_pipeline._semantic_title_anchor("How to use Docker networking") == "latin:docker"
    assert context_pipeline._semantic_title_anchor("How to use Photoshop layers") == "latin:photoshop"
    assert context_pipeline._semantic_title_anchor("如何使用 Docker 网络") == "latin:docker"
    assert context_pipeline._semantic_title_anchor("如何使用 Photoshop 图层") == "latin:photoshop"


def test_user_agent_and_human_or_chemical_agents_do_not_get_ai_title_anchor() -> None:
    for title in (
        "User-Agent request header",
        "User-Agent system header",
        "Travel agent itinerary",
        "Travel agent workflow",
        "Cleaning agent safety",
        "Cleaning agent tools",
        "Chemical agent system",
    ):
        assert context_pipeline._semantic_title_anchor(title) != "topic:智能体"

    assert context_pipeline._semantic_title_anchor("AI agent architecture") == "topic:智能体"
    assert context_pipeline._semantic_title_anchor("Agentic workflow patterns") == "topic:智能体"
    assert context_pipeline._semantic_title_anchor("Autonomous agent tools") == "topic:智能体"
    assert context_pipeline._semantic_title_anchor("Multi-agent memory") == "topic:智能体"
    assert context_pipeline._semantic_title_anchor("Agent memory") == "topic:智能体"
    assert context_pipeline._semantic_title_anchor("Agent tool") == "topic:智能体"
    assert context_pipeline._semantic_title_anchor("Agent system") == "topic:智能体"
    assert context_pipeline._semantic_title_anchor("Agent workflow") == "topic:智能体"
    assert context_pipeline._semantic_title_anchor("Guide to Agent memory") == "topic:智能体"
    assert context_pipeline._semantic_title_anchor("Notes on agent tool workflows") == "topic:智能体"


def test_dense_subset_projects_from_superset_and_missing_requested_id_is_sparse_only() -> None:
    superset = {
        "alpha": [3.0, 0.0],
        "beta": [0.0, 4.0],
        "unrelated": [float("nan")],
    }

    assert context_pipeline._normalized_dense_vectors(superset, ["alpha", "beta"]) == {
        "alpha": [1.0, 0.0],
        "beta": [0.0, 1.0],
    }
    assert context_pipeline._normalized_dense_vectors(superset, ["alpha", "missing"]) is None


def test_dense_subset_is_used_by_capacity_clustering(monkeypatch: pytest.MonkeyPatch) -> None:
    original_cosine = context_pipeline._cosine_similarity
    dense_calls: list[tuple[tuple[float, ...], tuple[float, ...]]] = []

    def recording_cosine(left: Iterable[float], right: Iterable[float]) -> float:
        dense_calls.append((tuple(left), tuple(right)))
        return original_cosine(list(left), list(right))

    monkeypatch.setattr(context_pipeline, "_cosine_similarity", recording_cosine)
    clusters = context_pipeline._capacity_constrained_clusters(
        {"alpha": {"topic": 1.0}, "beta": {"topic": 1.0}},
        max_members=2,
        target_members=2,
        dense_vectors_by_id={
            "alpha": [1.0, 0.0],
            "beta": [1.0, 0.0],
            "unrelated": [float("nan")],
        },
    )

    assert clusters == [("alpha", "beta")]
    assert dense_calls


def test_uppercase_markdown_counts_as_page_but_uppercase_description_does_not(tmp_path: Path) -> None:
    directory = tmp_path / "主题"
    directory.mkdir()
    (directory / "PAGE.MD").write_text("# 页面\n", encoding="utf-8")
    (directory / "DESCRIPTION.MD").write_text("# 主题\n", encoding="utf-8")

    assert context_pipeline._directory_ordinary_markdown_count(directory) == 1


def test_uppercase_markdown_at_context_root_triggers_layout_planning(tmp_path: Path) -> None:
    context_root = tmp_path / "context"
    context_root.mkdir()
    (context_root / "description.md").write_text("# Context\n", encoding="utf-8")
    (context_root / "ROOT.MD").write_text("# 智能体记忆\n\n记忆与检索。\n", encoding="utf-8")

    roots = context_pipeline._context_rebuild_roots(
        context_root,
        changed_paths={"ROOT.MD"},
        max_pages_per_directory=20,
        max_subdirectories_per_directory=20,
    )
    mapping = _plan_context_reclustering(
        context_root,
        changed_paths={"ROOT.MD"},
        max_pages_per_directory=20,
        max_subdirectories_per_directory=20,
    )

    assert roots == (context_root,)
    assert mapping["ROOT.MD"] != "ROOT.MD"
    context_pipeline._apply_context_reclustering(
        context_root,
        source_root=tmp_path / "source-meta",
        mapping=mapping,
        rebuild_roots=roots,
    )
    assert not (context_root / "ROOT.MD").exists()
    pages = context_pipeline._context_ordinary_pages(context_root)
    assert len(pages) == 1
    assert pages[0].parent != context_root
    assert "记忆与检索" in pages[0].read_text(encoding="utf-8")


def test_normalize_context_candidate_migrates_uppercase_markdown_with_case_sensitive_glob(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context_root = tmp_path / "context"
    source_root = tmp_path / "source-meta"
    context_root.mkdir()
    source_root.mkdir()
    (context_root / "description.md").write_text("# Context\n", encoding="utf-8")
    root_page = context_root / "ROOT.MD"
    root_page.write_text("# 智能体记忆\n\n记忆与检索。\n", encoding="utf-8")
    original_glob = Path.glob

    def case_sensitive_glob(path: Path, pattern: str | os.PathLike[str], **options: bool | None) -> Iterable[Path]:
        candidates = original_glob(path, pattern, **options)
        if os.fspath(pattern) != "*.md":
            return candidates
        return (candidate for candidate in candidates if candidate.suffix == ".md")

    monkeypatch.setattr(Path, "glob", case_sensitive_glob)

    context_pipeline._normalize_context_candidate(
        context_root,
        source_root=source_root,
        run_time=datetime(2026, 9, 5, tzinfo=timezone.utc),
        max_pages_per_directory=20,
        max_subdirectories_per_directory=20,
    )

    assert not root_page.exists()
    pages = context_pipeline._context_ordinary_pages(context_root)
    assert len(pages) == 1
    assert pages[0].parent != context_root
    assert pages[0].suffix == ".md"
    assert "记忆与检索" in pages[0].read_text(encoding="utf-8")
    context_pipeline._validate_candidate(
        context_root,
        final_context_root=context_root,
        source_root=source_root,
    )


def test_normalization_rejects_noncanonical_description_casing_without_mutation(tmp_path: Path) -> None:
    context_root = tmp_path / "context"
    source_root = tmp_path / "source-meta"
    context_root.mkdir()
    source_root.mkdir()
    noncanonical = context_root / "DESCRIPTION.MD"
    original = b"# User description\r\n\r\nKeep this body.\r\n"
    noncanonical.write_bytes(original)

    with pytest.raises(BaseError, match="description.md must use canonical lowercase casing"):
        context_pipeline._normalize_context_candidate(
            context_root,
            source_root=source_root,
            run_time=datetime(2026, 9, 5, tzinfo=timezone.utc),
        )

    assert {entry.name for entry in context_root.iterdir()} == {"DESCRIPTION.MD"}
    assert noncanonical.read_bytes() == original


def test_candidate_validator_rejects_noncanonical_description_casing(tmp_path: Path) -> None:
    context_root = tmp_path / "context"
    context_root.mkdir()
    noncanonical = context_root / "DESCRIPTION.MD"
    original = b"# User description\n"
    noncanonical.write_bytes(original)

    with pytest.raises(BaseError, match="description.md must use canonical lowercase casing"):
        context_pipeline._validate_candidate(context_root)

    assert noncanonical.read_bytes() == original


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (context_pipeline._ROOT_NAVIGATION_START, context_pipeline._ROOT_NAVIGATION_END),
        (context_pipeline._DIRECTORY_OVERVIEW_START, context_pipeline._DIRECTORY_OVERVIEW_END),
        (context_pipeline._SOURCE_LINKS_START, context_pipeline._SOURCE_LINKS_END),
        (context_pipeline._TOPIC_LINKS_START, context_pipeline._TOPIC_LINKS_END),
        (context_pipeline._RELATED_START, context_pipeline._RELATED_END),
    ],
)
def test_managed_identity_ignores_every_pcs_managed_block(
    tmp_path: Path,
    start: str,
    end: str,
) -> None:
    context_root = tmp_path / "context"
    page = context_root / "主题" / "页面.md"
    page.parent.mkdir(parents=True)
    body = "# 稳定页面\n\n这是用户正文。\n"
    page.write_text(body, encoding="utf-8")
    baseline = context_pipeline._context_page_identity(page, context_root=context_root)

    page.write_text(
        body + f"\n{start}\n## 受管内容\n\n- [旧目标](旧目标.md)\n{end}\n",
        encoding="utf-8",
    )
    first = context_pipeline._context_page_identity(page, context_root=context_root)
    page.write_text(
        body + f"\n{start}\n## 完全不同的受管内容\n\n- [新目标](新目标.md)\n{end}\n",
        encoding="utf-8",
    )
    second = context_pipeline._context_page_identity(page, context_root=context_root)

    page.write_text(
        "# 已修改的用户页面\n\n这是不同的用户正文。\n"
        f"\n{start}\n## 完全不同的受管内容\n\n- [新目标](新目标.md)\n{end}\n",
        encoding="utf-8",
    )
    changed_user_body = context_pipeline._context_page_identity(page, context_root=context_root)

    assert baseline == first == second
    assert changed_user_body != baseline


def test_managed_identity_keeps_managed_source_marker_authoritative(tmp_path: Path) -> None:
    context_root = tmp_path / "context"
    page = context_root / "主题" / "页面.md"
    page.parent.mkdir(parents=True)
    source_id = "src_" + "a" * 32
    page.write_text(
        "# 页面\n\n"
        f"<!-- personal-context-managed-source: {source_id} -->\n\n"
        f"{context_pipeline._RELATED_START}\n## 相关文档\n{context_pipeline._RELATED_END}\n",
        encoding="utf-8",
    )

    assert context_pipeline._context_page_identity(page, context_root=context_root) == source_id


def test_capacity_constrained_clusters_are_stable_and_bounded() -> None:
    fields = {
        f"agent-{index}": context_pipeline._semantic_fields("智能体记忆", ["上下文"], f"记忆 检索 {index}")
        for index in range(4)
    }
    fields.update(
        {
            f"sport-{index}": context_pipeline._semantic_fields("跑步训练", ["心率"], f"配速 训练 {index}")
            for index in range(4)
        }
    )
    vectors = context_pipeline._bm25_sparse_vectors(fields)

    results = [
        context_pipeline._capacity_constrained_clusters(vectors, max_members=3, target_members=2) for _ in range(5)
    ]

    assert results == [results[0]] * 5
    assert all(1 <= len(cluster) <= 3 for cluster in results[0])
    assert sorted(member for cluster in results[0] for member in cluster) == sorted(fields)
    assert all(len({member.split("-", 1)[0] for member in cluster}) == 1 for cluster in results[0])


def test_reclustering_merges_realistic_repeated_title_topics_without_encoder(tmp_path: Path) -> None:
    context_root = tmp_path / "context"
    source_root = tmp_path / "source-meta"
    context_root.mkdir()
    source_root.mkdir()
    (context_root / "description.md").write_text("# Context\n", encoding="utf-8")
    families = {
        "agent": (
            "Agent 记忆系统",
            "Agent 工具管理",
            "Agent 工作流框架",
            "Agent 系统协议",
            "AI Agents 技术栈",
            "Agentic AI 架构",
        ),
        "claude": (
            "Claude Code 配置",
            "Claude Code 上下文",
            "Claude Code CLI",
            "Claude Code 模型",
            "Claude Code Git 审查",
            "Claude Code 快捷键",
        ),
        "embodied": ("具身智能数据采集", "具身智能产品设计"),
        "isolated": (
            "跑步训练计划",
            "烘焙温度控制",
            "古典音乐欣赏",
            "家庭园艺灌溉",
            "财务报表阅读",
            "旅行路线规划",
            "摄影构图基础",
        ),
    }
    paths_by_family: dict[str, list[str]] = {}
    index = 0
    for family, titles in families.items():
        paths_by_family[family] = []
        for title in titles:
            relative = f"旧目录{index:02d}/页面{index:02d}.md"
            unique_preview = " ".join(f"detail{index:02d}_{token:02d}" for token in range(60))
            _write_cluster_page(context_root, relative, title=title, body=unique_preview)
            paths_by_family[family].append(relative)
            index += 1

    mapping = _plan_context_reclustering(
        context_root,
        changed_paths=set(),
        max_pages_per_directory=20,
        max_subdirectories_per_directory=20,
    )

    final_parent_by_path = {
        relative: PurePosixPath(mapping.get(relative, relative)).parent.as_posix()
        for relatives in paths_by_family.values()
        for relative in relatives
    }
    assert len({final_parent_by_path[path] for path in paths_by_family["agent"]}) == 1
    assert len({final_parent_by_path[path] for path in paths_by_family["claude"]}) == 1
    assert len({final_parent_by_path[path] for path in paths_by_family["embodied"]}) == 1
    assert len(set(final_parent_by_path.values())) <= 10
    assert all("github" not in parent.casefold() for parent in final_parent_by_path.values())


def test_readable_topic_names_never_expose_arbitrary_ngram() -> None:
    assert (
        context_pipeline._semantic_topic_name("主动上下文目录治理方案", ["容量与引用"], "") == "主动上下文目录治理方案"
    )
    assert context_pipeline._semantic_topic_name("MCP transport", ["SSE"], "") == "MCP transport"
    assert context_pipeline._semantic_topic_name("文档", ["容量与引用"], "") == "容量与引用"
    assert context_pipeline._semantic_topic_name("2026-09-02", [], "12345") is None


def _semantic_directory_with_pages(context_root: Path, name: str, *, count: int) -> Path:
    directory = context_root / name
    directory.mkdir(parents=True)
    (directory / "description.md").write_text(f"# {name}\n", encoding="utf-8")
    for index in range(count):
        (directory / f"第{index + 1}页.md").write_text(f"# 第{index + 1}页\n", encoding="utf-8")
    return directory


def _write_cluster_page(context_root: Path, relative: str, *, title: str, body: str) -> Path:
    page = context_root / relative
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(f"# {title}\n\n## 内容\n\n{body}\n", encoding="utf-8")
    return page


def _plan_context_reclustering(
    context_root: Path,
    *,
    changed_paths: set[str],
    max_pages_per_directory: int,
    max_subdirectories_per_directory: int,
) -> dict[str, str]:
    baseline_path_by_identity = {
        context_pipeline._context_page_identity(page, context_root=context_root): page.relative_to(
            context_root
        ).as_posix()
        for page in context_pipeline._context_ordinary_pages(context_root)
    }
    rebuild_roots = context_pipeline._context_rebuild_roots(
        context_root,
        changed_paths=changed_paths,
        max_pages_per_directory=max_pages_per_directory,
        max_subdirectories_per_directory=max_subdirectories_per_directory,
    )
    mapping, _actual_roots, _directory_roles, _fallback_reasons = context_pipeline._plan_context_reclustering(
        context_root,
        source_root=context_root.parent / "source-meta",
        rebuild_roots=rebuild_roots,
        baseline_path_by_identity=baseline_path_by_identity,
        max_pages_per_directory=max_pages_per_directory,
        max_subdirectories_per_directory=max_subdirectories_per_directory,
    )
    return mapping


def _assert_tree_capacity(context_root: Path, *, pages: int, subdirectories: int) -> None:
    for directory in [context_root, *(path for path in context_root.rglob("*") if path.is_dir())]:
        assert context_pipeline._directory_ordinary_markdown_count(directory) <= pages
        assert context_pipeline._directory_direct_subdirectory_count(directory) <= subdirectories


def _write_managed_cluster_page(
    workspace: Path,
    relative: str,
    *,
    provider: str,
    observed_at: str,
    title: str,
    body: str,
) -> tuple[Path, str]:
    digest = hashlib.sha256(f"{provider}|{relative}".encode()).hexdigest()
    item = RawChangeItem(
        logical_id=f"{provider}/{digest}",
        revision_id="rev-1",
        operation="upsert",
        title=title,
        content=body,
        original_ref=f"https://example.test/source/{digest}",
        metadata={"kind": "document"},
    )
    source_id = upsert_source_metadata(
        workspace / "source-meta",
        item,
        provider=provider,
        service_id=f"{provider}-service",
        observed_at=observed_at,
    )
    page = _write_cluster_page(workspace / "context", relative, title=title, body=body)
    source_target = os.path.relpath(
        workspace / "source-meta" / f"{source_id}.md",
        start=page.parent,
    ).replace("\\", "/")
    page.write_text(
        page.read_text(encoding="utf-8")
        + f"\n<!-- personal-context-managed-source: {source_id} -->\n\n[{source_id}]({source_target})\n",
        encoding="utf-8",
    )
    return page, source_id


def _plan_reclustering_with_page_provenance(
    workspace: Path,
    *,
    changed_paths: set[str],
    trigger_provider: str,
    trigger_time: datetime,
    max_pages_per_directory: int,
    max_subdirectories_per_directory: int,
) -> dict[str, str]:
    """Exercise both the current planner and its provider-neutral replacement."""

    parameters = inspect.signature(context_pipeline._plan_context_reclustering).parameters
    kwargs: dict[str, object] = {
        "max_pages_per_directory": max_pages_per_directory,
        "max_subdirectories_per_directory": max_subdirectories_per_directory,
    }
    if "changed_paths" in parameters:
        kwargs["changed_paths"] = changed_paths
    if "source_root" in parameters:
        kwargs["source_root"] = workspace / "source-meta"
    if "rebuild_roots" in parameters:
        kwargs["rebuild_roots"] = context_pipeline._context_rebuild_roots(
            workspace / "context",
            changed_paths=changed_paths,
            max_pages_per_directory=max_pages_per_directory,
            max_subdirectories_per_directory=max_subdirectories_per_directory,
        )
    if "baseline_path_by_identity" in parameters:
        kwargs["baseline_path_by_identity"] = {
            context_pipeline._context_page_identity(page, context_root=workspace / "context"): page.relative_to(
                workspace / "context"
            ).as_posix()
            for page in context_pipeline._context_ordinary_pages(workspace / "context")
        }
    if "provider" in parameters:
        kwargs["provider"] = trigger_provider
    if "run_time" in parameters:
        kwargs["run_time"] = trigger_time
    result = context_pipeline._plan_context_reclustering(workspace / "context", **kwargs)
    return result[0] if isinstance(result, tuple) else result


def test_reclustering_planner_signature_has_no_trigger_provider_or_time() -> None:
    parameters = inspect.signature(context_pipeline._plan_context_reclustering).parameters

    assert "source_root" in parameters
    assert "rebuild_roots" in parameters
    assert "baseline_path_by_identity" in parameters
    assert "provider" not in parameters
    assert "run_time" not in parameters


def _final_page_paths(context_root: Path, mapping: dict[str, str]) -> dict[str, str]:
    return {
        page.relative_to(context_root).as_posix(): mapping.get(
            page.relative_to(context_root).as_posix(),
            page.relative_to(context_root).as_posix(),
        )
        for page in context_pipeline._context_ordinary_pages(context_root)
    }


_MECHANICAL_DIRECTORY_SEGMENT = re.compile(r"(?:第\s*\d+\s*组|主题集合-.+)")


def _assert_no_mechanical_directory_segments(relative_paths: Iterable[str]) -> None:
    violations = sorted(
        {
            segment
            for relative in relative_paths
            for segment in PurePosixPath(relative).parts[:-1]
            if _MECHANICAL_DIRECTORY_SEGMENT.fullmatch(segment)
        }
    )
    assert not violations, f"mechanical directory segments are user-visible: {violations}"


def _source_metadata_bytes(source_root: Path) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in sorted(source_root.glob("src_*.md"))}


def _file_tree_bytes(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


def test_readable_isolated_pages_build_multilevel_normal_forest_with_zero_pending(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    context_root = workspace / "context"
    source_root = workspace / "source-meta"
    context_root.mkdir(parents=True)
    source_root.mkdir()
    (context_root / "description.md").write_text("# Context\n", encoding="utf-8")
    topics = (
        ("量子纠错实验", "表面码 综合征 解码"),
        ("法式面包发酵", "酵母 面团 温度"),
        ("马拉松配速训练", "心率 长跑 恢复"),
        ("古典吉他和声", "指法 和弦 复调"),
        ("阳台番茄灌溉", "土壤 水分 光照"),
    )
    old_paths: set[str] = set()
    for index, (title, body) in enumerate(topics):
        relative = f"旧主题{index}/{title}.md"
        _write_managed_cluster_page(
            workspace,
            relative,
            provider=("feishu", "github", "local_files")[index % 3],
            observed_at="2026-08-20T00:00:00+00:00",
            title=title,
            body=body,
        )
        old_paths.add(relative)

    mapping = _plan_reclustering_with_page_provenance(
        workspace,
        changed_paths={min(old_paths)},
        trigger_provider="feishu",
        trigger_time=datetime(2026, 9, 4, tzinfo=timezone.utc),
        max_pages_per_directory=1,
        max_subdirectories_per_directory=2,
    )
    targets = _final_page_paths(context_root, mapping)
    pending_targets = [target for target in targets.values() if "待整理" in PurePosixPath(target).parts]

    _assert_no_mechanical_directory_segments(targets.values())
    assert not pending_targets, f"{len(pending_targets)}/{len(targets)} readable pages were sent to 待整理"
    assert max(len(PurePosixPath(target).parts) - 1 for target in targets.values()) >= 3

    context_pipeline._apply_context_reclustering(context_root, source_root=source_root, mapping=mapping)

    _assert_no_mechanical_directory_segments(
        page.relative_to(context_root).as_posix() for page in context_pipeline._context_ordinary_pages(context_root)
    )
    _assert_tree_capacity(context_root, pages=1, subdirectories=2)
    assert not (context_root / "待整理").exists()


def _build_trigger_invariance_workspace(root: Path) -> tuple[Path, str, str]:
    workspace = root / "workspace"
    context_root = workspace / "context"
    source_root = workspace / "source-meta"
    context_root.mkdir(parents=True)
    source_root.mkdir()
    (context_root / "description.md").write_text("# Context\n", encoding="utf-8")
    normal_pages = (
        ("feishu", "智能体记忆", "上下文 检索 长期记忆"),
        ("github", "数据库索引", "BTree 查询优化 执行计划"),
        ("local_files", "马拉松训练", "配速 心率 恢复周期"),
        ("rss_feed", "阳台园艺", "番茄 土壤 滴灌 光照"),
    )
    for index, (provider, title, body) in enumerate(normal_pages):
        _write_managed_cluster_page(
            workspace,
            f"正常主题{index}/{title}.md",
            provider=provider,
            observed_at="2026-07-10T00:00:00+00:00",
            title=title,
            body=body,
        )
    low_relative = "待整理/旧来源/2026年01月/低置信/0001.md"
    low_locator = "https://example.test/source/20260819.md"
    low_item = RawChangeItem(
        logical_id=low_locator,
        revision_id="rev-1",
        operation="upsert",
        title="2026-08-19",
        content="12345 20260819 文档 内容",
        original_ref=low_locator,
        metadata={"kind": "document"},
    )
    low_source_id = upsert_source_metadata(
        source_root,
        low_item,
        provider="github",
        service_id="github-service",
        observed_at="2026-08-19T08:30:00+00:00",
    )
    low_page = _write_cluster_page(
        context_root,
        low_relative,
        title="2026-08-19",
        body="12345 20260819 文档 内容",
    )
    low_source_target = os.path.relpath(
        source_root / f"{low_source_id}.md",
        start=low_page.parent,
    ).replace("\\", "/")
    low_page.write_text(
        low_page.read_text(encoding="utf-8") + f"\n<!-- personal-context-managed-source: {low_source_id} -->\n\n"
        f"[{low_source_id}]({low_source_target})\n",
        encoding="utf-8",
    )
    return workspace, low_source_id, low_relative


def test_page_provenance_is_stable_across_trigger_provider_and_time(tmp_path: Path) -> None:
    feishu_sep_workspace, feishu_sep_source, low_relative = _build_trigger_invariance_workspace(
        tmp_path / "feishu-sep-trigger"
    )
    rss_sep_workspace, rss_sep_source, rss_sep_low_relative = _build_trigger_invariance_workspace(
        tmp_path / "rss-sep-trigger"
    )
    feishu_oct_workspace, feishu_oct_source, feishu_oct_low_relative = _build_trigger_invariance_workspace(
        tmp_path / "feishu-oct-trigger"
    )
    assert feishu_sep_source == rss_sep_source == feishu_oct_source
    assert low_relative == rss_sep_low_relative == feishu_oct_low_relative

    feishu_sep_mapping = _plan_reclustering_with_page_provenance(
        feishu_sep_workspace,
        changed_paths={"正常主题0/智能体记忆.md"},
        trigger_provider="feishu",
        trigger_time=datetime(2026, 9, 4, tzinfo=timezone.utc),
        max_pages_per_directory=1,
        max_subdirectories_per_directory=2,
    )
    rss_sep_mapping = _plan_reclustering_with_page_provenance(
        rss_sep_workspace,
        changed_paths={"正常主题0/智能体记忆.md"},
        trigger_provider="rss_feed",
        trigger_time=datetime(2026, 9, 4, tzinfo=timezone.utc),
        max_pages_per_directory=1,
        max_subdirectories_per_directory=2,
    )
    feishu_oct_mapping = _plan_reclustering_with_page_provenance(
        feishu_oct_workspace,
        changed_paths={"正常主题0/智能体记忆.md"},
        trigger_provider="feishu",
        trigger_time=datetime(2026, 10, 5, tzinfo=timezone.utc),
        max_pages_per_directory=1,
        max_subdirectories_per_directory=2,
    )
    feishu_sep_targets = _final_page_paths(feishu_sep_workspace / "context", feishu_sep_mapping)
    rss_sep_targets = _final_page_paths(rss_sep_workspace / "context", rss_sep_mapping)
    feishu_oct_targets = _final_page_paths(feishu_oct_workspace / "context", feishu_oct_mapping)

    assert feishu_sep_targets == rss_sep_targets == feishu_oct_targets
    normal_relatives = sorted(relative for relative in feishu_sep_targets if relative != low_relative)
    normal_in_fallback = {
        relative: feishu_sep_targets[relative]
        for relative in normal_relatives
        if "待整理" in PurePosixPath(feishu_sep_targets[relative]).parts
    }
    violations: dict[str, object] = {}
    if not feishu_sep_targets[low_relative].startswith("待整理/GitHub/2026年08月/"):
        violations["low_confidence_target"] = feishu_sep_targets[low_relative]
    if normal_in_fallback:
        violations["normal_pages_in_fallback"] = normal_in_fallback
    if any(target.startswith("待整理/RSS订阅/") for target in feishu_sep_targets.values()):
        violations["trigger_provider_leaked"] = True
    assert not violations, violations


def test_low_confidence_partition_exposes_sanitized_internal_reason(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    context_root = workspace / "context"
    source_root = workspace / "source-meta"
    context_root.mkdir(parents=True)
    source_root.mkdir()
    relative = "待整理/GitHub/2026年08月/低置信/0001.md"
    locator = "https://example.test/source/20260819.md"
    item = RawChangeItem(
        logical_id=locator,
        revision_id="rev-1",
        operation="upsert",
        title="2026-08-19",
        content="12345 20260819 文档 内容",
        original_ref=locator,
        metadata={"kind": "document"},
    )
    source_id = upsert_source_metadata(
        source_root,
        item,
        provider="github",
        service_id="github-service",
        observed_at="2026-08-19T08:30:00+00:00",
    )
    page = _write_cluster_page(
        context_root,
        relative,
        title="2026-08-19",
        body="12345 20260819 文档 内容",
    )
    source_target = os.path.relpath(source_root / f"{source_id}.md", start=page.parent).replace("\\", "/")
    page.write_text(
        page.read_text(encoding="utf-8") + f"\n<!-- personal-context-managed-source: {source_id} -->\n\n"
        f"[{source_id}]({source_target})\n",
        encoding="utf-8",
    )
    normal_relative = "旧主题/数据库索引实践.md"
    normal_page, _normal_source_id = _write_managed_cluster_page(
        workspace,
        normal_relative,
        provider="github",
        observed_at="2026-08-20T08:30:00+00:00",
        title="数据库索引实践",
        body="BTree 索引、查询优化与执行计划。",
    )
    context_before = _file_tree_bytes(context_root)
    source_meta_before = _file_tree_bytes(source_root)
    classifier = getattr(context_pipeline, "_context_page_partition", None)

    assert classifier is not None, "the planner must expose page-level partition reasons"
    low_result = classifier(
        page,
        context_root=context_root,
        source_root=source_root,
        baseline_relative=relative,
        allow_existing_fallback_promotion=True,
    )
    normal_result = classifier(
        normal_page,
        context_root=context_root,
        source_root=source_root,
        baseline_relative=normal_relative,
        allow_existing_fallback_promotion=True,
    )

    assert _file_tree_bytes(context_root) == context_before
    assert _file_tree_bytes(source_root) == source_meta_before
    assert low_result == ("fallback", None, "generic_or_numeric_only")
    normal_partition, readable_label, normal_reason = normal_result
    assert normal_partition == "normal"
    assert readable_label
    assert normal_reason is None


@pytest.mark.parametrize("entrypoint", ["route", "planner"])
def test_new_low_confidence_page_without_source_time_fails_closed(
    tmp_path: Path,
    entrypoint: str,
) -> None:
    context_root = tmp_path / "context"
    source_root = tmp_path / "source-meta"
    context_root.mkdir()
    source_root.mkdir()
    (context_root / "description.md").write_text("# Context\n", encoding="utf-8")
    page = _write_cluster_page(
        context_root,
        "0001.md",
        title="2026-09-05",
        body="12345 文档 内容",
    )

    with pytest.raises(BaseError, match="stable first-entry time"):
        if entrypoint == "route":
            context_pipeline._fallback_page_route(
                page,
                context_root=context_root,
                source_root=source_root,
                previous_relative=None,
            )
        else:
            context_pipeline._plan_context_reclustering(
                context_root,
                source_root=source_root,
                rebuild_roots=(context_root,),
                baseline_path_by_identity={},
                max_pages_per_directory=20,
                max_subdirectories_per_directory=20,
            )


def test_managed_page_fallback_route_ignores_related_document_provenance(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    page, _source_id = _write_managed_cluster_page(
        workspace,
        "待整理/GitHub/2026年08月/0001.md",
        provider="github",
        observed_at="2026-08-19T08:30:00+00:00",
        title="2026-08-19",
        body="12345 文档 内容",
    )
    related_page, _related_source_id = _write_managed_cluster_page(
        workspace,
        "飞书资料/协作记录.md",
        provider="feishu",
        observed_at="2026-07-01T08:30:00+00:00",
        title="飞书协作记录",
        body="团队协作和会议记录。",
    )
    related_target = os.path.relpath(related_page, start=page.parent).replace("\\", "/")
    page.write_text(
        page.read_text(encoding="utf-8").rstrip()
        + "\n\n<!-- personal-context-related:start -->\n"
        + "## 相关文档\n\n"
        + f"- [飞书协作记录]({related_target})\n"
        + "<!-- personal-context-related:end -->\n",
        encoding="utf-8",
    )

    route = context_pipeline._fallback_page_route(
        page,
        context_root=workspace / "context",
        source_root=workspace / "source-meta",
        previous_relative="待整理/GitHub/2026年08月/0001.md",
    )

    assert route == PurePosixPath("待整理/GitHub/2026年08月")


def test_unmanaged_aggregate_fallback_route_excludes_related_document_block(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    github_page, github_source_id = _write_managed_cluster_page(
        workspace,
        "GitHub资料/检索记录.md",
        provider="github",
        observed_at="2026-08-19T08:30:00+00:00",
        title="GitHub 检索记录",
        body="语义检索资料。",
    )
    related_page, _related_source_id = _write_managed_cluster_page(
        workspace,
        "飞书资料/协作记录.md",
        provider="feishu",
        observed_at="2026-07-01T08:30:00+00:00",
        title="飞书协作记录",
        body="团队协作和会议记录。",
    )
    aggregate = _write_cluster_page(
        workspace / "context",
        "聚合页/0001.md",
        title="2026-08-19",
        body="12345 文档 内容",
    )
    source_target = os.path.relpath(
        workspace / "source-meta" / f"{github_source_id}.md",
        start=aggregate.parent,
    ).replace("\\", "/")
    related_target = os.path.relpath(related_page, start=aggregate.parent).replace("\\", "/")
    aggregate.write_text(
        aggregate.read_text(encoding="utf-8").rstrip()
        + f"\n\n[GitHub 原子来源]({source_target})\n\n"
        + "<!-- personal-context-related:start -->\n"
        + "## 相关文档\n\n"
        + f"- [飞书协作记录]({related_target})\n"
        + "<!-- personal-context-related:end -->\n",
        encoding="utf-8",
    )

    route = context_pipeline._fallback_page_route(
        aggregate,
        context_root=workspace / "context",
        source_root=workspace / "source-meta",
        previous_relative=None,
    )

    assert github_page.is_file()
    assert route == PurePosixPath("待整理/GitHub/2026年08月")


def test_unmanaged_aggregate_fallback_route_does_not_follow_description_navigation(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    context_root = workspace / "context"
    source_root = workspace / "source-meta"
    github_source_id = upsert_source_metadata(
        source_root,
        RawChangeItem(
            logical_id="github/direct-source",
            revision_id="rev-1",
            operation="upsert",
            title="GitHub 原子来源",
            content="语义检索资料。",
            original_ref="https://example.test/github/direct-source",
            metadata={"kind": "document"},
        ),
        provider="github",
        service_id="github-service",
        observed_at="2026-08-19T08:30:00+00:00",
    )
    feishu_page, _feishu_source_id = _write_managed_cluster_page(
        workspace,
        "导航目录/飞书协作.md",
        provider="feishu",
        observed_at="2026-07-01T08:30:00+00:00",
        title="飞书协作记录",
        body="团队协作和会议记录。",
    )
    description = feishu_page.parent / "description.md"
    description.write_text(
        "# 导航目录\n\n"
        "<!-- personal-context:navigation:start -->\n"
        "## 导航\n\n"
        "- [飞书协作](飞书协作.md)\n"
        "<!-- personal-context:navigation:end -->\n",
        encoding="utf-8",
    )
    aggregate = _write_cluster_page(
        context_root,
        "聚合页/0002.md",
        title="2026-08-19",
        body="12345 文档 内容",
    )
    source_target = os.path.relpath(source_root / f"{github_source_id}.md", start=aggregate.parent).replace("\\", "/")
    description_target = os.path.relpath(description, start=aggregate.parent).replace("\\", "/")
    aggregate.write_text(
        aggregate.read_text(encoding="utf-8").rstrip()
        + f"\n\n[GitHub 原子来源]({source_target})\n"
        + f"[目录导航]({description_target})\n",
        encoding="utf-8",
    )

    route = context_pipeline._fallback_page_route(
        aggregate,
        context_root=context_root,
        source_root=source_root,
        previous_relative=None,
    )

    assert route == PurePosixPath("待整理/GitHub/2026年08月")


def test_fallback_route_does_not_swallow_duplicate_managed_source_marker(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    page, _source_id = _write_managed_cluster_page(
        workspace,
        "待整理/GitHub/2026年08月/0001.md",
        provider="github",
        observed_at="2026-08-19T08:30:00+00:00",
        title="2026-08-19",
        body="12345 文档 内容",
    )
    _other_page, other_source_id = _write_managed_cluster_page(
        workspace,
        "GitHub资料/另一个页面.md",
        provider="github",
        observed_at="2026-08-20T08:30:00+00:00",
        title="另一个页面",
        body="GitHub 资料。",
    )
    page.write_text(
        page.read_text(encoding="utf-8") + f"\n<!-- personal-context-managed-source: {other_source_id} -->\n",
        encoding="utf-8",
    )

    with pytest.raises(BaseError, match="managed source identity is duplicated"):
        context_pipeline._fallback_page_route(
            page,
            context_root=workspace / "context",
            source_root=workspace / "source-meta",
            previous_relative="待整理/GitHub/2026年08月/0001.md",
        )


def _direct_governance_config(
    profile: str,
    *,
    max_pages_per_directory: int,
    max_subdirectories_per_directory: int,
) -> PersonalContextConfig:
    return PersonalContextConfig.from_dict(
        {
            "collection_enabled": True,
            "agent_use_enabled": False,
            "strategy_profile": profile,
            "model_client": (
                {
                    "client_provider": "OpenAI",
                    "api_key": "test-secret",
                    "api_base": "https://model.invalid",
                }
                if profile == "balanced"
                else None
            ),
            "model_request": {"model": "test"} if profile == "balanced" else None,
            "max_pages_per_directory": max_pages_per_directory,
            "max_subdirectories_per_directory": max_subdirectories_per_directory,
            "fetch_services": [],
        }
    )


class _KeepRulesBalancedModel:
    def __init__(self, **_kwargs: object) -> None:
        pass

    async def invoke(self, _messages: list[object], **_kwargs: object) -> str:
        return (
            '{"items":[{"item_index":0,"summary":"数据库索引的可读摘要。",'
            '"page_title":"数据库索引实践","keywords":["数据库索引"]}]}'
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", ["rules", "balanced"])
async def test_direct_profiles_promote_readable_page_out_of_pending(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    profile: str,
) -> None:
    workspace = tmp_path / "workspace"
    context_root = workspace / "context"
    source_root = workspace / "source-meta"
    context_root.mkdir(parents=True)
    source_root.mkdir()
    (context_root / "description.md").write_text("# Context\n", encoding="utf-8")
    low_relative = "待整理/GitHub/2026年08月/低置信/0001.md"
    _page, source_id = _write_managed_cluster_page(
        workspace,
        low_relative,
        provider="github",
        observed_at="2026-08-19T08:30:00+00:00",
        title="2026-08-19",
        body="12345 20260819 文档 内容",
    )
    logical_id = "github/promotable"
    document = {
        "logical_id": logical_id,
        "revision_id": "rev-2",
        "title": "数据库索引实践",
        "markdown": "BTree 索引、查询优化与执行计划。\n",
    }
    batch = FetchBatch(
        batch_id="promotion",
        items=[
            RawChangeItem(
                logical_id=logical_id,
                revision_id="rev-2",
                operation="upsert",
                title="数据库索引实践",
                content="BTree 索引、查询优化与执行计划。",
                original_ref="https://example.test/source/promotable",
                metadata={"kind": "document"},
            )
        ],
    )
    service = context_pipeline.ContextPipelineService(
        home=tmp_path,
        config=_direct_governance_config(
            profile,
            max_pages_per_directory=2,
            max_subdirectories_per_directory=2,
        ),
        input_queue=asyncio.Queue(),
    )
    sandbox = tmp_path / f"{profile}-sandbox"
    sandbox.mkdir()
    monkeypatch.setattr(context_pipeline, "Model", _KeepRulesBalancedModel)

    actual_profile = await service._filesystem_with_fallback(
        processed={"documents": [document], "blocks": [], "deleted_ids": []},
        sandbox=sandbox,
        batch=batch,
        service_id="github-service",
        provider="github",
        source_ids_by_logical_id={logical_id: source_id},
        run_time=datetime(2026, 9, 5, tzinfo=timezone.utc),
    )

    promoted = context_pipeline._managed_pages_by_source(sandbox / "context")[source_id]
    assert actual_profile == profile
    assert "待整理" not in promoted.relative_to(sandbox / "context").parts
    assert promoted.read_text(encoding="utf-8").splitlines()[0] == "# 数据库索引实践"


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", ["rules", "balanced"])
async def test_forty_two_readable_pages_have_zero_fallback_through_direct_profiles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    profile: str,
) -> None:
    workspace = tmp_path / "workspace"
    context_root = workspace / "context"
    source_root = workspace / "source-meta"
    context_root.mkdir(parents=True)
    source_root.mkdir()
    (context_root / "description.md").write_text("# Context\n", encoding="utf-8")
    families = ("智能体记忆", "代码审查", "跑步训练", "烘焙温控", "古典音乐", "园艺灌溉", "财务分析")
    providers = ("feishu", "github", "local_files", "rss_feed")
    first_source_id = ""
    for index in range(42):
        family = families[index % len(families)]
        relative = f"隔离目录{index:02d}/{family}专题{index:02d}.md"
        _page, source_id = _write_managed_cluster_page(
            workspace,
            relative,
            provider=providers[index % len(providers)],
            observed_at="2026-08-22T00:00:00+00:00",
            title=f"{family}专题{index:02d}",
            body=f"{family} 清晰语义 内容样本 {index}",
        )
        if index == 0:
            first_source_id = source_id
    logical_id = "feishu/readable-00"
    document = {
        "logical_id": logical_id,
        "revision_id": "rev-2",
        "title": "智能体记忆专题00",
        "markdown": "智能体长期记忆、上下文检索与召回。\n",
    }
    batch = FetchBatch(
        batch_id="forty-two-readable",
        items=[
            RawChangeItem(
                logical_id=logical_id,
                revision_id="rev-2",
                operation="upsert",
                title="智能体记忆专题00",
                content="智能体长期记忆、上下文检索与召回。",
                original_ref="https://example.test/source/readable-00",
                metadata={"kind": "document"},
            )
        ],
    )
    service = context_pipeline.ContextPipelineService(
        home=tmp_path,
        config=_direct_governance_config(
            profile,
            max_pages_per_directory=2,
            max_subdirectories_per_directory=5,
        ),
        input_queue=asyncio.Queue(),
    )
    sandbox = tmp_path / f"{profile}-sandbox"
    sandbox.mkdir()
    monkeypatch.setattr(context_pipeline, "Model", _KeepRulesBalancedModel)

    actual_profile = await service._filesystem_with_fallback(
        processed={"documents": [document], "blocks": [], "deleted_ids": []},
        sandbox=sandbox,
        batch=batch,
        service_id="feishu-service",
        provider="feishu",
        source_ids_by_logical_id={logical_id: first_source_id},
        run_time=datetime(2026, 9, 5, tzinfo=timezone.utc),
    )

    candidate = sandbox / "context"
    pages = context_pipeline._context_ordinary_pages(candidate)
    fallback_pages = [page for page in pages if "待整理" in page.relative_to(candidate).parts]
    _assert_no_mechanical_directory_segments(page.relative_to(candidate).as_posix() for page in pages)
    assert actual_profile == profile
    assert len(pages) == 42
    assert not fallback_pages, f"{len(fallback_pages)}/42 readable pages were sent to 待整理"
    _assert_tree_capacity(candidate, pages=2, subdirectories=5)


def test_seventy_five_sixty_five_multisource_rebuild_has_zero_pending_and_preserves_source_meta(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    context_root = workspace / "context"
    source_root = workspace / "source-meta"
    context_root.mkdir(parents=True)
    source_root.mkdir()
    (context_root / "description.md").write_text("# Context\n", encoding="utf-8")
    topic_families = (
        "量子纠错",
        "Docker网络",
        "马拉松配速",
        "法式烘焙",
        "古典音乐",
        "阳台园艺",
        "财务报表",
        "旅行摄影",
        "汉代历史",
        "海洋生物",
        "机器视觉",
        "数据库索引",
        "编译器优化",
        "气象观测",
        "室内设计",
        "儿童教育",
        "咖啡烘焙",
        "无人机航拍",
        "跨境物流",
        "供应链预测",
        "法律检索",
        "医学影像",
        "天文观测",
        "电池材料",
        "语言学习",
    )
    variants = ("基础", "实战", "评估")
    providers = ("feishu", "github", "local_files", "rss_feed")
    source_ids: set[str] = set()
    old_paths: list[str] = []
    for index in range(75):
        family = topic_families[index // len(variants)]
        variant = variants[index % len(variants)]
        title = f"{family}{variant}"
        root_index = index if index < 65 else index - 65
        relative = f"原目录{root_index:02d}/{title}-{index:02d}.md"
        _page, source_id = _write_managed_cluster_page(
            workspace,
            relative,
            provider=providers[index % len(providers)],
            observed_at=f"2026-0{7 + index % 2}-15T00:00:00+00:00",
            title=title,
            body=f"{family} {variant} 专业资料 样本{index}",
        )
        source_ids.add(source_id)
        old_paths.append(relative)
    assert len(context_pipeline._context_ordinary_pages(context_root)) == 75
    assert len([path for path in context_root.iterdir() if path.is_dir()]) == 65
    assert not (context_root / "待整理").exists()
    source_meta_before = _source_metadata_bytes(source_root)

    mapping = _plan_reclustering_with_page_provenance(
        workspace,
        changed_paths={old_paths[0]},
        trigger_provider="feishu",
        trigger_time=datetime(2026, 9, 4, tzinfo=timezone.utc),
        max_pages_per_directory=2,
        max_subdirectories_per_directory=20,
    )
    planned_targets = _final_page_paths(context_root, mapping)
    pending_targets = [target for target in planned_targets.values() if "待整理" in PurePosixPath(target).parts]

    _assert_no_mechanical_directory_segments(planned_targets.values())
    assert not pending_targets, f"{len(pending_targets)}/75 readable pages were sent to 待整理"

    context_pipeline._apply_context_reclustering(context_root, source_root=source_root, mapping=mapping)

    pages = context_pipeline._context_ordinary_pages(context_root)
    _assert_no_mechanical_directory_segments(page.relative_to(context_root).as_posix() for page in pages)
    _assert_tree_capacity(context_root, pages=2, subdirectories=20)
    assert len(pages) == 75
    assert len([path for path in context_root.iterdir() if path.is_dir()]) < 65
    assert not (context_root / "待整理").exists()
    assert set(context_pipeline._managed_pages_by_source(context_root)) == source_ids
    assert _source_metadata_bytes(source_root) == source_meta_before


@pytest.mark.parametrize(("page_count", "minimum_directory_depth"), [(5, 2), (9, 3)])
def test_hierarchical_plan_adds_levels_when_branch_capacity_is_exceeded(
    tmp_path: Path,
    page_count: int,
    minimum_directory_depth: int,
) -> None:
    context_root = tmp_path / "context"
    source_root = tmp_path / "source-meta"
    context_root.mkdir()
    source_root.mkdir()
    (context_root / "description.md").write_text("# Context\n", encoding="utf-8")
    for index in range(page_count):
        _write_cluster_page(
            context_root,
            f"旧目录{index}/页面{index}.md",
            title=f"智能体记忆 {index}",
            body=f"主动上下文 记忆 检索 {index}",
        )

    mapping = _plan_context_reclustering(
        context_root,
        changed_paths={"旧目录0/页面0.md"},
        max_pages_per_directory=2,
        max_subdirectories_per_directory=2,
    )

    assert mapping
    assert max(len(Path(target).parts) - 1 for target in mapping.values()) >= minimum_directory_depth
    context_pipeline._apply_context_reclustering(
        context_root,
        source_root=source_root,
        mapping=mapping,
    )
    _assert_tree_capacity(context_root, pages=2, subdirectories=2)


def test_hierarchical_plan_merges_related_cross_provider_pages_without_provider_directories(tmp_path: Path) -> None:
    context_root = tmp_path / "context"
    context_root.mkdir()
    (context_root / "description.md").write_text("# Context\n", encoding="utf-8")
    for index in range(75):
        family = ("智能体记忆", "代码审查", "跑步训练")[index % 3]
        _write_cluster_page(
            context_root,
            f"旧目录{index}/页面{index}.md",
            title=f"{family} {index}",
            body=f"{family} 主动上下文 研究 {index}",
        )

    mapping = _plan_context_reclustering(
        context_root,
        changed_paths={"旧目录0/页面0.md"},
        max_pages_per_directory=20,
        max_subdirectories_per_directory=20,
    )

    assert len({Path(target).parts[0] for target in mapping.values()}) < 65
    assert len({Path(target).parent.as_posix() for target in mapping.values()}) <= math.ceil(75 / 12)
    assert all("feishu" not in target.casefold() and "github" not in target.casefold() for target in mapping.values())


def test_recluster_reuses_semantically_matching_old_directory_without_string_containment(tmp_path: Path) -> None:
    context_root = tmp_path / "context"
    source_root = tmp_path / "source-meta"
    old_directory = context_root / "既有知识库"
    old_directory.mkdir(parents=True)
    source_root.mkdir()
    (context_root / "description.md").write_text("# Context\n", encoding="utf-8")
    (old_directory / "description.md").write_text("# 语义检索架构规划\n", encoding="utf-8")
    old_paths: list[str] = []
    for index, suffix in enumerate(("系统优化", "召回实践", "索引设计")):
        relative = f"既有知识库/页面{index}.md"
        old_paths.append(relative)
        _write_cluster_page(
            context_root,
            relative,
            title=f"语义检索{suffix}",
            body="向量检索 语义搜索 召回排序 查询优化",
        )
    baseline_path_by_identity = context_pipeline._context_page_paths_by_identity(context_root)

    mapping, _roots, _roles, _reasons = context_pipeline._plan_context_reclustering(
        context_root,
        source_root=source_root,
        rebuild_roots=(context_root,),
        baseline_path_by_identity=baseline_path_by_identity,
        max_pages_per_directory=20,
        max_subdirectories_per_directory=20,
    )
    final_parents = {PurePosixPath(mapping.get(relative, relative)).parent.as_posix() for relative in old_paths}

    assert final_parents == {"既有知识库"}


def test_recluster_keeps_existing_normal_page_normal_when_its_label_is_generic(tmp_path: Path) -> None:
    context_root = tmp_path / "context"
    context_root.mkdir()
    (context_root / "description.md").write_text("# Context\n", encoding="utf-8")
    _write_cluster_page(
        context_root,
        "智能体记忆.md",
        title="智能体记忆",
        body="主动上下文 记忆 检索",
    )
    _write_cluster_page(
        context_root,
        "无主题.md",
        title="2026-09-02",
        body="12345",
    )

    mapping = _plan_context_reclustering(
        context_root,
        changed_paths={"智能体记忆.md", "无主题.md"},
        max_pages_per_directory=20,
        max_subdirectories_per_directory=20,
    )

    assert not mapping["智能体记忆.md"].startswith("待整理/")
    assert not mapping["无主题.md"].startswith("待整理/")


def test_recluster_does_not_repeat_provider_fallback_inside_existing_fallback(tmp_path: Path) -> None:
    context_root = tmp_path / "context"
    context_root.mkdir()
    (context_root / "description.md").write_text("# Context\n", encoding="utf-8")
    fallback = "待整理/飞书/2026年09月"
    changed_paths: set[str] = set()
    for index in range(3):
        relative = f"{fallback}/旧页面{index}.md"
        _write_cluster_page(
            context_root,
            relative,
            title="文档",
            body=str(index),
        )
        changed_paths.add(relative)

    mapping = _plan_context_reclustering(
        context_root,
        changed_paths=changed_paths,
        max_pages_per_directory=2,
        max_subdirectories_per_directory=20,
    )

    assert mapping
    assert all(target.startswith("待整理/未归属/2026年09月/") for target in mapping.values())
    assert all(target.count("待整理/") == 1 for target in mapping.values())


def test_seventy_five_page_recluster_applies_capacity_and_is_stable(tmp_path: Path) -> None:
    context_root = tmp_path / "context"
    source_root = tmp_path / "source-meta"
    context_root.mkdir()
    source_root.mkdir()
    (context_root / "description.md").write_text("# Context\n", encoding="utf-8")
    for index in range(75):
        family = ("智能体记忆", "代码审查", "跑步训练")[index % 3]
        _write_cluster_page(
            context_root,
            f"旧目录{index}/页面{index}.md",
            title=f"{family} {index}",
            body=f"{family} 主动上下文 研究 {index}",
        )
    mapping = _plan_context_reclustering(
        context_root,
        changed_paths={"旧目录0/页面0.md"},
        max_pages_per_directory=20,
        max_subdirectories_per_directory=20,
    )

    context_pipeline._apply_context_reclustering(
        context_root,
        source_root=source_root,
        mapping=mapping,
    )

    _assert_tree_capacity(context_root, pages=20, subdirectories=20)
    assert len([path for path in context_root.iterdir() if path.is_dir()]) < 65
    assert [path.name for path in context_root.iterdir() if path.is_file()] == ["description.md"]
    assert not any(
        provider in path.relative_to(context_root).as_posix().casefold()
        for path in context_root.rglob("*")
        for provider in ("feishu", "github")
    )
    current_page = context_pipeline._context_ordinary_pages(context_root)[0]
    assert (
        _plan_context_reclustering(
            context_root,
            changed_paths={current_page.relative_to(context_root).as_posix()},
            max_pages_per_directory=20,
            max_subdirectories_per_directory=20,
        )
        == {}
    )


def test_hierarchical_plan_leaves_isolated_topics_unmerged(tmp_path: Path) -> None:
    context_root = tmp_path / "context"
    context_root.mkdir()
    (context_root / "description.md").write_text("# Context\n", encoding="utf-8")
    words = ("aardvark", "buffalo", "cuttlefish", "dormouse", "echidna", "flamingo", "gazelle", "hedgehog")
    for index, word in enumerate(words):
        page = context_root / f"孤立目录{index}" / f"{word}.md"
        page.parent.mkdir()
        page.write_text(f"# {word}\n\n{word}\n", encoding="utf-8")

    mapping = _plan_context_reclustering(
        context_root,
        changed_paths={"孤立目录0/aardvark.md"},
        max_pages_per_directory=20,
        max_subdirectories_per_directory=20,
    )

    assert mapping == {}


def test_fragmented_related_tree_reclusters_only_after_improvement_gate(tmp_path: Path) -> None:
    context_root = tmp_path / "context"
    context_root.mkdir()
    (context_root / "description.md").write_text("# Context\n", encoding="utf-8")
    old_paths: list[str] = []
    for index in range(8):
        relative = f"记忆碎片{index}/页面{index}.md"
        old_paths.append(relative)
        _write_cluster_page(
            context_root,
            relative,
            title=f"智能体记忆专题{index}",
            body="主动上下文 智能体 记忆 检索 能力",
        )

    mapping = _plan_context_reclustering(
        context_root,
        changed_paths={old_paths[0]},
        max_pages_per_directory=20,
        max_subdirectories_per_directory=20,
    )
    final_directories = {Path(mapping.get(relative, relative)).parent.as_posix() for relative in old_paths}

    assert mapping
    assert len(final_directories) <= 6


def test_recluster_does_not_treat_small_changed_tree_as_fragmented(tmp_path: Path) -> None:
    context_root = tmp_path / "context"
    context_root.mkdir()
    (context_root / "description.md").write_text("# Context\n", encoding="utf-8")
    _write_cluster_page(
        context_root,
        "记忆甲/基础.md",
        title="智能体记忆基础",
        body="主动上下文 智能体 记忆 检索 能力",
    )
    _write_cluster_page(
        context_root,
        "记忆乙/进阶.md",
        title="智能体记忆进阶",
        body="主动上下文 智能体 记忆 检索 能力",
    )
    _write_cluster_page(
        context_root,
        "跑步分支/训练.md",
        title="马拉松训练",
        body="配速 心率 长跑 恢复 周期",
    )

    mapping = _plan_context_reclustering(
        context_root,
        changed_paths={"记忆甲/基础.md"},
        max_pages_per_directory=20,
        max_subdirectories_per_directory=20,
    )

    assert mapping == {}


def test_recluster_page_overflow_only_rebuilds_violated_subtree(tmp_path: Path) -> None:
    context_root = tmp_path / "context"
    context_root.mkdir()
    (context_root / "description.md").write_text("# Context\n", encoding="utf-8")
    for index in range(3):
        _write_cluster_page(
            context_root,
            f"拥挤分支/页面{index}.md",
            title=f"智能体记忆{index}",
            body="主动上下文 智能体 记忆 检索 能力",
        )
    stable = "跑步分支/训练.md"
    _write_cluster_page(
        context_root,
        stable,
        title="马拉松训练",
        body="配速 心率 长跑 恢复 周期",
    )

    mapping = _plan_context_reclustering(
        context_root,
        changed_paths={"拥挤分支/页面0.md"},
        max_pages_per_directory=2,
        max_subdirectories_per_directory=20,
    )

    assert mapping
    assert stable not in mapping
    assert all(source.startswith("拥挤分支/") for source in mapping)
    assert all(target.startswith("拥挤分支/") for target in mapping.values())


def test_hard_branch_capacity_keeps_isolated_leaf_topics_separate(tmp_path: Path) -> None:
    context_root = tmp_path / "context"
    context_root.mkdir()
    (context_root / "description.md").write_text("# Context\n", encoding="utf-8")
    words = (
        "aardvark",
        "buffalo",
        "cuttlefish",
        "dormouse",
        "echidna",
        "flamingo",
        "gazelle",
        "hedgehog",
        "ibex",
        "jellyfish",
        "kingfisher",
        "lemur",
        "mongoose",
        "narwhal",
        "ocelot",
        "pangolin",
        "quokka",
        "raccoon",
        "salamander",
        "tapir",
        "wallaby",
    )
    old_paths: list[str] = []
    for index, word in enumerate(words):
        relative = f"旧目录{index:02d}/{word}.md"
        old_paths.append(relative)
        page = context_root / relative
        page.parent.mkdir(parents=True)
        page.write_text(f"# {word}\n\n{word}\n", encoding="utf-8")

    mapping = _plan_context_reclustering(
        context_root,
        changed_paths={old_paths[0]},
        max_pages_per_directory=20,
        max_subdirectories_per_directory=20,
    )
    final_paths = [Path(mapping.get(relative, relative)) for relative in old_paths]

    assert len({path.parent.as_posix() for path in final_paths}) == len(words)
    assert len({path.parts[0] for path in final_paths}) <= 20
    assert not any(re.fullmatch(r"第\d+组", part) for path in final_paths for part in path.parts[:-1])


def test_recluster_adds_hash_suffix_only_when_a_filename_collides(tmp_path: Path) -> None:
    context_root = tmp_path / "context"
    context_root.mkdir()
    (context_root / "description.md").write_text("# Context\n", encoding="utf-8")
    old_paths: list[str] = []
    for index, label in enumerate(("基础", "进阶", "实践"), start=1):
        relative = f"旧主题/页面{index}.md"
        old_paths.append(relative)
        _write_cluster_page(
            context_root,
            relative,
            title=f"智能体记忆{label}",
            body="主动上下文 智能体 记忆 检索",
        )

    mapping = _plan_context_reclustering(
        context_root,
        changed_paths={old_paths[0]},
        max_pages_per_directory=2,
        max_subdirectories_per_directory=20,
    )
    final_paths = [Path(mapping.get(relative, relative)) for relative in old_paths]

    assert not any(re.search(r"-[0-9a-f]{6,12}$", path.stem) for path in final_paths)


def test_directory_content_signature_tracks_all_descendant_pages(tmp_path: Path) -> None:
    context_root = tmp_path / "context"
    page = context_root / "主题" / "页面甲.md"
    page.parent.mkdir(parents=True)
    (context_root / "description.md").write_text("# Context\n", encoding="utf-8")
    page.write_text("# 页面甲\n\n智能体记忆\n", encoding="utf-8")
    before = context_pipeline._directory_content_signature(context_root, context_root=context_root)

    page.write_text("# 页面乙\n\n代码审查\n", encoding="utf-8")

    assert context_pipeline._directory_content_signature(context_root, context_root=context_root) != before


@pytest.mark.asyncio
async def test_rules_publish_semantic_pages_without_sources_or_root_pages(tmp_path: Path) -> None:
    context_root = tmp_path / "workspace" / "context"
    source_root = tmp_path / "workspace" / "source-meta"
    context_root.mkdir(parents=True)
    (context_root / "description.md").write_text("# PersonalContext\n", encoding="utf-8")
    first_item = RawChangeItem(
        logical_id="feishu/context-layout",
        revision_id="rev-1",
        operation="upsert",
        title="主动上下文目录治理",
        content="目录容量、语义归档和相关文档。",
        original_ref="https://example.test/feishu/context-layout",
        metadata={"kind": "document"},
    )
    second_item = RawChangeItem(
        logical_id="github/context-layout",
        revision_id="rev-1",
        operation="upsert",
        title="PersonalContext 语义目录治理",
        content="主动上下文目录容量和相关文档实现。",
        original_ref="https://example.test/github/context-layout",
        metadata={"kind": "issue"},
    )
    first_source = upsert_source_metadata(
        source_root,
        first_item,
        provider="feishu",
        service_id="feishu-one",
        observed_at="2026-09-02T00:00:00+00:00",
    )
    second_source = upsert_source_metadata(
        source_root,
        second_item,
        provider="github",
        service_id="github-one",
        observed_at="2026-09-02T00:00:00+00:00",
    )
    for provider, item, source_id in (
        ("feishu", first_item, first_source),
        ("github", second_item, second_source),
    ):
        await context_pipeline._apply_rules_increment(
            context_root,
            provider=provider,
            processed={
                "documents": [
                    {
                        "logical_id": item.logical_id,
                        "title": item.title,
                        "markdown": item.content,
                    }
                ],
                "deleted_ids": [],
            },
            source_ids_by_logical_id={item.logical_id: source_id},
            deleted_source_ids=set(),
            run_time=datetime(2026, 9, 2, tzinfo=timezone.utc),
        )

    root_files = sorted(path.name for path in context_root.iterdir() if path.is_file())
    pages = sorted(path for path in context_root.rglob("*.md") if path.name != "description.md")
    assert root_files == ["description.md"]
    assert len(pages) == 2
    assert pages[0].parent == pages[1].parent
    assert pages[0].parent.name not in {"feishu", "github", "sources", "topics"}
    assert not (context_root / "sources").exists()
    assert not (context_root / "topics").exists()
    assert all(
        text.count("<!-- personal-context-managed-source:") == 1
        for text in (page.read_text(encoding="utf-8") for page in pages)
    )


@pytest.mark.asyncio
async def test_rules_long_source_titles_use_short_stable_semantic_paths(tmp_path: Path) -> None:
    context_root = tmp_path / "workspace" / "context"
    source_root = tmp_path / "workspace" / "source-meta"
    context_root.mkdir(parents=True)
    (context_root / "description.md").write_text("# PersonalContext\n", encoding="utf-8")
    items = [
        RawChangeItem(
            logical_id=f"local/evo-skill-{index}",
            revision_id="rev-1",
            operation="upsert",
            title=title,
            content="Automated agent skill discovery and evaluation.",
            original_ref=f"https://example.test/papers/{index}",
            metadata={"kind": "document"},
        )
        for index, title in enumerate(
            (
                "EvoSkill Automated Skill Discovery for Multi-Agent Systems.pdf",
                "EvoSkill Automated Skill Discovery for Multi-Agent Teams.pdf",
            )
        )
    ]

    for item in items:
        source_id = upsert_source_metadata(
            source_root,
            item,
            provider="local_files",
            service_id="local-papers",
            observed_at="2026-09-03T00:00:00+00:00",
        )
        await context_pipeline._apply_rules_increment(
            context_root,
            provider="local_files",
            processed={
                "documents": [
                    {
                        "logical_id": item.logical_id,
                        "title": item.title,
                        "markdown": item.content,
                    }
                ],
                "deleted_ids": [],
            },
            source_ids_by_logical_id={item.logical_id: source_id},
            deleted_source_ids=set(),
            run_time=datetime(2026, 9, 3, tzinfo=timezone.utc),
        )

    pages = sorted(path for path in context_root.rglob("*.md") if path.name != "description.md")

    assert len(pages) == 2
    assert len({page.relative_to(context_root).as_posix() for page in pages}) == 2
    assert all(len(page.stem) <= 20 for page in pages)
    assert all(len(part) <= 20 for page in pages for part in page.relative_to(context_root).parts[:-1])
    assert all(".pdf" not in part.casefold() for page in pages for part in page.parts)


@pytest.mark.asyncio
async def test_rules_transient_capacity_reclusters_same_topic_without_mechanical_groups(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    context_root = workspace / "context"
    source_root = workspace / "source-meta"
    context_root.mkdir(parents=True)
    (context_root / "description.md").write_text("# Context\n", encoding="utf-8")
    documents: list[dict[str, object]] = []
    source_ids: dict[str, str] = {}
    for index in range(9):
        logical_id = f"feishu/记忆-{index}"
        item = RawChangeItem(
            logical_id=logical_id,
            revision_id="rev-1",
            operation="upsert",
            title=f"智能体记忆 {index}",
            content=f"智能体记忆 上下文 检索内容 {index}",
            original_ref=f"https://example.test/记忆/{index}",
            metadata={"kind": "document"},
        )
        source_ids[logical_id] = upsert_source_metadata(
            source_root,
            item,
            provider="feishu" if index % 2 == 0 else "github",
            service_id=f"service-{index}",
            observed_at="2026-09-04T00:00:00+00:00",
        )
        documents.append({"logical_id": logical_id, "title": item.title, "markdown": item.content})

    def fail_if_model_called(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("Rules must not call a model")

    monkeypatch.setattr(context_pipeline.Model, "invoke", fail_if_model_called)
    await context_pipeline._apply_rules_increment(
        context_root,
        provider="feishu",
        processed={"documents": documents, "deleted_ids": []},
        source_ids_by_logical_id=source_ids,
        deleted_source_ids=set(),
        run_time=datetime(2026, 9, 4, tzinfo=timezone.utc),
        max_pages_per_directory=3,
        max_subdirectories_per_directory=2,
    )

    _assert_tree_capacity(context_root, pages=3, subdirectories=2)
    assert sorted(path.name for path in context_root.iterdir() if path.is_file()) == ["description.md"]
    pages = sorted(path for path in context_root.rglob("*.md") if path.name != "description.md")
    assert len(pages) == 9
    relatives = [page.relative_to(context_root).as_posix() for page in pages]
    _assert_no_mechanical_directory_segments(relatives)
    assert all("待整理" not in PurePosixPath(relative).parts for relative in relatives)
    assert all(
        not any(re.fullmatch(r"[0-9]{4}年[0-9]{2}月", part) for part in PurePosixPath(relative).parts[:-1])
        for relative in relatives
    )


@pytest.mark.asyncio
async def test_rules_transient_capacity_ranks_full_best_before_available_distractor(tmp_path: Path) -> None:
    context_root = tmp_path / "context"
    best = context_root / "主动上下文目录治理"
    distractor = context_root / "主动上下文目录"
    best.mkdir(parents=True)
    distractor.mkdir()
    (best / "description.md").write_text(
        "# 主动上下文目录治理\n\nBM25 聚类与目录容量。\n",
        encoding="utf-8",
    )
    (best / "已有页面.md").write_text(
        "# 主动上下文目录治理实践\n\nBM25 聚类与目录容量。\n",
        encoding="utf-8",
    )
    (distractor / "description.md").write_text(
        "# 主动上下文目录\n\n目录治理与检索。\n",
        encoding="utf-8",
    )
    document = {
        "title": "主动上下文目录治理实践",
        "markdown": "BM25 聚类与目录容量。",
    }
    title, headings, preview = context_pipeline._document_semantic_parts(document)
    query = context_pipeline._semantic_fields(title, headings, preview)
    assert context_pipeline._accepted_semantic_directory(query, [distractor]) == distractor
    assert context_pipeline._accepted_semantic_directory(query, [best, distractor]) == best

    sparse = context_pipeline._select_rules_directory(
        context_root,
        provider="feishu",
        document=document,
        source_id="src_" + "a" * 32,
        run_time=datetime(2026, 9, 5, tzinfo=timezone.utc),
        max_pages=1,
    )

    async def failed_embedding(_texts: list[str]) -> None:
        raise TimeoutError("sparse-only fallback")

    hybrid = await context_pipeline._select_rules_directory_hybrid(
        context_root,
        provider="rss_feed",
        document=document,
        source_id="src_" + "a" * 32,
        run_time=datetime(2026, 10, 5, tzinfo=timezone.utc),
        embed_texts=failed_embedding,
        max_pages=1,
    )

    assert sparse == best
    assert hybrid == best


@pytest.mark.asyncio
async def test_semantic_collision_is_stable_across_batch_trigger_provider_and_time(tmp_path: Path) -> None:
    items = (
        RawChangeItem(
            logical_id="notes/personal-context-cooking",
            revision_id="rev-1",
            operation="upsert",
            title="PersonalContext cooking recipes",
            content="Tomato omelette, kitchen ingredients, and cooking steps.",
            original_ref="file:///notes/cooking.md",
            metadata={"kind": "document"},
        ),
        RawChangeItem(
            logical_id="notes/personal-context-travel",
            revision_id="rev-1",
            operation="upsert",
            title="PersonalContext travel itinerary",
            content="Flights, hotels, passports, and destination planning.",
            original_ref="file:///notes/travel.md",
            metadata={"kind": "document"},
        ),
    )

    async def build(name: str, *, trigger_provider: str, run_time: datetime) -> dict[str, str]:
        workspace = tmp_path / name
        context_root = workspace / "context"
        source_root = workspace / "source-meta"
        context_root.mkdir(parents=True)
        source_ids: dict[str, str] = {}
        documents: list[dict[str, object]] = []
        for item in items:
            source_ids[item.logical_id] = upsert_source_metadata(
                source_root,
                item,
                provider="local_files" if item.logical_id.endswith("cooking") else "rss_feed",
                service_id=item.logical_id,
                observed_at="2026-09-05T00:00:00+00:00",
            )
            documents.append({"logical_id": item.logical_id, "title": item.title, "markdown": item.content})
        await context_pipeline._apply_rules_increment(
            context_root,
            source_root=source_root,
            provider=trigger_provider,
            processed={"documents": documents, "deleted_ids": []},
            source_ids_by_logical_id=source_ids,
            deleted_source_ids=set(),
            run_time=run_time,
        )
        pages = context_pipeline._managed_pages_by_source(context_root)
        return {source_id: pages[source_id].parent.name for source_id in sorted(pages)}

    first = await build(
        "first",
        trigger_provider="feishu",
        run_time=datetime(2026, 9, 5, tzinfo=timezone.utc),
    )
    second = await build(
        "second",
        trigger_provider="rss_feed",
        run_time=datetime(2026, 10, 5, tzinfo=timezone.utc),
    )

    assert first == second
    assert len(set(first.values())) == 2
    assert all("待整理" not in name for name in first.values())
    assert any(re.search(r"-[0-9a-f]{8}$", name) for name in first.values())


def test_layout_normalization_avoids_mechanical_group_for_readable_root_page(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context_root = tmp_path / "workspace" / "context"
    source_root = tmp_path / "workspace" / "source-meta"
    context_root.mkdir(parents=True)
    source_root.mkdir(parents=True)
    (context_root / "description.md").write_text("# Context\n", encoding="utf-8")
    (context_root / "菜谱.md").write_text("# 家常烹饪菜谱\n\n番茄炒蛋与清蒸鱼。\n", encoding="utf-8")

    def mechanical_route_forbidden(*_args: object, **_kwargs: object) -> Path:
        raise AssertionError("readable layout normalization must not use a mechanical capacity route")

    monkeypatch.setattr(context_pipeline, "_virtual_capacity_directory", mechanical_route_forbidden)

    mapping = context_pipeline._plan_context_layout_normalization(
        context_root,
        source_root=source_root,
        run_time=datetime(2026, 9, 5, tzinfo=timezone.utc),
    )

    assert set(mapping) == {"菜谱.md"}
    assert PurePosixPath(mapping["菜谱.md"]).parent == PurePosixPath("家常烹饪菜谱")


def test_layout_normalization_semantic_collision_uses_stable_suffix_before_directories_exist(tmp_path: Path) -> None:
    context_root = tmp_path / "workspace" / "context"
    source_root = tmp_path / "workspace" / "source-meta"
    context_root.mkdir(parents=True)
    source_root.mkdir(parents=True)
    (context_root / "description.md").write_text("# Context\n", encoding="utf-8")
    (context_root / "烹饪.md").write_text(
        "# PersonalContext cooking recipes\n\nTomato omelette and kitchen ingredients.\n",
        encoding="utf-8",
    )
    (context_root / "旅行.md").write_text(
        "# PersonalContext travel itinerary\n\nFlights, hotels, passports, and destinations.\n",
        encoding="utf-8",
    )

    first = context_pipeline._plan_context_layout_normalization(
        context_root,
        source_root=source_root,
        run_time=datetime(2026, 9, 5, tzinfo=timezone.utc),
    )
    second = context_pipeline._plan_context_layout_normalization(
        context_root,
        source_root=source_root,
        run_time=datetime(2026, 10, 5, tzinfo=timezone.utc),
    )

    first_parents = {PurePosixPath(target).parent.as_posix() for target in first.values()}
    assert first == second
    assert len(first_parents) == 2
    assert all("待整理" not in PurePosixPath(target).parts for target in first.values())
    assert any(re.search(r"-[0-9a-f]{8}$", parent) for parent in first_parents)


@pytest.mark.asyncio
async def test_layout_normalization_mechanical_group_is_absent_after_small_capacity_recluster(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    context_root = workspace / "context"
    source_root = workspace / "source-meta"
    context_root.mkdir(parents=True)
    source_root.mkdir(parents=True)
    (context_root / "description.md").write_text("# Context\n", encoding="utf-8")
    for index, (title, body) in enumerate(
        (
            ("家常烹饪菜谱", "番茄炒蛋与清蒸鱼。"),
            ("旅行路线规划", "航班、酒店与目的地。"),
            ("跑步训练计划", "配速、心率与马拉松。"),
            ("数据库索引实践", "BTree、查询与执行计划。"),
            ("古典音乐欣赏", "交响曲、室内乐与作曲家。"),
        )
    ):
        (context_root / f"根页面-{index}.md").write_text(f"# {title}\n\n{body}\n", encoding="utf-8")

    context_pipeline._normalize_context_candidate(
        context_root,
        source_root=source_root,
        run_time=datetime(2026, 9, 5, tzinfo=timezone.utc),
        max_pages_per_directory=2,
        max_subdirectories_per_directory=2,
    )
    await context_pipeline._apply_rules_increment(
        context_root,
        source_root=source_root,
        provider="feishu",
        processed={"documents": [], "deleted_ids": []},
        source_ids_by_logical_id={},
        deleted_source_ids=set(),
        run_time=datetime(2026, 9, 5, tzinfo=timezone.utc),
        max_pages_per_directory=2,
        max_subdirectories_per_directory=2,
    )

    pages = [path for path in context_root.rglob("*.md") if path.name != "description.md"]
    relatives = [page.relative_to(context_root).as_posix() for page in pages]
    assert len(pages) == 5
    _assert_tree_capacity(context_root, pages=2, subdirectories=2)
    _assert_no_mechanical_directory_segments(relatives)
    assert all("待整理" not in PurePosixPath(relative).parts for relative in relatives)
    assert all(
        not any(re.fullmatch(r"[0-9]{4}年[0-9]{2}月", part) for part in PurePosixPath(relative).parts[:-1])
        for relative in relatives
    )


@pytest.mark.asyncio
async def test_no_recluster_no_embedding_prunes_program_only_empty_directory(tmp_path: Path) -> None:
    context_root = tmp_path / "workspace" / "context"
    source_root = tmp_path / "workspace" / "source-meta"
    context_root.mkdir(parents=True)
    source_root.mkdir(parents=True)
    (context_root / "description.md").write_text("# Context\n", encoding="utf-8")
    for name, title in (("智能体", "智能体记忆"), ("烹饪", "家常烹饪")):
        directory = context_root / name
        directory.mkdir()
        (directory / "description.md").write_text(f"# {name}\n", encoding="utf-8")
        (directory / "页面.md").write_text(f"# {title}\n\n{title}说明。\n", encoding="utf-8")
    empty_navigation = context_root / "旧导航"
    empty_navigation.mkdir()
    (empty_navigation / "description.md").write_text(
        f"# 旧导航\n\n{context_pipeline._MANAGED_TOPIC_MARKER}\n",
        encoding="utf-8",
    )
    baseline_path_by_identity = context_pipeline._context_page_paths_by_identity(context_root)
    embedding_calls: list[list[str]] = []

    async def embeddings(texts: list[str]) -> list[list[float]]:
        embedding_calls.append(texts)
        return [[1.0, float(index + 1)] for index in range(len(texts))]

    changed = await context_pipeline._recluster_context_candidate(
        context_root,
        source_root=source_root,
        changed_paths={"旧导航/description.md"},
        baseline_path_by_identity=baseline_path_by_identity,
        max_pages_per_directory=3,
        max_subdirectories_per_directory=2,
        embed_texts=embeddings,
        preserve_existing_paths=False,
    )

    assert changed == set()
    assert embedding_calls == []
    assert not empty_navigation.exists()


@pytest.mark.asyncio
async def test_empty_program_directory_pruning_preserves_user_description_only_directory(tmp_path: Path) -> None:
    context_root = tmp_path / "workspace" / "context"
    source_root = tmp_path / "workspace" / "source-meta"
    context_root.mkdir(parents=True)
    source_root.mkdir(parents=True)
    (context_root / "description.md").write_text("# Context\n", encoding="utf-8")
    managed_directories: list[Path] = []
    for index in range(3):
        directory = context_root / f"旧导航{index}"
        directory.mkdir()
        (directory / "description.md").write_text(
            f"# 旧导航{index}\n\n{context_pipeline._MANAGED_TOPIC_MARKER}\n",
            encoding="utf-8",
        )
        managed_directories.append(directory)
    user_directory = context_root / "用户说明"
    user_directory.mkdir()
    user_description = user_directory / "description.md"
    user_bytes = "# 用户说明\n\n这是用户自写的空目录说明。\n".encode()
    user_description.write_bytes(user_bytes)

    await context_pipeline._recluster_context_candidate(
        context_root,
        source_root=source_root,
        changed_paths={"旧导航0/description.md"},
        baseline_path_by_identity={},
        max_pages_per_directory=2,
        max_subdirectories_per_directory=2,
        embed_texts=None,
        preserve_existing_paths=False,
    )

    assert all(not directory.exists() for directory in managed_directories)
    assert user_directory.is_dir()
    assert user_description.read_bytes() == user_bytes


def test_empty_program_directory_prune_removes_stale_link_and_keeps_live_link(tmp_path: Path) -> None:
    context_root = tmp_path / "context"
    context_root.mkdir()
    empty_navigation = context_root / "旧导航"
    empty_navigation.mkdir()
    (empty_navigation / "description.md").write_text(
        f"# 旧导航\n\n{context_pipeline._MANAGED_TOPIC_MARKER}\n",
        encoding="utf-8",
    )
    live = context_root / "保留主题"
    live.mkdir()
    (live / "description.md").write_text("# 保留主题\n", encoding="utf-8")
    (live / "页面.md").write_text("# 页面\n\n保留正文。\n", encoding="utf-8")
    root_description = context_root / "description.md"
    root_description.write_text(
        "# Context\n\n"
        f"{context_pipeline._ROOT_NAVIGATION_START}\n"
        "## 目录导航\n\n"
        "- [旧导航](旧导航/description.md)\n"
        "- [保留主题](保留主题/description.md)\n"
        f"{context_pipeline._ROOT_NAVIGATION_END}\n",
        encoding="utf-8",
    )

    context_pipeline._prune_empty_managed_directories(context_root)

    updated = root_description.read_text(encoding="utf-8")
    assert not empty_navigation.exists()
    assert "旧导航/description.md" not in updated
    assert "保留主题/description.md" in updated


def test_prune_empty_managed_directories_preserves_unmanaged_link_description(tmp_path: Path) -> None:
    context_root = tmp_path / "context"
    user_directory = context_root / "用户收藏导航"
    user_directory.mkdir(parents=True)
    description = user_directory / "description.md"
    original = "# 用户收藏导航\n\n- [外部说明](https://example.test/note.md)\n"
    description.write_text(original, encoding="utf-8")

    context_pipeline._prune_empty_managed_directories(context_root)

    assert user_directory.is_dir()
    assert description.read_text(encoding="utf-8") == original

    mixed_directory = context_root / "含用户正文的受管导航"
    mixed_directory.mkdir()
    mixed_description = mixed_directory / "description.md"
    mixed_original = (
        "# 含用户正文的受管导航\n\n用户补充说明必须保留。\n\n"
        f"{context_pipeline._MANAGED_TOPIC_MARKER}\n"
        f"{context_pipeline._ROOT_NAVIGATION_START}\n"
        "## 目录导航\n\n"
        f"{context_pipeline._ROOT_NAVIGATION_END}\n"
    )
    mixed_description.write_text(mixed_original, encoding="utf-8")

    context_pipeline._prune_empty_managed_directories(context_root)

    assert mixed_directory.is_dir()
    assert mixed_description.read_text(encoding="utf-8") == mixed_original


@pytest.mark.asyncio
async def test_no_recluster_no_embedding_calls_only_local_scope_when_hard_triggered(tmp_path: Path) -> None:
    context_root = tmp_path / "workspace" / "context"
    source_root = tmp_path / "workspace" / "source-meta"
    context_root.mkdir(parents=True)
    source_root.mkdir(parents=True)
    (context_root / "description.md").write_text("# Context\n", encoding="utf-8")
    overflow = context_root / "需重建"
    overflow.mkdir()
    (overflow / "description.md").write_text("# 需重建\n", encoding="utf-8")
    for index in range(3):
        (overflow / f"页面-{index}.md").write_text(
            f"# 智能体记忆 {index}\n\n智能体记忆、检索和上下文 {index}。\n",
            encoding="utf-8",
        )
    stable = context_root / "稳定分支"
    stable.mkdir()
    (stable / "description.md").write_text("# 稳定分支\n", encoding="utf-8")
    (stable / "页面.md").write_text("# 家常烹饪\n\n番茄炒蛋与清蒸鱼。\n", encoding="utf-8")
    baseline_path_by_identity = context_pipeline._context_page_paths_by_identity(context_root)
    embedding_calls: list[list[str]] = []

    async def embeddings(texts: list[str]) -> list[list[float]]:
        embedding_calls.append(texts)
        return [[1.0, float(index + 1)] for index in range(len(texts))]

    await context_pipeline._recluster_context_candidate(
        context_root,
        source_root=source_root,
        changed_paths={"需重建/页面-2.md"},
        baseline_path_by_identity=baseline_path_by_identity,
        max_pages_per_directory=2,
        max_subdirectories_per_directory=5,
        embed_texts=embeddings,
        preserve_existing_paths=False,
    )

    assert len(embedding_calls) == 1
    assert len(embedding_calls[0]) == 3
    assert all("家常烹饪" not in text for text in embedding_calls[0])


@pytest.mark.asyncio
async def test_rules_unrelated_description_is_not_rendered_after_local_recluster(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context_root = tmp_path / "workspace" / "context"
    source_root = tmp_path / "workspace" / "source-meta"
    context_root.mkdir(parents=True)
    source_root.mkdir(parents=True)
    (context_root / "description.md").write_text("# Context\n", encoding="utf-8")
    overflow = context_root / "主题A"
    overflow.mkdir()
    (overflow / "description.md").write_text("# 主题A\n", encoding="utf-8")
    for index in range(3):
        (overflow / f"页面{index}.md").write_text(
            f"# 智能体记忆 {index}\n\n智能体记忆、检索和工作流 {index}。\n",
            encoding="utf-8",
        )
    stable = context_root / "主题B"
    stable.mkdir()
    stable_description = stable / "description.md"
    stable_description.write_text("# 主题B\n\n用户说明。\n", encoding="utf-8")
    (stable / "页面.md").write_text("# 家常烹饪\n\n番茄炒蛋与清蒸鱼。\n", encoding="utf-8")
    context_pipeline._render_context_navigation(context_root)
    stable_before = stable_description.read_bytes()
    fixed_mtime_ns = 1_600_000_000_000_000_000
    os.utime(stable_description, ns=(fixed_mtime_ns, fixed_mtime_ns))
    original_signature = context_pipeline._directory_content_signature

    def scoped_signature(directory: Path, *, context_root: Path | None = None) -> str:
        if directory == stable:
            raise AssertionError("Rules finalization must not render unrelated branch B")
        return original_signature(directory, context_root=context_root)

    monkeypatch.setattr(context_pipeline, "_directory_content_signature", scoped_signature)

    await context_pipeline._apply_rules_increment(
        context_root,
        source_root=source_root,
        provider="feishu",
        processed={"documents": [], "deleted_ids": []},
        source_ids_by_logical_id={},
        deleted_source_ids=set(),
        run_time=datetime(2026, 9, 5, tzinfo=timezone.utc),
        max_pages_per_directory=2,
        max_subdirectories_per_directory=20,
    )

    assert stable_description.read_bytes() == stable_before
    assert stable_description.stat().st_mtime_ns == fixed_mtime_ns


def test_rules_capacity_allows_twentieth_and_rejects_twenty_first(tmp_path: Path) -> None:
    directory = _semantic_directory_with_pages(tmp_path / "context", "主动上下文", count=19)
    assert context_pipeline._directory_accepts_new_page(directory, max_pages=20) is True
    (directory / "第20页.md").write_text("# 第20页\n", encoding="utf-8")
    assert context_pipeline._directory_accepts_new_page(directory, max_pages=20) is False


def test_directory_capacities_count_direct_children_independently(tmp_path: Path) -> None:
    context_root = tmp_path / "context"
    topic = _semantic_directory_with_pages(context_root, "主题", count=2)
    for name in ("子主题一", "子主题二"):
        child = topic / name
        child.mkdir()
        (child / "description.md").write_text(f"# {name}\n", encoding="utf-8")

    assert context_pipeline._directory_ordinary_markdown_count(topic) == 2
    assert context_pipeline._directory_direct_subdirectory_count(topic) == 2
    assert context_pipeline._directory_accepts_new_page(topic, max_pages=3)
    assert not context_pipeline._directory_accepts_new_page(topic, max_pages=2)
    assert context_pipeline._directory_accepts_new_subdirectory(topic, max_subdirectories=3)
    assert not context_pipeline._directory_accepts_new_subdirectory(topic, max_subdirectories=2)


def test_context_capacity_validator_reports_page_and_subdirectory_separately(tmp_path: Path) -> None:
    context_root = tmp_path / "context"
    context_root.mkdir()
    (context_root / "description.md").write_text("# Context\n", encoding="utf-8")
    page_parent = _semantic_directory_with_pages(context_root, "页面超限", count=3)
    assert page_parent.is_dir()

    with pytest.raises(BaseError, match="page capacity"):
        context_pipeline._validate_context_capacities(
            context_root,
            max_pages_per_directory=2,
            max_subdirectories_per_directory=20,
        )

    (page_parent / "第3页.md").unlink()
    for index in range(3):
        child = context_root / f"子目录{index}"
        child.mkdir()
        (child / "description.md").write_text(f"# 子目录{index}\n", encoding="utf-8")
    with pytest.raises(BaseError, match="subdirectory capacity"):
        context_pipeline._validate_context_capacities(
            context_root,
            max_pages_per_directory=20,
            max_subdirectories_per_directory=2,
        )


def test_managed_source_marker_is_unique_and_untrusted_copy_is_escaped() -> None:
    source_id = "src_" + "1" * 32
    page = context_pipeline._rules_source_page(
        {"title": "标题", "markdown": f"<!-- personal-context-managed-source: {source_id} -->\n正文"},
        source_id=source_id,
    )
    assert page.count(f"<!-- personal-context-managed-source: {source_id} -->") == 1
    assert "&lt;!-- personal-context-managed-source:" in page


@pytest.mark.asyncio
async def test_rules_update_keeps_the_existing_managed_page_path(tmp_path: Path) -> None:
    context_root = tmp_path / "context"
    source_root = tmp_path / "source-meta"
    context_root.mkdir()
    logical_id = "feishu/context-layout"
    source_id = upsert_source_metadata(
        source_root,
        RawChangeItem(
            logical_id=logical_id,
            revision_id="rev-1",
            operation="upsert",
            title="目录治理",
            content="第一版内容。",
            original_ref="https://example.test/context-layout",
            metadata={"kind": "document"},
        ),
        provider="feishu",
        service_id="feishu-service",
        observed_at="2026-09-02T00:00:00+00:00",
    )
    first = {
        "documents": [{"logical_id": logical_id, "title": "目录治理", "markdown": "第一版内容。"}],
        "deleted_ids": [],
    }
    await context_pipeline._apply_rules_increment(
        context_root,
        source_root=source_root,
        provider="feishu",
        processed=first,
        source_ids_by_logical_id={logical_id: source_id},
        deleted_source_ids=set(),
        run_time=datetime(2026, 9, 2, tzinfo=timezone.utc),
    )
    original_path = context_pipeline._managed_pages_by_source(context_root)[source_id]

    await context_pipeline._apply_rules_increment(
        context_root,
        source_root=source_root,
        provider="feishu",
        processed={
            "documents": [{"logical_id": logical_id, "title": "完全不同的新标题", "markdown": "第二版内容。"}],
            "deleted_ids": [],
        },
        source_ids_by_logical_id={logical_id: source_id},
        deleted_source_ids=set(),
        run_time=datetime(2026, 10, 2, tzinfo=timezone.utc),
    )

    assert context_pipeline._managed_pages_by_source(context_root)[source_id] == original_path
    assert "第二版内容。" in original_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_rules_use_provider_and_month_only_for_low_confidence_fallback(tmp_path: Path) -> None:
    context_root = tmp_path / "context"
    source_root = tmp_path / "source-meta"
    context_root.mkdir()
    source_root.mkdir()
    logical_id = "feishu/numeric"
    item = RawChangeItem(
        logical_id=logical_id,
        revision_id="rev-1",
        operation="upsert",
        title="2026-09-02",
        content="12345",
        original_ref="https://example.test/source/20260902.md",
        metadata={"kind": "document"},
    )
    source_id = upsert_source_metadata(
        source_root,
        item,
        provider="feishu",
        service_id="feishu-service",
        observed_at="2026-09-02T00:00:00+00:00",
    )

    await context_pipeline._apply_rules_increment(
        context_root,
        source_root=source_root,
        provider="feishu",
        processed={
            "documents": [{"logical_id": logical_id, "title": "2026-09-02", "markdown": "12345"}],
            "deleted_ids": [],
        },
        source_ids_by_logical_id={logical_id: source_id},
        deleted_source_ids=set(),
        run_time=datetime(2026, 9, 2, tzinfo=timezone.utc),
    )

    page = context_pipeline._managed_pages_by_source(context_root)[source_id]
    assert page.parent.relative_to(context_root).as_posix() == "待整理/飞书/2026年09月"


def test_managed_source_identity_rejects_duplicate_pages(tmp_path: Path) -> None:
    context_root = tmp_path / "context"
    source_id = "src_" + "4" * 32
    for directory_name in ("主题一", "主题二"):
        directory = context_root / directory_name
        directory.mkdir(parents=True)
        (directory / "页面.md").write_text(
            f"# 页面\n\n<!-- personal-context-managed-source: {source_id} -->\n",
            encoding="utf-8",
        )

    with pytest.raises(BaseError, match="managed source identity is duplicated"):
        context_pipeline._managed_pages_by_source(context_root)


def test_managed_source_scan_ignores_directories_with_markdown_suffix(tmp_path: Path) -> None:
    context_root = tmp_path / "context"
    directory = context_root / "shared.md"
    directory.mkdir(parents=True)
    source_id = "src_" + "5" * 32
    page = directory / "共享页面.md"
    page.write_text(
        f"# 共享页面\n\n<!-- personal-context-managed-source: {source_id} -->\n",
        encoding="utf-8",
    )

    assert context_pipeline._managed_pages_by_source(context_root)[source_id] == page


def test_empty_context_navigation_fallback_is_idempotent(tmp_path: Path) -> None:
    context_root = tmp_path / "context"
    context_root.mkdir()

    context_pipeline._render_context_navigation(context_root, fallback_references=("[[ref:0]]",))
    first = (context_root / "description.md").read_text(encoding="utf-8")
    context_pipeline._render_context_navigation(context_root, fallback_references=("[[ref:0]]",))
    second = (context_root / "description.md").read_text(encoding="utf-8")

    assert second == first
    assert second.count("[[ref:0]]") == 1


def test_scoped_empty_navigation_scope_updates_and_clears_root_fallback_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context_root = tmp_path / "context"
    context_root.mkdir()
    context_pipeline._render_context_navigation(context_root, fallback_references=("[[ref:0]]",))

    context_pipeline._render_context_navigation(
        context_root,
        fallback_references=("[[ref:1]]",),
        affected_directories=set(),
    )
    updated = (context_root / "description.md").read_text(encoding="utf-8")
    assert "[[ref:0]]" not in updated
    assert updated.count("[[ref:1]]") == 1

    context_pipeline._render_context_navigation(
        context_root,
        fallback_references=(),
        affected_directories=set(),
    )
    cleared = (context_root / "description.md").read_text(encoding="utf-8")
    assert "[[ref:1]]" not in cleared
    assert "## Context 状态" not in cleared

    writes: list[Path] = []
    original_write = context_pipeline._atomic_write

    def record_write(path: Path, data: bytes) -> None:
        writes.append(path)
        original_write(path, data)

    monkeypatch.setattr(context_pipeline, "_atomic_write", record_write)
    context_pipeline._render_context_navigation(
        context_root,
        fallback_references=(),
        affected_directories=set(),
    )
    assert writes == []


def test_description_propagation_writes_bottom_up_and_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context_root = tmp_path / "context"
    leaf = context_root / "A" / "子目录"
    leaf.mkdir(parents=True)
    (leaf / "页面.md").write_text("# 页面\n\n正文。\n", encoding="utf-8")
    (context_root / "description.md").write_text("# Context\n\n用户保留段落。\n", encoding="utf-8")

    writes: list[str] = []
    original_write = context_pipeline._atomic_write

    def record_write(path: Path, data: bytes) -> None:
        writes.append(path.relative_to(context_root).as_posix())
        original_write(path, data)

    monkeypatch.setattr(context_pipeline, "_atomic_write", record_write)
    context_pipeline._render_context_navigation(context_root)

    assert "用户保留段落。" in (context_root / "description.md").read_text(encoding="utf-8")
    assert writes
    assert writes == sorted(writes, key=lambda value: (-len(Path(value).parts), value))

    writes.clear()
    context_pipeline._render_context_navigation(context_root)
    assert writes == []


def _source_meta_snapshot(source_root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(source_root).as_posix(): path.read_bytes()
        for path in sorted(source_root.rglob("*"))
        if path.is_file()
    }


def test_recluster_apply_moves_pages_rewrites_links_and_keeps_source_meta_unchanged(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    context_root = workspace / "context"
    source_root = workspace / "source-meta"
    context_root.mkdir(parents=True)
    (context_root / "description.md").write_text("# Context\n", encoding="utf-8")
    source_one = _register_migration_source(
        source_root,
        provider="feishu",
        suffix="one",
        title="主动上下文一",
    )
    source_two = _register_migration_source(
        source_root,
        provider="github",
        suffix="two",
        title="主动上下文二",
    )
    source_before = _source_meta_snapshot(source_root)
    directory = context_root / "旧主题"
    directory.mkdir()
    (directory / "受管一.md").write_text(
        f"# 受管一\n\n<!-- personal-context-managed-source: {source_one} -->\n\n"
        f"[受管二](受管二.md)\n[来源](../../source-meta/{source_one}.md)\n",
        encoding="utf-8",
    )
    (directory / "受管二.md").write_text(
        f"# 受管二\n\n<!-- personal-context-managed-source: {source_two} -->\n\n"
        f"[来源](../../source-meta/{source_two}.md)\n",
        encoding="utf-8",
    )
    (directory / "Agent综合页.md").write_text(
        "# Agent 综合页\n\n用户保留段落。\n\n[受管一](受管一.md)\n",
        encoding="utf-8",
    )
    unrelated = context_root / "B"
    unrelated.mkdir()
    (unrelated / "description.md").write_text("# B\n", encoding="utf-8")
    (unrelated / "B页.md").write_text("# B页\n\n不相关内容。\n", encoding="utf-8")

    mapping = {
        "旧主题/受管一.md": "新主题/受管一.md",
        "旧主题/受管二.md": "新主题/受管二.md",
        "旧主题/Agent综合页.md": "新主题/综合/Agent综合页.md",
    }

    context_pipeline._apply_context_reclustering(
        context_root,
        source_root=source_root,
        mapping=mapping,
    )

    assert _source_meta_snapshot(source_root) == source_before
    moved_agent_page = context_root / "新主题" / "综合" / "Agent综合页.md"
    assert "用户保留段落" in moved_agent_page.read_text(encoding="utf-8")
    assert "受管一.md" in moved_agent_page.read_text(encoding="utf-8")
    context_pipeline._validate_description_navigation(
        context_root,
        final_context_root=context_root,
        source_root=source_root,
    )
    context_pipeline._validate_context_capacities(
        context_root,
        max_pages_per_directory=2,
        max_subdirectories_per_directory=2,
    )
    assert (context_root / "B" / "B页.md").read_text(encoding="utf-8") == "# B页\n\n不相关内容。\n"


def test_recluster_removes_empty_legacy_directory_and_stale_description_links(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    context_root = workspace / "context"
    source_root = workspace / "source-meta"
    context_root.mkdir(parents=True)
    source_root.mkdir(parents=True)
    old_directory = context_root / "旧主题"
    old_directory.mkdir()
    (old_directory / "description.md").write_text(
        "# 旧主题\n\n- [页面](页面.md)\n",
        encoding="utf-8",
    )
    (old_directory / "页面.md").write_text("# 页面\n\n正文。\n", encoding="utf-8")
    (context_root / "description.md").write_text(
        "# Context\n\n- [旧主题](旧主题/description.md)\n",
        encoding="utf-8",
    )

    context_pipeline._apply_context_reclustering(
        context_root,
        source_root=source_root,
        mapping={"旧主题/页面.md": "新主题/页面.md"},
    )

    assert not old_directory.exists()
    assert "旧主题/description.md" not in (context_root / "description.md").read_text(encoding="utf-8")
    context_pipeline._validate_description_navigation(
        context_root,
        final_context_root=context_root,
        source_root=source_root,
    )


def test_recluster_rewrites_user_description_link_without_normalizing_crlf(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    context_root = workspace / "context"
    source_root = workspace / "source-meta"
    source_root.mkdir(parents=True)
    old_directory = context_root / "待移动"
    old_directory.mkdir(parents=True)
    (context_root / "description.md").write_text("# Context\n", encoding="utf-8")
    (old_directory / "description.md").write_text("# 待移动\n", encoding="utf-8")
    moving_page = old_directory / "旧页.md"
    moving_page.write_text("# 旧页\n\n移动目标。\n", encoding="utf-8")
    stable_page = context_root / "稳定主题" / "稳定页.md"
    stable_page.parent.mkdir()
    before = ("# 稳定页\r\n\r\n这是用户正文，指向即将重建的主题：[待移动主题](../待移动/description.md)。\r\n").encode()
    stable_page.write_bytes(before)

    context_pipeline._apply_context_reclustering(
        context_root,
        source_root=source_root,
        mapping={"待移动/旧页.md": "移动后/新页.md"},
    )
    after = stable_page.read_bytes()

    assert after == before.replace(
        b"../\xe5\xbe\x85\xe7\xa7\xbb\xe5\x8a\xa8/description.md",
        b"../\xe7\xa7\xbb\xe5\x8a\xa8\xe5\x90\x8e/description.md",
    )
    assert b"\n" not in after.replace(b"\r\n", b"")


def test_recluster_downgrades_description_link_that_maps_to_root_self_reference(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    context_root = workspace / "context"
    source_root = workspace / "source-meta"
    old_topic = context_root / "旧主题"
    old_topic.mkdir(parents=True)
    source_one = _register_migration_source(
        source_root,
        provider="feishu",
        suffix="root-self-one",
        title="数据库索引一",
    )
    source_two = _register_migration_source(
        source_root,
        provider="github",
        suffix="root-self-two",
        title="数据库索引二",
    )
    (context_root / "description.md").write_text(
        "# Context\n\n"
        "根用户正文：[旧主题入口](旧主题/description.md)。\n\n"
        "外链保持：[官网](https://example.test/docs)。\n\n"
        "内联保持：`[内联入口](旧主题/description.md)`。\n\n"
        "```markdown\n[围栏入口](旧主题/description.md)\n```\n",
        encoding="utf-8",
    )
    (old_topic / "description.md").write_text(
        "# 旧主题\n\n这是应合并到根说明的旧目录用户正文。\n",
        encoding="utf-8",
    )
    (old_topic / "索引一.md").write_text(
        f"# PostgreSQL 索引\n\n正文一。\n\n[来源](../../source-meta/{source_one}.md)\n",
        encoding="utf-8",
    )
    (old_topic / "索引二.md").write_text(
        f"# SQLite 索引\n\n正文二。\n\n[来源](../../source-meta/{source_two}.md)\n",
        encoding="utf-8",
    )
    mapping = {
        "旧主题/索引一.md": "PostgreSQL/索引一.md",
        "旧主题/索引二.md": "SQLite/索引二.md",
    }
    assert context_pipeline._recluster_description_mapping(context_root, mapping=mapping) == {
        "旧主题/description.md": "description.md"
    }

    context_pipeline._apply_context_reclustering(
        context_root,
        source_root=source_root,
        mapping=mapping,
    )

    root_description = (context_root / "description.md").read_text(encoding="utf-8")
    assert "根用户正文：旧主题入口。" in root_description
    assert "这是应合并到根说明的旧目录用户正文。" in root_description
    assert "[旧主题入口](" not in root_description
    assert "(description.md)" not in root_description
    assert "[官网](https://example.test/docs)" in root_description
    assert "`[内联入口](旧主题/description.md)`" in root_description
    assert "```markdown\n[围栏入口](旧主题/description.md)\n```" in root_description
    context_pipeline._validate_reference_graph(
        context_root,
        final_context_root=context_root,
        source_root=source_root,
        repairable=True,
    )


def test_rewrite_context_markdown_self_mapping_keeps_image_syntax(tmp_path: Path) -> None:
    context_root = tmp_path / "context"
    source_root = tmp_path / "source-meta"
    old_topic = context_root / "旧主题"
    old_topic.mkdir(parents=True)
    source_root.mkdir()
    (context_root / "description.md").write_text("# Context\n", encoding="utf-8")
    (old_topic / "description.md").write_text("# 旧主题\n", encoding="utf-8")

    rewritten = context_pipeline._rewrite_context_markdown_links(
        "图片：![旧主题图](旧主题/description.md)\n",
        context_root=context_root,
        source_root=source_root,
        old_page_relative="description.md",
        new_page_relative="description.md",
        mapping={"旧主题/description.md": "description.md"},
    )

    assert rewritten == "图片：![旧主题图](description.md)\n"


@pytest.mark.parametrize(
    ("visible_label", "old_target", "new_page_relative", "expected_label"),
    [
        ("", "旧主题/description.md", "description.md", "旧主题"),
        ("   ", "旧主题/description.md", "description.md", "旧主题"),
        ("", "旧主题/旧页面.md", "页面.md", "旧页面"),
    ],
)
def test_rewrite_context_markdown_self_mapping_derives_blank_label(
    tmp_path: Path,
    visible_label: str,
    old_target: str,
    new_page_relative: str,
    expected_label: str,
) -> None:
    context_root = tmp_path / "context"
    source_root = tmp_path / "source-meta"
    target = context_root.joinpath(*PurePosixPath(old_target).parts)
    target.parent.mkdir(parents=True)
    source_root.mkdir()
    target.write_text("# 旧内容\n", encoding="utf-8")

    rewritten = context_pipeline._rewrite_context_markdown_links(
        f"[{visible_label}]({old_target})",
        context_root=context_root,
        source_root=source_root,
        old_page_relative=new_page_relative,
        new_page_relative=new_page_relative,
        mapping={old_target: new_page_relative},
    )

    assert rewritten == expected_label
    assert "](description.md)" not in rewritten


def test_crlf_description_render_does_not_mix_bare_lf(tmp_path: Path) -> None:
    context_root = tmp_path / "context"
    topic = context_root / "主题"
    topic.mkdir(parents=True)
    (context_root / "description.md").write_bytes("# Context\r\n".encode())
    (topic / "description.md").write_bytes("# 主题\r\n\r\n用户说明。\r\n".encode())
    (topic / "页面.md").write_bytes("# 页面\r\n\r\n正文。\r\n".encode())

    context_pipeline._render_context_navigation(context_root)

    for description in (context_root / "description.md", topic / "description.md"):
        rendered = description.read_bytes()
        assert b"\r\n" in rendered
        assert b"\n" not in rendered.replace(b"\r\n", b"")


def test_crlf_description_relocation_merge_does_not_mix_bare_lf(tmp_path: Path) -> None:
    context_root = tmp_path / "context"
    source_root = tmp_path / "source-meta"
    source_root.mkdir()
    old_topic = context_root / "旧主题"
    new_topic = context_root / "新主题"
    old_topic.mkdir(parents=True)
    new_topic.mkdir()
    (context_root / "description.md").write_bytes("# Context\r\n".encode())
    (old_topic / "description.md").write_bytes("# 旧主题\r\n\r\n旧目录用户说明。\r\n".encode())
    (old_topic / "页面.md").write_bytes("# 智能体记忆\r\n\r\n记忆与检索。\r\n".encode())
    (new_topic / "description.md").write_bytes("# 新主题\r\n\r\n新目录用户说明。\r\n".encode())
    (new_topic / "保留页.md").write_bytes("# 智能体工具\r\n\r\n工具与工作流。\r\n".encode())

    context_pipeline._apply_context_reclustering(
        context_root,
        source_root=source_root,
        mapping={"旧主题/页面.md": "新主题/页面.md"},
    )

    merged = (new_topic / "description.md").read_bytes()
    assert "旧目录用户说明。".encode() in merged
    assert "新目录用户说明。".encode() in merged
    assert b"\r\n" in merged
    assert b"\n" not in merged.replace(b"\r\n", b"")


def test_recluster_preserves_unmanaged_directory_description_body(tmp_path: Path) -> None:
    context_root = tmp_path / "context"
    source_root = tmp_path / "source-meta"
    source_root.mkdir()
    old_directory = context_root / "旧主题"
    old_directory.mkdir(parents=True)
    (context_root / "description.md").write_text("# Context\n", encoding="utf-8")
    (old_directory / "description.md").write_text(
        "# 旧主题\n\n这是用户保留的目录说明。\n",
        encoding="utf-8",
    )
    (old_directory / "页面.md").write_text("# 页面\n\n正文。\n", encoding="utf-8")

    context_pipeline._apply_context_reclustering(
        context_root,
        source_root=source_root,
        mapping={"旧主题/页面.md": "新主题/页面.md"},
    )

    assert "这是用户保留的目录说明。" in (context_root / "新主题" / "description.md").read_text(encoding="utf-8")


def test_unrelated_description_bytes_and_mtime_are_unchanged_by_local_recluster(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context_root = tmp_path / "context"
    source_root = tmp_path / "source-meta"
    source_root.mkdir()
    context_root.mkdir()
    (context_root / "description.md").write_text("# Context\n", encoding="utf-8")
    topic_a = context_root / "主题A"
    topic_a.mkdir()
    (topic_a / "description.md").write_text("# 主题A\n", encoding="utf-8")
    (topic_a / "页面.md").write_text("# 智能体记忆\n\n记忆与检索。\n", encoding="utf-8")
    topic_b = context_root / "主题B"
    topic_b.mkdir()
    description_b = topic_b / "description.md"
    description_b.write_text("# 主题B\n\n这是用户维护且与 A 无关的说明。\n", encoding="utf-8")
    (topic_b / "页面.md").write_text("# 家常烹饪\n\n番茄炒蛋与清蒸鱼。\n", encoding="utf-8")
    context_pipeline._render_context_navigation(context_root)
    context_pipeline._validate_description_navigation(
        context_root,
        final_context_root=context_root,
        source_root=source_root,
    )
    original_b = description_b.read_bytes()
    fixed_mtime_ns = 1_600_000_000_000_000_000
    os.utime(description_b, ns=(fixed_mtime_ns, fixed_mtime_ns))
    original_signature = context_pipeline._directory_content_signature

    def scoped_signature(directory: Path, *, context_root: Path | None = None) -> str:
        if directory == topic_b:
            raise AssertionError("unrelated branch B must not be rendered")
        return original_signature(directory, context_root=context_root)

    monkeypatch.setattr(context_pipeline, "_directory_content_signature", scoped_signature)

    context_pipeline._apply_context_reclustering(
        context_root,
        source_root=source_root,
        mapping={"主题A/页面.md": "主题A新/页面.md"},
    )

    assert description_b.read_bytes() == original_b
    assert description_b.stat().st_mtime_ns == fixed_mtime_ns
    root_description = (context_root / "description.md").read_text(encoding="utf-8")
    assert "主题A/description.md" not in root_description
    assert "主题A新/description.md" in root_description
    assert "主题B/description.md" in root_description
    assert "页面.md" in (context_root / "主题A新" / "description.md").read_text(encoding="utf-8")
    context_pipeline._validate_description_navigation(
        context_root,
        final_context_root=context_root,
        source_root=source_root,
    )


def test_local_recluster_does_not_rewrite_unchanged_ancestor_description(tmp_path: Path) -> None:
    context_root = tmp_path / "context"
    source_root = tmp_path / "source-meta"
    source_root.mkdir()
    context_root.mkdir()
    (context_root / "description.md").write_text("# Context\n", encoding="utf-8")
    for index in range(3):
        _write_cluster_page(
            context_root,
            f"主题A/页面{index}.md",
            title=f"智能体记忆{index}",
            body="主动上下文 智能体 记忆 检索",
        )
    context_pipeline._render_context_navigation(context_root)
    root_before = (context_root / "description.md").read_bytes()
    mapping = _plan_context_reclustering(
        context_root,
        changed_paths={"主题A/页面0.md"},
        max_pages_per_directory=2,
        max_subdirectories_per_directory=20,
    )

    context_pipeline._apply_context_reclustering(
        context_root,
        source_root=source_root,
        mapping=mapping,
    )

    assert (context_root / "description.md").read_bytes() == root_before


@pytest.mark.skipif(os.name != "nt", reason="Windows extended-path regression")
def test_recluster_supports_existing_windows_path_longer_than_max_path(tmp_path: Path) -> None:
    context_root = tmp_path / "context"
    source_root = tmp_path / "source-meta"
    context_root.mkdir()
    source_root.mkdir()
    (context_root / "description.md").write_text("# Context\n", encoding="utf-8")
    long_parts = tuple(f"旧层{index}-" + ("x" * 60) for index in range(4))
    long_filename = ("长页面" * 20) + "回归.md"
    old_relative = Path(*long_parts, long_filename).as_posix()
    old_page = context_root.joinpath(*long_parts, long_filename)
    context_pipeline._atomic_write(
        context_root.joinpath(*long_parts, "description.md"),
        "# 长目录\n".encode(),
    )
    context_pipeline._atomic_write(old_page, "# 完整长标题\n\n长路径正文。\n".encode())
    assert len(str(old_page)) > 260

    context_pipeline._apply_context_reclustering(
        context_root,
        source_root=source_root,
        mapping={old_relative: "短目录/短页面.md"},
    )

    assert context_pipeline._path_is_file(context_root / "短目录" / "短页面.md")
    assert not context_pipeline._path_exists(old_page)


@pytest.mark.skipif(os.name != "nt", reason="Windows extended-path regression")
def test_navigation_and_capacity_validation_support_deep_existing_windows_path(tmp_path: Path) -> None:
    context_root = tmp_path / "context"
    context_root.mkdir()
    long_parts = tuple(f"既有层{index}-" + ("x" * 60) for index in range(4))
    page = context_root.joinpath(*long_parts, "页面.md")
    context_pipeline._atomic_write(context_root / "description.md", "# Context\n".encode())
    context_pipeline._atomic_write(page, "# 页面\n\n既有长路径正文。\n".encode())
    assert len(str(page.parent)) > 260

    context_pipeline._render_context_navigation(context_root)
    context_pipeline._validate_candidate(
        context_root,
        max_pages_per_directory=20,
        max_subdirectories_per_directory=20,
    )


@pytest.mark.skipif(os.name != "nt", reason="Windows extended-path regression")
def test_candidate_copy_and_publish_support_deep_existing_windows_path(tmp_path: Path) -> None:
    official = tmp_path / "official"
    candidate = tmp_path / "candidate"
    published = tmp_path / "published"
    long_parts = tuple(f"发布层{index}-" + ("x" * 60) for index in range(4))
    page = official.joinpath(*long_parts, "页面.md")
    context_pipeline._atomic_write(page, "# 页面\n\n长路径发布正文。\n".encode())
    assert len(str(page.parent)) > 260

    context_pipeline._copy_tree(official, candidate)
    removed = context_pipeline._copy_and_publish_tree(candidate, published)

    published_page = published.joinpath(*long_parts, "页面.md")
    assert removed == set()
    assert context_pipeline._extended_path(published_page).read_text(encoding="utf-8").endswith("长路径发布正文。\n")


@pytest.mark.skipif(os.name != "nt", reason="Windows extended-path regression")
def test_agent_candidate_validation_supports_deep_existing_windows_path(tmp_path: Path) -> None:
    context_root = tmp_path / "context"
    source_root = tmp_path / "source-meta"
    context_root.mkdir()
    source_root.mkdir()
    long_parts = tuple(f"校验层{index}-" + ("x" * 60) for index in range(4))
    page = context_root.joinpath(*long_parts, "页面.md")
    context_pipeline._atomic_write(context_root / "description.md", "# Context\n".encode())
    context_pipeline._atomic_write(page, "# 页面\n\n既有长路径正文。\n".encode())
    context_pipeline._render_context_navigation(context_root)
    baseline = context_pipeline._snapshot_managed_files(context_root)
    assert len(str(page.parent)) > 260

    context_pipeline._validate_agent_candidate(
        context_root,
        baseline=baseline,
        changed_paths=set(),
        baseline_root=context_root,
        final_context_root=context_root,
        source_root=source_root,
        baseline_managed_pages_by_source={},
        max_pages_per_directory=20,
        max_subdirectories_per_directory=20,
    )


@pytest.mark.skipif(os.name != "nt", reason="Windows extended-path regression")
def test_agent_change_detection_supports_deep_existing_windows_path(tmp_path: Path) -> None:
    context_root = tmp_path / "context"
    context_root.mkdir()
    long_parts = tuple(f"变更层{index}-" + ("x" * 60) for index in range(4))
    page = context_root.joinpath(*long_parts, "页面.md")
    context_pipeline._atomic_write(page, "# 页面\n\n旧正文。\n".encode())
    baseline = context_pipeline._snapshot_managed_files(context_root)
    context_pipeline._atomic_write(page, "# 页面\n\n新正文。\n".encode())
    assert len(str(page.parent)) > 260

    assert context_pipeline._agent_updated_context_knowledge_page(
        context_root,
        baseline_root=context_root,
        baseline=baseline,
    )


@pytest.mark.skipif(os.name != "nt", reason="Windows extended-path regression")
def test_agent_sandbox_reset_supports_deep_existing_windows_path(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    context_root = sandbox / "context"
    context_root.mkdir(parents=True)
    long_parts = tuple(f"清理层{index}-" + ("x" * 60) for index in range(4))
    page = context_root.joinpath(*long_parts, "页面.md")
    context_pipeline._atomic_write(page, "# 页面\n\n待清理正文。\n".encode())
    assert len(str(page.parent)) > 260

    context_pipeline._reset_filesystem_sandbox(sandbox)

    assert not context_pipeline._path_exists(context_root)


@pytest.mark.skipif(os.name != "nt", reason="Windows extended-path regression")
def test_layout_normalization_preserves_deep_existing_windows_path(tmp_path: Path) -> None:
    context_root = tmp_path / "context"
    source_root = tmp_path / "source-meta"
    context_root.mkdir()
    source_root.mkdir()
    long_parts = tuple(f"保留层{index}-" + ("x" * 60) for index in range(4))
    page = context_root.joinpath(*long_parts, "页面.md")
    context_pipeline._atomic_write(context_root / "description.md", "# Context\n".encode())
    context_pipeline._atomic_write(page, "# 页面\n\n既有长路径正文。\n".encode())
    context_pipeline._render_context_navigation(context_root)
    relative = page.relative_to(context_root).as_posix()
    assert len(str(page.parent)) > 260

    baseline, baseline_paths = context_pipeline._normalize_context_candidate(
        context_root,
        source_root=source_root,
        run_time=datetime(2026, 9, 4, tzinfo=timezone.utc),
        max_pages_per_directory=20,
        max_subdirectories_per_directory=20,
    )

    assert relative in baseline
    assert baseline_paths[relative] == relative
    assert context_pipeline._path_is_file(page)


@pytest.mark.skipif(os.name != "nt", reason="Windows extended-path regression")
def test_balanced_page_move_supports_deep_unrelated_windows_path(tmp_path: Path) -> None:
    context_root = tmp_path / "context"
    source_root = tmp_path / "source-meta"
    context_root.mkdir()
    source_root.mkdir()
    source_page = context_root / "旧主题" / "旧页面.md"
    target_page = context_root / "新主题" / "新页面.md"
    long_parts = tuple(f"移动层{index}-" + ("x" * 60) for index in range(4))
    deep_page = context_root.joinpath(*long_parts, "稳定页.md")
    context_pipeline._atomic_write(source_page, "# 旧页面\n\n旧正文。\n".encode())
    context_pipeline._atomic_write(deep_page, "# 稳定页\n\n既有长路径正文。\n".encode())
    assert len(str(deep_page.parent)) > 260

    context_pipeline._move_balanced_page(
        context_root,
        source_root=source_root,
        source_page=source_page,
        target_page=target_page,
        enriched_markdown="# 新页面\n\n新正文。\n",
    )

    assert context_pipeline._path_is_file(target_page)
    assert context_pipeline._path_is_file(deep_page)


def _write_legacy_source_page(path: Path, *, title: str, source_id: str, extra: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"# {title}\n\n## 摘要\n\n旧摘要。\n\n## 正文\n\n正文。\n\n"
        f"[来源1](../../../source-meta/{source_id}.md)\n{extra}",
        encoding="utf-8",
    )


def _register_migration_source(source_root: Path, *, provider: str, suffix: str, title: str) -> str:
    item = RawChangeItem(
        logical_id=f"{provider}/{suffix}",
        revision_id="rev-1",
        operation="upsert",
        title=title,
        content=f"{title}正文",
        original_ref=f"https://example.test/{provider}/{suffix}",
        metadata={"kind": "document"},
    )
    return upsert_source_metadata(
        source_root,
        item,
        provider=provider,
        service_id=f"{provider}-one",
        observed_at="2026-09-02T00:00:00+00:00",
    )


@pytest.mark.skipif(os.name != "nt", reason="Windows extended-path regression")
def test_short_reference_resolution_supports_deep_existing_windows_path(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    context_root = workspace / "context"
    source_root = workspace / "source-meta"
    context_root.mkdir(parents=True)
    source_root.mkdir()
    source_id = _register_migration_source(
        source_root,
        provider="feishu",
        suffix="long-reference",
        title="长路径来源",
    )
    long_parts = tuple(f"引用层{index}-" + ("x" * 60) for index in range(4))
    page = context_root.joinpath(*long_parts, "页面.md")
    context_pipeline._atomic_write(page, "# 页面\n\n[[ref:0]]\n".encode())
    assert len(str(page.parent)) > 260

    context_pipeline._resolve_short_references(
        context_root,
        final_context_root=context_root,
        source_root=source_root,
        alias_targets={"[[ref:0]]": source_id},
    )

    markdown = context_pipeline._extended_path(page).read_text(encoding="utf-8")
    assert "[[ref:" not in markdown
    assert f"{source_id}.md" in markdown


def test_legacy_layout_migration_is_deterministic_and_preserves_source_meta_bytes(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    context_root = workspace / "context"
    source_root = workspace / "source-meta"
    context_root.mkdir(parents=True)
    first_source = _register_migration_source(
        source_root,
        provider="feishu",
        suffix="context-layout",
        title="主动上下文目录治理",
    )
    second_source = _register_migration_source(
        source_root,
        provider="github",
        suffix="context-links",
        title="主动上下文相关文档",
    )
    user_source = _register_migration_source(
        source_root,
        provider="local_files",
        suffix="user-page",
        title="用户保留页",
    )
    source_snapshot = _source_meta_snapshot(source_root)

    first_digest = "1" * 32
    second_digest = "2" * 32
    legacy_service = context_root / "sources" / "mixed-service"
    _write_legacy_source_page(
        legacy_service / f"{first_digest}.md",
        title="主动上下文目录治理",
        source_id=first_source,
        extra=f"\n[相关旧页]({second_digest}.md)\n",
    )
    _write_legacy_source_page(
        legacy_service / f"{second_digest}.md",
        title="主动上下文相关文档",
        source_id=second_source,
    )
    (legacy_service / "description.md").write_text(
        f"# mixed-service 来源\n\n## 来源页\n\n- [目录治理]({first_digest}.md)\n- [相关文档]({second_digest}.md)\n",
        encoding="utf-8",
    )
    (context_root / "sources" / "description.md").write_text(
        "# 来源导航\n\n## 服务\n\n- [mixed-service](mixed-service/description.md)\n",
        encoding="utf-8",
    )
    (context_root / "sources" / "保留.md").write_text(
        f"# 用户自建 sources 页面\n\n不得删除。\n\n[来源](../../source-meta/{user_source}.md)\n",
        encoding="utf-8",
    )

    managed_topic = context_root / "topics" / "主动上下文"
    managed_topic.mkdir(parents=True)
    (managed_topic / "description.md").write_text(
        "# 主动上下文\n\n<!-- personal-context:managed-topic -->\n\n"
        "<!-- personal-context:source-links:start -->\n## PersonalContext 来源关联\n\n"
        f"- [目录治理](../../sources/mixed-service/{first_digest}.md)\n"
        f"- [相关文档](../../sources/mixed-service/{second_digest}.md)\n"
        "<!-- personal-context:source-links:end -->\n",
        encoding="utf-8",
    )
    (context_root / "topics" / "description.md").write_text(
        "# 主题导航\n\n<!-- personal-context:topic-links:start -->\n"
        "## PersonalContext 受控主题\n\n- [主动上下文](主动上下文/description.md)\n"
        "<!-- personal-context:topic-links:end -->\n",
        encoding="utf-8",
    )

    (context_root / "根页面一.md").write_text(
        f"# 健身计划\n\n[饮食建议](根页面二.md)\n\n[来源](../source-meta/{first_source}.md)\n",
        encoding="utf-8",
    )
    (context_root / "根页面二.md").write_text(
        f"# 饮食建议\n\n[健身计划](根页面一.md#安排)\n\n[来源](../source-meta/{second_source}.md)\n",
        encoding="utf-8",
    )
    (context_root / "description.md").write_text(
        "# PersonalContext\n\n<!-- personal-context:navigation:start -->\n"
        "## PersonalContext 导航\n\n- [按来源](sources/description.md)\n"
        "- [按主题](topics/description.md)\n<!-- personal-context:navigation:end -->\n",
        encoding="utf-8",
    )

    first_mapping = context_pipeline._plan_context_layout_normalization(
        context_root,
        source_root=source_root,
        run_time=datetime(2026, 9, 2, tzinfo=timezone.utc),
    )
    second_mapping = context_pipeline._plan_context_layout_normalization(
        context_root,
        source_root=source_root,
        run_time=datetime(2026, 9, 2, tzinfo=timezone.utc),
    )

    assert second_mapping == first_mapping
    assert {"根页面一.md", "根页面二.md"}.issubset(first_mapping)
    assert f"sources/mixed-service/{first_digest}.md" in first_mapping
    assert f"sources/mixed-service/{second_digest}.md" in first_mapping
    context_pipeline._apply_context_layout_normalization(
        context_root,
        source_root=source_root,
        mapping=first_mapping,
    )

    assert _source_meta_snapshot(source_root) == source_snapshot
    assert sorted(path.name for path in context_root.iterdir() if path.is_file()) == ["description.md"]
    assert (context_root / "sources" / "保留.md").read_text(encoding="utf-8").startswith("# 用户自建 sources 页面")
    assert not (context_root / "sources" / "mixed-service").exists()
    assert not (context_root / "topics").exists()
    managed_pages = context_pipeline._managed_pages_by_source(context_root)
    assert set(managed_pages) == {first_source, second_source}
    assert {path.parent.name for path in managed_pages.values()} == {"主动上下文"}
    moved_first = context_root / first_mapping["根页面一.md"]
    moved_second = context_root / first_mapping["根页面二.md"]
    assert f"../{moved_second.parent.name}/{moved_second.name}" in moved_first.read_text(encoding="utf-8")
    assert f"../{moved_first.parent.name}/{moved_first.name}#安排" in moved_second.read_text(encoding="utf-8")
    context_pipeline._validate_reference_graph(
        context_root,
        final_context_root=context_root,
        source_root=source_root,
        repairable=False,
    )


def test_normalize_migrates_uppercase_legacy_machine_page_with_case_sensitive_glob(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    context_root = workspace / "context"
    source_root = workspace / "source-meta"
    context_root.mkdir(parents=True)
    source_root.mkdir()
    source_id = _register_migration_source(
        source_root,
        provider="feishu",
        suffix="uppercase-legacy-page",
        title="主动上下文目录治理",
    )
    source_snapshot = _source_meta_snapshot(source_root)
    digest = "a" * 32
    legacy_service = context_root / "sources" / "legacy-service"
    legacy_page = legacy_service / f"{digest}.MD"
    _write_legacy_source_page(
        legacy_page,
        title="主动上下文目录治理",
        source_id=source_id,
    )
    (legacy_service / "description.md").write_text(
        f"# legacy-service 来源\n\n## 来源页\n\n- [目录治理]({digest}.MD)\n",
        encoding="utf-8",
    )
    (context_root / "sources" / "description.md").write_text(
        "# 来源导航\n\n## 服务\n\n- [legacy-service](legacy-service/description.md)\n",
        encoding="utf-8",
    )
    (context_root / "description.md").write_text(
        "# PersonalContext\n\n"
        "<!-- personal-context:navigation:start -->\n"
        "## PersonalContext 导航\n\n"
        "- [按来源](sources/description.md)\n"
        "<!-- personal-context:navigation:end -->\n",
        encoding="utf-8",
    )
    original_glob = Path.glob

    def case_sensitive_glob(path: Path, pattern: str | os.PathLike[str], **options: bool | None) -> Iterable[Path]:
        candidates = original_glob(path, pattern, **options)
        if not os.fspath(pattern).endswith(".md"):
            return candidates
        return (candidate for candidate in candidates if candidate.suffix == ".md")

    monkeypatch.setattr(Path, "glob", case_sensitive_glob)

    context_pipeline._normalize_context_candidate(
        context_root,
        source_root=source_root,
        run_time=datetime(2026, 9, 5, tzinfo=timezone.utc),
    )

    assert _source_meta_snapshot(source_root) == source_snapshot
    assert not legacy_page.exists()
    assert not (context_root / "sources").exists()
    managed_pages = context_pipeline._managed_pages_by_source(context_root)
    assert set(managed_pages) == {source_id}
    moved_page = managed_pages[source_id]
    assert moved_page.parent != legacy_service
    assert moved_page.suffix == ".md"
    context_pipeline._validate_candidate(
        context_root,
        final_context_root=context_root,
        source_root=source_root,
    )


def test_legacy_source_migration_rejects_ambiguous_source_identity(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    context_root = workspace / "context"
    source_root = workspace / "source-meta"
    first_source = _register_migration_source(
        source_root,
        provider="feishu",
        suffix="first",
        title="第一来源",
    )
    second_source = _register_migration_source(
        source_root,
        provider="github",
        suffix="second",
        title="第二来源",
    )
    page = context_root / "sources" / "service" / ("a" * 32 + ".md")
    _write_legacy_source_page(
        page,
        title="歧义来源",
        source_id=first_source,
        extra=f"\n[来源2](../../../source-meta/{second_source}.md)\n",
    )

    with pytest.raises(BaseError, match="unique atomic source"):
        context_pipeline._plan_context_layout_normalization(
            context_root,
            source_root=source_root,
            run_time=datetime(2026, 9, 2, tzinfo=timezone.utc),
        )


def test_layout_migration_rejects_a_destination_occupied_by_another_moving_page(tmp_path: Path) -> None:
    context_root = tmp_path / "workspace" / "context"
    source_root = tmp_path / "workspace" / "source-meta"
    context_root.mkdir(parents=True)
    source_root.mkdir(parents=True)
    (context_root / "页面一.md").write_text("# 页面一\n", encoding="utf-8")
    (context_root / "页面二.md").write_text("# 页面二\n", encoding="utf-8")

    with pytest.raises(BaseError, match="overwrite an existing page"):
        context_pipeline._apply_context_layout_normalization(
            context_root,
            source_root=source_root,
            mapping={
                "页面一.md": "页面二.md",
                "页面二.md": "主题/页面二.md",
            },
        )


def _write_related_page(
    context_root: Path,
    relative: str,
    *,
    title: str,
    body: str,
    source_id: str | None = None,
) -> Path:
    page = context_root / relative
    page.parent.mkdir(parents=True, exist_ok=True)
    marker = f"\n<!-- personal-context-managed-source: {source_id} -->\n" if source_id is not None else ""
    page.write_text(f"# {title}\n{marker}\n## 内容\n\n{body}\n", encoding="utf-8")
    return page


def test_navigation_coverage_requires_every_direct_page_and_child_description(tmp_path: Path) -> None:
    context_root = tmp_path / "context"
    topic = context_root / "主题"
    topic.mkdir(parents=True)
    (context_root / "description.md").write_text("# Context\n\n[主题](主题/description.md)\n", encoding="utf-8")
    (topic / "description.md").write_text("# 主题\n\n[页面一](页面一.md)\n", encoding="utf-8")
    (topic / "页面一.md").write_text("# 页面一\n", encoding="utf-8")
    (topic / "页面二.md").write_text("# 页面二\n", encoding="utf-8")

    with pytest.raises(BaseError, match="description coverage is incomplete"):
        context_pipeline._validate_description_coverage(context_root)

    context_pipeline._render_context_navigation(context_root)

    context_pipeline._validate_description_coverage(context_root)
    context_pipeline._validate_context_root_layout(context_root)
    (context_root / "根页面.md").write_text("# 根页面\n", encoding="utf-8")
    with pytest.raises(BaseError, match="root may only contain description.md"):
        context_pipeline._validate_context_root_layout(context_root)


def test_navigation_coverage_supports_safe_parentheses_in_paths(tmp_path: Path) -> None:
    context_root = tmp_path / "context"
    topic = context_root / "Agent 技能(Skills)"
    topic.mkdir(parents=True)
    (context_root / "description.md").write_text("# Context\n", encoding="utf-8")
    (topic / "能力说明(新版).md").write_text("# 能力说明\n", encoding="utf-8")

    context_pipeline._render_context_navigation(context_root)

    assert "<Agent 技能(Skills)/description.md>" in (context_root / "description.md").read_text(encoding="utf-8")
    topic_description = topic / "description.md"
    assert "<能力说明(新版).md>" in topic_description.read_text(encoding="utf-8")
    context_pipeline._validate_description_coverage(context_root)

    topic_description.write_text(
        topic_description.read_text(encoding="utf-8").replace("<能力说明(新版).md>", "能力说明(新版).md"),
        encoding="utf-8",
    )
    context_pipeline._validate_description_navigation(context_root)


def test_related_documents_are_high_confidence_stable_and_limited(tmp_path: Path) -> None:
    context_root = tmp_path / "context"
    source_id = "src_" + "5" * 32
    managed = _write_related_page(
        context_root,
        "主题甲/目录治理.md",
        title="主动上下文目录治理",
        body=(
            "BM25 中文关键词用于主动上下文目录治理。\n\n"
            "<!-- personal-context-related:start -->\n## 相关文档\n\n"
            "- [旧外链](https://example.test/old)\n"
            "- [旧来源](../../source-meta/src_00000000000000000000000000000000.md)\n"
            "<!-- personal-context-related:end -->"
        ),
        source_id=source_id,
    )
    candidates = [
        _write_related_page(
            context_root,
            f"主题{label}/候选.md",
            title="主动上下文目录治理",
            body="BM25 中文关键词用于主动上下文目录治理。",
        )
        for label in ("乙", "丙", "丁", "戊")
    ]
    unrelated = _write_related_page(
        context_root,
        "运动/跑步.md",
        title="跑步训练",
        body="配速、心率与马拉松训练。",
    )

    first_changed = context_pipeline._refresh_related_documents(context_root)
    first = managed.read_text(encoding="utf-8")
    second_changed = context_pipeline._refresh_related_documents(context_root)

    assert managed.relative_to(context_root).as_posix() in first_changed
    assert second_changed == set()
    assert first.count("<!-- personal-context-related:start -->") == 1
    related_block = first.split("<!-- personal-context-related:start -->", 1)[1].split(
        "<!-- personal-context-related:end -->", 1
    )[0]
    expected = sorted(
        candidates, key=lambda path: (path.relative_to(context_root).as_posix().casefold(), path.as_posix())
    )
    assert related_block.count("- [") == 3
    for page in expected[:3]:
        target = os.path.relpath(page, start=managed.parent).replace("\\", "/")
        assert f"]({target})" in related_block
    rejected_target = os.path.relpath(expected[3], start=managed.parent).replace("\\", "/")
    assert rejected_target not in related_block
    assert unrelated.name not in related_block
    assert "source-meta" not in related_block
    assert "https://" not in related_block
    assert "BM25 中文关键词用于主动上下文目录治理。" in first
    assert all("personal-context-related" not in page.read_text(encoding="utf-8") for page in candidates)


def test_related_documents_remove_low_confidence_and_recompute_after_move_or_delete(tmp_path: Path) -> None:
    context_root = tmp_path / "context"
    managed = _write_related_page(
        context_root,
        "主题/受管页.md",
        title="主动上下文目录治理",
        body=(
            "BM25 中文关键词用于主动上下文目录治理。\n\n"
            "<!-- personal-context-related:start -->\n## 相关文档\n\n"
            "- [过期链接](../旧目录/不存在.md)\n"
            "<!-- personal-context-related:end -->"
        ),
        source_id="src_" + "6" * 32,
    )
    _write_related_page(
        context_root,
        "运动/跑步.md",
        title="跑步训练",
        body="配速、心率与马拉松训练。",
    )

    context_pipeline._refresh_related_documents(context_root)

    assert "personal-context-related" not in managed.read_text(encoding="utf-8")
    related = _write_related_page(
        context_root,
        "旧目录/相关页.md",
        title="主动上下文目录治理",
        body="BM25 中文关键词用于主动上下文目录治理。",
    )
    context_pipeline._refresh_related_documents(context_root)
    assert "../旧目录/相关页.md" in managed.read_text(encoding="utf-8")

    moved = context_root / "新目录" / related.name
    moved.parent.mkdir(parents=True)
    related.replace(moved)
    context_pipeline._refresh_related_documents(context_root)
    moved_text = managed.read_text(encoding="utf-8")
    assert "../旧目录/相关页.md" not in moved_text
    assert "../新目录/相关页.md" in moved_text

    moved.unlink()
    context_pipeline._refresh_related_documents(context_root)
    assert "personal-context-related" not in managed.read_text(encoding="utf-8")


def _rules_pipeline_config() -> PersonalContextConfig:
    return PersonalContextConfig.from_dict(
        {
            "collection_enabled": True,
            "agent_use_enabled": False,
            "strategy_profile": "rules",
            "fetch_services": [],
        }
    )


def _balanced_pipeline_config() -> PersonalContextConfig:
    return PersonalContextConfig.from_dict(
        {
            "collection_enabled": True,
            "agent_use_enabled": False,
            "strategy_profile": "balanced",
            "model_client": {
                "client_provider": "OpenAI",
                "api_key": "model-secret",
                "api_base": "https://model.invalid",
            },
            "model_request": {"model": "test"},
            "fetch_services": [],
        }
    )


@pytest.mark.asyncio
async def test_embedding_client_is_optional_bounded_and_caches_duplicate_texts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    constructor: list[tuple[EmbeddingConfig, dict[str, object]]] = []

    class FakeEmbedding:
        async def embed_documents(self, texts: list[str]) -> list[list[float]]:
            calls.append(texts)
            return [[float(index + 1), 1.0] for index in range(len(texts))]

    def build_embedding(config: EmbeddingConfig, **kwargs: object) -> FakeEmbedding:
        constructor.append((config, kwargs))
        return FakeEmbedding()

    monkeypatch.setattr(context_pipeline, "APIEmbedding", build_embedding)
    embedding_config = EmbeddingConfig(
        model_name="embedding-model",
        base_url="https://embedding.invalid/v1/embeddings",
        api_key="top-secret",
    )
    service = context_pipeline.ContextPipelineService(
        home=tmp_path,
        config=_rules_pipeline_config(),
        input_queue=asyncio.Queue(),
        embedding_config=embedding_config,
    )

    first = await service._embed_semantic_texts(["目录治理", "目录治理", "相关文档"])
    second = await service._embed_semantic_texts(["相关文档", "目录治理"])

    assert first == [[1.0, 1.0], [1.0, 1.0], [2.0, 1.0]]
    assert second == [[2.0, 1.0], [1.0, 1.0]]
    assert calls == [["目录治理", "相关文档"]]
    assert set(service._embedding_cache) == {
        hashlib.sha256("目录治理".encode()).hexdigest(),
        hashlib.sha256("相关文档".encode()).hexdigest(),
    }
    assert constructor == [
        (
            embedding_config,
            {
                "timeout": 15,
                "max_retries": 1,
                "max_batch_size": 8,
                "max_concurrent": 2,
            },
        )
    ]


def test_missing_embedding_configuration_does_not_initialize_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_embedding(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("APIEmbedding must not be initialized")

    monkeypatch.setattr(context_pipeline, "APIEmbedding", unexpected_embedding)
    service = context_pipeline.ContextPipelineService(
        home=tmp_path,
        config=_rules_pipeline_config(),
        input_queue=asyncio.Queue(),
    )

    assert service._embedding is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "vectors",
    [
        [],
        [[1.0, 0.0]],
        [[0.0, 0.0], [1.0, 0.0]],
        [[math.nan, 1.0], [1.0, 0.0]],
        [[math.inf, 1.0], [1.0, 0.0]],
        [[1.0], [1.0, 0.0]],
    ],
)
async def test_invalid_embedding_response_falls_back_without_caching(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    vectors: list[list[float]],
) -> None:
    class FakeEmbedding:
        async def embed_documents(self, texts: list[str]) -> list[list[float]]:
            del texts
            return vectors

    monkeypatch.setattr(context_pipeline, "APIEmbedding", lambda *_args, **_kwargs: FakeEmbedding())
    service = context_pipeline.ContextPipelineService(
        home=tmp_path,
        config=_rules_pipeline_config(),
        input_queue=asyncio.Queue(),
        embedding_config=EmbeddingConfig(model_name="model", base_url="https://embedding.invalid", api_key="secret"),
    )

    assert await service._embed_semantic_texts(["甲", "乙"]) is None
    assert service._embedding_cache == {}


@pytest.mark.asyncio
async def test_hybrid_ranking_only_reranks_sparse_top_eight_and_failure_is_sparse_only() -> None:
    query = {"query": 1.0}
    candidates = [{f"candidate-{index}": 1.0} for index in range(10)]
    sparse_scores = [1.0 - index / 10 for index in range(10)]

    async def valid_embeddings(texts: list[str]) -> list[list[float]]:
        assert texts == ["query", *(f"candidate-{index}" for index in range(8))]
        return [[1.0, 0.0], *([[1.0, 0.0]] * 8)]

    ranked = await context_pipeline._rank_hybrid_semantic_candidates(
        query,
        candidates,
        query_text="query",
        candidate_texts=[f"candidate-{index}" for index in range(10)],
        embed_texts=valid_embeddings,
        sparse_scores=sparse_scores,
    )

    assert [index for index, _score in ranked] == list(range(8))

    async def failed_embeddings(_texts: list[str]) -> None:
        raise TimeoutError("secret endpoint must not escape")

    fallback = await context_pipeline._rank_hybrid_semantic_candidates(
        query,
        candidates,
        query_text="query",
        candidate_texts=[f"candidate-{index}" for index in range(10)],
        embed_texts=failed_embeddings,
        sparse_scores=sparse_scores,
    )
    assert fallback == list(enumerate(sparse_scores))


@pytest.mark.asyncio
async def test_related_document_embeddings_are_limited_to_sparse_top_eight(tmp_path: Path) -> None:
    context_root = tmp_path / "context"
    directory = context_root / "目录治理"
    directory.mkdir(parents=True)
    source_id = "src_" + "a" * 32
    (directory / "受管页面.md").write_text(
        "# 主动上下文目录治理\n\n"
        f"<!-- personal-context-managed-source: {source_id} -->\n\n"
        "语义目录治理、容量控制与相关文档。\n",
        encoding="utf-8",
    )
    for index in range(9):
        (directory / f"候选{index}.md").write_text(
            f"# 主动上下文候选{index}\n\n语义目录治理、容量控制与相关文档。\n",
            encoding="utf-8",
        )
    calls: list[list[str]] = []

    async def embeddings(texts: list[str]) -> list[list[float]]:
        calls.append(texts)
        return [[1.0, 0.0] for _text in texts]

    await context_pipeline._refresh_related_documents_hybrid(context_root, embed_texts=embeddings)

    assert [len(texts) for texts in calls] == [9]
    markdown = (directory / "受管页面.md").read_text(encoding="utf-8")
    assert markdown.count("- [") == context_pipeline._RELATED_LIMIT


@pytest.mark.asyncio
async def test_embedding_failure_keeps_balanced_profile_and_one_model_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    class FailingEmbedding:
        async def embed_documents(self, texts: list[str]) -> list[list[float]]:
            del texts
            raise TimeoutError(
                "secret=embedding-secret endpoint=https://embedding.invalid text=主动上下文目录治理 vector=[1,2]"
            )

    monkeypatch.setattr(context_pipeline, "APIEmbedding", lambda *_args, **_kwargs: FailingEmbedding())
    service = context_pipeline.ContextPipelineService(
        home=tmp_path,
        config=_balanced_pipeline_config(),
        input_queue=asyncio.Queue(),
        embedding_config=EmbeddingConfig(
            model_name="embedding-model",
            base_url="https://embedding.invalid",
            api_key="embedding-secret",
        ),
    )
    context_root = tmp_path / "workspace" / "context"
    topic = context_root / "主动上下文"
    topic.mkdir(parents=True)
    (context_root / "description.md").write_text("# PersonalContext\n", encoding="utf-8")
    (topic / "description.md").write_text("# 主动上下文\n", encoding="utf-8")
    (topic / "已有页面.md").write_text("# 目录治理\n\n主动上下文目录治理。\n", encoding="utf-8")
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    model_attempts: list[int] = []
    original_balanced = service._filesystem_balanced_model_attempt
    monkeypatch.setattr(context_pipeline, "Model", _KeepRulesBalancedModel)

    async def balanced_attempt(**kwargs: object) -> tuple[set[str], int]:
        model_attempts.append(1)
        return await original_balanced(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(service, "_filesystem_balanced_model_attempt", balanced_attempt)
    processed: dict[str, object] = {
        "documents": [
            {
                "logical_id": "notes/context-layout",
                "revision_id": "rev-1",
                "title": "主动上下文目录治理",
                "markdown": "目录容量、语义归档和相关文档。\n",
            }
        ],
        "blocks": [],
        "deleted_ids": [],
        "actual_profile": "balanced",
    }
    logical_id = "notes/context-layout"
    source_id = upsert_source_metadata(
        tmp_path / "workspace" / "source-meta",
        RawChangeItem(
            logical_id=logical_id,
            revision_id="rev-1",
            operation="upsert",
            title="主动上下文目录治理",
            content="目录容量、语义归档和相关文档。",
            original_ref="https://example.test/context-layout",
            metadata={"kind": "document"},
        ),
        provider="local_files",
        service_id="local",
        observed_at="2026-09-02T00:00:00+00:00",
    )

    with caplog.at_level(logging.WARNING):
        result = await service._filesystem_with_fallback(
            processed=processed,
            sandbox=sandbox,
            batch=FetchBatch(batch_id="batch-1", items=[]),
            provider="local_files",
            source_ids_by_logical_id={logical_id: source_id},
            run_time=datetime(2026, 9, 2, tzinfo=timezone.utc),
        )

    assert result == "balanced"
    assert processed["actual_profile"] == "balanced"
    assert model_attempts == [1]
    assert service._embedding_fallback_active is True
    rendered_log = " ".join(record.getMessage() for record in caplog.records)
    assert "embedding-secret" not in rendered_log
    assert "embedding.invalid" not in rendered_log
    assert "主动上下文目录治理" not in rendered_log
    assert "[1,2]" not in rendered_log
