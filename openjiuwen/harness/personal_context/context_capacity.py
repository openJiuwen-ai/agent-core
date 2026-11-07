# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Post-collection byte capacity maintenance for published knowledge Markdown."""

from __future__ import annotations

import posixpath
import shutil
import tempfile
import time
from pathlib import Path
from urllib.parse import unquote, urlsplit

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.common.logging import logger
from openjiuwen.harness.personal_context import context_pipeline as pipeline
from openjiuwen.harness.personal_context.context_usage import (
    _load_usage, _pages, _reconcile, _save_usage, _state_path, usage_lock,
)
from openjiuwen.harness.personal_context.source_markdown import rewrite_markdown_prose


def _markdown_sizes(root: Path) -> dict[str, int]:
    pipeline._assert_path_chain_no_symlinks(root)
    pipeline._assert_no_symlinks(root)
    return {
        page.relative_to(root).as_posix(): pipeline._extended_path(page).stat().st_size
        for page in pipeline._walk_tree_paths(root)
        if page.suffix.casefold() == ".md" and pipeline._path_is_file(page)
    }


def _repair_navigation(root: Path, removed: set[str]) -> None:
    """Prune empty topics and repair links without a model or a new body usage event."""
    for directory in sorted(
        (entry for entry in pipeline._walk_tree_paths(root) if pipeline._path_is_dir(entry)),
        key=lambda path: (-len(path.parts), path.as_posix()),
    ):
        entries = list(pipeline._extended_path(directory).iterdir())
        if all(entry.name.casefold() == "description.md" for entry in entries):
            for entry in entries:
                removed.add((directory / entry.name).relative_to(root).as_posix())
                entry.unlink()
            pipeline._extended_path(directory).rmdir()

    affected = {"description.md"}
    for relative in removed:
        for parent in Path(relative).parents:
            affected.add((parent / "description.md").as_posix())
    for relative in _markdown_sizes(root):
        page = pipeline._extended_path(root / relative)
        original = page.read_text(encoding="utf-8")
        markdown = original
        if relative in affected and pipeline._is_program_description(relative):
            # A model-written overview of removed knowledge is no longer current.
            bounds = pipeline._managed_block_bounds(
                markdown, start=pipeline._DIRECTORY_DESCRIPTION_START, end=pipeline._DIRECTORY_DESCRIPTION_END,
            )
            if bounds is not None:
                begin, finish = bounds
                markdown = markdown[:begin] + markdown[finish:]

        def remove_link(match):
            split = pipeline._split_markdown_destination(match.group(1))
            if split is None:
                return match.group(0)
            parsed = urlsplit(split[0])
            if parsed.scheme or parsed.netloc:
                return match.group(0)
            target = posixpath.normpath(posixpath.join(posixpath.dirname(relative), unquote(parsed.path)))
            if target not in removed:
                return match.group(0)
            return match.group(0)[1:].partition("](")[0]

        markdown = rewrite_markdown_prose(markdown, lambda text: pipeline._MARKDOWN_LINK.sub(remove_link, text))
        if markdown != original:
            pipeline._atomic_write(page, markdown.encode("utf-8"))
    pipeline._render_context_navigation(root)
    pipeline._validate_description_coverage(root)


def _single_page_size(root: Path, relative: str | None, all_paths: set[str], work: Path) -> int:
    """Measure a page with its required ancestor navigation, including the root."""
    candidate = work / "single"
    if candidate.exists():
        pipeline._remove_tree(candidate)
    candidate.mkdir()
    selected = {"description.md"}
    if relative is not None:
        selected.add(relative)
        parent = Path(relative).parent
        while parent != Path("."):
            selected.add((parent / "description.md").as_posix())
            parent = parent.parent
    for name in selected:
        source = root / name
        if pipeline._path_is_file(source):
            target = candidate / name
            pipeline._extended_path(target.parent).mkdir(parents=True, exist_ok=True)
            shutil.copy2(pipeline._extended_path(source), pipeline._extended_path(target))
    _repair_navigation(candidate, all_paths - selected)
    return sum(_markdown_sizes(candidate).values())


def maintain_capacity(home: Path, *, limit_bytes: int) -> dict[str, object]:
    """Maintain a completed collection; caller holds context_update_lock.

    Above the limit, reclaim to 60% of it. Stage deletions and navigation together,
    validate first, then use the existing per-file atomic publisher. Keep sources,
    cursors and retired usage records.
    """
    root = home / "workspace" / "context"
    # The caller holds context_update_lock. Readers only change the usage
    # ledger, so an under-limit size check needs neither that ledger nor its lock.
    sizes = _markdown_sizes(root)
    before_bytes = sum(sizes.values())
    result = {
        "status": "succeeded", "limit_bytes": limit_bytes, "before_bytes": before_bytes,
        "after_bytes": before_bytes, "removed_count": 0, "oversized_count": 0,
    }
    if before_bytes <= limit_bytes:
        return result
    with usage_lock(home):
        value = _load_usage(home)  # Corruption must fail before any deletion.
        _reconcile(value, _pages(root))
        target_bytes = limit_bytes * 3 // 5
        state = _state_path(home, "context_usage.json").parent
        with tempfile.TemporaryDirectory(prefix="context-capacity-", dir=state) as temporary:
            work = Path(temporary)
            all_paths = set(sizes)
            minimum_bytes = _single_page_size(root, None, all_paths, work)
            if minimum_bytes > target_bytes:
                reason = (
                    "navigation_exceeds_capacity" if minimum_bytes > limit_bytes
                    else "navigation_exceeds_reclaim_target"
                )
                return {**result, "status": "over_limit", "reason": reason}
            candidate = work / "context"
            pipeline._copy_tree(root, candidate)
            body_paths = [path for path in sizes if not pipeline._is_program_description(path)]
            removed: dict[str, str] = {}
            for relative in sorted(body_paths):
                if _single_page_size(root, relative, all_paths, work) > limit_bytes:
                    removed[relative] = "page_exceeds_capacity"
                    pipeline._extended_path(candidate / relative).unlink()
            removed_links = set(removed)
            if removed:
                _repair_navigation(candidate, removed_links)
            result["oversized_count"] = len(removed)

            def last_used(relative):
                record = value["pages"][relative]
                return (max(record["created_at"], record["updated_at"], record["last_read_at"] or 0), relative)

            after_bytes = sum(_markdown_sizes(candidate).values())
            for relative in sorted((path for path in body_paths if path not in removed), key=last_used):
                if after_bytes <= target_bytes:
                    break
                pipeline._extended_path(candidate / relative).unlink()
                removed[relative] = "least_recently_used"
                removed_links.add(relative)
                _repair_navigation(candidate, removed_links)
                after_bytes = sum(_markdown_sizes(candidate).values())
            if not removed:
                return {**result, "status": "over_limit", "reason": "navigation_exceeds_capacity"}
            pipeline._validate_description_coverage(candidate)
            pipeline._commit_context_tree(candidate, root)
            try:
                stamp = time.time()
                for relative, reason in removed.items():
                    value["pages"][relative].update(present=False, removed_at=stamp, removal_reason=reason)
                # Link repair is maintenance, never a substantive update signal.
                for relative, (fingerprint, _mtime) in _pages(root).items():
                    value["pages"][relative]["fingerprint"] = fingerprint
                _save_usage(home, value)
            except BaseError:
                raise  # Path safety rejections are not accounting failures.
            except Exception as exc:
                logger.warning("PersonalContext capacity usage accounting failed (%s)", type(exc).__name__)
            result.update(after_bytes=after_bytes, removed_count=len(removed))
            if after_bytes > target_bytes:
                result.update(status="over_limit", reason="navigation_exceeds_reclaim_target")
            return result
