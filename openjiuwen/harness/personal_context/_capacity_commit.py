# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Short-lived capacity commit worker, isolated so maintenance can be stopped.

Run by file path to avoid importing the Agent runtime in this small worker.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import time
from pathlib import Path
from typing import Callable

from filelock import FileLock, Timeout

if __package__:
    from .context_retirement import active_sandbox_ids, retire_empty_topic, retire_page
else:
    from context_retirement import active_sandbox_ids, retire_empty_topic, retire_page


def _safe_path(root: Path, relative: str = "", *, checked_parents: set[Path] | None = None) -> Path:
    path = root / relative
    if relative:
        relative_path = Path(relative)
        if relative_path.anchor or ".." in relative_path.parts or ":" in relative:
            raise ValueError("invalid maintenance path")
    for item in (path, *path.parents):
        if item != path and checked_parents is not None and item in checked_parents:
            break
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("maintenance path is a link")
        if checked_parents is not None and stat.S_ISDIR(info.st_mode):
            checked_parents.add(item)
    if os.name == "nt" and not str(path).startswith("\\\\?\\"):
        value = str(path.absolute())
        if value.startswith("\\\\"):
            return Path(os.path.join("\\\\?\\UNC", value[2:]))
        drive, tail = os.path.splitdrive(value)
        return Path(os.path.join(f"\\\\?\\{drive}", tail))
    return path


def _signature(path: Path) -> list[int]:
    info = path.stat()
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def _publication_waiting(state: Path) -> bool:
    """Observe normal publication intent across processes without waiting."""
    priority = state / "context_priority.lock"
    if not priority.exists():
        return False
    try:
        with FileLock(priority, timeout=0):
            return False
    except (Timeout, OSError):
        return True


def commit(
    manifest: dict, *, seconds: float, deadline: float | None = None,
    on_commit: Callable[[], None] | None = None,
) -> dict:
    """Apply only prepared changes, never rewrite the entire published tree."""
    deadline = deadline if deadline is not None else time.monotonic() + seconds
    if seconds <= 0:
        return {"status": "over_limit", "reason": "time_budget", "removed": []}
    root = _safe_path(Path(manifest["root"]))
    candidate = _safe_path(Path(manifest["candidate"]))
    lock_path = _safe_path(Path(manifest["lock"]))
    home = lock_path.parent.parent
    if root != _safe_path(home, "workspace/context"):
        raise ValueError("maintenance root does not match state")
    removed = []
    if _publication_waiting(lock_path.parent):
        return {"status": "over_limit", "reason": "publication_busy", "removed": removed}

    def unchanged(relative, checked_parents=None):
        try:
            current = _signature(_safe_path(root, relative, checked_parents=checked_parents))
        except FileNotFoundError:
            current = None
        return current == manifest["baseline"].get(relative)

    # Only mutation targets can veto this optional batch. A reference in an
    # unrelated page must not keep an otherwise unused body alive.
    checked_parents: set[Path] = set()
    for relative in set(manifest["replace"]) | set(manifest["remove"]) | {"description.md"}:
        if time.monotonic() >= deadline:
            return {"status": "over_limit", "reason": "time_budget", "removed": removed}
        if not unchanged(relative, checked_parents):
            return {"status": "over_limit", "reason": "context_changed", "removed": removed}
    deadline = min(deadline, time.monotonic() + seconds)
    if on_commit is not None:
        on_commit()
    try:
        with FileLock(lock_path, timeout=0):
            if _publication_waiting(lock_path.parent):
                return {"status": "over_limit", "reason": "publication_busy", "removed": removed}
            if not unchanged("description.md"):
                return {"status": "over_limit", "reason": "context_changed", "removed": removed}
            if manifest["remove"]:
                try:
                    active_sandbox_ids(home)
                except (OSError, ValueError):
                    return {"status": "over_limit", "reason": "sandbox_state_unavailable", "removed": removed}
            # Root replacement is the normal publication's generation marker.
            # Repair links first. An interrupted maintenance may leave extra
            # bodies, but must never delete before their references are repaired.
            for relative in manifest["replace"]:
                if time.monotonic() >= deadline:
                    return {"status": "over_limit", "reason": "time_budget", "removed": removed}
                if not unchanged(relative):
                    return {"status": "over_limit", "reason": "context_changed", "removed": removed}
                source = _safe_path(candidate, relative)
                destination = _safe_path(root, relative)
                os.replace(source, destination)
            bodies = [name for name in manifest["remove"] if Path(name).name.casefold() != "description.md"]
            descriptions = sorted(
                (name for name in manifest["remove"] if Path(name).name.casefold() == "description.md"),
                key=lambda name: -len(Path(name).parts),
            )
            for relative in bodies:
                if time.monotonic() >= deadline:
                    return {"status": "over_limit", "reason": "time_budget", "removed": removed}
                if not unchanged(relative):
                    return {"status": "over_limit", "reason": "context_changed", "removed": removed}
                retire_page(home, relative)
                removed.append(relative)
            for relative in descriptions:
                if time.monotonic() >= deadline:
                    return {"status": "over_limit", "reason": "time_budget", "removed": removed}
                if not unchanged(relative):
                    return {"status": "over_limit", "reason": "context_changed", "removed": removed}
                directory = _safe_path(root, relative).parent
                # Move an empty topic together with its description. Interruption
                # must never leave a published directory without navigation.
                if any(item.name.casefold() != "description.md" for item in directory.iterdir()):
                    return {"status": "over_limit", "reason": "context_changed", "removed": removed}
                retire_empty_topic(home, Path(relative).parent.as_posix())
                removed.append(relative)
    except Timeout:
        return {"status": "over_limit", "reason": "publication_busy", "removed": removed}
    return {"status": "succeeded", "removed": removed}


if __name__ == "__main__":
    try:
        plan = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
        # Imports and snapshot checks use the overall budget. Signal the parent
        # only when the short mutation window starts, immediately before locking.
        result = commit(
            plan, seconds=float(sys.argv[3]), deadline=float(sys.argv[2]),
            on_commit=lambda: Path(sys.argv[1]).with_suffix(".ready").touch(),
        )
    except Exception as exc:
        result = {"status": "failed", "reason": type(exc).__name__, "removed": []}
    # This stream is the parent's JSON result protocol, not a log sink.
    json.dump(result, sys.stdout)
