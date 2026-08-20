# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Bilingual metadata for the registered find_* Code Graph tools."""

from __future__ import annotations

from typing import Any, Dict

from openjiuwen.harness.prompts.tools.base import ToolMetadataProvider

_KINDS = ["file", "module", "class", "function", "method", "interface", "struct", "trait"]


def _search_code_params(language: str) -> Dict[str, Any]:
    lang = language if language in ("cn", "en") else "cn"
    return {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Issue text, symbol name, or words from the implementing function body"
                    if lang == "en"
                    else "问题文本、符号名，或实现函数体内的词"
                ),
            },
            "symbol_kinds": {
                "type": "array",
                "items": {"type": "string", "enum": _KINDS},
                "description": (
                    "Optional kinds to keep (class, method, function, ...)"
                    if lang == "en"
                    else "可选的符号类型过滤（class、method、function 等）"
                ),
            },
            "path_prefix": {
                "type": "string",
                "description": (
                    "Only search under this repo-relative directory, e.g. src/"
                    if lang == "en"
                    else "仅在该仓库相对目录下搜索，例如 src/"
                ),
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "description": "Maximum matches to return" if lang == "en" else "最多返回的匹配数量",
            },
            "include_tests": {
                "type": "boolean",
                "description": (
                    "Include test files. Default false." if lang == "en" else "是否包含测试文件，默认 false。"
                ),
            },
        },
        "required": ["query"],
    }


def _list_symbols_params(language: str) -> Dict[str, Any]:
    lang = language if language in ("cn", "en") else "cn"
    return {
        "type": "object",
        "properties": {
            "file": {
                "type": "string",
                "description": (
                    "Repo-relative file path whose structure should be listed"
                    if lang == "en"
                    else "要列出结构的仓库相对文件路径"
                ),
            },
            "parent_symbol": {
                "type": "string",
                "description": (
                    "Parent class/module name or symbol_id" if lang == "en" else "父类/模块名或 symbol_id"
                ),
            },
            "kinds": {
                "type": "array",
                "items": {"type": "string", "enum": _KINDS},
                "description": "Optional kinds to keep" if lang == "en" else "可选的符号类型过滤",
            },
            "depth": {
                "type": "integer",
                "minimum": 1,
                "maximum": 5,
                "description": "Containment depth to expand" if lang == "en" else "向下展开的包含深度",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 200,
                "description": "Maximum symbols to return" if lang == "en" else "最多返回的符号数量",
            },
        },
        "required": [],
    }


def _select_context_params(language: str) -> Dict[str, Any]:
    lang = language if language in ("cn", "en") else "cn"
    return {
        "type": "object",
        "properties": {
            "symbol_id": {
                "type": "string",
                "description": "Symbol id to keep as evidence" if lang == "en" else "作为证据保留的 symbol_id",
            },
            "file": {
                "type": "string",
                "description": "Repo-relative file path" if lang == "en" else "仓库相对路径",
            },
            "start_line": {
                "type": "integer",
                "minimum": 1,
                "description": "First selected line (1-based)" if lang == "en" else "起始行（从 1 计）",
            },
            "end_line": {
                "type": "integer",
                "minimum": 1,
                "description": "Last selected line (inclusive)" if lang == "en" else "结束行（含）",
            },
            "reason": {
                "type": "string",
                "description": "Why this location matters" if lang == "en" else "该位置为何重要",
            },
            "confidence": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": "Confidence in [0, 1]" if lang == "en" else "置信度，范围 [0, 1]",
            },
            "evidence_id": {
                "type": "string",
                "description": "Optional read_symbol / read_code evidence id"
                if lang == "en"
                else "可选的 read_symbol/read_code 证据 id",
            },
        },
        "required": ["reason"],
    }


def _search_text_params(language: str) -> Dict[str, Any]:
    lang = language if language in ("cn", "en") else "cn"
    return {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Natural-language or keyword query" if lang == "en" else "自然语言或关键词查询",
            },
            "path_prefix": {
                "type": "string",
                "description": (
                    "Optional repo-relative directory prefix to search under"
                    if lang == "en"
                    else "可选的仓库相对目录前缀，限定搜索范围"
                ),
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "description": "Maximum matches to return" if lang == "en" else "最多返回的匹配数",
            },
            "include_tests": {
                "type": "boolean",
                "description": (
                    "Include test files. Default false." if lang == "en" else "是否包含测试文件，默认 false。"
                ),
            },
        },
        "required": ["query"],
    }


def _read_code_params(language: str) -> Dict[str, Any]:
    lang = language if language in ("cn", "en") else "cn"
    return {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Repo-relative file path" if lang == "en" else "仓库相对文件路径",
            },
            "start_line": {
                "type": "integer",
                "minimum": 1,
                "description": "First line to read, 1-based" if lang == "en" else "起始行号，从 1 开始",
            },
            "end_line": {
                "type": "integer",
                "minimum": 1,
                "description": "Last line to read, inclusive" if lang == "en" else "结束行号（含）",
            },
        },
        "required": ["path"],
    }


def _name_kind_path_params(language: str) -> Dict[str, Any]:
    lang = language if language in ("cn", "en") else "cn"
    return {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Symbol name, qualified name, or symbol_id"
                if lang == "en"
                else "符号名、限定名或 symbol_id",
            },
            "kind": {
                "type": "string",
                "enum": _KINDS,
                "description": "Optional kind to disambiguate" if lang == "en" else "可选类型，用于消歧",
            },
            "path_hint": {
                "type": "string",
                "description": "Repo-relative path fragment" if lang == "en" else "仓库相对路径片段",
            },
        },
        "required": ["name"],
    }


def _read_symbol_params(language: str) -> Dict[str, Any]:
    lang = language if language in ("cn", "en") else "cn"
    return {
        "type": "object",
        "properties": {
            "symbol_id": {
                "type": "string",
                "description": "Full symbol_id from resolve_symbol"
                if lang == "en"
                else "resolve_symbol 返回的完整 symbol_id",
            },
            "context_before": {
                "type": "integer",
                "minimum": 0,
                "description": "Extra lines before the definition" if lang == "en" else "定义前额外行数",
            },
            "context_after": {
                "type": "integer",
                "minimum": 0,
                "description": "Extra lines after the definition" if lang == "en" else "定义后额外行数",
            },
        },
        "required": ["symbol_id"],
    }


def _symbol_limit_params(language: str) -> Dict[str, Any]:
    lang = language if language in ("cn", "en") else "cn"
    return {
        "type": "object",
        "properties": {
            "symbol_id": {
                "type": "string",
                "description": "Full symbol_id" if lang == "en" else "完整 symbol_id",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 50,
                "description": "Maximum neighbours" if lang == "en" else "最多返回的邻居数",
            },
        },
        "required": ["symbol_id"],
    }


def _submit_params(language: str) -> Dict[str, Any]:
    lang = language if language in ("cn", "en") else "cn"
    return {
        "type": "object",
        "properties": {
            "locations": {
                "type": "array",
                "items": {"type": "object"},
                "description": (
                    "Spans to submit: symbol_id or evidence_id from a previous read"
                    if lang == "en"
                    else "要提交的 span：此前读取的 symbol_id 或 evidence_id"
                ),
            },
            "summary": {
                "type": "string",
                "description": "Why these spans cover the issue" if lang == "en" else "这些 span 为何覆盖问题",
            },
            "status": {
                "type": "string",
                "enum": ["COMPLETE", "PARTIAL"],
                "description": (
                    "COMPLETE when the selected spans cover the issue; PARTIAL otherwise"
                    if lang == "en"
                    else "选中 span 已覆盖问题时用 COMPLETE，否则用 PARTIAL"
                ),
            },
        },
        "required": ["summary"],
    }


def _trace_call_paths_params(language: str) -> Dict[str, Any]:
    lang = language if language in ("cn", "en") else "cn"
    return {
        "type": "object",
        "properties": {
            "symbol_id": {
                "type": "string",
                "description": (
                    "Full symbol_id from a previous Code Graph tool result"
                    if lang == "en"
                    else "此前 Code Graph 工具结果中的完整 symbol_id"
                ),
            },
            "direction": {
                "type": "string",
                "enum": ["callers", "callees", "both"],
                "description": (
                    "callers = who reaches this symbol; callees = what it reaches"
                    if lang == "en"
                    else "callers = 谁调用它；callees = 它调用了什么"
                ),
            },
            "max_depth": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10,
                "description": "Hops to follow" if lang == "en" else "追踪跳数",
            },
            "max_paths": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "description": "Maximum paths to return" if lang == "en" else "最多返回的路径数",
            },
            "include_tests": {
                "type": "boolean",
                "description": (
                    "Include test files. Default false." if lang == "en" else "是否包含测试文件，默认 false。"
                ),
            },
        },
        "required": ["symbol_id", "direction"],
    }


class ResolveSymbolMetadataProvider(ToolMetadataProvider):
    def get_name(self) -> str:
        return "resolve_symbol"

    def get_description(self, language: str = "cn") -> str:
        if language == "en":
            return (
                "Resolve an exact class/function/method name to a symbol_id. "
                "Use this first when the issue names the entity. Not BM25."
            )
        return "按精确类名/函数名/方法名解析 symbol_id。issue 已点名时优先使用。不是 BM25。"

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return _name_kind_path_params(language)

    def is_idempotent(self) -> bool:
        return True


class FindCodeSymbolsMetadataProvider(ToolMetadataProvider):
    def get_name(self) -> str:
        return "find_code_symbols"

    def get_description(self, language: str = "cn") -> str:
        if language == "en":
            return (
                "Generate up to 5 definition candidates when the exact name is unknown. "
                "Exact name hits rank first. Then read_symbol; do not keep rewording."
            )
        return "无法精确解析时生成最多 5 个定义候选。精确名优先于 BM25。随后 read_symbol，不要反复改写查询。"

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return _search_code_params(language)

    def is_idempotent(self) -> bool:
        return True


class SearchSourceTextMetadataProvider(ToolMetadataProvider):
    def get_name(self) -> str:
        return "search_source_text"

    def get_description(self, language: str = "cn") -> str:
        if language == "en":
            return (
                "Search source for exact literals: error messages, config keys, "
                "decorators, registry calls. Not for callers or inheritance."
            )
        return "搜索源码中的精确字面量：报错、配置键、decorator、registry。不要用来找调用者或继承。"

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return _search_text_params(language)

    def is_idempotent(self) -> bool:
        return True


class InspectCodeStructureMetadataProvider(ToolMetadataProvider):
    def get_name(self) -> str:
        return "inspect_code_structure"

    def get_description(self, language: str = "cn") -> str:
        if language == "en":
            return (
                "List classes, functions, and methods in a file or parent symbol. "
                "Prefer this over reading a whole file."
            )
        return "列出文件或父符号中的类、函数、方法。优先于读取整个文件。"

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return _list_symbols_params(language)

    def is_idempotent(self) -> bool:
        return True


class ReadSymbolMetadataProvider(ToolMetadataProvider):
    def get_name(self) -> str:
        return "read_symbol"

    def get_description(self, language: str = "cn") -> str:
        if language == "en":
            return (
                "Read one symbol definition plus a few neighbouring lines. "
                "Prefer this over read_file for classes and functions."
            )
        return "读取一个符号定义及少量邻行。对类和函数优先于 read_file。"

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return _read_symbol_params(language)

    def is_idempotent(self) -> bool:
        return True


class ReadCodeMetadataProvider(ToolMetadataProvider):
    def get_name(self) -> str:
        return "read_code"

    def get_description(self, language: str = "cn") -> str:
        if language == "en":
            return (
                "Read numbered source under the bound repository root. Max about 400 lines. "
                "Read-only: cannot edit or run shell. Use before editing or select_code_context."
            )
        return "读取仓库内带行号源码，单次最多约 400 行。只读，不能编辑或执行 shell。改代码或标记上下文前使用。"

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return _read_code_params(language)

    def is_idempotent(self) -> bool:
        return True


class _RelationMetadataProvider(ToolMetadataProvider):
    _name = ""
    _en = ""
    _cn = ""

    def get_name(self) -> str:
        return self._name

    def get_description(self, language: str = "cn") -> str:
        return self._en if language == "en" else self._cn

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return _symbol_limit_params(language)

    def is_idempotent(self) -> bool:
        return True


class FindCallersMetadataProvider(_RelationMetadataProvider):
    _name = "find_callers"
    _en = (
        "Who directly calls this symbol? One hop. Unresolved sites that use "
        "this name are listed separately — they are not graph edges. Use "
        "instead of text search."
    )
    _cn = (
        "谁直接调用这个符号？单跳。同名但无法解析的调用点会单独列出，"
        "那不是图上的边。不要用文本搜索代替。"
    )


class FindCalleesMetadataProvider(_RelationMetadataProvider):
    _name = "find_callees"
    _en = "What does this symbol call directly? One hop."
    _cn = "这个符号直接调用了什么？单跳。"


class FindImportersMetadataProvider(_RelationMetadataProvider):
    _name = "find_importers"
    _en = "Which modules import this symbol?"
    _cn = "哪些模块导入了这个符号？"


class FindBaseClassesMetadataProvider(_RelationMetadataProvider):
    _name = "find_base_classes"
    _en = "Which classes does this class inherit from?"
    _cn = "这个类继承自谁？"


class FindSubclassesMetadataProvider(_RelationMetadataProvider):
    _name = "find_subclasses"
    _en = "Which classes inherit or implement this symbol?"
    _cn = "哪些类继承或实现了它？"


class TraceCallPathsMetadataProvider(ToolMetadataProvider):
    def get_name(self) -> str:
        return "trace_call_paths"

    def get_description(self, language: str = "cn") -> str:
        if language == "en":
            return (
                "Multi-hop call paths. direction is required (callers or callees). "
                "Default depth 3, max 5 paths."
            )
        return "多跳调用路径。必须传 direction（callers 或 callees）。默认深度 3，最多 5 条路径。"

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return _trace_call_paths_params(language)

    def is_idempotent(self) -> bool:
        return True


class SelectCodeContextMetadataProvider(ToolMetadataProvider):
    def get_name(self) -> str:
        return "select_code_context"

    def get_description(self, language: str = "cn") -> str:
        if language == "en":
            return (
                "Mark a previously read symbol as selected context. "
                "Prefer the smallest enclosing function or method. "
                "This does not edit code and does not end the task."
            )
        return (
            "把已读取的符号标记为选定上下文。尽量选最小的函数/方法。"
            "不会改代码，也不会结束任务。"
        )

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return _select_context_params(language)


class SubmitCodeContextMetadataProvider(ToolMetadataProvider):
    """Eval-only: registered when ``prompt_mode=locate`` (ContextBench)."""

    def get_name(self) -> str:
        return "submit_code_context"

    def get_description(self, language: str = "cn") -> str:
        if language == "en":
            return (
                "Submit the locate result. Pass locations from read_symbol. "
                "The system generates <PATCH_CONTEXT>; do not type File/Lines yourself."
            )
        return "提交定位结果。传入 read_symbol 的 locations。系统生成 <PATCH_CONTEXT>，不要手写 File/Lines。"

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return _submit_params(language)
