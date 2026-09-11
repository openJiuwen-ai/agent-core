"""Original-link identity is not a publication allowlist for arbitrary paths."""

import importlib
from pathlib import Path

import pytest

from openjiuwen.harness.personal_context.models import RawChangeItem
from openjiuwen.harness.personal_context.source_metadata import upsert_source_metadata


def module():
    return importlib.import_module("openjiuwen.harness.personal_context.source_link_book")


def test_same_relative_link_from_different_sources_has_different_identity():
    first, a = module().register_source_links("[Read](../README.md)", "C:/one/docs/a.md")
    second, b = module().register_source_links("[Read](../README.md)", "C:/two/docs/a.md")
    assert first != second
    assert next(iter(a.values()))["resolved_target"] == "c:\\one\\readme.md"
    assert next(iter(b.values()))["resolved_target"] == "c:\\two\\readme.md"


def test_known_target_rewrites_after_page_moves_and_unknown_target_is_text(tmp_path: Path):
    api = module()
    source = tmp_path / "sources"
    target = RawChangeItem(
        logical_id="readme",
        revision_id="r1",
        operation="upsert",
        title="Readme",
        content="content",
        original_ref="C:/docs/README.md",
        metadata={},
    )
    sid = upsert_source_metadata(
        source, target, provider="local_files", service_id="docs", observed_at="2026-09-10T00:00:00Z"
    )
    markdown, book = api.register_source_links("[Read](../README.md) and [Missing](../other.md)", "C:/docs/zh/a.md")
    context = tmp_path / "context"
    page = context / "moved" / "deep" / "a.md"
    page.parent.mkdir(parents=True)
    page.write_text(markdown, encoding="utf-8")
    api.resolve_source_links(context, final_context_root=context, source_root=source, book=book)
    result = page.read_text(encoding="utf-8")
    assert f"../../../sources/{sid}.md" in result
    assert "Missing（原文链接：../other.md）" in result
    assert "pcs-source-link:" not in result


def test_unknown_book_token_is_rejected(tmp_path: Path):
    page = tmp_path / "page.md"
    page.write_text("[invented](pcs-source-link:" + "a" * 32 + ")", encoding="utf-8")
    with pytest.raises(Exception, match="unregistered source link"):
        module().resolve_source_links(tmp_path, final_context_root=tmp_path, source_root=tmp_path / "sources", book={})


def test_source_cannot_supply_a_program_token():
    markdown, book = module().register_source_links("[fake](pcs-source-link:" + "a" * 32 + ")", "C:/docs/a.md")
    assert "a" * 32 not in book
    assert "pcs-source-link:" not in next(iter(book.values()))["resolved_target"]


def test_link_registration_does_not_require_original_files(tmp_path: Path):
    markdown, book = module().register_source_links("[x](../does-not-exist.md)", str(tmp_path / "missing" / "a.md"))
    assert len(book) == 1
    assert "pcs-source-link:" in markdown


def test_raw_program_like_link_cannot_impersonate_a_registered_target(tmp_path: Path):
    api = module()
    text, book = api.register_source_links("[fake](pcs-source-link:" + "a" * 32 + ")", "C:/docs/a.md")
    page = tmp_path / "a.md"
    page.write_text(text, encoding="utf-8")
    api.resolve_source_links(tmp_path, final_context_root=tmp_path, source_root=tmp_path / "sources", book=book)
    assert "pcs-source-link:" not in page.read_text(encoding="utf-8")
    assert "原文链接" in page.read_text(encoding="utf-8")


def test_link_tokens_in_code_examples_are_not_active(tmp_path: Path):
    literal = "`[example](pcs-source-link:" + "a" * 32 + ")`"
    page = tmp_path / "a.md"
    page.write_text(literal, encoding="utf-8")
    module().resolve_source_links(tmp_path, final_context_root=tmp_path, source_root=tmp_path / "sources", book={})
    assert page.read_text(encoding="utf-8") == literal


def test_record_tampering_is_rejected_before_filesystem_stage():
    _, book = module().register_source_links("[link](../README.md)", "C:/docs/zh/a.md")
    next(iter(book.values()))["resolved_target"] = "C:/other.md"
    with pytest.raises(Exception, match="source link record identity is invalid"):
        module().collect_source_link_book([{"source_links": book}])
