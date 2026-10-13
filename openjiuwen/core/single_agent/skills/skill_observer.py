# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Host-owned observation of local skill documents, independent of an Agent.

This is a discovery notification, not a filesystem write barrier. The host owns
scheduling, delivery policy and persistence. No network or vendor API is used.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable

from openjiuwen.core.common.logging import logger

MAX_SKILL_DOCUMENT_BYTES = 1024 * 1024


@dataclass(frozen=True)
class SkillDocumentSnapshot:
    """Immutable bytes captured while the document's file identity was stable."""

    directory: Path
    document_path: Path
    content: bytes
    fingerprint: str


@dataclass(frozen=True)
class SkillDiscoveryEvent:
    snapshot: SkillDocumentSnapshot
    reason: str
    context: Any = None


SkillDiscoveryHook = Callable[[SkillDiscoveryEvent], Awaitable[bool | None]]


def read_skill_document(directory: Path, max_bytes: int = MAX_SKILL_DOCUMENT_BYTES) -> SkillDocumentSnapshot:
    """Read SKILL.md without following a document symlink outside its directory.

    Raises OSError/ValueError on missing, oversized or concurrently written files.
    Callers may retry later; a document changing during the read is not delivered.
    """
    directory = Path(directory).resolve()
    document = directory / "SKILL.md"
    if not document.is_file():
        document = next((p for p in directory.iterdir() if p.name.casefold() == "skill.md" and p.is_file()), document)
    if document.resolve().parent != directory:
        raise ValueError("Skill document escapes its directory")
    path_before = document.stat()
    with document.open("rb") as handle:
        before = os.fstat(handle.fileno())
        content = handle.read(max_bytes + 1)
        after = os.fstat(handle.fileno())
    current = document.stat()

    def identity(value):
        return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns

    if len(content) > max_bytes:
        raise ValueError("Skill document exceeds snapshot limit")
    # Windows Python can give stat() and fstat() different ctime semantics.
    # Compare ctime only within the same API, while cross-checking file identity,
    # size and mtime to detect a replaced path or a write during the read.
    handle_changed = (
        identity(before) != identity(after)
        or before.st_ctime_ns != after.st_ctime_ns
    )
    path_changed = (
        identity(path_before) != identity(current)
        or path_before.st_ctime_ns != current.st_ctime_ns
    )
    handle_path_mismatch = identity(after) != identity(current)
    if handle_changed or path_changed or handle_path_mismatch:
        raise OSError("Skill document changed while being read")
    return SkillDocumentSnapshot(directory, document, content, hashlib.sha256(content).hexdigest())


class SkillDirectoryObserver:
    """Reusable scanner for explicit local roots or individual skill directories.

    Multiple observers may share a host callback (e.g. background and tool scans).
    The callback can return False to retry notification on the next scan.
    """

    def __init__(self, hook: SkillDiscoveryHook, *, max_bytes: int = MAX_SKILL_DOCUMENT_BYTES) -> None:
        self._hook = hook
        self._max_bytes = max_bytes
        self._seen: dict[str, str] = {}
        self._settling: dict[str, tuple[str, float]] = {}
        self._lock = asyncio.Lock()

    def _snapshots(self, roots: Iterable[str | Path]) -> list[SkillDocumentSnapshot]:
        snapshots = {}
        for raw_root in roots:
            root = Path(raw_root).expanduser().resolve()
            if not root.is_dir():
                continue
            try:
                children = list(root.iterdir())
                direct = any(p.name.casefold() == "skill.md" and p.is_file() for p in children)
                candidates = [root] if direct else [p for p in children if p.is_dir() and not p.name.startswith("_")]
                for candidate in candidates:
                    if not candidate.resolve().is_relative_to(root):
                        continue
                    try:
                        snapshot = read_skill_document(candidate, self._max_bytes)
                        snapshots[str(snapshot.directory)] = snapshot
                    except FileNotFoundError:
                        continue
                    except (OSError, ValueError) as exc:
                        logger.debug("[SkillDiscovery] snapshot deferred: %s (%s)", candidate, type(exc).__name__)
            except OSError as exc:
                logger.debug("[SkillDiscovery] directory scan deferred: %s (%s)", root, type(exc).__name__)
        return list(snapshots.values())

    async def scan(
        self, roots: Iterable[str | Path], *, context: Any = None, settle_seconds: float = 0,
    ) -> list[SkillDiscoveryEvent]:
        """Notify before the host exposes discovered documents; never await cloud I/O."""
        async with self._lock:
            snapshots = await asyncio.to_thread(self._snapshots, list(roots))
            events = []
            for snapshot in snapshots:
                key = str(snapshot.directory)
                previous = self._seen.get(key)
                if previous == snapshot.fingerprint:
                    continue
                now = time.monotonic()
                pending = self._settling.get(key)
                if pending is None or pending[0] != snapshot.fingerprint:
                    pending = (snapshot.fingerprint, now)
                    self._settling[key] = pending
                if now - pending[1] < settle_seconds:
                    continue
                event = SkillDiscoveryEvent(snapshot, "changed" if previous else "discovered", context)
                try:
                    accepted = await self._hook(event)
                except Exception as exc:
                    logger.warning("[SkillDiscovery] host notification failed (%s)", type(exc).__name__)
                    continue
                if accepted is False:
                    continue
                self._seen[key] = snapshot.fingerprint
                self._settling.pop(key, None)
                events.append(event)
            return events
