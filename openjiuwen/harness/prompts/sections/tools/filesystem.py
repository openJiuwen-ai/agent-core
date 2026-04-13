# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Bilingual descriptions and input params for filesystem tools."""
from __future__ import annotations

from typing import Any, Dict

from openjiuwen.harness.prompts.sections.tools.base import (
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
    "cn": "增强版文件读取工具。支持文本、图片、PDF 与 Jupyter Notebook。",
    "en": "Enhanced file reader for text, images, PDFs, and Jupyter notebooks.",
}

WRITE_FILE_DESCRIPTION: Dict[str, str] = {
    "cn": "写入文件内容。如果文件已存在，将完全覆盖。",
    "en": "Write file contents. Overwrites existing files only after a full read_file call.",
}

_LEGACY_EDIT_FILE_DESCRIPTION: Dict[str, str] = {
    "cn": "编辑文件的指定部分。使用字符串替换方式修改文件。",
    "en": "Edit a specific part of a file using string replacement.",
}

EDIT_FILE_DESCRIPTION: Dict[str, str] = {
    "cn": (
        "增强版文件编辑工具，对已有文件执行精确的字符串替换操作，仅传输差量。\n\n"
        "核心行为：\n"
        "- 前置读取要求：编辑前必须通过 read_file 完整读取过该文件\n"
        "- 唯一性验证：old_string 须唯一匹配；多个匹配时须设置 replace_all=true 或提供更多上下文\n"
        "- 引号容错：自动尝试直引号与弯引号互转后匹配\n"
        "- 去消毒处理：自动将 HTML 实体（&lt; &gt; &amp; 等）还原为原始字符后匹配\n"
        "- 新文件创建：old_string='' 且目标文件不存在时创建新文件\n"
        "- 格式化处理：自动去除 new_string 行尾空白（.md/.mdx 文件除外）；保留文件原有行尾风格（LF/CRLF）\n"
        "- 外部修改检测：写入前通过时间戳 + 文件大小双重校验，若文件被外部修改则拒绝写入\n\n"
        "拒绝条件：文件超过 1 GiB / old_string 与 new_string 相同 / .ipynb 文件 / "
        "文件不存在且 old_string 非空 / 文件已存在且 old_string 为空"
    ),
    "en": (
        "Enhanced file edit tool. Performs exact string replacement on existing files, "
        "transmitting only the diff.\n\n"
        "Core behaviour:\n"
        "- Pre-read requirement: file must be fully read via read_file before editing\n"
        "- Uniqueness validation: old_string must match exactly once; set replace_all=true or add "
        "more context when multiple matches exist\n"
        "- Quote tolerance: automatically retries with straight/curly quote substitution\n"
        "- XML desanitization: reverses HTML entity encoding (&lt; &gt; &amp; etc.) before matching\n"
        "- New file creation: old_string='' and non-existent target path creates the file\n"
        "- Formatting: strips trailing whitespace from new_string lines (except .md/.mdx); "
        "preserves original EOL style (LF/CRLF)\n"
        "- External modification detection: rejects writes when mtime + size have changed since last read\n\n"
        "Rejected when: file > 1 GiB / old_string == new_string / .ipynb file / "
        "file missing with non-empty old_string / file exists with empty old_string"
    ),
}

GLOB_DESCRIPTION: Dict[str, str] = {
    "cn": "使用 glob 模式查找文件。",
    "en": "Find files using glob patterns with structured results, optional path input, and default result truncation.",
}

LIST_DIR_DESCRIPTION: Dict[str, str] = {
    "cn": "列出目录内容。",
    "en": "List directory contents.",
}

GREP_DESCRIPTION: Dict[str, str] = {
    "cn": "在文件中搜索内容。支持正则表达式。",
    "en": (
        "Search file contents with regex, structured output modes, pagination, "
        "context lines, file-type filters, and glob filters."
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
    "file_path": {"cn": "要读取的绝对路径", "en": "Absolute path of the file to read"},
    "offset": {
        "cn": "要跳过的行数（0 表示从头读取）。仅在文件过大无法一次读完时提供",
        "en": (
            "Number of lines to skip before reading (0 = start of file). "
            "Only provide when the file is too large to read at once"
        ),
    },
    "limit": {
        "cn": "最多读取的行数（默认及上限均为 2000）。仅在文件过大无法一次读完时提供",
        "en": (
            "Maximum number of lines to read (default and cap: 2000). "
            "Only provide when the file is too large to read at once"
        ),
    },
    "pages": {
        "cn": "PDF 专属页码范围，例如 '1-5'、'3'、'10-20'。每次最多 20 页",
        "en": "PDF-only page range, e.g. '1-5', '3', '10-20'. Maximum 20 pages per request",
    },
}

WRITE_FILE_PARAMS: Dict[str, Dict[str, str]] = {
    "file_path": {"cn": "要写入的文件路径", "en": "Absolute path of the file to write"},
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
        "cn": "目标文件的绝对路径",
        "en": "Absolute path to the target file",
    },
    "old_string": {
        "cn": (
            "要替换的原始文本（空字符串可用于创建新文件或向空文件写入内容）。"
            "必须在文件中唯一匹配，否则须设置 replace_all=true 或提供更多上下文"
        ),
        "en": (
            "The text to replace (empty string creates a new file or writes to an empty file). "
            "Must match exactly once unless replace_all=true or more context is provided"
        ),
    },
    "new_string": {
        "cn": "替换后的文本，必须与 old_string 不同",
        "en": "The replacement text, must differ from old_string",
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
