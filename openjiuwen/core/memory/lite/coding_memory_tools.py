# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Coding Memory tools for JiuWenClaw - Using @tool decorator for openjiuwen."""

import os
import asyncio
from typing import Optional, Dict, Any, List, TYPE_CHECKING

from openjiuwen.core.foundation.tool.tool import tool
from openjiuwen.core.foundation.store.base_embedding import EmbeddingConfig
from openjiuwen.core.common.logging import memory_logger as logger
from openjiuwen.harness.workspace.workspace import Workspace

from .manager import MemoryIndexManager
from .config import MemorySettings, create_memory_settings, is_memory_enabled
from .frontmatter import parse_frontmatter, validate_frontmatter

if TYPE_CHECKING:
    from openjiuwen.core.sys_operation.sys_operation import SysOperation

_global_manager: Optional[MemoryIndexManager] = None
_global_workspace: "Workspace" = None
_global_settings: Optional[MemorySettings] = None
_global_agent_id: str = "default"
_global_embedding_config: Optional[EmbeddingConfig] = None
_global_sys_operation: Optional["SysOperation"] = None
_global_coding_memory_dir: str = None
MAX_INDEX_LINES = 200


def _upsert_memory_index(memory_dir: str, filename: str, frontmatter: Dict[str, str]):
    """增量更新 MEMORY.md 索引。只更新/插入目标文件对应的一行，不扫描其他文件。
    
    策略：
    - 读取现有 MEMORY.md → 按文件名查找对应行
    - 找到 → 原地替换该行(name/description 可能被 edit 改过)
    - 未找到 → 插入到开头(最新的在最前)
    - 超过 MAX_INDEX_LINES → 截断末尾
    """
    try:
        asyncio.get_event_loop().run_until_complete(
            _upsert_memory_index_async(memory_dir, filename, frontmatter)
        )
    except Exception as e:
        logger.error(f"Failed to upsert memory index: {e}")


async def _upsert_memory_index_async(memory_dir: str, filename: str, frontmatter: Dict[str, str]):
    """异步增量更新 MEMORY.md 索引。"""
    index_path = os.path.join(memory_dir, "MEMORY.md")
    new_entry = f"- [{frontmatter['name']}]({filename}) — {frontmatter['description']}"

    lines = []
    if _global_sys_operation:
        try:
            result = await _global_sys_operation.fs().read_file(index_path)
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
    if _global_sys_operation:
        await _global_sys_operation.fs().write_file(
            index_path, content=new_content, create_if_not_exist=True, prepend_newline=False
        )


def _remove_from_memory_index(memory_dir: str, filename: str):
    """从 MEMORY.md 索引中删除指定文件的条目。"""
    try:
        asyncio.get_event_loop().run_until_complete(_remove_from_memory_index_async(memory_dir, filename))
    except Exception as e:
        logger.error(f"Failed to remove from memory index: {e}")


async def _remove_from_memory_index_async(memory_dir: str, filename: str):
    """异步从 MEMORY.md 索引中删除指定文件的条目。"""
    index_path = os.path.join(memory_dir, "MEMORY.md")
    try:
        if not _global_sys_operation:
            logger.warning("No sys_operation available, cannot remove from index")
            return
        result = await _global_sys_operation.fs().read_file(index_path)
        if not result or not hasattr(result, 'data') or not result.data:
            return
        content = result.data.content
        lines = content.split("\n") if content else []
        lines = [line for line in lines if f"]({filename})" not in line]
        new_content = "\n".join(lines)
        await _global_sys_operation.fs().write_file(
            index_path, content=new_content, create_if_not_exist=True, prepend_newline=False
        )
    except Exception as e:
        logger.error(f"Failed to remove from memory index: {e}")


def _validate_coding_memory_path(path: str) -> tuple[bool, str]:
    if ".." in path or path.startswith("/"):
        return (False, "Invalid path: directory traversal not allowed")
    if not path.endswith(".md"):
        return (False, "Path must end with .md")
    coding_memory_dir = _global_workspace.get_node_path("coding_memory")
    resolved = os.path.join(coding_memory_dir, os.path.basename(path))
    return (True, resolved)


def get_embedding_config() -> Optional[EmbeddingConfig]:
    """Get the global embedding configuration.
    
    Returns:
        EmbeddingConfig instance if set, None otherwise.
    """
    return _global_embedding_config


def get_sys_operation() -> Optional["SysOperation"]:
    """Get the global sys_operation instance.
    
    Returns:
        SysOperation instance if set, None otherwise.
    """
    return _global_sys_operation


async def init_memory_manager_async(
    workspace: "Workspace", 
    agent_id: str = "default",
    embedding_config: Optional[EmbeddingConfig] = None,
    sys_operation: Optional["SysOperation"] = None,
) -> Optional[MemoryIndexManager]:
    """Initialize memory manager with file watching.
    
    Args:
        workspace: Workspace instance.
        agent_id: Agent identifier.
        embedding_config: Embedding configuration for vector search.
        sys_operation: SysOperation instance for file operations.
    
    Returns:
        MemoryIndexManager instance, or None if memory is disabled.
    """
    global _global_manager, _global_workspace, _global_settings, _global_agent_id, _global_embedding_config,\
        _global_sys_operation, _global_coding_memory_dir

    if not is_memory_enabled():
        logger.info("Memory system is disabled")
        return None
    
    if _global_manager is not None:
        return _global_manager

    node_path = workspace.get_node_path("coding_memory")
    coding_memory_dir = str(node_path) if node_path else ""
    settings = create_memory_settings(coding_memory_dir)

    _global_workspace = workspace
    _global_settings = settings
    _global_agent_id = agent_id
    _global_embedding_config = embedding_config
    _global_sys_operation = sys_operation
    _global_coding_memory_dir = coding_memory_dir
    
    try:
        _global_manager = await MemoryIndexManager.get(
            agent_id=agent_id,
            workspace=workspace,
            settings=settings,
            embedding_config=embedding_config,
            sys_operation=sys_operation,
        )
        
        if _global_manager:
            logger.info(f"Coding Memory manager initialized for: {coding_memory_dir}")
        
        return _global_manager
        
    except Exception as e:
        logger.error(f"Failed to initialize Coding Memory manager: {e}")
        return None


def _read_file_safe(filepath: str) -> str:
    """安全读取文件全文，文件不存在或读取失败返回空字符串"""
    try:
        return asyncio.get_event_loop().run_until_complete(_read_file_safe_async(filepath))
    except Exception:
        return ""


async def _read_file_safe_async(filepath: str) -> str:
    """异步安全读取文件全文，文件不存在或读取失败返回空字符串"""
    try:
        if not _global_sys_operation:
            return ""
        result = await _global_sys_operation.fs().read_file(filepath)
        if result and hasattr(result, 'data') and result.data:
            return result.data.content
        return ""
    except Exception:
        return ""


async def _read_head_async(filepath: str, max_lines: int = 30) -> str:
    """异步读取文件前 N 行，用于 frontmatter 提取（性能保护）"""
    try:
        if not _global_sys_operation:
            return ""
        result = await _global_sys_operation.fs().read_file(filepath, head=max_lines)
        if result and hasattr(result, 'data') and result.data:
            return result.data.content
        return ""
    except Exception:
        return ""


async def _count_memory_files_async(memory_dir: str) -> int:
    """异步统计目录下的 .md 记忆文件数(排除 MEMORY.md)"""
    try:
        if not _global_sys_operation:
            return 0
        result = await _global_sys_operation.fs().list_files(
            memory_dir,
            recursive=False
        )
        if result and hasattr(result, 'data') and result.data:
            return sum(1 for f in result.data.list_items if f.name != "MEMORY.md")
        return 0
    except Exception:
        return 0


@tool
async def coding_memory_read(
    path: str,
    offset: Optional[int] = None,
    limit: Optional[int] = None
) -> Dict[str, Any]:
    """读取 coding_memory/ 下的记忆文件。支持全文读取或按行范围读取。

    Args:
        path: 文件名(如 user_role.md)
        offset: 起始行号（从 1 开始），不传则从头读
        limit: 读取行数，不传则读到末尾

    Returns:
        文件内容字典
    """
    try:
        is_valid, result = _validate_coding_memory_path(path)
        if not is_valid:
            return {
                "success": False,
                "path": path,
                "content": "",
                "error": result
            }
        
        full_path = result
        
        if _global_sys_operation:
            line_range = None
            if offset is not None and limit is not None:
                line_range = (offset, offset + limit - 1)
            elif offset is not None:
                line_range = (offset, -1)
            
            read_result = await _global_sys_operation.fs().read_file(
                full_path,
                line_range=line_range,
            )
            content = read_result.data.content
            lines = content.split("\n") if content else []
            total_lines = len(lines)
            
            if offset is not None:
                start = max(0, offset - 1)
            else:
                start = 0
            
            if limit is not None:
                end = min(start + limit, total_lines)
            else:
                end = total_lines
            
            selected_lines = lines[start:end]
            selected_content = "\n".join(selected_lines)
            
            return {
                "success": True,
                "path": full_path,
                "content": selected_content,
                "totalLines": total_lines,
                "start_line": start + 1,
                "end_line": end,
                "truncated": limit is not None and end < total_lines
            }
        else:
            logger.error(f"Read memory failed, no available path _global_sys_operation")
            return {
                "success": False,
                "path": path,
                "error": f"Read failed, no available _global_sys_operation."
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
        # 1. 路径校验
        is_valid, resolved = _validate_coding_memory_path(path)
        if not is_valid:
            return {
                "success": False,
                "path": path,
                "error": resolved
            }

        # 2. Frontmatter 校验
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

        # 3. 写入文件
        if _global_sys_operation:
            write_result = await _global_sys_operation.fs().write_file(
                resolved,
                content=content,
                create_if_not_exist=True,
                append=True
            )
            file_existed = write_result.data.size > 0

            logger.info(f"Append content to file: {resolved}")

            # 4. 增量更新 MEMORY.md 索引
            _upsert_memory_index(_global_coding_memory_dir, os.path.basename(resolved), fm)

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
                "error": "no available _global_sys_operation"
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

        if not _global_sys_operation:
            return {"success": False, "error": "no available _global_sys_operation"}

        read_result = await _global_sys_operation.fs().read_file(resolved)
        if read_result is None or not hasattr(read_result, 'data') or read_result.data is None:
            return {"success": False, "error": f"failed to read file: {path}"}

        content = read_result.data.content
        if old_text not in content:
            return {"success": False, "error": "old_text not found in file"}

        count = content.count(old_text)
        if count > 1:
            return {"success": False, "error": f"old_text appears {count} times, please be more specific"}

        new_content = content.replace(old_text, new_text, 1)
        await _global_sys_operation.fs().write_file(resolved, content=new_content, create_if_not_exist=True)

        fm = parse_frontmatter(new_content)
        if fm and validate_frontmatter(fm)[0]:
            _upsert_memory_index(_global_coding_memory_dir, os.path.basename(resolved), fm)

        return {"success": True, "path": resolved, "new_content": new_content}

    except Exception as e:
        logger.error(f"coding_memory_edit failed: {e}")
        return {"success": False, "error": str(e)}


def get_decorated_tools() -> List:
    """获取使用 @tool 装饰器的工具列表"""
    return [coding_memory_read, coding_memory_write, coding_memory_edit]
