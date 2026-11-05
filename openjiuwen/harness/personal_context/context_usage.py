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
    return FileLock(_state_path(home, "context_usage.lock"), timeout=30, thread_local=False)


@asynccontextmanager
async def context_update_lock(home: Path):
    """Fence candidate construction and maintenance; readers only take usage_lock."""
    lock = FileLock(_state_path(home, "context_update.lock"), thread_local=False)
    while True:
        try:
            lock.acquire(timeout=0)
            break
        except Timeout:
            await asyncio.sleep(0.05)
    try:
        yield
    finally:
        lock.release()


def _load_usage(home: Path) -> dict:
    path = _state_path(home, "context_usage.json")
    if not path.exists():
        return {"version": 1, "pages": {}}
    value = json.loads(path.read_text(encoding="utf-8"))
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
    return value


def _save_usage(home: Path, value: dict) -> None:
    from openjiuwen.harness.personal_context.context_pipeline import _atomic_write

    _atomic_write(
        _state_path(home, "context_usage.json"),
        (json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"),
    )


def _fingerprint(root: Path, relative: str, relocations: Mapping[str, str]) -> str:
    from openjiuwen.harness.personal_context import context_pipeline as pipeline

    markdown = pipeline._extended_path(root / relative).read_text(encoding="utf-8")
    # Program-generated related/navigation links do not represent a body edit.
    for start, end in (
        (pipeline._RELATED_START, pipeline._RELATED_END),
        (pipeline._TOPIC_LINKS_START, pipeline._TOPIC_LINKS_END),
    ):
        bounds = pipeline._managed_block_bounds(markdown, start=start, end=end)
        if bounds is not None:
            begin, finish = bounds
            markdown = markdown[:begin] + markdown[finish:]

    def replace(match):
        split = pipeline._split_markdown_destination(match.group(1))
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

    normalized = rewrite_markdown_prose(markdown, lambda text: pipeline._MARKDOWN_LINK_TOKEN.sub(replace, text))
    return hashlib.sha256(normalized.strip().encode("utf-8")).hexdigest()


def _pages(root: Path, relocations: Mapping[str, str] | None = None) -> dict[str, tuple[str, float]]:
    from openjiuwen.harness.personal_context import context_pipeline as pipeline

    pipeline._assert_path_chain_no_symlinks(root)
    pipeline._assert_no_symlinks(root)
    return {
        path.relative_to(root).as_posix(): (
            _fingerprint(root, path.relative_to(root).as_posix(), relocations or {}),
            pipeline._extended_path(path).stat().st_mtime,
        )
        for path in pipeline._walk_tree_paths(root)
        if path.suffix.casefold() == ".md" and not pipeline._is_program_description(path)
        and pipeline._path_is_file(path)
    }


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


def publish_with_usage(home: Path, candidate: Path, relocations: Mapping[str, str]) -> None:
    """Commit under the read/delete lock, preserving times across tracked moves."""
    from openjiuwen.harness.personal_context import context_pipeline as pipeline

    root = home / "workspace" / "context"
    snapshots = None
    try:
        # The caller holds context_update_lock, so only usage timestamps can
        # change during these scans. Do not hold up readers for body hashing.
        before = _pages(root)
        mapped_before = _pages(root, relocations) if relocations else before
        after = _pages(candidate)
        snapshots = before, mapped_before, after
    except Exception as exc:
        logger.warning("PersonalContext publication usage scan failed (%s)", type(exc).__name__)
    with usage_lock(home):
        value = None
        try:
            if snapshots is not None:
                # Load after taking the lock to retain reads during the scan.
                value = _load_usage(home)
                before, mapped_before, after = snapshots
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
        # Actual publication errors must propagate, independently of accounting.
        pipeline._commit_context_tree(candidate, root)
        if value is not None:
            try:
                _save_usage(home, value)
            except Exception as exc:
                logger.warning("PersonalContext publication usage save failed (%s)", type(exc).__name__)


def read_with_usage(home: Path, path: str, *, allowed: Callable[[], bool]) -> dict[str, object]:
    """Read a graph-compatible relative page and record only an authorized success."""
    from openjiuwen.harness.personal_context.context_graph import read_context_graph_page
    from openjiuwen.harness.personal_context.context_pipeline import _assert_path_chain_no_symlinks
    from openjiuwen.harness.personal_context.status_codes import StatusCode, build_error

    def check_allowed():
        if not allowed():
            raise build_error(StatusCode.CONTEXT_PROACTIVE_STATE_INVALID, error_msg="PCS read is no longer enabled")

    with usage_lock(home):
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
                value = _load_usage(home)
                fingerprint = _fingerprint(root, path, {})
                record = value["pages"].get(path)
                if record is None or not record["present"]:
                    record = _new_record(fingerprint, (root / path).stat().st_mtime, approximate=True)
                elif record["fingerprint"] != fingerprint:
                    record.update(fingerprint=fingerprint, updated_at=(root / path).stat().st_mtime)
                record["last_read_at"] = time.time()
                value["pages"][path] = record
                _save_usage(home, value)
            except Exception as exc:
                logger.warning("PersonalContext read usage accounting failed (%s)", type(exc).__name__)
        check_allowed()
        return result
