# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""General lite memory tool implementations without @tool decorator."""

from __future__ import annotations

import os
import re
from typing import Any, Dict, Optional, TYPE_CHECKING

from openjiuwen.core.common.logging import memory_logger as logger
from openjiuwen.core.memory.lite.memory_tool_context import MemoryToolContext

if TYPE_CHECKING:
    from openjiuwen.harness.workspace.workspace import Workspace


def validate_memory_path(path: str, workspace: "Workspace") -> tuple[bool, str]:
    """Validate that path is within the memory directory. ``workspace`` is required."""
    if workspace is None:
        return (False, "Workspace not initialized")
    if ".." in path or path.startswith("/"):
        return (False, "Invalid path: directory traversal not allowed")
    basename = os.path.basename(path)
    memory_dir = workspace.get_node_path("memory")
    if basename == "USER.md":
        resolved_path = workspace.get_node_path("USER.md")
    elif basename == "MEMORY.md":
        memory_rel = workspace.get_directory("MEMORY.md")
        resolved_path = os.path.join(memory_dir, memory_rel) if memory_dir and memory_rel else None
    elif re.match(r"^\d{4}-\d{2}-\d{2}\.md$", basename):
        daily_rel = workspace.get_directory("daily_memory")
        resolved_path = os.path.join(memory_dir, daily_rel, basename) if memory_dir and daily_rel else None
    else:
        resolved_path = os.path.join(memory_dir, basename) if memory_dir else None
    if resolved_path is None:
        return (False, f"Cannot resolve path: {path}")
    return (True, str(resolved_path))


async def memory_search_with_context(
    ctx: Optional[MemoryToolContext],
    query: str,
    max_results: Optional[int] = None,
    min_score: Optional[float] = None,
    session_key: Optional[str] = None,
) -> Dict[str, Any]:
    if ctx is None:
        return {
            "results": [],
            "disabled": True,
            "error": "Memory manager not available",
        }
    if not await ctx.ensure_manager():
        return {
            "results": [],
            "disabled": True,
            "error": "Memory manager not available",
        }
    if not ctx.manager:
        return {
            "results": [],
            "disabled": True,
            "error": "Memory manager not initialized",
        }
    try:
        opts: Dict[str, Any] = {}
        if max_results is not None:
            opts["max_results"] = max_results
        if min_score is not None:
            opts["min_score"] = min_score
        if session_key is not None:
            opts["session_key"] = session_key
        results = await ctx.manager.search(query, opts=opts if opts else None)
        for r in results:
            if r["start_line"] == r["end_line"]:
                r["citation"] = f"{r['path']}#L{r['start_line']}"
            else:
                r["citation"] = f"{r['path']}#L{r['start_line']}-L{r['end_line']}"
        status = ctx.manager.status()
        return {
            "results": results,
            "provider": status.get("provider"),
            "model": status.get("model"),
            "disabled": False,
        }
    except Exception as e:
        logger.error(f"Memory search failed: {e}")
        return {"results": [], "disabled": True, "error": str(e)}


async def memory_get_with_context(
    ctx: Optional[MemoryToolContext],
    path: str,
    from_line: Optional[int] = None,
    lines: Optional[int] = None,
) -> Dict[str, Any]:
    ws = ctx.workspace if ctx else None
    if ws is None:
        return {"path": path, "text": "", "disabled": True, "error": "Workspace not initialized"}
    is_valid, result = validate_memory_path(path, ws)
    if not is_valid:
        return {"path": path, "text": "", "disabled": True, "error": result}
    resolved_path = result
    if ctx is None:
        return {"path": resolved_path, "text": "", "disabled": True, "error": "Memory manager not available"}
    if not await ctx.ensure_manager():
        return {"path": resolved_path, "text": "", "disabled": True, "error": "Memory manager not available"}
    if not ctx.manager:
        return {"path": resolved_path, "text": "", "disabled": True, "error": "Memory manager not initialized"}
    try:
        rf = await ctx.manager.read_file(
            rel_path=resolved_path, from_line=from_line, lines=lines
        )
        return {**rf, "disabled": False}
    except Exception as e:
        logger.error(f"Memory get failed: {e}")
        return {"path": resolved_path, "text": "", "disabled": True, "error": str(e)}


async def write_memory_with_context(
    ctx: Optional[MemoryToolContext],
    path: str,
    content: str,
    append: bool = False,
) -> Dict[str, Any]:
    try:
        if ctx is None:
            return {"success": False, "path": path, "error": "Workspace not initialized"}
        ws = ctx.workspace if ctx else None
        if ws is None:
            return {"success": False, "path": path, "error": "Workspace not initialized"}
        is_valid, result = validate_memory_path(path, ws)
        if not is_valid:
            return {"success": False, "path": path, "error": result}
        resolved_path = result
        sys_op = ctx.sys_operation if ctx else None
        if sys_op:
            write_result = await sys_op.fs().write_file(
                resolved_path,
                content=content,
                create_if_not_exist=True,
                prepend_newline=append,
                append=True,
            )
            file_existed = write_result.data.size > 0
            logger.info(f"{'Appended to' if append else 'Wrote'} file: {resolved_path}")
            return {
                "success": True,
                "path": resolved_path,
                "fullPath": resolved_path,
                "appended": append,
                "fileExisted": file_existed,
            }
        logger.error("Memory write failed, no available sys_operation")
    except Exception as e:
        logger.error(f"Write failed: {e}")
        return {"success": False, "path": path, "error": str(e)}
    return {"success": False, "path": path, "error": "Memory write failed, no available sys_operation"}


async def edit_memory_with_context(
    ctx: Optional[MemoryToolContext],
    path: str,
    old_text: str,
    new_text: str,
) -> Dict[str, Any]:
    try:
        ws = ctx.workspace if ctx else None
        if ws is None:
            return {"success": False, "path": path, "error": "Workspace not initialized"}
        is_valid, result = validate_memory_path(path, ws)
        if not is_valid:
            return {"success": False, "path": path, "error": result}
        resolved_path = result
        sys_op = ctx.sys_operation if ctx else None
        if sys_op:
            read_result = await sys_op.fs().read_file(resolved_path)
            content = read_result.data.content
            if old_text not in content:
                return {
                    "success": False,
                    "path": path,
                    "error": "old_text not found in file. Use read_memory tool to check exact content.",
                }
            occurrences = content.count(old_text)
            if occurrences > 1:
                return {
                    "success": False,
                    "path": path,
                    "error": f"old_text appears {occurrences} times in file. Be more specific.",
                }
            new_content = content.replace(old_text, new_text, 1)
            await sys_op.fs().write_file(
                resolved_path,
                content=new_content,
                create_if_not_exist=True,
                prepend_newline=False,
                append_newline=False,
            )
            logger.info(f"Edited file: {resolved_path}")
            return {
                "success": True,
                "path": resolved_path,
                "replaced": old_text,
                "new_text": new_text,
            }
        logger.error("Edit failed, no available sys_operation")
        return {
            "success": False,
            "path": path,
            "error": "Edit failed, no available sys_operation.",
        }
    except Exception as e:
        logger.error(f"Edit failed: {e}")
        return {"success": False, "path": path, "error": str(e)}


def _line_range_to_fs_read(
    first_line: Optional[int], line_cap: Optional[int]
) -> Optional[tuple[int, int]]:
    """Map tool offset/limit to ``read_file(line_range=...)`` (1-based file lines; ``-1`` = through EOF)."""
    if first_line is None:
        return None
    if line_cap is not None:
        return (first_line, first_line + line_cap - 1)
    return (first_line, -1)


def _view_lines(
    all_lines: list[str],
    first_line: Optional[int],
    line_cap: Optional[int],
) -> tuple[str, int, int, int, bool]:
    """``first_line`` is 1-based; returns ``(excerpt, total, start_idx, end_idx, truncated)``."""
    total = len(all_lines)
    start_idx = max(0, first_line - 1) if first_line is not None else 0
    if line_cap is None:
        end_idx = total
    else:
        end_idx = min(start_idx + line_cap, total)
    text = "\n".join(all_lines[start_idx:end_idx])
    cut = line_cap is not None and end_idx < total
    return text, total, start_idx, end_idx, cut


async def read_memory_with_context(
    ctx: Optional[MemoryToolContext],
    path: str,
    offset: Optional[int] = None,
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    try:
        ws = ctx.workspace if ctx else None
        if ws is None:
            return {"success": False, "path": path, "content": "", "error": "Workspace not initialized"}
        is_valid, result = validate_memory_path(path, ws)
        if not is_valid:
            return {"success": False, "path": path, "content": "", "error": result}
        full_path = result
        sys_op = ctx.sys_operation if ctx else None
        if not sys_op:
            logger.error("Read memory failed, no available sys_operation")
            return {"success": False, "path": path, "error": "Read failed, no available sys_operation."}
        read_result = await sys_op.fs().read_file(
            full_path,
            line_range=_line_range_to_fs_read(offset, limit),
        )
        line_list = (read_result.data.content or "").split("\n")
        body, n_total, start_idx, end_idx, truncated = _view_lines(
            line_list, offset, limit
        )
        return {
            "success": True,
            "path": full_path,
            "content": body,
            "totalLines": n_total,
            "start_line": start_idx + 1,
            "end_line": end_idx,
            "truncated": truncated,
        }
    except Exception as e:
        logger.error(f"Read failed: {e}")
        return {"success": False, "path": path, "content": "", "error": str(e)}


__all__ = [
    "validate_memory_path",
    "memory_search_with_context",
    "memory_get_with_context",
    "write_memory_with_context",
    "edit_memory_with_context",
    "read_memory_with_context",
]
