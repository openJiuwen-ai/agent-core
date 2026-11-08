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

from filelock import FileLock, Timeout


def _safe_path(root: Path, relative: str = "") -> Path:
    path = root / relative
    if relative:
        relative_path = Path(relative)
        if relative_path.anchor or ".." in relative_path.parts or ":" in relative:
            raise ValueError("invalid maintenance path")
    for item in (path, *path.parents):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("maintenance path is a link")
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


def commit(manifest: dict, *, seconds: float) -> dict:
    """Apply only prepared changes, never rewrite the entire published tree."""
    deadline = time.monotonic() + seconds
    if seconds <= 0:
        return {"status": "over_limit", "reason": "time_budget", "removed": []}
    root = _safe_path(Path(manifest["root"]))
    candidate = _safe_path(Path(manifest["candidate"]))
    lock_path = _safe_path(Path(manifest["lock"]))
    retired = _safe_path(candidate.parent / "retired")
    retired.mkdir(exist_ok=True)
    removed = []
    try:
        with FileLock(lock_path, timeout=0):
            for relative, expected in manifest["baseline"].items():
                if time.monotonic() >= deadline:
                    return {"status": "over_limit", "reason": "time_budget", "removed": removed}
                try:
                    current = _signature(_safe_path(root, relative))
                except FileNotFoundError:
                    current = None
                if current != expected:
                    return {"status": "over_limit", "reason": "context_changed", "removed": removed}
            # Root replacement is the normal publication's generation marker.
            # Repair links first. An interrupted maintenance may leave extra
            # bodies, but must never delete before their references are repaired.
            for relative in manifest["replace"]:
                if time.monotonic() >= deadline:
                    return {"status": "over_limit", "reason": "time_budget", "removed": removed}
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
                _safe_path(root, relative).unlink(missing_ok=True)
                removed.append(relative)
            for index, relative in enumerate(descriptions):
                if time.monotonic() >= deadline:
                    return {"status": "over_limit", "reason": "time_budget", "removed": removed}
                directory = _safe_path(root, relative).parent
                # Move an empty topic together with its description. Interruption
                # must never leave a published directory without navigation.
                if any(item.name.casefold() != "description.md" for item in directory.iterdir()):
                    return {"status": "over_limit", "reason": "context_changed", "removed": removed}
                os.replace(directory, retired / str(index))
                removed.append(relative)
    except Timeout:
        return {"status": "over_limit", "reason": "publication_busy", "removed": removed}
    return {"status": "succeeded", "removed": removed}


if __name__ == "__main__":
    try:
        plan = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
        # Startup/imports run without the publication lock. Tell the parent
        # when the short mutation window begins so it can enforce it by kill.
        Path(sys.argv[1]).with_suffix(".ready").touch()
        result = commit(plan, seconds=min(float(sys.argv[3]), max(0, float(sys.argv[2]) - time.monotonic())))
    except Exception as exc:
        result = {"status": "failed", "reason": type(exc).__name__, "removed": []}
    sys.stdout.write(f"{json.dumps(result)}\n")
