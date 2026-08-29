"""Application-neutral records for a live installed-Skill catalog."""

from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
import threading
from collections import OrderedDict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from openjiuwen.symphony.retrieval.build.scanners.common import (
    find_skill_file,
    first_paragraph,
    read_skill_file,
    sha256_file,
)


_SKILL_FILE_CACHE_MAX_ENTRIES = 2_048
_SKILL_FILE_CACHE_LOCK = threading.Lock()
_SKILL_FILE_CACHE: OrderedDict[
    str,
    tuple[tuple[int, int, int, int], tuple[dict[str, Any], str, str]],
] = OrderedDict()
_SCANNED_RECORD_CACHE_LOCK = threading.Lock()
_SCANNED_RECORD_CACHE: OrderedDict[tuple[str, ...], "SkillRecord"] = OrderedDict()
_ROOT_LAYOUT_CACHE_LOCK = threading.Lock()
_ROOT_LAYOUT_CACHE: OrderedDict[str, tuple[tuple[Any, ...], tuple[tuple[str, Path], ...]]] = OrderedDict()
_INVENTORY_CACHE_LOCK = threading.Lock()
_INVENTORY_FLIGHTS: dict[
    tuple[tuple[str, ...], tuple[str, ...], tuple[tuple[str, str], ...]],
    "_InventoryFlight",
] = {}
_LAST_INVENTORIES: OrderedDict[
    tuple[tuple[str, ...], tuple[str, ...], tuple[tuple[str, str], ...]],
    "SkillInventory",
] = OrderedDict()
_NORMALIZED_CACHE_LOCK = threading.Lock()
_NORMALIZED_CACHE: OrderedDict[int, tuple[tuple["SkillRecord", ...], "SkillInventory"]] = OrderedDict()


@dataclass(frozen=True)
class SkillRecord:
    """One executable Skill projected into Symphony discovery."""

    worker_id: str
    name: str
    description: str
    skill_file: str
    source: str = "local"
    version: str = ""
    author: str = ""
    content_hash: str = ""
    source_root: str = ""


@dataclass(frozen=True)
class SkillInventory:
    """Stable snapshot of the currently visible Skill records."""

    items: tuple[SkillRecord, ...]
    fingerprint: str

    @property
    def count(self) -> int:
        return len(self.items)


@dataclass
class _InventoryFlight:
    """One exact live scan shared only by overlapping callers."""

    ready: threading.Event
    result: SkillInventory | None = None
    error: BaseException | None = None


def inventory_from_records(records: Iterable[SkillRecord]) -> SkillInventory:
    """Normalize and fingerprint records supplied by an application adapter."""

    cacheable = isinstance(records, tuple) and all(record.content_hash for record in records)
    if cacheable:
        with _NORMALIZED_CACHE_LOCK:
            cached = _NORMALIZED_CACHE.get(id(records))
            if cached is not None and cached[0] is records:
                _NORMALIZED_CACHE.move_to_end(id(records))
                return cached[1]

    prepared = [
        (str(record.worker_id if record.worker_id is not None else ""), _with_content_hash(record))
        for record in records
    ]
    normalized = tuple(
        sorted(
            _with_unique_worker_ids(prepared),
            key=lambda item: (item.name.casefold(), item.worker_id.casefold()),
        )
    )
    inventory = _inventory_from_normalized(normalized)
    if cacheable:
        with _NORMALIZED_CACHE_LOCK:
            _NORMALIZED_CACHE[id(records)] = (records, inventory)
            _NORMALIZED_CACHE.move_to_end(id(records))
            while len(_NORMALIZED_CACHE) > 128:
                _NORMALIZED_CACHE.popitem(last=False)
    return inventory


def _inventory_from_normalized(normalized: tuple[SkillRecord, ...]) -> SkillInventory:
    payload = [
        {
            "worker_id": item.worker_id,
            "name": item.name,
            "description": item.description,
            "skill_file": item.skill_file,
            "source": item.source,
            "version": item.version,
            "author": item.author,
            "content_hash": item.content_hash,
            "source_root": item.source_root,
        }
        for item in normalized
    ]
    fingerprint = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return SkillInventory(items=normalized, fingerprint=fingerprint)


def scan_skill_directories(
    directories: Sequence[str | Path],
    *,
    disabled_skills: Iterable[str] = (),
    source_by_name: Mapping[str, str] | None = None,
) -> SkillInventory:
    """Return a live direct-child inventory, reusing unchanged file snapshots."""

    roots = tuple(str(Path(directory).expanduser().resolve()) for directory in directories)
    disabled = tuple(sorted(str(value) for value in disabled_skills))
    sources = tuple(sorted((str(key), str(value)) for key, value in (source_by_name or {}).items()))
    key = (roots, disabled, sources)
    with _INVENTORY_CACHE_LOCK:
        flight = _INVENTORY_FLIGHTS.get(key)
        if flight is None:
            flight = _InventoryFlight(threading.Event())
            _INVENTORY_FLIGHTS[key] = flight
            leader = True
        else:
            leader = False
    if not leader:
        flight.ready.wait()
        if flight.error is not None:
            raise flight.error
        result = flight.result
        if result is None:
            raise RuntimeError("Skill inventory scan completed without a result")
        return result
    try:
        inventory = _scan_skill_directories_uncached(
            roots,
            disabled_skills=disabled,
            source_by_name=dict(sources),
        )
        with _INVENTORY_CACHE_LOCK:
            previous = _LAST_INVENTORIES.get(key)
            if previous is not None and previous.fingerprint == inventory.fingerprint:
                inventory = previous
            else:
                _LAST_INVENTORIES[key] = inventory
            _LAST_INVENTORIES.move_to_end(key)
            while len(_LAST_INVENTORIES) > 128:
                _LAST_INVENTORIES.popitem(last=False)
        flight.result = inventory
        return inventory
    except BaseException as exc:
        flight.error = exc
        raise
    finally:
        with _INVENTORY_CACHE_LOCK:
            _INVENTORY_FLIGHTS.pop(key, None)
            flight.ready.set()


def _scan_skill_directories_uncached(
    directories: Sequence[str | Path],
    *,
    disabled_skills: Iterable[str] = (),
    source_by_name: Mapping[str, str] | None = None,
) -> SkillInventory:
    """Scan direct child Skill folders in application precedence order.

    Hidden folders, underscore-prefixed folders, disabled Skills, and folders
    without a ``SKILL.md`` variant are excluded before discovery ranking.
    """

    disabled = {str(value) for value in disabled_skills}
    sources = {str(key): str(value) for key, value in (source_by_name or {}).items()}
    prepared: list[tuple[str, SkillRecord]] = []
    seen_source_ids: set[str] = set()
    for raw_directory in directories:
        root = Path(raw_directory).expanduser().resolve()
        for child_name, skill_file in _skill_sources(root):
            metadata, body, content_hash = _cached_skill_file(skill_file)
            worker_id = _canonical_skill_id(child_name)
            name = str(metadata.get("name") or worker_id).strip() or worker_id
            if child_name in seen_source_ids:
                continue
            source = sources.get(name, sources.get(worker_id, "local"))
            cache_key = (child_name, str(skill_file), str(root), content_hash, source)
            with _SCANNED_RECORD_CACHE_LOCK:
                record = _SCANNED_RECORD_CACHE.get(cache_key)
            if record is None:
                description = str(metadata.get("description") or "").strip() or first_paragraph(body, limit=500) or name
                record = SkillRecord(
                    worker_id=worker_id,
                    name=sanitize_model_text(name),
                    description=sanitize_model_text(description),
                    skill_file=str(skill_file),
                    source=sanitize_model_text(source),
                    version=sanitize_model_text(metadata.get("version") or ""),
                    author=sanitize_model_text(metadata.get("author") or ""),
                    content_hash=content_hash,
                    source_root=str(root),
                )
                with _SCANNED_RECORD_CACHE_LOCK:
                    _SCANNED_RECORD_CACHE[cache_key] = record
                    _SCANNED_RECORD_CACHE.move_to_end(cache_key)
                    while len(_SCANNED_RECORD_CACHE) > _SKILL_FILE_CACHE_MAX_ENTRIES:
                        _SCANNED_RECORD_CACHE.popitem(last=False)
            prepared.append((child_name, record))
            seen_source_ids.add(child_name)
    normalized = tuple(
        sorted(
            _with_unique_worker_ids(prepared),
            key=lambda item: (item.name.casefold(), item.worker_id.casefold()),
        )
    )
    inventory = _inventory_from_normalized(normalized)
    if not disabled:
        return inventory
    return _inventory_from_normalized(
        tuple(
            item
            for item in inventory.items
            if item.worker_id not in disabled and Path(item.skill_file).parent.name not in disabled
        )
    )


def _skill_sources(root: Path) -> tuple[tuple[str, Path], ...]:
    """Resolve the stable authorized layout once; file contents remain live."""

    try:
        stat = root.stat()
        children = sorted(root.iterdir(), key=lambda path: path.name.casefold())
    except OSError:
        return ()
    if not root.is_dir():
        return ()
    child_signature: list[tuple[Any, ...]] = []
    for child in children:
        if child.name.startswith(".") or child.name.startswith("_"):
            continue
        try:
            child_stat = child.stat()
        except OSError:
            child_signature.append((child.name, "missing"))
            continue
        child_signature.append((child.name, child_stat.st_ino, child_stat.st_mtime_ns, child_stat.st_ctime_ns))
    signature = (stat.st_ino, stat.st_mtime_ns, stat.st_ctime_ns, *child_signature)
    key = str(root)
    with _ROOT_LAYOUT_CACHE_LOCK:
        cached = _ROOT_LAYOUT_CACHE.get(key)
        if cached is not None and cached[0] == signature:
            _ROOT_LAYOUT_CACHE.move_to_end(key)
            return cached[1]
    sources: list[tuple[str, Path]] = []
    for child in children:
        if child.name.startswith(".") or child.name.startswith("_"):
            continue
        try:
            resolved_child = child.resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if not resolved_child.is_dir() or not resolved_child.is_relative_to(root):
            continue
        skill_file = find_skill_file(child)
        if skill_file is None:
            continue
        try:
            resolved_skill_file = skill_file.resolve(strict=True)
        except OSError:
            continue
        if not resolved_skill_file.is_relative_to(root):
            continue
        sources.append((child.name, skill_file.absolute()))
    result = tuple(sources)
    with _ROOT_LAYOUT_CACHE_LOCK:
        _ROOT_LAYOUT_CACHE[key] = (signature, result)
        _ROOT_LAYOUT_CACHE.move_to_end(key)
        while len(_ROOT_LAYOUT_CACHE) > 128:
            _ROOT_LAYOUT_CACHE.popitem(last=False)
    return result


def _with_content_hash(record: SkillRecord) -> SkillRecord:
    path = Path(os.path.abspath(Path(record.skill_file).expanduser()))
    source_root = (
        Path(os.path.abspath(Path(record.source_root).expanduser())) if str(record.source_root or "").strip() else None
    )
    resolved = path.resolve(strict=True)
    if source_root is not None:
        resolved_root = source_root.resolve(strict=True)
        if not resolved.is_relative_to(resolved_root):
            raise ValueError(f"Skill source escapes its authorized root: {record.worker_id}")
    if record.content_hash:
        content_hash = record.content_hash
    else:
        content_hash = sha256_file(resolved)
    return SkillRecord(
        worker_id=_canonical_skill_id(record.worker_id),
        name=sanitize_model_text(record.name or record.worker_id),
        description=sanitize_model_text(record.description or record.name or record.worker_id),
        skill_file=str(path),
        source=sanitize_model_text(record.source or "local"),
        version=sanitize_model_text(record.version or ""),
        author=sanitize_model_text(record.author or ""),
        content_hash=content_hash,
        source_root=str(source_root) if source_root is not None else "",
    )


def _with_unique_worker_ids(
    records: list[tuple[str, SkillRecord]],
) -> list[SkillRecord]:
    """Keep canonical IDs unique without allowing hostile aliases to hide Skills."""

    reserved = {record.worker_id for _, record in records}
    used: set[str] = set()
    normalized: list[SkillRecord] = []
    for raw_worker_id, record in sorted(
        records,
        key=lambda pair: (
            pair[1].worker_id.casefold(),
            pair[0] != pair[1].worker_id,
            pair[0].casefold(),
            pair[0],
            pair[1].skill_file,
            pair[1].source_root,
            pair[1].content_hash,
            pair[1].name,
            pair[1].description,
            pair[1].source,
            pair[1].version,
            pair[1].author,
        ),
    ):
        worker_id = record.worker_id
        if worker_id in used:
            identity = "\0".join((raw_worker_id, record.skill_file, record.source_root))
            suffix = hashlib.sha256(identity.encode("utf-8", errors="surrogatepass")).hexdigest()[:10]
            worker_id = f"{record.worker_id}~h{suffix}"
            index = 2
            while worker_id in used or worker_id in reserved:
                worker_id = f"{record.worker_id}~h{suffix}-{index}"
                index += 1
            record = replace(record, worker_id=worker_id)
        used.add(worker_id)
        normalized.append(record)
    return normalized


def _cached_skill_file(path: Path) -> tuple[dict[str, Any], str, str]:
    """Reuse parsed metadata while file identity and content stats are unchanged."""

    stat = path.stat()
    signature = (stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
    key = str(path.absolute())
    with _SKILL_FILE_CACHE_LOCK:
        cached = _SKILL_FILE_CACHE.get(key)
        if cached is not None and cached[0] == signature:
            _SKILL_FILE_CACHE.move_to_end(key)
            metadata, body, content_hash = cached[1]
            return dict(metadata), body, content_hash
        metadata, body, content_hash = read_skill_file(
            path,
            errors="replace",
            fallback_to_simple=False,
        )
        body = body.strip()
        value = (dict(metadata), body, content_hash)
        _SKILL_FILE_CACHE[key] = (signature, value)
        _SKILL_FILE_CACHE.move_to_end(key)
        while len(_SKILL_FILE_CACHE) > _SKILL_FILE_CACHE_MAX_ENTRIES:
            _SKILL_FILE_CACHE.popitem(last=False)
        return dict(metadata), body, content_hash


_INSTRUCTION_INJECTION = re.compile(
    r"(?:tool[_ -]?call|system[_ -]?reminder|ignore|disregard|override|previous instructions|prior instructions)",
    re.IGNORECASE,
)
_UNTRUSTED_PREFIX = "[untrusted Skill metadata] "


def _canonical_skill_id(value: Any) -> str:
    """Return a stable JSON/UTF-8-safe identifier for hostile filesystem names."""

    raw = str(value or "").strip()
    parts: list[str] = []
    for char in raw:
        category = unicodedata.category(char)
        if category in {"Cc", "Cs"}:
            parts.append(f"~u{ord(char):04x}")
        else:
            parts.append(char)
    return "".join(parts).strip() or "unnamed"


def sanitize_model_text(value: Any) -> str:
    """Escape model-visible metadata while preserving ordinary text verbatim."""

    raw = str(value or "").strip()
    unsafe = bool(_INSTRUCTION_INJECTION.search(raw) or "<" in raw or ">" in raw)
    cleaned: list[str] = []
    for char in raw:
        category = unicodedata.category(char)
        if category == "Cs":
            cleaned.append("\ufffd")
            unsafe = True
        elif category == "Cc" and char not in {"\n", "\r", "\t"}:
            cleaned.append(" ")
            unsafe = True
        else:
            cleaned.append(char)
    escaped = "".join(cleaned).replace("<", "&lt;").replace(">", "&gt;").strip()
    if unsafe and escaped and not escaped.startswith(_UNTRUSTED_PREFIX):
        return f"{_UNTRUSTED_PREFIX}{escaped}"
    return escaped


__all__ = [
    "SkillInventory",
    "SkillRecord",
    "inventory_from_records",
    "sanitize_model_text",
    "scan_skill_directories",
]
