# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Coding memory tool implementations without ``@tool``."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from openjiuwen.core.common.logging import memory_logger as logger
from openjiuwen.core.memory.lite.coding_memory_tool_context import CodingMemoryToolContext
from openjiuwen.core.memory.lite.frontmatter import parse_frontmatter, validate_frontmatter

if TYPE_CHECKING:
    from openjiuwen.harness.workspace.workspace import Workspace

MAX_INDEX_LINES = 200


def validate_coding_memory_path(path: str, workspace: "Workspace") -> tuple[bool, str]:
    if workspace is None:
        return (False, "Workspace not initialized")
    if ".." in path or path.startswith("/"):
        return (False, "Invalid path: directory traversal not allowed")
    if not path.endswith(".md"):
        return (False, "Path must end with .md")
    memory_dir = workspace.get_node_path("coding_memory")
    if not memory_dir:
        return (False, "coding_memory node not configured")
    resolved = os.path.join(memory_dir, os.path.basename(path))
    return (True, resolved)


async def upsert_coding_memory_index(
    memory_dir: str,
    filename: str,
    frontmatter: Dict[str, str],
    sys_operation: Optional[Any],
) -> None:
    if not sys_operation:
        return
    index_path = os.path.join(memory_dir, "MEMORY.md")
    new_entry = f"- [{frontmatter['name']}]({filename}) — {frontmatter['description']}"
    lines: List[str] = []
    try:
        result = await sys_operation.fs().read_file(index_path)
        if result and hasattr(result, "data") and result.data:
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
    await sys_operation.fs().write_file(
        index_path, content=new_content, create_if_not_exist=True, prepend_newline=False
    )


async def remove_from_coding_memory_index(
    memory_dir: str,
    filename: str,
    sys_operation: Optional[Any],
) -> None:
    if not sys_operation:
        return
    index_path = os.path.join(memory_dir, "MEMORY.md")
    try:
        result = await sys_operation.fs().read_file(index_path)
        if not result or not hasattr(result, "data") or not result.data:
            return
        content = result.data.content
        lines = content.split("\n") if content else []
        lines = [line for line in lines if f"]({filename})" not in line]
        new_content = "\n".join(lines)
        await sys_operation.fs().write_file(
            index_path, content=new_content, create_if_not_exist=True, prepend_newline=False
        )
    except Exception as e:
        logger.error(f"Failed to remove from memory index: {e}")


async def coding_memory_read_with_context(
    ctx: Optional[CodingMemoryToolContext],
    path: str,
    offset: Optional[int] = None,
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    try:
        ws = ctx.workspace if ctx else None
        if ws is None:
            return {"success": False, "path": path, "content": "", "error": "Workspace not initialized"}
        is_valid, result = validate_coding_memory_path(path, ws)
        if not is_valid:
            return {"success": False, "path": path, "content": "", "error": result}
        full_path = result
        sys_op = ctx.sys_operation if ctx else None
        if not sys_op:
            logger.error("Read memory failed, no available sys_operation")
            return {"success": False, "path": path, "error": "Read failed, no available sys_operation."}
        if offset is not None and limit is not None:
            fs_lines = (offset, offset + limit - 1)
        elif offset is not None:
            fs_lines = (offset, -1)
        else:
            fs_lines = None
        payload = await sys_op.fs().read_file(full_path, line_range=fs_lines)
        data = (payload.data.content or "")
        # Split, then [from_idx, to_idx) in 0-based list indices; read_file offset is 1-based.
        rows = data.split("\n")
        n = len(rows)
        from_idx = 0 if offset is None else max(0, offset - 1)
        to_idx = n if limit is None else min(from_idx + limit, n)
        return {
            "success": True,
            "path": full_path,
            "content": "\n".join(rows[from_idx:to_idx]),
            "totalLines": n,
            "start_line": from_idx + 1,
            "end_line": to_idx,
            "truncated": limit is not None and to_idx < n,
        }
    except Exception as e:
        logger.error(f"Read failed: {e}")
        return {"success": False, "path": path, "content": "", "error": str(e)}


async def coding_memory_write_with_context(
    ctx: Optional[CodingMemoryToolContext],
    path: str,
    content: str,
) -> Dict[str, Any]:
    try:
        if ctx is None:
            return {"success": False, "path": path, "error": "Workspace not initialized"}
        ws = ctx.workspace
        if ws is None:
            return {"success": False, "path": path, "error": "Workspace not initialized"}
        is_valid, resolved = validate_coding_memory_path(path, ws)
        if not is_valid:
            return {"success": False, "path": path, "error": resolved}
        fm = parse_frontmatter(content)
        if fm is None:
            return {"success": False, "path": path, "error": "must contain frontmatter(name/description/type)"}
        valid, err = validate_frontmatter(fm)
        if not valid:
            return {"success": False, "path": path, "error": err}
        sys_op = ctx.sys_operation
        if sys_op:
            write_result = await sys_op.fs().write_file(
                resolved,
                content=content,
                create_if_not_exist=True,
                append=True,
            )
            file_existed = write_result.data.size > 0
            logger.info(f"Append content to file: {resolved}")
            await upsert_coding_memory_index(
                ctx.coding_memory_dir or "",
                os.path.basename(resolved),
                fm,
                sys_op,
            )
            return {
                "success": True,
                "path": resolved,
                "fullPath": resolved,
                "appended": True,
                "fileExisted": file_existed,
                "type": fm.get("type"),
            }
        logger.error("Memory write failed, no available sys_operation")
        return {"success": False, "path": path, "error": "no available coding_memory_sys_operation"}
    except Exception as e:
        logger.error(f"Update memory index failed: {e}")
        return {"success": False, "path": path, "error": str(e)}


async def coding_memory_edit_with_context(
    ctx: Optional[CodingMemoryToolContext],
    path: str,
    old_text: str,
    new_text: str,
) -> Dict[str, Any]:
    try:
        if not old_text:
            return {"success": False, "error": "old_text cannot be empty"}
        if ctx is None:
            return {"success": False, "error": "Workspace not initialized"}
        ws = ctx.workspace
        if ws is None:
            return {"success": False, "error": "Workspace not initialized"}
        is_valid, resolved = validate_coding_memory_path(path, ws)
        if not is_valid:
            return {"success": False, "error": resolved}
        sys_op = ctx.sys_operation
        if not sys_op:
            return {"success": False, "error": "no available sys_operation"}
        read_result = await sys_op.fs().read_file(resolved)
        if read_result is None or not hasattr(read_result, "data") or read_result.data is None:
            return {"success": False, "error": f"failed to read file: {path}"}
        blob = read_result.data.content
        occurrences = blob.count(old_text)
        if occurrences == 0:
            return {"success": False, "error": "old_text not found in file"}
        if occurrences > 1:
            return {
                "success": False,
                "error": f"old_text appears {occurrences} times, please be more specific",
            }
        new_content = blob.replace(old_text, new_text, 1)
        await sys_op.fs().write_file(resolved, content=new_content, create_if_not_exist=True)
        fm = parse_frontmatter(new_content)
        if fm:
            valid, _ = validate_frontmatter(fm)
            if valid:
                await upsert_coding_memory_index(
                    ctx.coding_memory_dir or "",
                    os.path.basename(resolved),
                    fm,
                    sys_op,
                )
        return {"success": True, "path": resolved, "new_content": new_content}
    except Exception as e:
        logger.error(f"coding_memory_edit failed: {e}")
        return {"success": False, "error": str(e)}


__all__ = [
    "MAX_INDEX_LINES",
    "validate_coding_memory_path",
    "upsert_coding_memory_index",
    "remove_from_coding_memory_index",
    "coding_memory_read_with_context",
    "coding_memory_write_with_context",
    "coding_memory_edit_with_context",
]
