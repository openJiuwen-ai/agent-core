# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Temporary GC bodies retained for the sandboxes alive when they were removed.

This module is also imported by the standalone capacity worker. Keep imports
limited to the standard library and filelock, without loading the Agent runtime.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import time
import uuid
from contextlib import suppress
from pathlib import Path

from filelock import FileLock, Timeout

if __package__:
    from .path_safety import _extended_path
else:
    from path_safety import _extended_path

SandboxLease = tuple[str, Path | None]


def _safe_path(root: Path, relative: str = "") -> Path:
    relative = relative.replace("\\", "/")
    if Path(relative).anchor or ":" in relative or ".." in relative.split("/"):
        raise ValueError("invalid retirement path")
    path = _extended_path(Path(root).absolute() / relative)
    for item in (path, *path.parents):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("retirement path is a link")
    return path


_SANDBOX_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")


def _lease_path(home: Path, sandbox_id: str) -> Path:
    if not isinstance(sandbox_id, str) or _SANDBOX_ID.fullmatch(sandbox_id) is None:
        raise ValueError("invalid sandbox identity")
    return _safe_path(home, f"state/context-sandboxes/{sandbox_id}.lock")


_RUN_OWNER = re.compile(r"\.pcs-live-([1-9][0-9]{0,9})-([0-9a-f]{64}-[0-9a-f]{32})")


def is_run_owner_marker(name: str) -> bool:
    return _RUN_OWNER.fullmatch(name) is not None


def _pid_is_alive(pid: int) -> bool:
    if pid == os.getpid():
        return True
    if not 0 < pid <= 0xffffffff:
        raise ValueError("invalid sandbox owner")
    if os.name == "nt":
        import _winapi
        try:
            handle = _winapi.OpenProcess(_winapi.SYNCHRONIZE, False, pid)
        except OSError as exc:
            return exc.winerror != 87  # ERROR_INVALID_PARAMETER means no such process.
        try:
            return _winapi.WaitForSingleObject(handle, 0) != _winapi.WAIT_OBJECT_0
        finally:
            _winapi.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    return True


def register_sandbox(home: Path, sandbox_id: str | None = None) -> SandboxLease:
    """Publish a complete owner PID before reading the baseline, without a shared lock."""
    sandbox_id = uuid.uuid4().hex if sandbox_id is None else sandbox_id
    path = _lease_path(home, sandbox_id).with_suffix(".owner")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{os.getpid()}-{uuid.uuid4().hex}.pending")
    try:
        with temporary.open("x", encoding="ascii") as stream:
            stream.write(str(os.getpid()))
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise Timeout(str(path)) from exc
    finally:
        with suppress(OSError):
            temporary.unlink(missing_ok=True)
    return sandbox_id, path


def create_run_sandbox(home: Path, sandbox_id: str, sandbox: Path) -> tuple[Path, SandboxLease]:
    """Use the actual writable run directory as the owner if registration is unavailable."""
    stage_name = f".pcs-live-{os.getpid()}-{sandbox_id}"
    if not is_run_owner_marker(stage_name):
        raise ValueError("invalid run sandbox identity")
    physical_sandbox = _safe_path(sandbox)
    try:
        lease = register_sandbox(home, sandbox_id)
    except (OSError, ValueError):
        # The new directory carries its owner even before the fixed run name
        # becomes visible. Both layouts reserve the fixed name with mkdir.
        stage = physical_sandbox.parent / stage_name
        stage.mkdir(parents=True)
        try:
            physical_sandbox.mkdir()
            try:
                actual = stage.rename(physical_sandbox / stage.name)
            except OSError:
                physical_sandbox.rmdir()
                raise
        finally:
            with suppress(FileNotFoundError):
                stage.rmdir()
        return actual, (sandbox_id, None)
    try:
        physical_sandbox.mkdir(parents=True, exist_ok=False)
    except OSError:
        release_sandbox(lease)
        raise
    return sandbox, lease


def release_sandbox(lease: SandboxLease) -> None:
    """Release only this sandbox; optional maintenance removes unreferenced bodies."""
    _, marker = lease
    if marker is not None:
        try:
            marker.unlink(missing_ok=True)
        except OSError:
            # A shared reader can deny deletion on Windows. End this lifetime
            # without waiting for that reader or for the host process to exit.
            marker.with_suffix(".released").touch()


def _is_sandbox_active(home: Path, sandbox_id: str) -> bool:
    path = _lease_path(home, sandbox_id)
    if not path.exists():
        return False
    try:
        with FileLock(path, timeout=0, thread_local=False):
            return False
    except Timeout:
        return True


def _children(directory: Path):
    try:
        return list(directory.iterdir())
    except FileNotFoundError:
        return []


def _physical_owners(home: Path):
    root = _safe_path(home, "workspace/sandboxes")
    for service in _children(root):
        for run in _children(_safe_path(root, service.name)):
            match = _RUN_OWNER.fullmatch(run.name)
            if match:
                # Enumeration observed this owner even if it has since moved
                # into the fixed run directory. Do not re-open the old path.
                yield match
            else:
                for item in _children(_safe_path(service, run.name)):
                    match = _RUN_OWNER.fullmatch(item.name)
                    if match:
                        yield match


def run_may_be_active(home: Path, run_root: Path, active_ids: set[str]) -> bool:
    """Protect published runs and their temporary owner directory during startup."""
    match = _RUN_OWNER.fullmatch(run_root.name)
    if match:
        return _pid_is_alive(int(match[1]))
    relative = run_root.relative_to(home / "workspace/sandboxes").as_posix()
    prefix = hashlib.sha256(relative.encode("utf-8")).hexdigest() + "-"
    return any(sandbox_id.startswith(prefix) for sandbox_id in active_ids)


def is_sandbox_active(home: Path, sandbox_id: str) -> bool:
    """Probes outside the normal run may be conservative on inaccessible state."""
    _lease_path(home, sandbox_id)
    return sandbox_id in active_sandbox_ids(home)


def active_sandbox_ids(home: Path) -> tuple[str, ...]:
    """Snapshot active leases under the caller's context_update.lock."""
    directory = _safe_path(home, "state/context-sandboxes")
    active = set()
    for entry in _children(directory):
        if entry.suffix == ".owner":
            try:
                if _safe_path(directory, entry.stem + ".released").exists():
                    continue
                pid = int(_safe_path(directory, entry.name).read_text(encoding="ascii"))
            except FileNotFoundError:
                continue
            if _pid_is_alive(pid):
                active.add(entry.stem)
        elif entry.suffix == ".lock" and _is_sandbox_active(home, entry.stem):
            active.add(entry.stem)
    for owner in _physical_owners(home):
        if _pid_is_alive(int(owner[1])):
            active.add(owner[2])
    return tuple(sorted(active))


def _write_record(path: Path, record: dict) -> None:
    temporary = path.with_suffix(".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(record, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY, 0o700)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def _retire(home: Path, relative: str, active_ids: tuple[str, ...] | None, kind: str) -> dict:
    relative = relative.replace("\\", "/")
    if not relative or Path(relative).anchor or relative == ".":
        raise ValueError("invalid retired body")
    source = _safe_path(home, "workspace/context/" + relative)
    if kind == "page" and not stat.S_ISREG(source.stat().st_mode):
        raise ValueError("invalid retired body")
    if kind == "topic":
        if not source.is_dir() or [item.name for item in source.iterdir()] not in ([], ["description.md"]):
            raise ValueError("only an empty topic may be retired")
        description = _safe_path(source, "description.md")
        if description.exists() and not stat.S_ISREG(description.stat().st_mode):
            raise ValueError("invalid topic description")
    sandbox_ids = active_sandbox_ids(home) if active_ids is None else tuple(sorted(set(active_ids)))
    for sandbox_id in sandbox_ids:
        _lease_path(home, sandbox_id)
    directory = _safe_path(home, "state/context-retired")
    directory.mkdir(parents=True, exist_ok=True)
    if source.stat().st_dev != directory.stat().st_dev:
        raise ValueError("retired bodies must remain on the same filesystem")
    record_id = uuid.uuid4().hex
    record_path = _safe_path(directory, record_id + ".json")
    body_path = _safe_path(directory, record_id + ".body")
    _write_record(record_path, {
        "version": 1, "kind": kind, "relative_path": relative, "sandbox_ids": list(sandbox_ids),
    })
    os.replace(source, body_path)
    return {
        "relative_path": relative, "sandbox_ids": list(sandbox_ids),
        "record_path": str(record_path), "body_path": str(body_path),
    }


def retire_page(home: Path, relative: str, active_ids: tuple[str, ...] | None = None) -> dict:
    """Record intent, then atomically move one body on the same filesystem.

    The caller holds context_update.lock throughout this operation. A record with
    no .body is a prepared or already cleaned record, never an instruction to touch
    the original path. This module never restores an old body over published data.
    """
    return _retire(home, relative, active_ids, "page")


def retire_empty_topic(home: Path, relative: str, active_ids: tuple[str, ...] | None = None) -> dict:
    """Atomically retire a topic containing only description.md under the same lock."""
    return _retire(home, relative, active_ids, "topic")


def _remove_body(path: Path, kind: str) -> None:
    if not path.exists():
        return
    if kind == "page":
        path.unlink()
    elif kind == "topic":
        # A previous cleanup may already have removed the description. Never
        # recursively delete a body directory with any unexpected contents.
        names = [item.name for item in path.iterdir()]
        if names not in ([], ["description.md"]):
            raise ValueError("retired topic is not empty")
        _safe_path(path, "description.md").unlink(missing_ok=True)
        path.rmdir()
    else:
        raise ValueError("invalid retired body kind")


def _publication_waiting(state: Path) -> bool:
    try:
        with FileLock(_safe_path(state, "context_priority.lock"), timeout=0, thread_local=False):
            return False
    except Timeout:
        return True


def cleanup_retired(home: Path, *, seconds: float = 0.02, max_records: int = 32) -> int:
    """Best-effort bounded maintenance, never a prerequisite for sandbox release.

    Only this module's state paths are removed. Sharing the update lock prevents
    cleanup between the prepared record and the body move, including empty-ref
    records left by an interrupted worker. Malformed records remain untouched.
    """
    if seconds <= 0 or max_records <= 0:
        return 0
    deadline = time.monotonic() + seconds
    removed = 0
    try:
        state = _safe_path(home, "state")
        if not state.exists():
            return 0
        # Never hold the intent lock. Publishers must be able to signal priority
        # even when one filesystem operation in optional cleanup stalls.
        if not _publication_waiting(state):
            with FileLock(_safe_path(state, "context_update.lock"), timeout=0, thread_local=False):
                if _publication_waiting(state):
                    return 0
                directory = _safe_path(state, "context-retired")
                if directory.exists():
                    active_ids = None
                    with os.scandir(directory) as entries:
                        checked = 0
                        for entry in entries:
                            if checked >= max_records or time.monotonic() >= deadline:
                                break
                            checked += 1
                            if re.fullmatch(r"[0-9a-f]{32}\.(json|tmp)", entry.name) is None:
                                continue
                            try:
                                record_path = _safe_path(directory, entry.name)
                                body_path = _safe_path(directory, record_path.stem + ".body")
                                if record_path.suffix == ".tmp":
                                    if not body_path.exists():
                                        record_path.unlink(missing_ok=True)
                                    continue
                                record = json.loads(record_path.read_text(encoding="utf-8"))
                                ids = record["sandbox_ids"]
                                kind = record["kind"]
                                if (
                                    record.get("version") != 1 or not isinstance(ids, list)
                                    or kind not in {"page", "topic"}
                                ):
                                    continue
                                if not all(isinstance(item, str) and _SANDBOX_ID.fullmatch(item) for item in ids):
                                    continue
                                if ids and active_ids is None:
                                    try:
                                        active_ids = set(active_sandbox_ids(home))
                                    except (OSError, ValueError):
                                        break
                                if ids and any(sandbox_id in active_ids for sandbox_id in ids):
                                    continue
                                if time.monotonic() >= deadline:
                                    break
                                _remove_body(body_path, kind)
                                record_path.unlink(missing_ok=True)
                                removed += 1
                            except (OSError, ValueError, KeyError, TypeError):
                                continue
                leases = _safe_path(state, "context-sandboxes")
                if leases.exists():
                    with os.scandir(leases) as entries:
                        for index, entry in enumerate(entries):
                            if index >= max_records or time.monotonic() >= deadline:
                                break
                            if entry.name.endswith(".released"):
                                try:
                                    path = _safe_path(leases, entry.name)
                                    _safe_path(leases, path.stem + ".owner").unlink(missing_ok=True)
                                    path.unlink(missing_ok=True)
                                except OSError:
                                    continue
                            elif entry.name.endswith(".owner"):
                                try:
                                    path = _safe_path(leases, entry.name)
                                    if not _pid_is_alive(int(path.read_text(encoding="ascii"))):
                                        path.unlink(missing_ok=True)
                                except (OSError, ValueError):
                                    continue
                            elif entry.name.endswith(".pending"):
                                pending = re.fullmatch(r"[A-Za-z0-9_-]+\.([0-9]+)-[0-9a-f]{32}\.pending", entry.name)
                                if pending and not _pid_is_alive(int(pending[1])):
                                    _safe_path(leases, entry.name).unlink(missing_ok=True)
    except (OSError, ValueError, Timeout):
        pass
    return removed
