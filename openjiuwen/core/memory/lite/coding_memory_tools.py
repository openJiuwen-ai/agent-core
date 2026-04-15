# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Coding Memory tools for JiuWenClaw - Using @tool decorator for openjiuwen."""

import os
from typing import Optional, Dict, Any, List, TYPE_CHECKING

from openjiuwen.core.foundation.tool.tool import tool
from openjiuwen.core.foundation.store.base_embedding import EmbeddingConfig
from openjiuwen.core.common.logging import memory_logger as logger
from openjiuwen.harness.workspace.workspace import Workspace

from .manager import MemoryIndexManager, MemoryManagerParams
from .config import MemorySettings, create_memory_settings, is_memory_enabled
from .frontmatter import parse_frontmatter, validate_frontmatter

if TYPE_CHECKING:
    from openjiuwen.core.sys_operation.sys_operation import SysOperation

coding_memory_manager: Optional[MemoryIndexManager] = None
coding_memory_workspace: "Workspace" = None
coding_memory_sys_operation: Optional["SysOperation"] = None
coding_memory_dir: str = "coding_memory"
MAX_INDEX_LINES = 200


async def _upsert_memory_index(memory_dir: str, filename: str, frontmatter: Dict[str, str]):
    """Async incremental update of the MEMORY.md index."""
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
    """Async removal of the MEMORY.md index line for ``filename``."""
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
) -> Optional[MemoryIndexManager]:

    global coding_memory_manager, coding_memory_workspace, coding_memory_sys_operation, coding_memory_dir

    if not is_memory_enabled():
        logger.info("Memory system is disabled")
        return None
    
    if coding_memory_manager is not None:
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
async def coding_memory_write(path: str, content: str):
    try:
        # 1. Path validation
        is_valid, resolved = _validate_coding_memory_path(path)
        if not is_valid:
            return {
                "success": False,
                "path": path,
                "error": resolved
            }

        # 2. Frontmatter validation
        fm = parse_frontmatter(content)
        if fm is None:
            return {
                "success": False,
                "path": path,
                "error": "must contain frontmatter(name/description/type)"
            }
        valid, err = validate_frontmatter(fm)
        if not valid:
            return {
                "success": False,
                "path": path,
                "error": err
            }

        # 3. Write file
        if coding_memory_sys_operation:
            write_result = await coding_memory_sys_operation.fs().write_file(
                resolved,
                content=content,
                create_if_not_exist=True,
                append=True
            )
            file_existed = write_result.data.size > 0

            logger.info(f"Append content to file: {resolved}")

            # 4. Incrementally update MEMORY.md index
            await _upsert_memory_index(coding_memory_dir, os.path.basename(resolved), fm)

            return {
                "success": True,
                "path": resolved,
                "fullPath": resolved,
                "appended": True,
                "fileExisted": file_existed,
                "type": fm.get("type")
            }
        else:
            logger.error(f"Memory write failed, no available _global_sys_operation")
            return {
                "success": False,
                "path": path,
                "error": "no available coding_memory_sys_operation"
            }

    except Exception as e:
        logger.error(f"Update memory index failed: {e}")
        return {
            "success": False,
            "path": path,
            "error": str(e)
        }


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
