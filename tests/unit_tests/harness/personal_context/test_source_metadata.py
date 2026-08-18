from __future__ import annotations

import hashlib
import importlib
from pathlib import Path
from types import ModuleType

import pytest

from openjiuwen.harness.personal_context import PersonalContext
from openjiuwen.harness.personal_context.models import RawChangeItem


def _module() -> ModuleType:
    try:
        return importlib.import_module("openjiuwen.harness.personal_context.source_metadata")
    except ModuleNotFoundError:
        pytest.fail("source_metadata module is missing")


def _item(
    *,
    locator: str = "https://github.com/openjiuwen/agent-core/pull/42",
    title: str = "Improve PersonalContext",
    revision: str = "revision-1",
    content: str = "SOURCE-BODY-MUST-NOT-BE-PERSISTED",
) -> RawChangeItem:
    return RawChangeItem(
        logical_id=f"github:pull:{hashlib.sha256(locator.encode()).hexdigest()}",
        revision_id=revision,
        operation="upsert",
        title=title,
        content=content,
        original_ref=locator,
        metadata={"resource": "pull_request"},
        raw_snapshot='{"private":"RAW-MUST-NOT-BE-PERSISTED"}',
    )


def test_source_id_uses_trimmed_locator_and_128_bit_digest() -> None:
    module = _module()
    locator = "https://github.com/openjiuwen/agent-core/pull/42"

    assert module.normalize_source_locator(f"  {locator}\r\n") == locator
    assert module.source_id_for_locator(locator) == f"src_{hashlib.sha256(locator.encode()).hexdigest()[:32]}"


def test_source_locator_removes_url_credentials_query_and_fragment_but_preserves_local_path(tmp_path: Path) -> None:
    module = _module()
    local_path = str((tmp_path / "private note.md").resolve())

    assert (
        module.normalize_source_locator("https://user:pass@example.test/private/note?token=secret#fragment")
        == "https://example.test/private/note"
    )
    assert module.normalize_source_locator(local_path) == local_path


def test_source_metadata_uses_safe_url_locator_for_identity_and_storage(tmp_path: Path) -> None:
    module = _module()
    source_root = tmp_path / "source-meta"
    original = "https://user:pass@example.test/private/note?token=secret#fragment"
    safe = "https://example.test/private/note"

    source_id = module.upsert_source_metadata(
        source_root,
        _item(locator=original),
        provider="browser_bookmarks",
        service_id="bookmarks-a",
        observed_at="2026-08-12T00:00:00Z",
    )

    assert source_id == module.source_id_for_locator(safe)
    metadata = module.read_source_metadata(source_root / f"{source_id}.md")
    assert metadata["locator"] == safe
    assert "user:pass" not in str(metadata)
    assert "token=secret" not in str(metadata)


def test_source_metadata_deduplicates_locator_across_provider_and_service(tmp_path: Path) -> None:
    module = _module()
    source_root = tmp_path / "source-meta"
    item = _item()

    first = module.upsert_source_metadata(
        source_root,
        item,
        provider="github",
        service_id="github-a",
        observed_at="2026-08-12T00:00:00Z",
    )
    second = module.upsert_source_metadata(
        source_root,
        item.model_copy(update={"revision_id": "revision-2"}),
        provider="browser_bookmarks",
        service_id="bookmarks-a",
        observed_at="2026-08-12T01:00:00Z",
    )

    assert second == first
    assert [path.name for path in source_root.glob("*.md")] == [f"{first}.md"]
    metadata = module.read_source_metadata(source_root / f"{first}.md")
    assert metadata == {
        "source_id": first,
        "source_type": "pull_request",
        "title": "Improve PersonalContext",
        "locator": item.original_ref,
        "provider": "browser_bookmarks",
        "service": "bookmarks-a",
        "first_seen": "2026-08-12T00:00:00Z",
        "last_seen": "2026-08-12T01:00:00Z",
        "latest_revision": "revision-2",
        "latest_hash": hashlib.sha256(item.raw_snapshot.encode()).hexdigest(),
    }


def test_same_title_with_different_locator_creates_distinct_files(tmp_path: Path) -> None:
    module = _module()
    source_root = tmp_path / "source-meta"

    first = module.upsert_source_metadata(
        source_root,
        _item(locator="https://example.com/one", title="Same title"),
        provider="github",
        service_id="one",
        observed_at="2026-08-12T00:00:00Z",
    )
    second = module.upsert_source_metadata(
        source_root,
        _item(locator="https://example.com/two", title="Same title"),
        provider="github",
        service_id="two",
        observed_at="2026-08-12T00:00:00Z",
    )

    assert first != second
    assert {path.stem for path in source_root.glob("*.md")} == {first, second}


def test_source_metadata_markdown_contains_only_metadata(tmp_path: Path) -> None:
    module = _module()
    source_root = tmp_path / "source-meta"
    source_id = module.upsert_source_metadata(
        source_root,
        _item(),
        provider="github",
        service_id="github-a",
        observed_at="2026-08-12T00:00:00Z",
    )

    markdown = (source_root / f"{source_id}.md").read_text(encoding="utf-8")
    assert markdown.startswith("# Improve PersonalContext\n")
    assert "SOURCE-BODY-MUST-NOT-BE-PERSISTED" not in markdown
    assert "RAW-MUST-NOT-BE-PERSISTED" not in markdown
    assert "summary" not in markdown.casefold()
    assert "blocks" not in markdown.casefold()


def test_source_metadata_rejects_corruption_filename_mismatch_and_symlink(tmp_path: Path) -> None:
    module = _module()
    source_root = tmp_path / "source-meta"
    source_root.mkdir()
    corrupt = source_root / f"src_{'0' * 32}.md"
    corrupt.write_text("# broken\n", encoding="utf-8")

    with pytest.raises(PersonalContext.Error):
        module.read_source_metadata(corrupt)

    source_id = module.upsert_source_metadata(
        source_root,
        _item(locator="https://example.com/valid"),
        provider="github",
        service_id="github-a",
        observed_at="2026-08-12T00:00:00Z",
    )
    valid = source_root / f"{source_id}.md"
    mismatched = source_root / f"src_{'f' * 32}.md"
    mismatched.write_bytes(valid.read_bytes())
    with pytest.raises(PersonalContext.Error):
        module.read_source_metadata(mismatched)

    outside = tmp_path / "outside.md"
    outside.write_text("do not change", encoding="utf-8")
    symlink_locator = "https://example.com/symlink"
    symlink_id = module.source_id_for_locator(symlink_locator)
    link = source_root / f"{symlink_id}.md"
    try:
        link.symlink_to(outside)
    except (NotImplementedError, OSError):
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(PersonalContext.Error):
        module.upsert_source_metadata(
            source_root,
            _item(locator=symlink_locator),
            provider="github",
            service_id="github-a",
            observed_at="2026-08-12T00:00:00Z",
        )
    assert outside.read_text(encoding="utf-8") == "do not change"


@pytest.mark.parametrize("locator", ["", " ", "\r\n"])
def test_normalize_source_locator_rejects_blank(locator: str) -> None:
    with pytest.raises(PersonalContext.Error):
        _module().normalize_source_locator(locator)
