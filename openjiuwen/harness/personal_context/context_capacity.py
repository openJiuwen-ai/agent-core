# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Post-collection byte capacity maintenance for published knowledge Markdown."""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import posixpath
import subprocess
import sys
import tempfile
import threading
import time
from functools import partial
from pathlib import Path
from typing import Callable
from urllib.parse import unquote, urlsplit

from filelock import Timeout

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.common.logging import logger
from openjiuwen.harness.personal_context.context_pipeline import (
    _DIRECTORY_DESCRIPTION_END,
    _DIRECTORY_DESCRIPTION_START,
    _MARKDOWN_LINK,
    _assert_no_symlinks,
    _assert_path_chain_no_symlinks,
    _atomic_write,
    _copy_tree,
    _extended_path,
    _is_program_description,
    _managed_block_bounds,
    _path_is_dir,
    _path_is_file,
    _render_context_navigation,
    _split_markdown_destination,
    _validate_description_coverage,
    _walk_tree_paths,
)
from openjiuwen.harness.personal_context.context_usage import (
    _load_usage, _pages, _reconcile, _save_usage, _state_path, usage_lock,
)
from openjiuwen.harness.personal_context.source_markdown import rewrite_markdown_prose


_CAPACITY_WORKERS: dict[Path, concurrent.futures.Future] = {}
_CAPACITY_WORKERS_LOCK = threading.Lock()
_COMMIT_TIMEOUT_SECONDS = 1.0
_COMMIT_LOCK_SECONDS = 0.1


async def run_capacity_maintenance(
    home: Path, *, limit_bytes: int, timeout: float, cancelled: Callable[[], bool],
) -> dict[str, object]:
    """Bound optional work without waiting for a stuck preparation thread."""
    skipped = {"status": "failed", "reason": "time_budget"}
    if timeout <= 0 or cancelled():
        return skipped
    deadline = time.monotonic() + timeout
    stop = threading.Event()
    if not _CAPACITY_WORKERS_LOCK.acquire(blocking=False):
        return {**skipped, "reason": "maintenance_busy"}
    try:
        previous = _CAPACITY_WORKERS.get(home)
        if previous is not None and not previous.done():
            return {**skipped, "reason": "maintenance_busy"}
        future = concurrent.futures.Future()
        _CAPACITY_WORKERS[home] = future
    finally:
        _CAPACITY_WORKERS_LOCK.release()

    def work():
        try:
            result = maintain_capacity(home, limit_bytes=limit_bytes, cancelled=stop.is_set, deadline=deadline)
        except Exception as exc:
            result = {"status": "failed", "reason": type(exc).__name__}
        future.set_result(result)
        if _CAPACITY_WORKERS_LOCK.acquire(blocking=False):
            try:
                if _CAPACITY_WORKERS.get(home) is future:
                    _CAPACITY_WORKERS.pop(home, None)
            finally:
                _CAPACITY_WORKERS_LOCK.release()

    try:
        threading.Thread(target=work, name="pcs-capacity", daemon=True).start()
    except Exception as exc:
        # A failed optional worker startup must not disable all future attempts.
        future.set_result({"status": "failed", "reason": type(exc).__name__})
        return future.result()
    try:
        while not future.done():
            remaining = deadline - time.monotonic()
            if remaining <= 0 or cancelled():
                return skipped
            await asyncio.sleep(min(0.005, remaining))
        return future.result()
    finally:
        # A late preparation may finish its own temporary files, never publish.
        stop.set()


def _file_signature(path: Path) -> list[int]:
    info = _extended_path(path).stat()
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def _commit_candidate(
    manifest: Path, *, deadline: float, cancelled: Callable[[], bool],
) -> dict[str, object]:
    """Only the disposable child can mutate published knowledge during GC."""
    remaining = min(_COMMIT_TIMEOUT_SECONDS, deadline - time.monotonic())
    skipped = {"status": "failed", "reason": "commit_time_budget", "removed": []}
    if remaining <= 0 or cancelled():
        return skipped
    end = min(deadline, time.monotonic() + remaining)
    ready_path = manifest.with_suffix(".ready")
    process = subprocess.Popen(
        [sys.executable, str(Path(__file__).with_name("_capacity_commit.py")), str(manifest), str(end),
         str(_COMMIT_LOCK_SECONDS)],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    ready = False
    try:
        while True:
            if not ready and ready_path.exists():
                ready = True
                end = min(end, time.monotonic() + _COMMIT_LOCK_SECONDS)
            if cancelled() or time.monotonic() >= end:
                process.kill()
                return skipped
            try:
                output, _ = process.communicate(timeout=min(0.005, max(0, end - time.monotonic())))
                break
            except subprocess.TimeoutExpired:
                pass
        return json.loads(output.splitlines()[-1]) if output else {**skipped, "reason": "commit_failed"}
    finally:
        if process.poll() is None:
            process.kill()
        # The process owns any maintenance write lock. Termination releases it,
        # unlike cancelling a Python thread that can later resume an old commit.
        try:
            process.wait(timeout=0.1)
        except subprocess.TimeoutExpired:
            logger.warning("PersonalContext capacity worker termination is still pending")
        if process.stdout is not None:
            process.stdout.close()


def _markdown_sizes(root: Path) -> dict[str, int]:
    _assert_path_chain_no_symlinks(root)
    _assert_no_symlinks(root)
    return {
        page.relative_to(root).as_posix(): _extended_path(page).stat().st_size
        for page in _walk_tree_paths(root)
        if page.suffix.casefold() == ".md" and _path_is_file(page)
    }


def _repair_navigation(root: Path, removed: set[str]) -> None:
    """Prune empty topics and repair links without a model or a new body usage event."""
    for directory in sorted(
        (entry for entry in _walk_tree_paths(root) if _path_is_dir(entry)),
        key=lambda path: (-len(path.parts), path.as_posix()),
    ):
        entries = list(_extended_path(directory).iterdir())
        if all(entry.name.casefold() == "description.md" for entry in entries):
            for entry in entries:
                removed.add((directory / entry.name).relative_to(root).as_posix())
                entry.unlink()
            _extended_path(directory).rmdir()

    affected = {"description.md"}
    for relative in removed:
        for parent in Path(relative).parents:
            affected.add((parent / "description.md").as_posix())
    for relative in _markdown_sizes(root):
        page = _extended_path(root / relative)
        original = page.read_text(encoding="utf-8")
        markdown = original
        if relative in affected and _is_program_description(relative):
            # A model-written overview of removed knowledge is no longer current.
            bounds = _managed_block_bounds(
                markdown, start=_DIRECTORY_DESCRIPTION_START, end=_DIRECTORY_DESCRIPTION_END,
            )
            if bounds is not None:
                begin, finish = bounds
                markdown = markdown[:begin] + markdown[finish:]

        def remove_link(match, page_relative=relative):
            split = _split_markdown_destination(match.group(1))
            if split is None:
                return match.group(0)
            parsed = urlsplit(split[0])
            if parsed.scheme or parsed.netloc:
                return match.group(0)
            target = posixpath.normpath(posixpath.join(posixpath.dirname(page_relative), unquote(parsed.path)))
            if target not in removed:
                return match.group(0)
            return match.group(0)[1:].partition("](")[0]

        markdown = rewrite_markdown_prose(markdown, partial(_MARKDOWN_LINK.sub, remove_link))
        if markdown != original:
            _atomic_write(page, markdown.encode("utf-8"))
    _render_context_navigation(root)
    _validate_description_coverage(root)


def maintain_capacity(
    home: Path, *, limit_bytes: int, cancelled: Callable[[], bool] | None = None,
    deadline: float | None = None,
) -> dict[str, object]:
    """Prepare without a publication lock and try one short, optional commit.

    Above the limit, select a batch toward 60% using existing file sizes. Rebuild
    navigation once and report the resulting size without iterative fine-tuning.
    Keep sources, cursors and retired usage records.
    """
    root = home / "workspace" / "context"
    deadline = deadline if deadline is not None else time.monotonic() + 2.0
    cancelled = cancelled or (lambda: False)

    def expired():
        return cancelled() or time.monotonic() >= deadline

    # Preparation never owns a lock needed by normal publication or reads.
    sizes = _markdown_sizes(root)
    before_bytes = sum(sizes.values())
    result = {
        "status": "succeeded", "limit_bytes": limit_bytes, "before_bytes": before_bytes,
        "after_bytes": before_bytes, "removed_count": 0, "oversized_count": 0,
    }
    if before_bytes <= limit_bytes:
        return result
    if expired():
        return {**result, "status": "over_limit", "reason": "time_budget"}
    baseline = {relative: _file_signature(root / relative) for relative in sizes}
    try:
        with usage_lock(home).acquire(timeout=0):
            value = _load_usage(home)  # Invalid current records rebuild from the page scan below.
    except Timeout:
        return {**result, "status": "over_limit", "reason": "usage_busy"}
    pages = _pages(root)
    _reconcile(value, pages)
    target_bytes = limit_bytes * 3 // 5
    # Avoid clearing knowledge when the root itself already exceeds the target.
    # This is a conservative size check, not an exact minimal-navigation rebuild.
    root_bytes = sizes.get("description.md", 0)
    if root_bytes > target_bytes:
        reason = "navigation_exceeds_capacity" if root_bytes > limit_bytes else "navigation_exceeds_reclaim_target"
        return {**result, "status": "over_limit", "reason": reason}

    body_paths = [path for path in sizes if not _is_program_description(path)]
    removed = {path: "page_exceeds_capacity" for path in body_paths if sizes[path] > limit_bytes}
    oversized_count = len(removed)
    estimated_bytes = before_bytes - sum(sizes[path] for path in removed)

    def last_used(relative):
        record = value["pages"][relative]
        return (max(record["created_at"], record["updated_at"], record["last_read_at"] or 0), relative)

    for relative in sorted((path for path in body_paths if path not in removed), key=last_used):
        if estimated_bytes <= target_bytes:
            break
        removed[relative] = "least_recently_used"
        estimated_bytes -= sizes[relative]
    if not removed:
        return {**result, "status": "over_limit", "reason": "navigation_exceeds_capacity"}

    state = _state_path(home, "context_usage.json").parent
    with tempfile.TemporaryDirectory(prefix="context-capacity-", dir=state) as temporary:
        candidate = Path(temporary) / "context"
        _copy_tree(root, candidate)
        if expired():
            return {**result, "status": "over_limit", "reason": "time_budget"}
        for relative in removed:
            _extended_path(candidate / relative).unlink()
        _repair_navigation(candidate, set(removed))
        after_bytes = sum(_markdown_sizes(candidate).values())
        if expired():
            return {**result, "status": "over_limit", "reason": "time_budget"}
        fingerprints = None
        try:
            fingerprints = _pages(candidate)
        except BaseError:
            raise
        except Exception as exc:
            logger.warning("PersonalContext capacity usage scan failed (%s)", type(exc).__name__)
        try:
            lock = usage_lock(home)
            lock.acquire(timeout=0)
        except Timeout:
            return {**result, "status": "over_limit", "reason": "usage_busy"}
        try:
            latest = _load_usage(home)
            _reconcile(latest, pages)
            if any(
                (latest["pages"][path]["last_read_at"] or 0) > (value["pages"][path]["last_read_at"] or 0)
                for path in removed
            ):
                return {**result, "status": "over_limit", "reason": "usage_changed"}
            if expired():
                return {**result, "status": "over_limit", "reason": "time_budget"}
            candidate_paths = {
                page.relative_to(candidate).as_posix(): page
                for page in _walk_tree_paths(candidate)
                if page.suffix.casefold() == ".md" and _path_is_file(page)
            }
            replacements = []
            for relative, page in candidate_paths.items():
                try:
                    original = _extended_path(root / relative).read_bytes()
                except FileNotFoundError:
                    original = None
                if _extended_path(page).read_bytes() != original:
                    replacements.append(relative)
            manifest = Path(temporary) / "commit.json"
            manifest.write_text(json.dumps({
                "root": str(root), "candidate": str(candidate),
                "lock": str(_state_path(home, "context_update.lock")), "baseline": baseline,
                "replace": sorted(replacements, key=lambda name: name == "description.md"),
                "remove": sorted(set(sizes) - set(candidate_paths)),
            }), encoding="utf-8")
            outcome = _commit_candidate(manifest, deadline=deadline, cancelled=cancelled)
            actually_removed = set(outcome.get("removed", []))
            if outcome["status"] != "succeeded":
                if outcome["status"] == "failed" or actually_removed:
                    return {"status": "failed", "reason": outcome.get("reason", "commit_incomplete")}
                return {**result, "status": outcome["status"], "reason": outcome.get("reason", "time_budget"),
                        "removed_count": len(actually_removed & set(removed))}
            try:
                stamp = time.time()
                for relative, reason in removed.items():
                    if relative in actually_removed:
                        latest["pages"][relative].update(present=False, removed_at=stamp, removal_reason=reason)
                # Link repair is maintenance, never a substantive update signal.
                if fingerprints is not None:
                    for relative, (fingerprint, _mtime) in fingerprints.items():
                        latest["pages"][relative]["fingerprint"] = fingerprint
                _save_usage(home, latest)
            except BaseError:
                raise
            except Exception as exc:
                logger.warning("PersonalContext capacity usage accounting failed (%s)", type(exc).__name__)
        finally:
            lock.release()
        result.update(after_bytes=after_bytes, removed_count=len(removed), oversized_count=oversized_count)
        if after_bytes > target_bytes:
            result.update(status="over_limit", reason="navigation_exceeds_reclaim_target")
        return result
