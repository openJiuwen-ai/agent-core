"""Local directory discovery contract, without models or cloud services."""
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from openjiuwen.core.single_agent.skills.skill_observer import SkillDirectoryObserver, read_skill_document
from openjiuwen.harness.rails.skills.skill_use_rail import SkillUseRail


def skill(root: Path, name="example", content=b"---\nname: example\ndescription: test\n---\nhello"):
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_bytes(content)
    return directory


@pytest.mark.asyncio
async def test_new_changed_and_same_content_are_distinct_even_with_same_mtime(tmp_path):
    events = []
    async def notify(event):
        events.append(event)
    observer = SkillDirectoryObserver(notify)
    directory = skill(tmp_path, content=b"first")
    await observer.scan([tmp_path])
    await observer.scan([tmp_path])
    assert len(events) == 1
    stamp = (directory / "SKILL.md").stat()
    (directory / "SKILL.md").write_bytes(b"other")
    os.utime(directory / "SKILL.md", ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    marker = object()
    await observer.scan([tmp_path], context=marker)
    assert [e.reason for e in events] == ["discovered", "changed"]
    assert events[0].snapshot.content == b"first"
    assert events[1].snapshot.content == b"other"
    assert events[1].context is marker


@pytest.mark.asyncio
async def test_missing_zip_only_and_oversized_documents_are_not_events(tmp_path):
    events = []
    async def notify(event):
        events.append(event)
    (tmp_path / "skill.zip").write_bytes(b"not extracted")
    skill(tmp_path, content=b"too much data")
    observer = SkillDirectoryObserver(notify, max_bytes=4)
    assert await observer.scan([tmp_path, tmp_path / "absent"]) == []
    assert events == []
    (tmp_path / "example" / "SKILL.md").write_bytes(b"ok")
    await observer.scan([tmp_path])
    assert len(events) == 1


@pytest.mark.asyncio
async def test_callback_failure_or_full_queue_can_retry(tmp_path):
    attempts = 0
    async def notify(_):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("host unavailable")
        return attempts > 2
    directory = skill(tmp_path)
    observer = SkillDirectoryObserver(notify)
    assert await observer.scan([directory]) == []
    assert await observer.scan([directory]) == []
    assert len(await observer.scan([directory])) == 1
    assert await observer.scan([directory]) == []
    assert attempts == 3


@pytest.mark.asyncio
async def test_background_scan_waits_for_stable_content(tmp_path, monkeypatch):
    now = [0.0]
    monkeypatch.setattr("openjiuwen.core.single_agent.skills.skill_observer.time.monotonic", lambda: now[0])
    events = []
    async def notify(event):
        events.append(event)
    directory = skill(tmp_path, content=b"partial")
    observer = SkillDirectoryObserver(notify)
    await observer.scan([tmp_path], settle_seconds=1)
    now[0] = .5
    (directory / "SKILL.md").write_bytes(b"complete")
    await observer.scan([tmp_path], settle_seconds=1)
    now[0] = 1.6
    await observer.scan([tmp_path], settle_seconds=1)
    assert [e.snapshot.content for e in events] == [b"complete"]


@pytest.mark.asyncio
async def test_rail_reload_and_class_loader_share_hook(tmp_path):
    events = []
    async def notify(event):
        events.append(event)
    skill(tmp_path)
    observer = SkillDirectoryObserver(notify)
    rail = SkillUseRail(str(tmp_path), include_tools=False, skill_observer=observer)
    await rail.reload_skills()
    assert [s.name for s in rail.skills] == ["example"]
    await SkillUseRail.load_skills_from_dir(str(tmp_path), skill_observer=observer)
    assert len(events) == 1


def test_snapshot_rejects_oversize_and_preserves_bytes(tmp_path):
    directory = skill(tmp_path, content="中文\r\n".encode())
    assert read_skill_document(directory).content == "中文\r\n".encode()
    with pytest.raises(ValueError, match="limit"):
        read_skill_document(directory, max_bytes=2)


def _with_ctime(stat, ctime_ns):
    return SimpleNamespace(
        st_dev=stat.st_dev, st_ino=stat.st_ino, st_size=stat.st_size,
        st_mtime_ns=stat.st_mtime_ns, st_ctime_ns=ctime_ns,
    )


def test_snapshot_allows_distinct_stat_and_fstat_ctime(tmp_path, monkeypatch):
    """Windows Python can expose different ctime values for the same file."""
    directory = skill(tmp_path, content=b"stable document")
    path_ctime = (directory / "SKILL.md").stat().st_ctime_ns
    original_fstat = os.fstat

    def handle_stat(fd):
        return _with_ctime(original_fstat(fd), path_ctime + 1_000_000_000)

    monkeypatch.setattr(os, "fstat", handle_stat)
    assert read_skill_document(directory).content == b"stable document"


@pytest.mark.parametrize("changed_source", ["handle", "path"])
def test_snapshot_rejects_ctime_change_within_same_stat_api(tmp_path, monkeypatch, changed_source):
    directory = skill(tmp_path)
    document = directory / "SKILL.md"
    original_fstat, original_stat = os.fstat, Path.stat
    handle_stat_calls = 0

    def handle_stat(fd):
        nonlocal handle_stat_calls
        handle_stat_calls += 1
        stat = original_fstat(fd)
        if changed_source == "handle" and handle_stat_calls == 2:
            return _with_ctime(stat, stat.st_ctime_ns + 1)
        return stat

    def path_stat(path, *args, **kwargs):
        stat = original_stat(path, *args, **kwargs)
        if changed_source == "path" and path == document and handle_stat_calls == 2:
            return _with_ctime(stat, stat.st_ctime_ns + 1)
        return stat

    monkeypatch.setattr(os, "fstat", handle_stat)
    monkeypatch.setattr(Path, "stat", path_stat)
    with pytest.raises(OSError, match="changed while being read"):
        read_skill_document(directory)


def test_snapshot_rejects_content_modified_during_read(tmp_path, monkeypatch):
    directory = skill(tmp_path, content=b"before")
    original_fstat = os.fstat
    handle_stat_calls = 0

    def handle_stat(fd):
        nonlocal handle_stat_calls
        handle_stat_calls += 1
        if handle_stat_calls == 2:
            with (directory / "SKILL.md").open("ab") as writer:
                writer.write(b" appended during read")
        return original_fstat(fd)

    monkeypatch.setattr(os, "fstat", handle_stat)
    with pytest.raises(OSError, match="changed while being read"):
        read_skill_document(directory)


def test_snapshot_rejects_replaced_file_with_same_size_and_mtime(tmp_path, monkeypatch):
    directory = skill(tmp_path, content=b"before")
    document = directory / "SKILL.md"
    replacement = directory / "replacement.md"
    replacement.write_bytes(b"after!")
    stamp = document.stat()
    os.utime(replacement, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    original_fstat, original_stat = os.fstat, Path.stat
    handle_stat_calls = 0
    replaced = False

    def handle_stat(fd):
        nonlocal handle_stat_calls
        handle_stat_calls += 1
        return original_fstat(fd)

    def path_stat(path, *args, **kwargs):
        nonlocal replaced
        if path == document and handle_stat_calls == 2 and not replaced:
            # Both reads from the open handle have completed; replace the name
            # before its final stat to exercise the path/handle identity check.
            os.replace(replacement, document)
            replaced = True
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(os, "fstat", handle_stat)
    monkeypatch.setattr(Path, "stat", path_stat)
    with pytest.raises(OSError, match="changed while being read"):
        read_skill_document(directory)
    assert replaced
