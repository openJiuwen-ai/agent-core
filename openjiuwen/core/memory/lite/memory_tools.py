# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Memory tools for JiuWenClaw - Using @tool decorator for openjiuwen."""

import os
import re
from typing import Optional, Dict, Any, List, TYPE_CHECKING

from openjiuwen.core.foundation.tool.tool import tool
from openjiuwen.core.foundation.store.base_embedding import EmbeddingConfig
from openjiuwen.core.common.logging import memory_logger as logger
from openjiuwen.deepagents.workspace.workspace import WorkspaceNode, Workspace

from .manager import MemoryIndexManager
from .config import MemorySettings, create_memory_settings, is_memory_enabled

if TYPE_CHECKING:
    from openjiuwen.core.sys_operation.sys_operation import SysOperation

_global_manager: Optional[MemoryIndexManager] = None
_global_workspace: "Workspace" = None
_global_settings: Optional[MemorySettings] = None
_global_agent_id: str = "default"
_global_embedding_config: Optional[EmbeddingConfig] = None
_global_sys_operation: Optional["SysOperation"] = None


def _validate_memory_path(path: str) -> tuple[bool, str]:
    """Validate that path is within memory directory.
    
    Returns:
        (is_valid, resolved_path_or_error)
    """
    if ".." in path or path.startswith("/"):
        return (False, "Invalid path: directory traversal not allowed")
    
    if _global_workspace is None:
        return (False, "Workspace not initialized")
    
    basename = os.path.basename(path)
    memory_dir = _global_workspace.get_node_path("memory")

    if basename == "USER.md":
        resolved_path = _global_workspace.get_node_path("USER.md")
    elif basename == "MEMORY.md":
        memory_rel = _global_workspace.get_directory("MEMORY.md")
        resolved_path = os.path.join(memory_dir, memory_rel) if memory_dir and memory_rel else None
    elif re.match(r'^\d{4}-\d{2}-\d{2}\.md$', basename):
        daily_rel = _global_workspace.get_directory("daily_memory")
        resolved_path = os.path.join(memory_dir, daily_rel, basename) if memory_dir and daily_rel else None
    else:
        resolved_path = os.path.join(memory_dir, basename) if memory_dir else None

    if resolved_path is None:
        return (False, f"Cannot resolve path: {path}")

    return (True, str(resolved_path))


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
        _global_sys_operation

    if not is_memory_enabled():
        logger.info("Memory system is disabled")
        return None
    
    if _global_manager is not None:
        return _global_manager

    memory_dir = str(workspace.get_node_path("memory")) if workspace.get_node_path("memory") else ""
    settings = create_memory_settings(memory_dir)

    _global_workspace = workspace
    _global_settings = settings
    _global_agent_id = agent_id
    _global_embedding_config = embedding_config
    _global_sys_operation = sys_operation
    
    try:
        _global_manager = await MemoryIndexManager.get(
            agent_id=agent_id,
            workspace=workspace,
            settings=settings,
            embedding_config=embedding_config,
            sys_operation=sys_operation,
        )
        
        if _global_manager:
            logger.info(f"Memory manager initialized for: {memory_dir}")
        
        return _global_manager
        
    except Exception as e:
        logger.error(f"Failed to initialize memory manager: {e}")
        return None


async def _ensure_global_manager() -> bool:
    """Ensure global memory manager is initialized."""
    global _global_manager, _global_settings, _global_workspace, _global_agent_id
    
    if _global_manager is not None:
        return True
    
    try:
        _global_settings = _global_settings or MemorySettings()
        _global_manager = await MemoryIndexManager.get(
            agent_id=_global_agent_id,
            workspace=_global_workspace,
            settings=_global_settings,
            sys_operation=_global_sys_operation,
        )
        return True
    except Exception as e:
        logger.error(f"Failed to initialize global memory manager: {e}")
        return False


@tool(
    name="memory_search",
    description="在长期记忆系统中搜索用户的记忆信息。在回答关于之前的工作内容、决策、日期、人物、偏好或待办事项的问题之前，必须先调用此工具。",
)
async def memory_search(
    query: str,
    max_results: Optional[int] = None,
    min_score: Optional[float] = None,
    session_key: Optional[str] = None
) -> Dict[str, Any]:
    """在长期记忆系统中搜索用户的记忆信息。在回答关于之前的工作内容、决策、日期、人物、偏好或待办事项的问题之前，必须先调用此工具。

    Args:
        query: 搜索查询内容
        max_results: 最大返回结果数量 (1-50)
        min_score: 最小相关性分数 (0-1)
        session_key: 可选的会话键

    Returns:
        搜索结果字典，包含 results 列表
    """
    if not await _ensure_global_manager():
        return {
            "results": [],
            "disabled": True,
            "error": "Memory manager not available"
        }
    
    if not _global_manager:
        return {
            "results": [],
            "disabled": True,
            "error": "Memory manager not initialized"
        }
    
    try:
        opts = {}
        if max_results is not None:
            opts["max_results"] = max_results
        if min_score is not None:
            opts["min_score"] = min_score
        if session_key is not None:
            opts["session_key"] = session_key
        
        results = await _global_manager.search(query, opts=opts if opts else None)
        
        for r in results:
            if r["start_line"] == r["end_line"]:
                r["citation"] = f"{r['path']}#L{r['start_line']}"
            else:
                r["citation"] = f"{r['path']}#L{r['start_line']}-L{r['end_line']}"
        
        status = _global_manager.status()
        
        return {
            "results": results,
            "provider": status.get("provider"),
            "model": status.get("model"),
            "disabled": False
        }
        
    except Exception as e:
        logger.error(f"Memory search failed: {e}")
        return {
            "results": [],
            "disabled": True,
            "error": str(e)
        }


@tool
async def memory_get(
    path: str,
    from_line: Optional[int] = None,
    lines: Optional[int] = None
) -> Dict[str, Any]:
    """安全地读取 memory/*.md 文件的指定行。在 memory_search 之后使用，只读取需要的行，保持上下文简洁。

    Args:
        path: 文件路径，仅允许 memory/ 目录下的文件
        from_line: 起始行号 (从1开始)
        lines: 读取的行数

    Returns:
        文件内容字典
    """
    is_valid, result = _validate_memory_path(path)
    if not is_valid:
        return {
            "path": path,
            "text": "",
            "disabled": True,
            "error": result
        }

    resolved_path = result

    if not await _ensure_global_manager():
        return {
            "path": resolved_path,
            "text": "",
            "disabled": True,
            "error": "Memory manager not available"
        }

    if not _global_manager:
        return {
            "path": resolved_path,
            "text": "",
            "disabled": True,
            "error": "Memory manager not initialized"
        }

    try:
        result = await _global_manager.read_file(
            rel_path=resolved_path,
            from_line=from_line,
            lines=lines
        )
        return {
            **result,
            "disabled": False
        }

    except Exception as e:
        logger.error(f"Memory get failed: {e}")
        return {
            "path": resolved_path,
            "text": "",
            "disabled": True,
            "error": str(e)
        }


@tool
async def write_memory(
    path: str,
    content: str,
    append: bool = False
) -> Dict[str, Any]:
    """在 memory 目录下创建或更新记忆文件。仅用于写入记忆相关内容，如 USER.md、MEMORY.md 或 memory/*.md 文件。
    禁止用于创建代码文件、配置文件或其他非记忆类文件。

    Args:
        path: 文件路径，仅允许 memory/ 目录下的文件（如 "memory/xxx.md" 或 "USER.md"）
        content: 要写入的内容
        append: 是否追加模式 (默认覆盖)

    Returns:
        操作结果字典
    """
    try:
        is_valid, result = _validate_memory_path(path)
        if not is_valid:
            return {
                "success": False,
                "path": path,
                "error": result
            }
        
        resolved_path = result
        
        if _global_sys_operation:
            write_result = await _global_sys_operation.fs().write_file(
                resolved_path,
                content=content,
                create_if_not_exist=True,
                prepend_newline=append,
                append=True
            )
            file_existed = write_result.data.size > 0
            
            logger.info(f"{'Appended to' if append else 'Wrote'} file: {resolved_path}")
            
            return {
                "success": True,
                "path": resolved_path,
                "fullPath": resolved_path,
                "appended": append,
                "fileExisted": file_existed
            }
        else:
            logger.error(f"Memory write failed, no available _global_sys_operation")
        
    except Exception as e:
        logger.error(f"Write failed: {e}")
        return {
            "success": False,
            "path": path,
            "error": str(e)
        }


@tool
async def edit_memory(
    path: str,
    old_text: str,
    new_text: str
) -> Dict[str, Any]:
    """精确编辑 memory 目录下的文件内容。仅用于更新记忆文件（如 USER.md、MEMORY.md）。
    old_text 必须完全匹配文件中的内容。如果 old_text 出现多次，需要更具体地指定。

    Args:
        path: 文件路径，仅允许 memory/ 目录下的文件
        old_text: 要查找的文本 (必须完全匹配)
        new_text: 替换的文本

    Returns:
        操作结果字典
    """
    try:
        is_valid, result = _validate_memory_path(path)
        if not is_valid:
            return {
                "success": False,
                "path": path,
                "error": result
            }
        
        resolved_path = result
        
        if _global_sys_operation:
            read_result = await _global_sys_operation.fs().read_file(resolved_path)
            content = read_result.data.content
            
            if old_text not in content:
                return {
                    "success": False,
                    "path": path,
                    "error": "old_text not found in file. Use read_memory tool to check exact content."
                }
            
            occurrences = content.count(old_text)
            if occurrences > 1:
                return {
                    "success": False,
                    "path": path,
                    "error": f"old_text appears {occurrences} times in file. Be more specific."
                }
            
            new_content = content.replace(old_text, new_text, 1)
            
            await _global_sys_operation.fs().write_file(
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
                "new_text": new_text
            }
        else:
            logger.error(f"Edit failed, no available _global_sys_operation")
            return {
                "success": False,
                "path": path,
                "error": f"Edit failed, no available _global_sys_operation."
            }
        
    except Exception as e:
        logger.error(f"Edit failed: {e}")
        return {
            "success": False,
            "path": path,
            "error": str(e)
        }


@tool
async def read_memory(
    path: str,
    offset: Optional[int] = None,
    limit: Optional[int] = None
) -> Dict[str, Any]:
    """读取 memory 目录下的文件内容。仅用于读取记忆文件（如 USER.md、MEMORY.md 或 memory/*.md）。

    Args:
        path: 文件路径，仅允许 memory/ 目录下的文件
        offset: 起始行号 (从1开始)
        limit: 读取的行数

    Returns:
        文件内容字典
    """
    try:
        is_valid, result = _validate_memory_path(path)
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


def get_decorated_tools() -> List:
    """获取使用 @tool 装饰器的工具列表"""
    return [memory_search, memory_get, write_memory, edit_memory, read_memory]
