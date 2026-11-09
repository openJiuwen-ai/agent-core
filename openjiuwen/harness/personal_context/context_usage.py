# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""File-backed usage accounting shared by publication, capacity maintenance and pcs_read."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import posixpath
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable, Mapping
from urllib.parse import unquote, urlsplit, urlunsplit

from filelock import FileLock, Timeout

from openjiuwen.core.common.logging import logger
from openjiuwen.harness.personal_context.source_markdown import rewrite_markdown_prose


def _state_path(home: Path, name: str) -> Path:
    from openjiuwen.harness.personal_context.context_pipeline import _assert_path_chain_no_symlinks

    path = home / "state" / name
    _assert_path_chain_no_symlinks(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def usage_lock(home: Path) -> FileLock:
    """Serialize short disk operations across host and Agent worker processes."""
    return FileLock(_state_path(home, "context_usage.lock"), timeout=0, thread_local=False)


@asynccontextmanager
async def context_update_lock(
    home: Path, *, timeout: float = 0.5, cancelled: Callable[[], bool] | None = None,
):
    """Fence writers with a short, cancellable acquisition deadline."""
    lock = FileLock(_state_path(home, "context_update.lock"), thread_local=False)
    deadline = time.monotonic() + timeout
    while True:
        if cancelled is not None and cancelled():
            raise asyncio.CancelledError
        try:
            lock.acquire(timeout=0)
            break
        except Timeout:
            if time.monotonic() >= deadline:
                raise
            await asyncio.sleep(0.05)
    try:
        yield
    finally:
        lock.release()


def _load_usage(home: Path) -> dict:
    path = _state_path(home, "context_usage.json")
    if not path.exists():
        return {"version": 1, "pages": {}}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeError):
        value = None
    # A different schema belongs to its writer. Never downgrade it implicitly.
    if isinstance(value, dict) and type(value.get("version")) is int and value["version"] != 1:
        raise ValueError("unsupported Context usage ledger version")
    try:
        if not isinstance(value, dict) or value.get("version") != 1 or not isinstance(value.get("pages"), dict):
            raise ValueError("invalid Context usage ledger")
        for relative, record in value["pages"].items():
            if not isinstance(relative, str) or not isinstance(record, dict):
                raise ValueError("invalid Context usage record")
            if not isinstance(record.get("fingerprint"), str) or not isinstance(record.get("present"), bool):
                raise ValueError("invalid Context usage record")
            for key in ("created_at", "updated_at", "last_read_at"):
                stamp = record.get(key)
                if stamp is None and key == "last_read_at":
                    continue
                if type(stamp) not in (int, float) or not math.isfinite(stamp) or stamp < 0:
                    raise ValueError("invalid Context usage timestamp")
    except ValueError:
        # Callers already have the body/sandbox scan and rebuild missing entries.
        # The next best-effort save replaces damaged current-format data.
        logger.warning("PersonalContext damaged usage ledger will rebuild from file metadata")
        return {"version": 1, "pages": {}}
    return value


def _save_usage(home: Path, value: dict) -> None:
    from openjiuwen.harness.personal_context.context_pipeline import _atomic_write

    _atomic_write(
        _state_path(home, "context_usage.json"),
        (json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"),
    )


def _fingerprint(
    root: Path, relative: str, relocations: Mapping[str, str], *, markdown: str | None = None,
) -> str:
    from openjiuwen.harness.personal_context.context_pipeline import (
        _MARKDOWN_LINK_TOKEN,
        _RELATED_END,
        _RELATED_START,
        _TOPIC_LINKS_END,
        _TOPIC_LINKS_START,
        _extended_path,
        _managed_block_bounds,
        _split_markdown_destination,
    )

    if markdown is None:
        markdown = _extended_path(root / relative).read_text(encoding="utf-8")
    else:
        # Graph reads preserve line endings; match read_text's newline handling.
        markdown = markdown.replace("\r\n", "\n").replace("\r", "\n")
    # Program-generated related/navigation links do not represent a body edit.
    for start, end in (
        (_RELATED_START, _RELATED_END),
        (_TOPIC_LINKS_START, _TOPIC_LINKS_END),
    ):
        bounds = _managed_block_bounds(markdown, start=start, end=end)
        if bounds is not None:
            begin, finish = bounds
            markdown = markdown[:begin] + markdown[finish:]

    def replace(match):
        split = _split_markdown_destination(match.group(1))
        if split is None:
            return match.group(0)
        target, suffix, bracketed = split
        parsed = urlsplit(target)
        if parsed.scheme or parsed.netloc:
            return match.group(0)
        resolved = posixpath.normpath(posixpath.join(posixpath.dirname(relative), unquote(parsed.path)))
        resolved = relocations.get(resolved, resolved)
        canonical = urlunsplit(("", "", resolved, parsed.query, parsed.fragment))
        canonical = (f"<{canonical}>" if bracketed else canonical) + suffix
        start = match.start(1) - match.start()
        end = match.end(1) - match.start()
        return match.group(0)[:start] + canonical + match.group(0)[end:]

    normalized = rewrite_markdown_prose(markdown, lambda text: _MARKDOWN_LINK_TOKEN.sub(replace, text))
    return hashlib.sha256(normalized.strip().encode("utf-8")).hexdigest()


def _pages(root: Path, relocations: Mapping[str, str] | None = None) -> dict[str, tuple[str, float]]:
    from openjiuwen.harness.personal_context.context_pipeline import (
        _assert_no_symlinks,
        _assert_path_chain_no_symlinks,
        _extended_path,
        _is_program_description,
        _path_is_file,
        _walk_tree_paths,
    )

    _assert_path_chain_no_symlinks(root)
    _assert_no_symlinks(root)
    pages = {}
    for path in _walk_tree_paths(root):
        if path.suffix.casefold() != ".md" or _is_program_description(path) or not _path_is_file(path):
            continue
        relative = path.relative_to(root).as_posix()
        pages[relative] = (
            _fingerprint(root, relative, relocations or {}),
            _extended_path(path).stat().st_mtime,
        )
    return pages


def _new_record(fingerprint: str, stamp: float, *, approximate: bool) -> dict:
    return {
        "fingerprint": fingerprint, "created_at": stamp, "updated_at": stamp,
        "last_read_at": None, "mtime_baseline": approximate, "present": True,
    }


def _reconcile(value: dict, pages: Mapping[str, tuple[str, float]]) -> None:
    """Bootstrap legacy pages and reconcile external edits using their actual mtime."""
    records = value["pages"]
    for relative, (fingerprint, mtime) in pages.items():
        record = records.get(relative)
        if record is None or not record["present"]:
            records[relative] = _new_record(fingerprint, mtime, approximate=True)
        elif record["fingerprint"] != fingerprint:
            record.update(fingerprint=fingerprint, updated_at=mtime)
    for relative, record in records.items():
        if relative not in pages:
            record["present"] = False


def publish_with_usage(
    home: Path, candidate: Path, relocations: Mapping[str, str], *,
    snapshots: tuple[dict, dict] | None = None,
) -> None:
    """Publish independently of best-effort accounting; caller fences writers."""
    from openjiuwen.harness.personal_context.context_pipeline import _commit_context_tree

    root = home / "workspace" / "context"
    prepared = None
    try:
        if snapshots is None:
            before = _pages(root)
            mapped_before = _pages(root, relocations) if relocations else before
            after = _pages(candidate)
        else:
            previous, current = snapshots
            # A failed optional fingerprint invalidates accounting, not publication.
            before = {path: (item[0], item[2]) for path, item in previous.items()}
            mapped_before = {path: (item[1], item[2]) for path, item in previous.items()}
            after = {path: (item[0], item[2]) for path, item in current.items()}
        prepared = before, mapped_before, after
    except Exception as exc:
        logger.warning("PersonalContext publication usage scan failed (%s)", type(exc).__name__)

    # Failure to acquire the optional ledger lock never vetoes publication.
    lock = None
    if prepared is not None:
        try:
            lock = usage_lock(home)
            lock.acquire(timeout=0)
        except Exception as exc:
            lock = None
            logger.warning("PersonalContext publication usage lock unavailable (%s)", type(exc).__name__)
    if lock is None:
        _commit_context_tree(candidate, root)
        return
    try:
        value = None
        try:
            value = _load_usage(home)
            before, mapped_before, after = prepared
            _reconcile(value, before)
            old_records = {path: dict(record) for path, record in value["pages"].items()}
            pending = set(after)
            stamp = time.time()
            for record in value["pages"].values():
                record["present"] = False
            unmatched = []
            for old in sorted(before, key=lambda path: (relocations.get(path, path) != path, path)):
                target = relocations.get(old, old)
                if target not in pending:
                    unmatched.append(old)
                    continue
                record = dict(old_records[old])
                if mapped_before[old][0] != after[target][0]:
                    record["updated_at"] = stamp
                record.update(fingerprint=after[target][0], present=True)
                value["pages"][target] = record
                pending.remove(target)
            # Exact content identity also preserves an untracked rename.
            for target in sorted(pending):
                matched = next((old for old in unmatched if mapped_before[old][0] == after[target][0]), None)
                if matched is None:
                    record = _new_record(after[target][0], stamp, approximate=False)
                else:
                    unmatched.remove(matched)
                    record = {**old_records[matched], "fingerprint": after[target][0], "present": True}
                value["pages"][target] = record
        except Exception as exc:
            value = None
            logger.warning("PersonalContext publication usage accounting failed (%s)", type(exc).__name__)
        # Readers never wait for this lock. While paths change they can still
        # read files, but may omit a timestamp instead of mixing page identities.
        _commit_context_tree(candidate, root)
        if value is not None:
            try:
                _save_usage(home, value)
            except Exception as exc:
                logger.warning("PersonalContext publication usage save failed (%s)", type(exc).__name__)
    finally:
        try:
            lock.release()
        except Exception as exc:
            logger.warning("PersonalContext publication usage release failed (%s)", type(exc).__name__)


def read_with_usage(home: Path, path: str, *, allowed: Callable[[], bool]) -> dict[str, object]:
    """Read a graph-compatible relative page and record only an authorized success."""
    from openjiuwen.harness.personal_context.context_graph import read_context_graph_page
    from openjiuwen.harness.personal_context.context_pipeline import _assert_path_chain_no_symlinks
    from openjiuwen.harness.personal_context.status_codes import StatusCode, build_error

    def check_allowed():
        if not allowed():
            raise build_error(StatusCode.CONTEXT_PROACTIVE_STATE_INVALID, error_msg="PCS read is no longer enabled")

    check_allowed()
    root = home / "workspace" / "context"
    _assert_path_chain_no_symlinks(root)
    if isinstance(path, str) and ":" in path:
        raise build_error(StatusCode.CONTEXT_PROACTIVE_STATE_INVALID, error_msg="path must be relative to Context")
    if isinstance(path, str):
        _assert_path_chain_no_symlinks(root / path)
    result = read_context_graph_page(home, f"page:{path}")
    check_allowed()
    if Path(path).name.casefold() != "description.md":
        try:
            # Read first. Busy or broken accounting only loses this usage event.
            fingerprint = _fingerprint(root, path, {}, markdown=result["markdown"])
            mtime = (root / path).stat().st_mtime
            with usage_lock(home).acquire(timeout=0):
                value = _load_usage(home)
                record = value["pages"].get(path)
                if record is None or not record["present"]:
                    record = _new_record(fingerprint, mtime, approximate=True)
                elif record["fingerprint"] != fingerprint:
                    record.update(fingerprint=fingerprint, updated_at=mtime)
                record["last_read_at"] = time.time()
                value["pages"][path] = record
                _save_usage(home, value)
        except Exception as exc:
            logger.warning("PersonalContext read usage accounting failed (%s)", type(exc).__name__)
    check_allowed()
    return result
