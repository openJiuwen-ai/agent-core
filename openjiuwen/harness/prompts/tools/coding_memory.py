# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Bilingual descriptions and input params for coding memory tools."""

from __future__ import annotations

from typing import Any, Dict

from openjiuwen.harness.prompts.tools.base import ToolMetadataProvider


CODING_MEMORY_READ_DESCRIPTION: Dict[str, str] = {
    "cn": "按 offset/limit 读取 coding_memory/ 下记忆文件的部分内容（用于分页阅读）。",
    "en": "Read a portion of a memory file under coding_memory/ using offset/limit (for paging).",
}


CODING_MEMORY_WRITE_DESCRIPTION: Dict[str, str] = {
    "cn": "写入记忆内容到 coding_memory/ 下的 Markdown 文件（要求 frontmatter）。",
    "en": "Write memory content to a markdown file under coding_memory/ (frontmatter required).",
}


CODING_MEMORY_EDIT_DESCRIPTION: Dict[str, str] = {
    "cn": "在 coding_memory/ 下的记忆文件中做精确字符串替换（old_text → new_text）。",
    "en": "Perform an exact string replacement inside a coding memory file (old_text → new_text).",
}


CODING_MEMORY_READ_PARAMS: Dict[str, Dict[str, str]] = {
    "path": {"cn": "coding_memory/ 下的目标文件路径（相对路径）", "en": "Target path under coding_memory/ (relative path)"},
    "offset": {"cn": "从第几行开始读取（可选）", "en": "Line offset to start reading from (optional)"},
    "limit": {"cn": "最多读取多少行（可选）", "en": "Maximum number of lines to read (optional)"},
}


CODING_MEMORY_WRITE_PARAMS: Dict[str, Dict[str, str]] = {
    "path": {"cn": "coding_memory/ 下的目标文件路径（相对路径）", "en": "Target path under coding_memory/ (relative path)"},
    "content": {"cn": "要写入的内容（含 frontmatter）", "en": "Content to write (with frontmatter)"},
}


CODING_MEMORY_EDIT_PARAMS: Dict[str, Dict[str, str]] = {
    "path": {"cn": "coding_memory/ 下的目标文件路径（相对路径）", "en": "Target path under coding_memory/ (relative path)"},
    "old_text": {"cn": "要替换的原始文本", "en": "Original text to replace"},
    "new_text": {"cn": "替换后的新文本", "en": "New replacement text"},
}


def _desc(params: Dict[str, Dict[str, str]], key: str, language: str) -> str:
    return params[key].get(language, params[key]["cn"])


def get_coding_memory_read_input_params(language: str = "cn") -> Dict[str, Any]:
    p = CODING_MEMORY_READ_PARAMS
    return {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": _desc(p, "path", language)},
            "offset": {"type": "integer", "description": _desc(p, "offset", language)},
            "limit": {"type": "integer", "description": _desc(p, "limit", language)},
        },
        "required": ["path"],
    }


def get_coding_memory_write_input_params(language: str = "cn") -> Dict[str, Any]:
    p = CODING_MEMORY_WRITE_PARAMS
    return {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": _desc(p, "path", language)},
            "content": {"type": "string", "description": _desc(p, "content", language)},
        },
        "required": ["path", "content"],
    }


def get_coding_memory_edit_input_params(language: str = "cn") -> Dict[str, Any]:
    p = CODING_MEMORY_EDIT_PARAMS
    return {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": _desc(p, "path", language)},
            "old_text": {"type": "string", "description": _desc(p, "old_text", language)},
            "new_text": {"type": "string", "description": _desc(p, "new_text", language)},
        },
        "required": ["path", "old_text", "new_text"],
    }


class CodingMemoryReadMetadataProvider(ToolMetadataProvider):
    def get_name(self) -> str:
        return "coding_memory_read"


    def get_description(self, language: str = "cn") -> str:
        return CODING_MEMORY_READ_DESCRIPTION.get(language, CODING_MEMORY_READ_DESCRIPTION["cn"])


    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return get_coding_memory_read_input_params(language)


class CodingMemoryWriteMetadataProvider(ToolMetadataProvider):
    def get_name(self) -> str:
        return "coding_memory_write"


    def get_description(self, language: str = "cn") -> str:
        return CODING_MEMORY_WRITE_DESCRIPTION.get(language, CODING_MEMORY_WRITE_DESCRIPTION["cn"])


    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return get_coding_memory_write_input_params(language)


class CodingMemoryEditMetadataProvider(ToolMetadataProvider):
    def get_name(self) -> str:
        return "coding_memory_edit"


    def get_description(self, language: str = "cn") -> str:
        return CODING_MEMORY_EDIT_DESCRIPTION.get(language, CODING_MEMORY_EDIT_DESCRIPTION["cn"])


    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return get_coding_memory_edit_input_params(language)


__all__ = [
    "CodingMemoryReadMetadataProvider",
    "CodingMemoryWriteMetadataProvider",
    "CodingMemoryEditMetadataProvider",
]
