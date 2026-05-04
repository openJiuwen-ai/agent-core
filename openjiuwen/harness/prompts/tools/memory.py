# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Bilingual descriptions and input params for memory tools."""

from __future__ import annotations

from typing import Any, Dict

from openjiuwen.harness.prompts.tools.base import (
    ToolMetadataProvider,
)


# ---------------------------------------------------------------------------
# Tool-level descriptions
# ---------------------------------------------------------------------------


MEMORY_SEARCH_DESCRIPTION: Dict[str, str] = {
    "cn": "在长期记忆中检索过往信息（决策、偏好、人物、日期、TODO 等），返回相关片段与引用线索。",
    "en": (
        "Search long-term memory (prior decisions, preferences, people, dates, todos) "
        "and return relevant snippets and references."
    ),
}


MEMORY_GET_DESCRIPTION: Dict[str, str] = {
    "cn": "按行号切片读取 memory/ 下的记忆 Markdown 文件内容（from_line + lines）。",
    "en": "Read a slice of a memory markdown file under memory/ (from_line + lines).",
}


WRITE_MEMORY_DESCRIPTION: Dict[str, str] = {
    "cn": "写入记忆内容到 memory/ 下的 Markdown 文件；支持覆盖写或追加写（append）。",
    "en": "Write memory content to a markdown file under memory/; supports overwrite or append.",
}


EDIT_MEMORY_DESCRIPTION: Dict[str, str] = {
    "cn": "在 memory/ 下的记忆文件中做精确字符串替换（old_text → new_text）。",
    "en": "Perform an exact string replacement inside a memory file (old_text → new_text).",
}


READ_MEMORY_DESCRIPTION: Dict[str, str] = {
    "cn": "按 offset/limit 读取 memory/ 下记忆文件的部分内容（用于分页阅读）。",
    "en": "Read a portion of a memory file under memory/ using offset/limit (for paging).",
}

# ---------------------------------------------------------------------------
# Parameter-level bilingual descriptions
# ---------------------------------------------------------------------------


MEMORY_SEARCH_PARAMS: Dict[str, Dict[str, str]] = {
    "query": {"cn": "检索关键词或问题", "en": "Search query string"},
    "max_results": {"cn": "最多返回条数（可选）", "en": "Maximum number of results (optional)"},
    "min_score": {"cn": "最小相关度阈值（可选）", "en": "Minimum relevance score threshold (optional)"},
    "session_key": {"cn": "会话键（可选，用于上下文隔离/过滤）", "en": "Session key (optional, for scoping/filtering)"},
}


MEMORY_GET_PARAMS: Dict[str, Dict[str, str]] = {
    "path": {"cn": "memory/ 下的目标文件路径（相对路径）", "en": "Target path under memory/ (relative path)"},
    "from_line": {"cn": "起始行号（可选）", "en": "Starting line number (optional)"},
    "lines": {"cn": "读取行数（可选）", "en": "Number of lines to read (optional)"},
}


WRITE_MEMORY_PARAMS: Dict[str, Dict[str, str]] = {
    "path": {"cn": "memory/ 下的目标文件路径（相对路径）", "en": "Target path under memory/ (relative path)"},
    "content": {"cn": "要写入的内容", "en": "Content to write"},
    "append": {"cn": "是否追加写入（默认 false）", "en": "Append to file instead of overwrite (default false)"},
}


EDIT_MEMORY_PARAMS: Dict[str, Dict[str, str]] = {
    "path": {"cn": "memory/ 下的目标文件路径（相对路径）", "en": "Target path under memory/ (relative path)"},
    "old_text": {"cn": "要替换的原始文本", "en": "Original text to replace"},
    "new_text": {"cn": "替换后的新文本", "en": "New replacement text"},
}


READ_MEMORY_PARAMS: Dict[str, Dict[str, str]] = {
    "path": {"cn": "memory/ 下的目标文件路径（相对路径）", "en": "Target path under memory/ (relative path)"},
    "offset": {"cn": "从第几行开始读取（可选）", "en": "Line offset to start reading from (optional)"},
    "limit": {"cn": "最多读取多少行（可选）", "en": "Maximum number of lines to read (optional)"},
}

# ---------------------------------------------------------------------------
# Builder functions
# ---------------------------------------------------------------------------


def _desc(params: Dict[str, Dict[str, str]], key: str, lang: str) -> str:
    return params[key].get(lang, params[key]["cn"])


def get_memory_search_input_params(language: str = "cn") -> Dict[str, Any]:
    p = MEMORY_SEARCH_PARAMS
    return {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": _desc(p, "query", language)},
            "max_results": {"type": "integer", "description": _desc(p, "max_results", language)},
            "min_score": {"type": "number", "description": _desc(p, "min_score", language)},
            "session_key": {"type": "string", "description": _desc(p, "session_key", language)},
        },
        "required": ["query"],
    }


def get_memory_get_input_params(language: str = "cn") -> Dict[str, Any]:
    p = MEMORY_GET_PARAMS
    return {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": _desc(p, "path", language)},
            "from_line": {"type": "integer", "description": _desc(p, "from_line", language)},
            "lines": {"type": "integer", "description": _desc(p, "lines", language)},
        },
        "required": ["path"],
    }


def get_write_memory_input_params(language: str = "cn") -> Dict[str, Any]:
    p = WRITE_MEMORY_PARAMS
    return {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": _desc(p, "path", language)},
            "content": {"type": "string", "description": _desc(p, "content", language)},
            "append": {"type": "boolean", "description": _desc(p, "append", language)},
        },
        "required": ["path", "content"],
    }


def get_edit_memory_input_params(language: str = "cn") -> Dict[str, Any]:
    p = EDIT_MEMORY_PARAMS
    return {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": _desc(p, "path", language)},
            "old_text": {"type": "string", "description": _desc(p, "old_text", language)},
            "new_text": {"type": "string", "description": _desc(p, "new_text", language)},
        },
        "required": ["path", "old_text", "new_text"],
    }


def get_read_memory_input_params(language: str = "cn") -> Dict[str, Any]:
    p = READ_MEMORY_PARAMS
    return {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": _desc(p, "path", language)},
            "offset": {"type": "integer", "description": _desc(p, "offset", language)},
            "limit": {"type": "integer", "description": _desc(p, "limit", language)},
        },
        "required": ["path"],
    }


class MemorySearchMetadataProvider(ToolMetadataProvider):
    """MemorySearch ToolMetaProvider。"""


    def get_name(self) -> str:
        return "memory_search"


    def get_description(self, language: str = "cn") -> str:
        return MEMORY_SEARCH_DESCRIPTION.get(language, MEMORY_SEARCH_DESCRIPTION["cn"])


    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return get_memory_search_input_params(language)


class MemoryGetMetadataProvider(ToolMetadataProvider):
    """MemoryGet ToolMetaProvider。"""


    def get_name(self) -> str:
        return "memory_get"


    def get_description(self, language: str = "cn") -> str:
        return MEMORY_GET_DESCRIPTION.get(language, MEMORY_GET_DESCRIPTION["cn"])


    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return get_memory_get_input_params(language)


class WriteMemoryMetadataProvider(ToolMetadataProvider):
    """WriteMemory ToolMetaProvider。"""


    def get_name(self) -> str:
        return "write_memory"


    def get_description(self, language: str = "cn") -> str:
        return WRITE_MEMORY_DESCRIPTION.get(language, WRITE_MEMORY_DESCRIPTION["cn"])


    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return get_write_memory_input_params(language)


class EditMemoryMetadataProvider(ToolMetadataProvider):
    """EditMemory ToolMetaProvider。"""


    def get_name(self) -> str:
        return "edit_memory"


    def get_description(self, language: str = "cn") -> str:
        return EDIT_MEMORY_DESCRIPTION.get(language, EDIT_MEMORY_DESCRIPTION["cn"])


    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return get_edit_memory_input_params(language)


class ReadMemoryMetadataProvider(ToolMetadataProvider):
    """ReadMemory ToolMetaProvider。"""


    def get_name(self) -> str:
        return "read_memory"


    def get_description(self, language: str = "cn") -> str:
        return READ_MEMORY_DESCRIPTION.get(language, READ_MEMORY_DESCRIPTION["cn"])


    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return get_read_memory_input_params(language)


__all__ = [
    "MemorySearchMetadataProvider",
    "MemoryGetMetadataProvider",
    "WriteMemoryMetadataProvider",
    "EditMemoryMetadataProvider",
    "ReadMemoryMetadataProvider",
]