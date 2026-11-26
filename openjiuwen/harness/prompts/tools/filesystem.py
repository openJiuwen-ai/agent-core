# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Bilingual descriptions and input params for filesystem tools."""
from __future__ import annotations

from typing import Any, Dict

from openjiuwen.harness.prompts.tools.base import (
    ToolMetadataProvider,
)

# ---------------------------------------------------------------------------
# Tool-level descriptions
# ---------------------------------------------------------------------------
_LEGACY_READ_FILE_DESCRIPTION: Dict[str, str] = {
    "cn": "读取文件内容。这是查看文件的主要工具。",
    "en": "Read file contents. This is the primary tool for viewing files.",
}

READ_FILE_DESCRIPTION: Dict[str, str] = {
    "cn": (
        "按绝对路径读取文件。文本默认最多返回 2000 行并带行号，长文件用 offset/limit 分段，按内容查找用 grep。支持 PDF 和 Jupyter "
        "Notebook，启用图片能力时可读图片。不支持读取目录。"
    ),
    "en": (
        "Read an absolute file path. Text defaults to up to 2,000 numbered lines. Use "
        "offset/limit for long files and grep to find content. Supports PDF and Jupyter "
        "notebooks, and images when image input is enabled. Does not read directories."
    ),
}

WRITE_FILE_DESCRIPTION: Dict[str, str] = {
    "cn": (
        "创建或完整覆盖文件。覆盖前必须先读取原文件，局部修改用 edit_file。仅在用户要求时创建文档或 README。"
    ),
    "en": (
        "Create/overwrite a complete file; existing content MUST be read first. Use edit_file for "
        "targeted edits. Create docs/README only on request."
    ),
}

_LEGACY_EDIT_FILE_DESCRIPTION: Dict[str, str] = {
    "cn": "编辑文件的指定部分。使用字符串替换方式修改文件。",
    "en": "Edit a specific part of a file using string replacement.",
}

EDIT_FILE_DESCRIPTION: Dict[str, str] = {
    "cn": (
        "精确替换文本。编辑已有文件前先完整读取。old_string 默认必须唯一匹配，重复时补足上下文或用 replace_all。空 old_string "
        "可创建缺失文件或写入已有空文件，不能清空非空文件。不支持 .ipynb。"
    ),
    "en": (
        "Replace exact text. Fully read an existing file first. Match old_string once, or add "
        "context/use replace_all. An empty old_string can create a missing file or write to an "
        "existing empty file. It cannot clear a non-empty file. No .ipynb edits."
    ),
}

GLOB_DESCRIPTION: Dict[str, str] = {
    "cn": (
        "按 glob 模式查找路径，按文件名排序，默认最多返回 100 条。"
    ),
    "en": "Find glob-matched paths, filename-sorted; default max 100 results.",
}

LIST_DIR_DESCRIPTION: Dict[str, str] = {
    "cn": "列出目录内容。",
    "en": "List directory contents.",
}

GREP_DESCRIPTION: Dict[str, str] = {
    "cn": (
        "用正则搜索文件内容，可按 glob/type 过滤。输出支持 content、files_with_matches、count。请使用本工具，避免用 Shell 的 grep "
        "或 rg 替代。"
    ),
    "en": (
        "Regex content search with glob/type filters and content/files_with_matches/count output; "
        "never substitute shell grep/rg."
    ),
}

# ---------------------------------------------------------------------------
# Parameter-level bilingual descriptions
# ---------------------------------------------------------------------------
_LEGACY_READ_FILE_PARAMS: Dict[str, Dict[str, str]] = {
    "file_path": {"cn": "要读取的文件路径", "en": "Path of the file to read"},
    "offset": {"cn": "开始读取的行号（默认1）", "en": "Line number to start reading from (default 1)"},
    "limit": {"cn": "读取的最大行数", "en": "Maximum number of lines to read"},
}

READ_FILE_PARAMS: Dict[str, Dict[str, str]] = {
    "file_path": {"cn": "要读取的文件绝对路径", "en": "The absolute path to the file to read"},
    "offset": {
        "cn": "起始行号。用于长文件或已知起点。",
        "en": "Start line; supply for large files or a known needed location.",
    },
    "limit": {
        "cn": "读取行数。用于长文件或指定范围。",
        "en": "Lines to read for large files or a known needed range.",
    },
    "pages": {
        "cn": "PDF 页码范围（如 1-5），每次最多 20 页。",
        "en": "PDF pages (e.g. 1-5); max 20 per call.",
    },
    "caption": {
        "cn": "读取技能目录内的图片时，填 SKILL.md 中的图片说明（Markdown alt）。",
        "en": "Skill image: use SKILL.md's figure alt text.",
    },
}

WRITE_FILE_PARAMS: Dict[str, Dict[str, str]] = {
    "file_path": {"cn": "要写入的文件绝对路径", "en": "Absolute path of the file to write"},
    "content": {"cn": "要写入的内容", "en": "Content to write"},
}

_LEGACY_EDIT_FILE_PARAMS: Dict[str, Dict[str, str]] = {
    "file_path": {"cn": "要编辑的文件路径", "en": "Path of the file to edit"},
    "old_string": {"cn": "要替换的原始字符串", "en": "Original string to replace"},
    "new_string": {"cn": "替换后的新字符串", "en": "New string to replace with"},
    "replace_all": {"cn": "是否替换所有匹配项", "en": "Whether to replace all occurrences"},
}

EDIT_FILE_PARAMS: Dict[str, Dict[str, str]] = {
    "file_path": {
        "cn": "要编辑的文件绝对路径",
        "en": "The absolute path to the file to edit",
    },
    "old_string": {
        "cn": (
            "精确的原始文本，默认只匹配一处。空值仅用于创建缺失文件或写入已有空文件。"
        ),
        "en": (
            "Exact original text. Match once unless replace_all. Empty only for creating a missing "
            "file or writing to an existing empty file."
        ),
    },
    "new_string": {
        "cn": "替换后的文本，必须与 old_string 不同",
        "en": "Replacement text, different from old_string.",
    },
    "replace_all": {
        "cn": "是否替换文件中所有匹配项，默认 false",
        "en": "Replace all occurrences of old_string in the file, default false",
    },
}

GLOB_PARAMS: Dict[str, Dict[str, str]] = {
    "pattern": {"cn": "glob 模式（如 *.py, **/*.js）", "en": "Glob pattern (e.g. *.py, **/*.js)"},
    "path": {
        "cn": "搜索目录，省略时默认当前工作目录",
        "en": "Directory to search. Defaults to the current working directory when omitted",
    },
}

LIST_DIR_PARAMS: Dict[str, Dict[str, str]] = {
    "path": {"cn": "目录路径", "en": "Directory path"},
    "show_hidden": {"cn": "显示隐藏文件", "en": "Show hidden files"},
}

GREP_PARAMS: Dict[str, Dict[str, str]] = {
    "pattern": {"cn": "搜索模式（正则表达式）", "en": "Search pattern (regular expression)"},
    "path": {
        "cn": "搜索路径（文件或目录），默认为当前工作目录",
        "en": "Search path (file or directory). Defaults to the current working directory",
    },
    "ignore_case": {"cn": "忽略大小写（兼容旧字段）", "en": "Ignore case (legacy compatibility alias)"},
    "glob": {"cn": "glob 过滤模式，例如 *.py 或 *.{ts,tsx}", "en": "Glob filter pattern such as *.py or *.{ts,tsx}"},
    "output_mode": {
        "cn": "输出模式：content、files_with_matches 或 count，默认 content",
        "en": "Output mode: content, files_with_matches, or count. Defaults to content",
    },
    "-B": {
        "cn": "每个匹配前显示的上下文行数，仅在 content 模式生效",
        "en": "Lines of leading context before each match; only used in content mode",
    },
    "-A": {
        "cn": "每个匹配后显示的上下文行数，仅在 content 模式生效",
        "en": "Lines of trailing context after each match; only used in content mode",
    },
    "-C": {
        "cn": "每个匹配前后都显示的上下文行数，仅在 content 模式生效",
        "en": "Lines of context before and after each match; only used in content mode",
    },
    "context": {"cn": "-C 的别名，用于设置前后对称上下文行数", "en": "Alias of -C for symmetric context lines"},
    "-n": {"cn": "在 content 模式显示行号，默认 true", "en": "Show line numbers in content mode. Defaults to true"},
    "-i": {"cn": "大小写不敏感搜索", "en": "Case-insensitive search"},
    "type": {"cn": "文件类型过滤，例如 py、js、ts，需要 rg", "en": "File type filter such as py, js, or ts. Requires rg"},
    "head_limit": {
        "cn": "只返回前 N 条记录或行。0 表示不限制，默认 250",
        "en": "Return only the first N entries or lines. Use 0 for unlimited. Defaults to 250",
    },
    "offset": {
        "cn": "先跳过前 N 条记录或行，再应用 head_limit，默认 0",
        "en": "Skip the first N entries or lines before applying head_limit. Defaults to 0",
    },
    "multiline": {"cn": "启用多行正则模式，需要 rg", "en": "Enable multiline regex mode. Requires rg"},
}


# ---------------------------------------------------------------------------
# Builder functions
# ---------------------------------------------------------------------------
def _desc(params: Dict[str, Dict[str, str]], key: str, lang: str) -> str:
    return params[key].get(lang, params[key]["cn"])


def get_read_file_input_params(language: str = "cn") -> Dict[str, Any]:
    p = READ_FILE_PARAMS
    return {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": _desc(p, "file_path", language)},
            "offset": {"type": "integer", "description": _desc(p, "offset", language)},
            "limit": {"type": "integer", "description": _desc(p, "limit", language)},
            "pages": {"type": "string", "description": _desc(p, "pages", language)},
            "caption": {"type": "string", "description": _desc(p, "caption", language)},
        },
        "required": ["file_path"],
    }


def get_write_file_input_params(language: str = "cn") -> Dict[str, Any]:
    p = WRITE_FILE_PARAMS
    return {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": _desc(p, "file_path", language)},
            "content": {"type": "string", "description": _desc(p, "content", language)},
        },
        "required": ["file_path", "content"],
    }


def get_edit_file_input_params(language: str = "cn") -> Dict[str, Any]:
    p = EDIT_FILE_PARAMS
    return {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": _desc(p, "file_path", language)},
            "old_string": {"type": "string", "description": _desc(p, "old_string", language)},
            "new_string": {"type": "string", "description": _desc(p, "new_string", language)},
            "replace_all": {"type": "boolean", "description": _desc(p, "replace_all", language)},
        },
        "required": ["file_path", "old_string", "new_string"],
    }


def get_glob_input_params(language: str = "cn") -> Dict[str, Any]:
    p = GLOB_PARAMS
    return {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": _desc(p, "pattern", language)},
            "path": {"type": "string", "description": _desc(p, "path", language)},
        },
        "required": ["pattern"],
    }


def get_list_dir_input_params(language: str = "cn") -> Dict[str, Any]:
    p = LIST_DIR_PARAMS
    return {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": _desc(p, "path", language)},
            "show_hidden": {"type": "boolean", "description": _desc(p, "show_hidden", language)},
        },
        "required": [],
    }


def get_grep_input_params(language: str = "cn") -> Dict[str, Any]:
    p = GREP_PARAMS
    return {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": _desc(p, "pattern", language)},
            "path": {"type": "string", "description": _desc(p, "path", language)},
            "ignore_case": {"type": "boolean", "description": _desc(p, "ignore_case", language)},
            "glob": {"type": "string", "description": _desc(p, "glob", language)},
            "output_mode": {
                "type": "string",
                "enum": ["content", "files_with_matches", "count"],
                "description": _desc(p, "output_mode", language),
            },
            "-B": {"type": "integer", "description": _desc(p, "-B", language)},
            "-A": {"type": "integer", "description": _desc(p, "-A", language)},
            "-C": {"type": "integer", "description": _desc(p, "-C", language)},
            "context": {"type": "integer", "description": _desc(p, "context", language)},
            "-n": {"type": "boolean", "description": _desc(p, "-n", language)},
            "-i": {"type": "boolean", "description": _desc(p, "-i", language)},
            "type": {"type": "string", "description": _desc(p, "type", language)},
            "head_limit": {"type": "integer", "description": _desc(p, "head_limit", language)},
            "offset": {"type": "integer", "description": _desc(p, "offset", language)},
            "multiline": {"type": "boolean", "description": _desc(p, "multiline", language)},
        },
        "required": ["pattern"],
    }


class ReadFileMetadataProvider(ToolMetadataProvider):
    """ReadFile 工具的元数据 provider。"""

    def get_name(self) -> str:
        return "read_file"

    def get_description(self, language: str = "cn") -> str:
        return READ_FILE_DESCRIPTION.get(language, READ_FILE_DESCRIPTION["cn"])

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return get_read_file_input_params(language)


class _LegacyReadFileMetadataProvider(ToolMetadataProvider):
    """Legacy read-file metadata provider kept private for compatibility helpers."""

    def get_name(self) -> str:
        return "read_file"

    def get_description(self, language: str = "cn") -> str:
        return READ_FILE_DESCRIPTION.get(language, READ_FILE_DESCRIPTION["cn"])

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return get_read_file_input_params(language)


class WriteFileMetadataProvider(ToolMetadataProvider):
    """WriteFile 工具的元数据 provider。"""

    def get_name(self) -> str:
        return "write_file"

    def get_description(self, language: str = "cn") -> str:
        return WRITE_FILE_DESCRIPTION.get(
            language, WRITE_FILE_DESCRIPTION["cn"]
        )

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return get_write_file_input_params(language)


class EditFileMetadataProvider(ToolMetadataProvider):
    """EditFile 工具的元数据 provider。"""

    def get_name(self) -> str:
        return "edit_file"

    def get_description(self, language: str = "cn") -> str:
        return EDIT_FILE_DESCRIPTION.get(language, EDIT_FILE_DESCRIPTION["cn"])

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return get_edit_file_input_params(language)


class _LegacyEditFileMetadataProvider(ToolMetadataProvider):
    """Legacy edit-file metadata provider kept private for compatibility helpers."""

    def get_name(self) -> str:
        return "edit_file"

    def get_description(self, language: str = "cn") -> str:
        return EDIT_FILE_DESCRIPTION.get(language, EDIT_FILE_DESCRIPTION["cn"])

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return get_edit_file_input_params(language)


class GlobMetadataProvider(ToolMetadataProvider):
    """Glob 工具的元数据 provider。"""

    def get_name(self) -> str:
        return "glob"

    def get_description(self, language: str = "cn") -> str:
        return GLOB_DESCRIPTION.get(language, GLOB_DESCRIPTION["cn"])

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return get_glob_input_params(language)


class ListDirMetadataProvider(ToolMetadataProvider):
    """ListDir 工具的元数据 provider。"""

    def get_name(self) -> str:
        return "list_files"

    def get_description(self, language: str = "cn") -> str:
        return LIST_DIR_DESCRIPTION.get(
            language, LIST_DIR_DESCRIPTION["cn"]
        )

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return get_list_dir_input_params(language)


class GrepMetadataProvider(ToolMetadataProvider):
    """Grep 工具的元数据 provider。"""

    def get_name(self) -> str:
        return "grep"

    def get_description(self, language: str = "cn") -> str:
        return GREP_DESCRIPTION.get(language, GREP_DESCRIPTION["cn"])

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return get_grep_input_params(language)
