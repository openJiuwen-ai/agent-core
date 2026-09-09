"""Regression tests for preserving Markdown labels during page moves."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

import pytest

import openjiuwen.harness.personal_context.context_pipeline as context_pipeline


def _create_context(tmp_path: Path) -> tuple[Path, Path]:
    context_root = tmp_path / "context"
    source_root = tmp_path / "source-meta"
    context_root.mkdir()
    source_root.mkdir()
    (context_root / "description.md").write_text(
        "# Context\n\n[旧主题](old/description.md)\n",
        encoding="utf-8",
    )
    old_directory = context_root / "old"
    old_directory.mkdir()
    (old_directory / "description.md").write_text("# Old\n\n[a.md](a.md)\n", encoding="utf-8")
    (old_directory / "a.md").write_text("# A\n", encoding="utf-8")
    return context_root, source_root


def test_move_balanced_page_rewrites_only_destination_when_label_matches_old_path(tmp_path: Path) -> None:
    context_root, source_root = _create_context(tmp_path)
    source_page = context_root / "old" / "a.md"
    target_page = context_root / "new" / "b.md"

    context_pipeline._move_balanced_page(
        context_root,
        source_root=source_root,
        source_page=source_page,
        target_page=target_page,
        enriched_markdown=source_page.read_text(encoding="utf-8"),
    )

    description = (context_root / "old" / "description.md").read_text(encoding="utf-8")
    assert description == "# Old\n\n[a.md](../new/b.md)\n"
    assert context_pipeline._classify_reference_target(
        "../new/b.md",
        page_relative=PurePosixPath("old/description.md"),
        context_root=context_root,
        final_context_root=context_root,
        source_root=source_root,
        error=context_pipeline._pipeline_error,
    ) == ("context", "new/b.md")


def test_move_balanced_page_rewrites_same_directory_destination(tmp_path: Path) -> None:
    context_root, source_root = _create_context(tmp_path)
    source_page = context_root / "old" / "a.md"
    target_page = context_root / "old" / "b.md"

    context_pipeline._move_balanced_page(
        context_root,
        source_root=source_root,
        source_page=source_page,
        target_page=target_page,
        enriched_markdown=source_page.read_text(encoding="utf-8"),
    )

    description = (context_root / "old" / "description.md").read_text(encoding="utf-8")
    assert description == "# Old\n\n[a.md](b.md)\n"


@pytest.mark.parametrize(
    ("markdown", "expected"),
    [
        ("[old/a.md](old/a.md)", "[old/a.md](new/b.md)"),
        ("[prefix old/a.md suffix](old/a.md)", "[prefix old/a.md suffix](new/b.md)"),
        (
            "[old/a.md old/a.md](old/a.md)",
            "[old/a.md old/a.md](new/b.md)",
        ),
        ("[同名页面](old/a.md)", "[同名页面](new/b.md)"),
        ('[空格页面](<old/带 空格.md> "标题")', '[空格页面](<new/目标 空格.md> "标题")'),
        ("![old/a.md](old/a.md)", "![old/a.md](new/b.md)"),
        ("[old/a.md](https://example.test/old/a.md)", "[old/a.md](https://example.test/old/a.md)"),
        ("`[old/a.md](old/a.md)`", "`[old/a.md](old/a.md)`"),
        ("[old/a.md](old/a.md#标题)\r\n", "[old/a.md](new/b.md#标题)\r\n"),
    ],
)
def test_rewrite_context_markdown_links_preserves_labels_and_existing_exclusions(
    tmp_path: Path,
    markdown: str,
    expected: str,
) -> None:
    context_root = tmp_path / "context"
    source_root = tmp_path / "source-meta"
    (context_root / "old").mkdir(parents=True)
    (context_root / "new").mkdir()
    source_root.mkdir()
    for relative in ("old/a.md", "old/带 空格.md"):
        (context_root / relative).write_text("# Page\n", encoding="utf-8")

    rewritten = context_pipeline._rewrite_context_markdown_links(
        markdown,
        context_root=context_root,
        source_root=source_root,
        old_page_relative="description.md",
        new_page_relative="description.md",
        mapping={"old/a.md": "new/b.md", "old/带 空格.md": "new/目标 空格.md"},
    )

    assert rewritten == expected


def test_move_balanced_page_rewrites_links_again_after_a_second_move(tmp_path: Path) -> None:
    context_root, source_root = _create_context(tmp_path)
    first_source = context_root / "old" / "a.md"
    first_target = context_root / "new" / "b.md"
    context_pipeline._move_balanced_page(
        context_root,
        source_root=source_root,
        source_page=first_source,
        target_page=first_target,
        enriched_markdown=first_source.read_text(encoding="utf-8"),
    )
    assert (context_root / "old" / "description.md").read_text(encoding="utf-8") == ("# Old\n\n[a.md](../new/b.md)\n")

    second_target = context_root / "最终" / "页面.md"
    context_pipeline._move_balanced_page(
        context_root,
        source_root=source_root,
        source_page=first_target,
        target_page=second_target,
        enriched_markdown=first_target.read_text(encoding="utf-8"),
    )

    description = (context_root / "old" / "description.md").read_text(encoding="utf-8")
    assert description == "# Old\n\n[a.md](../最终/页面.md)\n"
    assert not first_source.exists()
    assert not first_target.exists()
    assert second_target.is_file()
    assert context_pipeline._classify_reference_target(
        "../最终/页面.md",
        page_relative=PurePosixPath("old/description.md"),
        context_root=context_root,
        final_context_root=context_root,
        source_root=source_root,
        error=context_pipeline._pipeline_error,
    ) == ("context", "最终/页面.md")
