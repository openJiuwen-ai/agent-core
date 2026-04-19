# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Coding Memory tools for JiuWenClaw - Using @tool decorator for openjiuwen."""

import asyncio
import os
from typing import Optional, Dict, Any, List, TYPE_CHECKING

from openjiuwen.core.common.logging import memory_logger as logger
from openjiuwen.core.foundation.store.base_embedding import EmbeddingConfig
from openjiuwen.core.foundation.tool.tool import tool
from openjiuwen.core.memory.manage.update.mem_update_checker import (
    MemUpdateChecker,
    MemoryStatus,
)
from openjiuwen.harness.workspace.workspace import Workspace

from .manager import MemoryIndexManager, MemoryManagerParams
from .config import create_memory_settings, is_memory_enabled
from .frontmatter import (
    parse_frontmatter,
    validate_frontmatter,
    enrich_frontmatter,
    rebuild_content_with_frontmatter,
    _extract_body,
)
from .conflict_types import WriteResult, WriteMode

if TYPE_CHECKING:
    from openjiuwen.core.sys_operation.sys_operation import SysOperation

coding_memory_manager: Optional[MemoryIndexManager] = None
coding_memory_workspace: "Workspace" = None
coding_memory_sys_operation: Optional["SysOperation"] = None
coding_memory_dir: str = "coding_memory"
MAX_INDEX_LINES = 200

# File-level lock registry: one asyncio.Lock per resolved path, retained for the process lifetime.
# Do not remove entries after use: after ``async with lock`` releases, waiters may still be
# scheduled but not yet acquired; ``Lock.locked()`` can be false while removal would split
# waiters and new getters onto different Lock objects, breaking mutual exclusion.
_file_locks: Dict[str, asyncio.Lock] = {}

# File lock creation lock: protects file lock dictionary initialization (prevents race conditions)
_file_locks_init_lock = asyncio.Lock()

# MEMORY.md index lock: protects concurrent read/write of the MEMORY.md index file
# Different file write coroutines hold their own file locks, but all modify the same MEMORY.md,
# so an independent lock is needed to prevent concurrent index updates from causing data loss.
_memory_index_lock = asyncio.Lock()

# Optimistic concurrency: maximum retry count (re-run conflict detection when snapshot expires)
_MAX_CONFLICT_RETRIES = 2


async def _get_file_lock(path: str) -> asyncio.Lock:
    """Get a file-level lock for the specified path (coroutine-safe).

    Uses double-checked locking pattern to ensure only one lock object
    is created per file path, avoiding protection failure when multiple
    coroutines create locks concurrently.

    Args:
        path: File path

    Returns:
        asyncio.Lock: Lock object for the given path
    """
    if path not in _file_locks:
        async with _file_locks_init_lock:
            if path not in _file_locks:
                _file_locks[path] = asyncio.Lock()
    return _file_locks[path]


async def _snapshot_memory_files(memory_dir: str) -> frozenset:
    """Lightweight snapshot: returns a frozen set of .md filenames under the memory directory (excludes MEMORY.md).

    Used for optimistic concurrency validation -- compares snapshot before writing;
    if snapshot is stale, conflict detection is retried.
    """
    try:
        if not memory_dir or not coding_memory_sys_operation:
            return frozenset()
        result = await coding_memory_sys_operation.fs().list_files(
            memory_dir, recursive=False
        )
        if result and hasattr(result, 'data') and result.data:
            names = []
            for f in result.data.list_items:
                if f.is_directory:
                    continue
                if not f.name.lower().endswith(".md"):
                    continue
                if f.name.casefold() == "memory.md":
                    continue
                names.append(f.name)
            return frozenset(names)
        return frozenset()
    except Exception:
        return frozenset()


async def _search_similar(
    body: str, exclude_path: str, top_k: int = 5, threshold: float = 0.75
) -> Dict[str, str]:
    """Retrieve existing memories similar to body, excluding the specified path and MEMORY.md.

    Args:
        body: Content to search for
        exclude_path: File path to exclude
        top_k: Maximum number of results
        threshold: Similarity threshold

    Returns:
        Dict[str, str]: {path: body content} dictionary
    """
    old_memories = {}
    if not coding_memory_manager:
        return old_memories

    results = await coding_memory_manager.search(body, opts={"max_results": top_k})
    for r in results:
        if (
            r.get("score", 0) > threshold
            and r["path"] != "MEMORY.md"
            and r["path"] != exclude_path
        ):
            old_content = await _read_file_safe(
                os.path.join(coding_memory_dir, r["path"])
            )
            if old_content:
                old_body = _extract_body(old_content)
                if old_body:
                    old_memories[r["path"]] = old_body
    return old_memories


async def _run_checker(
    new_id: str, new_body: str, old_memories: Dict[str, str]
) -> List[Any]:
    """Invoke MemUpdateChecker.

    Args:
        new_id: ID of the new memory (filename)
        new_body: Body content of the new memory
        old_memories: Old memory dictionary {id: body}

    Returns:
        List[MemoryActionItem]: List of actions
    """
    checker = MemUpdateChecker()
    new_memories = {new_id: new_body}
    llm = coding_memory_manager.llm if coding_memory_manager else None

    if not llm:
        return []

    try:
        return await checker.check(
            new_memories=new_memories,
            old_memories=old_memories,
            base_chat_model=llm,
        )
    except Exception as e:
        logger.warning(f"MemUpdateChecker failed: {e}")
        return []


async def _prepare_append_mode(resolved: str, basename: str, body: str, fm: Dict[str, str]) -> Dict[str, Any]:
    """Prepare append mode and return conflict detection results.

    Args:
        resolved: Full file path
        basename: File name
        body: Content to append
        fm: Frontmatter dictionary

    Returns:
        Dictionary containing conflict detection results
    """
    result = {}

    # Build old_memories: existing content in this file + other similar files
    old_memories = {}

    # Existing content in this file (using __self__ as identifier)
    existing_content = await _read_file_safe(resolved)
    existing_body = _extract_body(existing_content)
    if existing_body:
        old_memories["__self__"] = existing_body

    # Other similar files
    other = await _search_similar(body, basename, top_k=5, threshold=0.75)
    old_memories.update(other)

    # MemUpdateChecker
    if old_memories and coding_memory_manager and coding_memory_manager.llm:
        actions = await _run_checker(basename, body, old_memories)

        # REDUNDANT: skip write
        if actions and not any(a.id == basename for a in actions):
            return WriteResult(
                success=True, path=resolved, mode=WriteMode.SKIP,
                note="Content is redundant with existing memories",
                type=fm.get("type")
            ).to_dict()

        # Collect all conflicts
        conflicting = []
        for a in actions:
            if a.status == MemoryStatus.DELETE and a.id != basename:
                conflicting.append(basename if a.id == "__self__" else a.id)
        if conflicting:
            result["conflict_detected"] = True
            result["conflicting_files"] = conflicting
            result["note"] = (
                f"Conflicts with: {', '.join(conflicting)}. "
                f"Use coding_memory_read to review, then coding_memory_edit to update."
            )

    return result


async def _append_to_existing_file(resolved: str, body: str, fm: Dict[str, str]):
    """Append content to an existing file and update frontmatter.

    Args:
        resolved: Full file path
        body: Body content to append
        fm: Frontmatter dictionary
    """
    if not coding_memory_sys_operation:
        logger.error("_append_to_existing_file: coding_memory_sys_operation is None")
        return

    # Append body
    await coding_memory_sys_operation.fs().write_file(
        resolved, content="\n\n" + body, append=True
    )

    # Update frontmatter updated_at
    full_content = await _read_file_safe(resolved)
    fm_parsed = parse_frontmatter(full_content)
    if fm_parsed:
        fm_parsed = enrich_frontmatter(fm_parsed, is_edit=True)
        updated_content = rebuild_content_with_frontmatter(full_content, fm_parsed)
        await coding_memory_sys_operation.fs().write_file(
            resolved, content=updated_content, create_if_not_exist=False
        )


async def _upsert_memory_index(memory_dir: str, filename: str, frontmatter: Dict[str, str]):
    """Async incremental update of the MEMORY.md index.

    Uses _memory_index_lock to prevent concurrent modifications to MEMORY.md
    from different file-write coroutines that hold their own file-level locks.
    """
    async with _memory_index_lock:
        sys_op = coding_memory_sys_operation
        if not sys_op:
            return

        index_path = os.path.join(memory_dir, "MEMORY.md")
        new_entry = f"- [{frontmatter['name']}]({filename}) — {frontmatter['description']}"

        lines: List[str] = []
        try:
            result = await sys_op.fs().read_file(index_path)
            if result and hasattr(result, 'data') and result.data:
                content = result.data.content
                lines = content.split("\n") if content else []
        except Exception as e:
            logger.warning(f"Failed to read memory index: {e}")

        found = False
        for i, line in enumerate(lines):
            if f"]({filename})" in line:
                lines[i] = new_entry
                found = True
                break

        if not found:
            lines.insert(0, new_entry)

        new_content = "\n".join(lines[:MAX_INDEX_LINES])
        await sys_op.fs().write_file(
            index_path, content=new_content, create_if_not_exist=True, prepend_newline=False
        )


async def _remove_from_memory_index(memory_dir: str, filename: str):
    """Async removal of the MEMORY.md index line for ``filename``.

    Uses _memory_index_lock to prevent concurrent modifications to MEMORY.md.
    """
    async with _memory_index_lock:
        index_path = os.path.join(memory_dir, "MEMORY.md")
        try:
            if not coding_memory_sys_operation:
                logger.warning("coding_memory_sys_operation is none, please init first")
                return
            result = await coding_memory_sys_operation.fs().read_file(index_path)
            if not result or not hasattr(result, 'data') or not result.data:
                return
            content = result.data.content
            lines = content.split("\n") if content else []
            lines = [line for line in lines if f"]({filename})" not in line]
            new_content = "\n".join(lines)
            await coding_memory_sys_operation.fs().write_file(
                index_path, content=new_content, create_if_not_exist=True, prepend_newline=False
            )
        except Exception as e:
            logger.error(f"Failed to remove from memory index: {e}")


def _validate_coding_memory_path(path: str) -> tuple[bool, str]:
    if ".." in path or path.startswith("/"):
        return (False, "Invalid path: directory traversal not allowed")
    if not path.endswith(".md"):
        return (False, "Path must end with .md")
    memory_dir = coding_memory_workspace.get_node_path("coding_memory")
    resolved = os.path.join(memory_dir, os.path.basename(path))
    return (True, resolved)


async def init_memory_manager_async(
    workspace: "Workspace",
    agent_id: str = "default",
    embedding_config: Optional[EmbeddingConfig] = None,
    sys_operation: Optional["SysOperation"] = None,
    llm: Optional[Any] = None,
) -> Optional[MemoryIndexManager]:

    global coding_memory_manager, coding_memory_workspace, coding_memory_sys_operation, coding_memory_dir

    if not is_memory_enabled():
        logger.info("Memory system is disabled")
        return None

    if coding_memory_manager is not None:
        # Update llm reference
        if llm:
            coding_memory_manager.llm = llm
        return coding_memory_manager

    node_path = workspace.get_node_path("coding_memory")
    coding_memory_dir = str(node_path) if node_path else ""
    settings = create_memory_settings(coding_memory_dir)
    coding_memory_sys_operation = sys_operation
    coding_memory_workspace = workspace

    try:
        params = MemoryManagerParams(
            agent_id=agent_id,
            workspace=workspace,
            settings=settings,
            embedding_config=embedding_config,
            sys_operation=sys_operation,
            node_name="coding_memory",
        )
        coding_memory_manager = await MemoryIndexManager.get(params)

        if coding_memory_manager:
            # Attach llm
            coding_memory_manager.llm = llm
            logger.info(f"initialized Coding Memory manager for: {coding_memory_dir}")

        return coding_memory_manager

    except Exception as e:
        logger.error(f"Failed to initialize Coding Memory manager: {e}")
        return None


async def _read_file_safe(filepath: str) -> str:
    """Async full-file read; return empty string if missing or on error."""
    try:
        if not coding_memory_sys_operation:
            return ""
        result = await coding_memory_sys_operation.fs().read_file(filepath)
        if result and hasattr(result, 'data') and result.data:
            return result.data.content
        return ""
    except Exception:
        return ""


async def _read_head_async(filepath: str, max_lines: int = 30) -> str:
    """Read the first ``max_lines`` lines for frontmatter extraction (performance cap)."""
    try:
        if not coding_memory_sys_operation:
            return ""
        result = await coding_memory_sys_operation.fs().read_file(filepath, head=max_lines)
        if result and hasattr(result, 'data') and result.data:
            return result.data.content
        return ""
    except Exception:
        return ""


async def _count_memory_files_async(memory_dir: str) -> int:
    """Count ``.md`` memory files under ``memory_dir`` (excludes MEMORY.md)."""
    try:
        if not coding_memory_sys_operation:
            return 0
        result = await coding_memory_sys_operation.fs().list_files(
            memory_dir,
            recursive=False
        )
        if result and hasattr(result, 'data') and result.data:
            count = 0
            for f in result.data.list_items:
                if f.is_directory:
                    continue
                if not f.name.lower().endswith(".md"):
                    continue
                if f.name.casefold() == "memory.md":
                    continue
                count += 1
            return count
        return 0
    except Exception:
        return 0


@tool(name="coding_memory_read")
async def coding_memory_read(
    path: str,
    offset: Optional[int] = None,
    limit: Optional[int] = None
) -> Dict[str, Any]:
    """Read a markdown file under ``coding_memory/`` (full file or line range).

    Args:
        path: File name (e.g. ``user_role.md``).
        offset: 1-based start line; omit to read from the beginning.
        limit: Number of lines to read; omit to read through end of file.

    Returns:
        Dict with content and line metadata, or error fields.
    """
    try:
        is_valid, result = _validate_coding_memory_path(path)
        if not is_valid:
            return {
                "success": False,
                "path": path,
                "content": "",
                "error": result,
            }

        full_path = result
        sys_op = coding_memory_sys_operation
        if not sys_op:
            logger.error("Read memory failed, no available path _global_sys_operation")
            return {
                "success": False,
                "path": path,
                "error": "Read failed, no available _global_sys_operation.",
            }

        line_range = None
        if offset is not None:
            line_range = (
                (offset, offset + limit - 1) if limit is not None else (offset, -1)
            )

        read_result = await sys_op.fs().read_file(full_path, line_range=line_range)
        content = read_result.data.content
        lines = content.split("\n") if content else []
        total_lines = len(lines)

        start = max(0, offset - 1) if offset is not None else 0
        end = min(start + limit, total_lines) if limit is not None else total_lines

        return {
            "success": True,
            "path": full_path,
            "content": "\n".join(lines[start:end]),
            "totalLines": total_lines,
            "start_line": start + 1,
            "end_line": end,
            "truncated": limit is not None and end < total_lines,
        }

    except Exception as e:
        logger.error(f"Read failed: {e}")
        return {
            "success": False,
            "path": path,
            "content": "",
            "error": str(e)
        }


@tool(name="coding_memory_write")
async def coding_memory_write(path: str, content: str) -> Dict[str, Any]:
    """Write a memory file with conflict detection.

    Supports create/append modes with automatic conflict detection.
    Creates file if it doesn't exist, appends if it already exists.

    Uses optimistic concurrency: conflict detection (vector search + LLM)
    runs outside any lock, allowing concurrent writes. Before committing
    the write, a snapshot check verifies that no new files were added
    during detection; if the snapshot is stale, detection is retried.

    Args:
        path: File path (e.g., "user_role.md").
        content: Content with frontmatter.

    Returns:
        WriteResult as dict.
    """
    try:
        # ① Validate
        is_valid, resolved = _validate_coding_memory_path(path)
        if not is_valid:
            return {"success": False, "path": path, "error": resolved}

        fm = parse_frontmatter(content)
        if not fm:
            return {"success": False, "path": path, "error": "must contain frontmatter"}
        valid, err = validate_frontmatter(fm)
        if not valid:
            return {"success": False, "path": path, "error": err}

        if not coding_memory_sys_operation:
            return {
                "success": False,
                "path": path,
                "error": "no available coding_memory_sys_operation, please init first",
            }

        # ② Metadata population
        fm = enrich_frontmatter(fm, is_edit=False)
        content = rebuild_content_with_frontmatter(content, fm)
        body = _extract_body(content)
        if not body:
            return {"success": False, "path": path, "error": "no content body"}

        basename = os.path.basename(resolved)

        # ③ Optimistic concurrency: conflict detection runs outside lock, snapshot validated before write
        result: Dict[str, Any] = {}
        for attempt in range(_MAX_CONFLICT_RETRIES):
            # Snapshot current directory file list (outside lock, allows concurrent detection)
            snapshot = await _snapshot_memory_files(coding_memory_dir)
            file_exists = basename in snapshot

            if not file_exists:
                # Create mode: search for other similar files
                old_memories = await _search_similar(body, basename, top_k=5, threshold=0.75)
                actions = None
                if old_memories and coding_memory_manager and coding_memory_manager.llm:
                    actions = await _run_checker(basename, body, old_memories)

                # REDUNDANT handling
                if actions and not any(a.id == basename for a in actions):
                    return WriteResult(
                        success=True, path=resolved, mode=WriteMode.SKIP,
                        note="Content is redundant with existing memories",
                        type=fm.get("type")
                    ).to_dict()

                # Collect conflict info
                conflicting = []
                if actions:
                    conflicting = [a.id for a in actions if a.id != basename and a.status == MemoryStatus.DELETE]

                result = {"conflict_detected": bool(conflicting), "conflicting_files": conflicting}
                if conflicting:
                    result["note"] = (
                        f"Conflicts with: {', '.join(conflicting)}. "
                        f"Use coding_memory_read to review, then coding_memory_edit to update."
                    )

            else:
                # Append mode: search this file + other similar files
                result = await _prepare_append_mode(resolved, basename, body, fm)
                if result.get("mode") == WriteMode.SKIP.value:
                    return result

            # ④ File-level lock protects actual write + snapshot validation
            file_lock = await _get_file_lock(resolved)
            snapshot_stale = False
            async with file_lock:
                # Validate snapshot freshness before write
                current_snapshot = await _snapshot_memory_files(coding_memory_dir)
                if current_snapshot != snapshot:
                    # Snapshot stale: concurrent writes produced new files, retry conflict detection
                    logger.info(
                        f"Snapshot stale on attempt {attempt + 1}, retrying conflict detection"
                    )
                    snapshot_stale = True
                else:
                    if not file_exists:
                        # Create new file
                        await coding_memory_sys_operation.fs().write_file(
                            resolved, content=content, create_if_not_exist=True
                        )
                    else:
                        # Append to existing file
                        await _append_to_existing_file(resolved, body, fm)

            if snapshot_stale:
                continue  # Retry

            # Update index (index has its own lock, doesn't need to run inside file lock)
            await _upsert_memory_index(coding_memory_dir, basename, fm)

            return WriteResult(
                success=True, path=resolved,
                mode=WriteMode.CREATE if not file_exists else WriteMode.APPEND,
                type=fm.get("type"),
                **result
            ).to_dict()

        # All retries failed due to stale snapshot, last detection result is still available
        # Write with current latest state (degrade to no snapshot validation)
        logger.warning(f"Exceeded max conflict retries ({_MAX_CONFLICT_RETRIES}), writing without snapshot validation")

        file_lock = await _get_file_lock(resolved)
        async with file_lock:
            file_exists_now = basename in (await _snapshot_memory_files(coding_memory_dir))
            if not file_exists_now:
                await coding_memory_sys_operation.fs().write_file(
                    resolved, content=content, create_if_not_exist=True
                )
            else:
                await _append_to_existing_file(resolved, body, fm)

        # Update index (index has its own lock, doesn't need to run inside file lock)
        await _upsert_memory_index(coding_memory_dir, basename, fm)

        return WriteResult(
            success=True, path=resolved,
            mode=WriteMode.CREATE if not file_exists_now else WriteMode.APPEND,
            type=fm.get("type"),
        ).to_dict()
    except Exception as e:
        logger.error(f"coding_memory_write failed: {e}")
        return {"success": False, "path": path, "error": str(e)}


@tool(name="coding_memory_edit")
async def coding_memory_edit(path: str, old_text: str, new_text: str):
    try:
        if not old_text:
            return {"success": False, "error": "old_text cannot be empty"}

        is_valid, resolved = _validate_coding_memory_path(path)
        if not is_valid:
            return {"success": False, "error": resolved}

        sys_op = coding_memory_sys_operation
        if not sys_op:
            return {"success": False, "error": "no available _global_sys_operation"}

        # Acquire file-level lock to protect read-then-write operation,
        # preventing races with other write/edit coroutines
        file_lock = await _get_file_lock(resolved)
        async with file_lock:
            read_result = await sys_op.fs().read_file(resolved)
            if read_result is None or not hasattr(read_result, "data") or read_result.data is None:
                return {"success": False, "error": f"failed to read file: {path}"}

            content = read_result.data.content
            occurrences = content.count(old_text)
            if occurrences == 0:
                return {"success": False, "error": "old_text not found in file"}
            if occurrences > 1:
                return {
                    "success": False,
                    "error": f"old_text appears {occurrences} times, please be more specific",
                }

            new_content = content.replace(old_text, new_text, 1)
            await sys_op.fs().write_file(resolved, content=new_content, create_if_not_exist=True)

        # Update index (index has its own lock, doesn't need to run inside file lock)
        fm = parse_frontmatter(new_content)
        if fm:
            valid, _ = validate_frontmatter(fm)
            if valid:
                await _upsert_memory_index(coding_memory_dir, os.path.basename(resolved), fm)

        return {"success": True, "path": resolved, "new_content": new_content}

    except Exception as e:
        logger.error(f"coding_memory_edit failed: {e}")
        return {"success": False, "error": str(e)}


def get_decorated_tools() -> List:
    """Return the list of tools registered with the ``@tool`` decorator."""
    return [coding_memory_read, coding_memory_write, coding_memory_edit]
