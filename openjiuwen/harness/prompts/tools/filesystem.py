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
        "从本地文件系统读取文件。"
        "file_path 必须是绝对路径。默认从文件开头最多读取 2000 行，结果带行号返回。"
        "支持图片、PDF 和 Jupyter Notebook。只能读取文件，不能读取目录。"
        "大文件请用 offset/limit 读取指定部分，或用 grep 搜索具体内容。"
    ),
    "en": (
        "Read a file from the local filesystem. "
        "file_path must be an absolute path. By default, reads up to 2000 lines "
        "from the beginning and returns results with line numbers. "
        "Supports images, PDFs, and Jupyter notebooks. Can read files only, not directories. "
        "For large files, use offset/limit to read a specific portion, or use grep to search for specific content."
    ),
}

WRITE_FILE_DESCRIPTION: Dict[str, str] = {
    "cn": (
        "向本地文件系统写入文件。\n\n"
        "用法：\n"
        "- 如果目标路径已有文件，本工具会覆盖该文件。\n"
        "- 如果目标文件已存在，必须先使用 read_file 读取文件内容；否则本工具会失败。\n"
        "- 修改已有文件时优先使用 edit_file；edit_file 只提交差异。write_file 主要用于创建新文件或完整重写文件。\n"
        "- 除非用户明确要求，不要创建文档文件（*.md）或 README 文件。"
    ),
    "en": (
        "Writes a file to the local filesystem.\n\n"
        "Usage:\n"
        "- This tool will overwrite the existing file if there is one at the provided path.\n"
        "- If this is an existing file, you MUST use read_file first to read the file's contents. "
        "This tool will fail if you did not read the file first.\n"
        "- Prefer edit_file for modifying existing files; it only sends the diff. "
        "Only use write_file to create new files or for complete rewrites.\n"
        "- NEVER create documentation files (*.md) or README files unless explicitly requested by the User."
    ),
}

_LEGACY_EDIT_FILE_DESCRIPTION: Dict[str, str] = {
    "cn": "编辑文件的指定部分。使用字符串替换方式修改文件。",
    "en": "Edit a specific part of a file using string replacement.",
}

EDIT_FILE_DESCRIPTION: Dict[str, str] = {
    "cn": (
        "对文件执行精确字符串替换。"
        "编辑已有文件前必须先完整调用 read_file。"
        "old_string 必须唯一匹配；如果有多个匹配，请提供更多上下文或设置 replace_all=true。"
        "old_string 为空且目标文件不存在时，可创建新文件。"
        "不支持编辑 .ipynb 文件。"
    ),
    "en": (
        "Performs exact string replacements in files. "
        "Existing files must be fully read with read_file before editing. "
        "old_string must match exactly once; if multiple matches exist, provide more context or set replace_all=true. "
        "Creates a new file when old_string is empty and the target file does not exist. "
        "Does not support editing .ipynb files."
    ),
}

GLOB_DESCRIPTION: Dict[str, str] = {
    "cn": (
        "快速按 glob 模式查找文件，适用于任意规模代码库。"
        "支持 **/*.js、src/**/*.ts 等模式，返回按文件名排序的匹配路径。"
        "默认最多返回 100 个结果。需要按文件名模式找文件时使用本工具。"
    ),
    "en": (
        "Fast file pattern matching tool that works with any codebase size. "
        "Supports glob patterns like **/*.js or src/**/*.ts and returns matching paths sorted by file name. "
        "Returns up to 100 results by default. Use this tool when you need to find files by name patterns."
    ),
}

LIST_DIR_DESCRIPTION: Dict[str, str] = {
    "cn": "列出目录内容。",
    "en": "List directory contents.",
}

GREP_DESCRIPTION: Dict[str, str] = {
    "cn": (
        "基于 ripgrep 的强大搜索工具。"
        "搜索任务必须使用 grep，不要在 bash 命令中直接调用 `grep` 或 `rg`。"
        "grep 工具已针对权限和访问控制做过优化。"
        "支持完整正则语法、glob/type 文件过滤，以及输出模式："
        "content、files_with_matches、count。"
    ),
    "en": (
        "A powerful search tool built on ripgrep. "
        "ALWAYS use grep for search tasks. NEVER invoke `grep` or `rg` as a bash command. "
        "The grep tool has been optimized for correct permissions and access. "
        "Supports full regex syntax, glob/type filters, and output modes: "
        "content, files_with_matches, count."
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
        "cn": "开始读取的行号。仅当文件太大、无法一次读取，或已知道要读取的位置时提供。",
        "en": (
            "The line number to start reading from. "
            "Only provide if the file is too large to read at once or you already know the needed location."
        ),
    },
    "limit": {
        "cn": "要读取的行数。仅当文件太大、无法一次读取，或只需要指定范围时提供。",
        "en": (
            "The number of lines to read. "
            "Only provide if the file is too large to read at once or only a specific range is needed."
        ),
    },
    "pages": {
        "cn": "PDF 专属页码范围，例如 '1-5'、'3'、'10-20'。每次最多 20 页",
        "en": "PDF-only page range, e.g. '1-5', '3', '10-20'. Maximum 20 pages per request",
    },
    "caption": {
        "cn": "可选。读取 skills/… 下的图片时，填入 SKILL.md 中的图片说明文字（Markdown alt），用于多模态用户提示。",
        "en": (
            "Optional. When reading an image under skills/, pass the figure caption "
            "(markdown alt text from SKILL.md) for the multimodal user prompt."
        ),
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
