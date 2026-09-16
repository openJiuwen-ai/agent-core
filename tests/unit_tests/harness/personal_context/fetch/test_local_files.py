from __future__ import annotations

import asyncio
import errno
import hashlib
import io
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.harness.personal_context.config import PersonalContextFetchServiceConfig
from openjiuwen.harness.personal_context.fetch import local_files
from openjiuwen.harness.personal_context.fetch import retry as retry_module
from openjiuwen.harness.personal_context.fetch.cursor_selection import record_completed_candidates
from openjiuwen.harness.personal_context.fetch.local_files import LocalFilesFetchService
from openjiuwen.harness.personal_context.status_codes import StatusCode


def _config(
    root: Path,
    *,
    max_items: int | None = None,
    time_range: dict[str, object] | None = None,
) -> PersonalContextFetchServiceConfig:
    return PersonalContextFetchServiceConfig(
        service_id="notes",
        provider="local_files",
        enabled=True,
        interval_seconds=60,
        max_items_per_run=max_items,
        time_range=time_range or {"mode": "all"},
        source={"root_dir": str(root)},
        credentials={},
    )


async def _batches(
    service: LocalFilesFetchService,
    cursor: dict[str, object] | None = None,
    *,
    run_started_at: datetime | None = None,
):
    candidates = await service.prepare_run(
        run_id="run-1",
        run_started_at=run_started_at or datetime.now(UTC),
        cursor=cursor,
    )
    batches = [
        batch
        async for batch in service.fetch(
            run_id="run-1",
            cursor=cursor,
            candidates=candidates,
        )
    ]
    if batches:
        committed = record_completed_candidates(batches[-1].next_cursor, candidates)
        batches[-1] = batches[-1].model_copy(update={"next_cursor": committed})
    return batches


def _items(batches):
    return [item for batch in batches for item in batch.items]


async def _no_retry_sleep(_delay: float) -> None:
    return None


class _FakePdf:
    def __init__(self, text: str):
        self.pages = [SimpleNamespace(extract_text=lambda: text)]

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _StopAfterFirstPage:
    def __iter__(self):
        yield SimpleNamespace(extract_text=lambda: "x" * 2_000_000)
        raise AssertionError("PDF extraction should stop after reaching the content cap")


class _MultiPagePdf:
    def __init__(self):
        self.pages = _StopAfterFirstPage()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_local_files_reads_supported_extensions_and_preserves_json_text(tmp_path: Path, monkeypatch):
    root = tmp_path / "source"
    root.mkdir()
    (root / "note.md").write_text("# Note", encoding="utf-8")
    (root / "plain.txt").write_text("plain", encoding="utf-8")
    json_text = '{"valid": true, "items": [1, 2]}'
    (root / "data.json").write_text(json_text, encoding="utf-8")
    (root / "report.pdf").write_bytes(b"pdf")
    monkeypatch.setattr(local_files.pdfplumber, "open", lambda _path: _FakePdf("pdf text"))

    batches = asyncio.run(_batches(LocalFilesFetchService(_config(root), home=tmp_path / "home")))
    items = _items(batches)

    assert {Path(item.original_ref).suffix for item in items} == {".md", ".txt", ".json", ".pdf"}
    by_name = {Path(item.original_ref).name: item for item in items}
    assert by_name["data.json"].content == json_text
    assert by_name["report.pdf"].content == "pdf text"
    assert all(item.metadata["path"] == Path(item.original_ref).relative_to(root).as_posix() for item in items)
    assert all(item.raw_snapshot is not None for item in items)
    assert by_name["data.json"].raw_snapshot == json_text.encode("utf-8")


def test_local_files_metadata_scan_does_not_retain_file_content(tmp_path: Path):
    root = tmp_path / "source"
    root.mkdir()
    (root / "note.md").write_text("body", encoding="utf-8")

    candidates = local_files._scan_files(root)

    assert len(candidates) == 1
    assert "content" not in candidates[0]
    assert "raw_snapshot" not in candidates[0]


def test_local_files_only_materializes_selected_changes(tmp_path: Path, monkeypatch):
    root = tmp_path / "source"
    root.mkdir()
    for index in range(5):
        (root / f"{index}.txt").write_text(str(index), encoding="utf-8")
    service = LocalFilesFetchService(_config(root, max_items=2), home=tmp_path / "home")
    original_materialize = local_files._materialize_candidate
    materialized_paths: list[str] = []

    def tracking_materialize(candidate):
        materialized_paths.append(str(candidate["relative_path"]))
        return original_materialize(candidate)

    monkeypatch.setattr(local_files, "_materialize_candidate", tracking_materialize)
    batches = asyncio.run(_batches(service))

    assert len(materialized_paths) == 2
    assert set(materialized_paths) == {Path(item.original_ref).relative_to(root).as_posix() for item in _items(batches)}


def test_local_files_retries_one_busy_materialization(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "source"
    root.mkdir()
    (root / "note.md").write_text("hello", encoding="utf-8")
    service = LocalFilesFetchService(_config(root), home=tmp_path / "home")
    original = local_files._materialize_candidate
    calls = 0

    def flaky(candidate):
        nonlocal calls
        calls += 1
        if calls == 1:
            error = OSError(errno.EBUSY, "busy")
            raise local_files._file_error("local file read failed", error)
        return original(candidate)

    monkeypatch.setattr(local_files, "_materialize_candidate", flaky)
    monkeypatch.setattr(retry_module, "_sleep", _no_retry_sleep)
    monkeypatch.setattr(retry_module, "_jitter_seconds", lambda: 0.0)

    items = _items(asyncio.run(_batches(service)))

    assert calls == 2
    assert len(items) == 1
    assert items[0].content == "hello"


def test_local_files_does_not_retry_permission_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "source"
    root.mkdir()
    (root / "note.md").write_text("hello", encoding="utf-8")
    service = LocalFilesFetchService(_config(root), home=tmp_path / "home")
    calls = 0

    def denied(_candidate):
        nonlocal calls
        calls += 1
        error = PermissionError(errno.EACCES, "denied")
        raise local_files._file_error("local file read failed", error)

    monkeypatch.setattr(local_files, "_materialize_candidate", denied)
    monkeypatch.setattr(retry_module, "_sleep", _no_retry_sleep)

    with pytest.raises(BaseError) as caught:
        asyncio.run(_batches(service))

    assert calls == 1
    assert caught.value.status is StatusCode.CONTEXT_PROACTIVE_FILE_EXECUTION_ERROR


def test_local_files_marks_concurrent_read_change_as_retryable() -> None:
    stats = iter(
        (
            SimpleNamespace(st_size=4, st_mtime_ns=1),
            SimpleNamespace(st_size=4, st_mtime_ns=2),
        )
    )

    class ChangingPath:
        def stat(self):
            return next(stats)

        def open(self, _mode: str):
            return io.BytesIO(b"body")

    with pytest.raises(BaseError) as caught:
        local_files._read_checked(ChangingPath(), extension=".txt")  # type: ignore[arg-type]

    assert retry_module.classify_file_error(caught.value) == "file_changed"


def test_local_files_prepare_uses_mtime_time_ranges_and_reads_only_latest_selected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "time-source"
    root.mkdir()
    run_started_at = datetime(2026, 8, 10, 12, tzinfo=UTC)
    files: dict[str, Path] = {}
    for name, offset_days in (
        ("old.txt", -4),
        ("start.txt", -3),
        ("middle.txt", -1),
        ("end.txt", 0),
        ("future.txt", 1),
    ):
        path = root / name
        path.write_text(name, encoding="utf-8")
        timestamp = run_started_at + timedelta(days=offset_days)
        os.utime(path, ns=(int(timestamp.timestamp() * 1_000_000_000),) * 2)
        files[name] = path

    materialized: list[str] = []
    original_materialize = local_files._materialize_candidate

    def tracking_materialize(candidate):
        materialized.append(str(candidate["relative_path"]))
        return original_materialize(candidate)

    monkeypatch.setattr(local_files, "_materialize_candidate", tracking_materialize)
    recent = LocalFilesFetchService(
        _config(root, max_items=2, time_range={"mode": "recent", "recent_days": 3}),
        home=tmp_path / "recent-home",
    )

    candidates = asyncio.run(recent.prepare_run(run_id="recent", run_started_at=run_started_at, cursor=None))

    assert [candidate["stable_id"] for candidate in candidates] == [
        os.path.normcase(str(files["end.txt"].resolve())),
        os.path.normcase(str(files["middle.txt"].resolve())),
    ]
    assert all(candidate["resource_lane"] == "file" for candidate in candidates)
    assert all(candidate["revision_id"] for candidate in candidates)
    assert materialized == []

    batches = asyncio.run(recent.fetch(run_id="recent", cursor=None, candidates=candidates).__anext__())
    assert [item.title for item in batches.items] == ["end.txt", "middle.txt"]
    assert materialized == ["end.txt", "middle.txt"]

    fixed = LocalFilesFetchService(
        _config(
            root,
            time_range={
                "mode": "fixed",
                "start_at": (run_started_at - timedelta(days=3)).isoformat(),
                "end_at": run_started_at.isoformat(),
            },
        ),
        home=tmp_path / "fixed-home",
    )
    fixed_candidates = asyncio.run(fixed.prepare_run(run_id="fixed", run_started_at=run_started_at, cursor=None))
    assert {candidate["stable_id"] for candidate in fixed_candidates} == {
        os.path.normcase(str(files["start.txt"].resolve())),
        os.path.normcase(str(files["middle.txt"].resolve())),
    }

    all_files = LocalFilesFetchService(_config(root), home=tmp_path / "all-home")
    assert len(asyncio.run(all_files.prepare_run(run_id="all", run_started_at=run_started_at, cursor=None))) == 5


def test_local_files_recent_window_recomputes_and_removed_files_do_not_emit_delete(tmp_path: Path) -> None:
    root = tmp_path / "rolling-source"
    root.mkdir()
    path = root / "rolling.txt"
    path.write_text("rolling", encoding="utf-8")
    candidate_time = datetime(2026, 8, 8, 12, tzinfo=UTC)
    os.utime(path, ns=(int(candidate_time.timestamp() * 1_000_000_000),) * 2)
    service = LocalFilesFetchService(
        _config(root, time_range={"mode": "recent", "recent_days": 3}),
        home=tmp_path / "rolling-home",
    )

    first = asyncio.run(_batches(service, run_started_at=datetime(2026, 8, 10, 12, tzinfo=UTC)))
    cursor = first[-1].next_cursor
    assert [item.operation for item in _items(first)] == ["upsert"]
    assert cursor is not None

    path.unlink()
    second_candidates = asyncio.run(
        service.prepare_run(
            run_id="second",
            run_started_at=datetime(2026, 8, 12, 12, tzinfo=UTC),
            cursor=cursor,
        )
    )
    assert second_candidates == ()


def test_local_files_skips_fixed_directories_and_symlink_paths(tmp_path: Path):
    root = tmp_path / "source"
    root.mkdir()
    for dirname in (".git", ".venv", "node_modules", "__pycache__", ".personal_context"):
        ignored = root / dirname
        ignored.mkdir()
        (ignored / "ignored.md").write_text("ignored", encoding="utf-8")
    (root / "kept.md").write_text("kept", encoding="utf-8")

    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    try:
        (root / "outside.md").symlink_to(outside)
        (root / "link-dir").symlink_to(tmp_path / "outside-dir", target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable on this Windows runner")

    items = _items(asyncio.run(_batches(LocalFilesFetchService(_config(root), home=tmp_path / "home"))))
    assert [Path(item.original_ref).name for item in items] == ["kept.md"]


def test_local_files_detects_add_modify_ignores_delete_and_then_emits_empty_run(tmp_path: Path):
    root = tmp_path / "source"
    root.mkdir()
    first = root / "first.md"
    second = root / "second.txt"
    first.write_text("one", encoding="utf-8")
    second.write_text("two", encoding="utf-8")
    service = LocalFilesFetchService(_config(root), home=tmp_path / "home")

    initial = asyncio.run(_batches(service))
    cursor = initial[-1].next_cursor
    assert cursor is not None

    first.write_text("one changed", encoding="utf-8")
    second.unlink()
    (root / "third.json").write_text("{}", encoding="utf-8")
    changed_batches = asyncio.run(_batches(service, cursor))
    changed = _items(changed_batches)
    assert {(item.operation, Path(item.original_ref).name) for item in changed} == {
        ("upsert", "first.md"),
        ("upsert", "third.json"),
    }
    assert all(item.operation == "upsert" for item in changed)

    # The cursor returned from the completed changed run makes the following run empty.
    next_cursor = changed_batches[-1].next_cursor
    assert next_cursor is not None
    empty = asyncio.run(_batches(service, next_cursor))
    assert len(empty) == 1
    assert empty[0].items == ()


def test_local_files_first_run_sorts_mtime_descending_then_path(tmp_path: Path):
    root = tmp_path / "source"
    root.mkdir()
    older = root / "z.md"
    same_a = root / "a.md"
    same_b = root / "b.md"
    older.write_text("old", encoding="utf-8")
    same_a.write_text("a", encoding="utf-8")
    same_b.write_text("b", encoding="utf-8")
    os.utime(older, ns=(100, 100))
    os.utime(same_a, ns=(200, 200))
    os.utime(same_b, ns=(200, 200))

    items = _items(asyncio.run(_batches(LocalFilesFetchService(_config(root), home=tmp_path / "home"))))
    assert [Path(item.original_ref).name for item in items] == ["a.md", "b.md", "z.md"]


def test_local_files_batches_at_twenty_and_max_items_only_advances_emitted_items(tmp_path: Path):
    root = tmp_path / "source"
    root.mkdir()
    for index in range(45):
        (root / f"{index:02}.txt").write_text(str(index), encoding="utf-8")
    service = LocalFilesFetchService(_config(root, max_items=25), home=tmp_path / "home")

    batches = asyncio.run(_batches(service))
    assert [len(batch.items) for batch in batches] == [20, 5]
    assert all(len(batch.items) <= 20 for batch in batches)
    selection = batches[-1].next_cursor["_selection"]
    assert isinstance(selection, dict)
    assert len(selection["completed"]) == 25

    remaining = _items(asyncio.run(_batches(service, batches[-1].next_cursor)))
    assert len(remaining) == 20
    assert {Path(item.original_ref).name for item in remaining}.isdisjoint(
        {Path(item.original_ref).name for item in _items(batches)}
    )


def test_local_files_backlog_truncation_reuses_mtime_path_order(tmp_path: Path):
    root = tmp_path / "source"
    root.mkdir()
    for index in range(6):
        path = root / f"{index}.txt"
        path.write_text(str(index), encoding="utf-8")
        timestamp = 1_700_000_000_000_000_000 + index * 1_000_000_000
        os.utime(path, ns=(timestamp, timestamp))
    service = LocalFilesFetchService(_config(root, max_items=2), home=tmp_path / "home")

    first = asyncio.run(_batches(service))
    first_names = [Path(item.original_ref).name for item in _items(first)]
    second = asyncio.run(_batches(service, first[-1].next_cursor))
    second_names = [Path(item.original_ref).name for item in _items(second)]

    assert first_names == ["5.txt", "4.txt"]
    assert second_names == ["3.txt", "2.txt"]


def test_local_files_touch_reselects_file_but_keeps_content_revision(tmp_path: Path):
    root = tmp_path / "source"
    root.mkdir()
    path = root / "note.md"
    path.write_text("same", encoding="utf-8")
    service = LocalFilesFetchService(_config(root), home=tmp_path / "home")

    first = asyncio.run(_batches(service))
    original_revision = first[0].items[0].revision_id
    original_mtime = path.stat().st_mtime_ns
    os.utime(path, ns=(original_mtime + 1000, original_mtime + 1000))
    second = asyncio.run(_batches(service, first[0].next_cursor))

    assert original_revision == hashlib.sha256(b"same").hexdigest()
    assert len(second[0].items) == 1
    assert second[0].items[0].revision_id == original_revision


def test_local_files_rejects_text_larger_than_one_mib_before_reading(tmp_path: Path, monkeypatch):
    root = tmp_path / "source"
    root.mkdir()
    target = root / "large.txt"
    target.write_bytes(b"x" * (1024 * 1024 + 1))

    read_called = False

    def unexpected_read(_path: Path):
        nonlocal read_called
        read_called = True
        raise AssertionError("oversized file must be rejected before read_bytes")

    monkeypatch.setattr(Path, "read_bytes", unexpected_read)
    with pytest.raises(BaseError) as caught:
        asyncio.run(_batches(LocalFilesFetchService(_config(root), home=tmp_path / "home")))
    assert caught.value.status is StatusCode.CONTEXT_PROACTIVE_FILE_EXECUTION_ERROR
    assert read_called is False


def test_local_files_omits_large_raw_snapshot_but_keeps_content(tmp_path: Path, monkeypatch):
    root = tmp_path / "source"
    root.mkdir()
    content = b"x" * (2 * 1024 * 1024 + 1)
    target = root / "large.txt"
    target.write_bytes(content)

    # The text provider limit is one MiB, so use a PDF-sized path for the raw
    # snapshot boundary while keeping the extracted text small.
    target.unlink()
    pdf = root / "large.pdf"
    pdf.write_bytes(content)

    monkeypatch.setattr(local_files.pdfplumber, "open", lambda _path: _FakePdf("pdf text"))
    items = _items(asyncio.run(_batches(LocalFilesFetchService(_config(root), home=tmp_path / "home"))))

    item = items[0]
    assert item.content == "pdf text"
    assert item.raw_snapshot is None
    assert item.metadata["raw_snapshot_available"] is False


def test_local_files_truncates_pdf_content_at_raw_item_limit(tmp_path: Path, monkeypatch):
    root = tmp_path / "source"
    root.mkdir()
    (root / "large.pdf").write_bytes(b"pdf")
    long_text = "x" * 2_000_001
    monkeypatch.setattr(local_files.pdfplumber, "open", lambda _path: _FakePdf(long_text))

    items = _items(asyncio.run(_batches(LocalFilesFetchService(_config(root), home=tmp_path / "home"))))

    assert len(items) == 1
    assert len(items[0].content) == 2_000_000
    assert items[0].metadata["content_truncated"] is True


def test_local_files_pdf_extraction_stops_after_content_cap(tmp_path: Path, monkeypatch):
    root = tmp_path / "source"
    root.mkdir()
    (root / "large.pdf").write_bytes(b"pdf")
    monkeypatch.setattr(local_files.pdfplumber, "open", lambda _path: _MultiPagePdf())

    items = _items(asyncio.run(_batches(LocalFilesFetchService(_config(root), home=tmp_path / "home"))))

    assert len(items) == 1
    assert len(items[0].content) == 2_000_000


def test_local_files_replaced_external_symlink_is_ignored(tmp_path: Path):
    root = tmp_path / "source"
    root.mkdir()
    target = root / "note.md"
    target.write_text("inside", encoding="utf-8")
    service = LocalFilesFetchService(_config(root), home=tmp_path / "home")
    initial = asyncio.run(_batches(service))

    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    target.unlink()
    try:
        target.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable on this Windows runner")

    changed = _items(asyncio.run(_batches(service, initial[-1].next_cursor)))

    assert changed == []


def test_local_files_read_change_fails_entire_run_with_file_error(tmp_path: Path, monkeypatch):
    root = tmp_path / "source"
    root.mkdir()
    target = root / "note.md"
    target.write_text("before", encoding="utf-8")
    original_read = local_files._read_checked

    def changing_read(path: Path, *, extension: str, collect: bool):
        content = original_read(path, extension=extension, collect=collect)
        if path == target:
            path.write_text("after", encoding="utf-8")
        return content

    monkeypatch.setattr(local_files, "_read_checked", changing_read)
    with pytest.raises(BaseError) as caught:
        asyncio.run(_batches(LocalFilesFetchService(_config(root), home=tmp_path / "home")))
    assert caught.value.status is StatusCode.CONTEXT_PROACTIVE_FILE_EXECUTION_ERROR


def test_local_files_pdf_failure_is_wrapped_as_file_error(tmp_path: Path, monkeypatch):
    root = tmp_path / "source"
    root.mkdir()
    (root / "broken.pdf").write_bytes(b"broken")

    def fail(_path):
        raise ValueError("invalid pdf")

    monkeypatch.setattr(local_files.pdfplumber, "open", fail)
    with pytest.raises(BaseError) as caught:
        asyncio.run(_batches(LocalFilesFetchService(_config(root), home=tmp_path / "home")))
    assert caught.value.status is StatusCode.CONTEXT_PROACTIVE_FILE_EXECUTION_ERROR


def test_local_files_no_change_emits_empty_batch_without_creating_home_state(tmp_path: Path):
    root = tmp_path / "source"
    root.mkdir()
    (root / "note.md").write_text("same", encoding="utf-8")
    home = tmp_path / "home"
    service = LocalFilesFetchService(_config(root), home=home)
    first = asyncio.run(_batches(service))
    second = asyncio.run(_batches(service, first[-1].next_cursor))

    assert len(second) == 1
    assert second[0].items == ()
    assert second[0].next_cursor == first[-1].next_cursor
    assert not home.exists()


def test_local_files_rejects_legacy_files_cursor(tmp_path: Path):
    root = tmp_path / "source"
    root.mkdir()
    (root / "note.md").write_text("same", encoding="utf-8")
    cursor = {"files": {"note.md": {"revision_id": "legacy"}}}

    with pytest.raises(BaseError) as caught:
        asyncio.run(_batches(LocalFilesFetchService(_config(root), home=tmp_path / "home"), cursor))
    assert caught.value.status is StatusCode.CONTEXT_PROACTIVE_FETCH_EXECUTION_ERROR


def test_local_files_os_walk_onerror_is_file_error(tmp_path: Path, monkeypatch):
    root = tmp_path / "source"
    root.mkdir()

    def failing_walk(*_args, **kwargs):
        kwargs["onerror"](OSError("permission denied"))
        return iter(())

    monkeypatch.setattr(local_files.os, "walk", failing_walk)
    with pytest.raises(BaseError) as caught:
        asyncio.run(_batches(LocalFilesFetchService(_config(root), home=tmp_path / "home")))
    assert caught.value.status is StatusCode.CONTEXT_PROACTIVE_FILE_EXECUTION_ERROR


def test_local_files_logical_id_isolated_by_normalized_root(tmp_path: Path):
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_a.mkdir()
    root_b.mkdir()
    (root_a / "same.md").write_text("same", encoding="utf-8")
    (root_b / "same.md").write_text("same", encoding="utf-8")

    item_a = _items(asyncio.run(_batches(LocalFilesFetchService(_config(root_a), home=tmp_path / "home-a"))))[0]
    item_b = _items(asyncio.run(_batches(LocalFilesFetchService(_config(root_b), home=tmp_path / "home-b"))))[0]

    assert item_a.logical_id != item_b.logical_id
    assert item_a.logical_id.endswith(":same.md")
    assert item_b.logical_id.endswith(":same.md")


def test_local_files_continues_205_source_history_across_bounded_runs(tmp_path: Path):
    root = tmp_path / "source"
    root.mkdir()
    expected_names = {f"{index:03}.txt" for index in range(205)}
    for index, name in enumerate(sorted(expected_names)):
        path = root / name
        path.write_text(f"body-{index}", encoding="utf-8")
        timestamp = 1_700_000_000_000_000_000 - index * 1_000_000
        os.utime(path, ns=(timestamp, timestamp))

    service = LocalFilesFetchService(
        _config(root, max_items=100),
        home=tmp_path / "home",
    )
    cursor: dict[str, object] | None = None
    rounds = []
    for _round in range(3):
        batches = asyncio.run(_batches(service, cursor))
        rounds.append(_items(batches))
        assert batches
        cursor = batches[-1].next_cursor

    assert [len(items) for items in rounds] == [100, 100, 5]
    logical_ids = [item.logical_id for items in rounds for item in items]
    assert len(logical_ids) == len(set(logical_ids)) == 205
    assert {Path(item.original_ref).name for items in rounds for item in items} == expected_names
    assert cursor is not None
    selection = cursor["_selection"]
    assert isinstance(selection, dict)
    assert len(selection["completed"]) == len(expected_names)


def test_local_files_new_overflow_stays_ahead_of_history(tmp_path: Path):
    root = tmp_path / "overflow-source"
    root.mkdir()

    def write(name: str, timestamp: int) -> None:
        path = root / name
        path.write_text(name, encoding="utf-8")
        value = 1_700_000_000_000_000_000 + timestamp * 1_000_000
        os.utime(path, ns=(value, value))

    for timestamp in (3, 2, 1):
        write(f"base-{timestamp}.txt", timestamp)
    service = LocalFilesFetchService(_config(root, max_items=2), home=tmp_path / "overflow-home")
    first = asyncio.run(_batches(service))
    cursor = first[-1].next_cursor
    assert cursor is not None

    for timestamp in range(4, 9):
        write(f"new-{timestamp}.txt", timestamp)
    round_names: list[list[str]] = []
    for _ in range(3):
        batches = asyncio.run(_batches(service, cursor))
        round_names.append([Path(item.original_ref).name for item in _items(batches)])
        cursor = batches[-1].next_cursor
        assert cursor is not None

    assert round_names == [
        ["new-8.txt", "new-7.txt"],
        ["new-6.txt", "new-5.txt"],
        ["new-4.txt", "base-1.txt"],
    ]


def test_local_files_prioritizes_new_and_modified_sources_before_history(tmp_path: Path):
    root = tmp_path / "source"
    root.mkdir()
    base_timestamp = 1_700_000_000_000_000_000
    paths = [root / f"{index:03}.txt" for index in range(205)]
    for index, path in enumerate(paths):
        path.write_text(f"body-{index}", encoding="utf-8")
        timestamp = base_timestamp - index * 1_000_000
        os.utime(path, ns=(timestamp, timestamp))

    service = LocalFilesFetchService(
        _config(root, max_items=100),
        home=tmp_path / "home",
    )
    first = asyncio.run(_batches(service))
    first_items = _items(first)
    assert len(first_items) == 100
    first_cursor = first[-1].next_cursor
    assert first_cursor is not None

    new_paths = [root / "new-a.txt", root / "new-b.txt"]
    for index, path in enumerate(new_paths):
        path.write_text(f"new-{index}", encoding="utf-8")
        timestamp = base_timestamp + (index + 1) * 1_000_000
        os.utime(path, ns=(timestamp, timestamp))

    modified = paths[99]
    modified.write_text("modified-after-first-run", encoding="utf-8")
    old_timestamp = base_timestamp - 1_000_000_000
    os.utime(modified, ns=(old_timestamp, old_timestamp))

    second = asyncio.run(_batches(service, first_cursor))
    second_items = _items(second)
    assert len(second_items) == 100
    assert [Path(item.original_ref).name for item in second_items[:3]] == [
        "new-b.txt",
        "new-a.txt",
        modified.name,
    ]
    assert second_items[2].revision_id != first_items[-1].revision_id
