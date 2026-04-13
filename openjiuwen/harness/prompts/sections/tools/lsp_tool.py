"""LSP tool metadata for bilingual tool registration in prompts sections."""

from __future__ import annotations

from typing import Any, Dict

from openjiuwen.harness.prompts.sections.tools.base import (
    ToolMetadataProvider,
)

# ---------------------------------------------------------------------------
# Tool description (bilingual)
# ---------------------------------------------------------------------------
LSP_TOOL_DESCRIPTION_EN = (
    "LSP (Language Server Protocol) tool providing code navigation features for AI agents.\n\n"
    "Available operations:\n"
    "- goToDefinition: Jump to symbol definition location\n"
    "- findReferences: Find all reference locations of a symbol\n"
    "- documentSymbol: Get all symbols in a document (outline)\n"
    "- workspaceSymbol: Search symbols across the entire workspace\n"
    "- goToImplementation: Find concrete implementations of an interface/abstract method\n"
    "- prepareCallHierarchy: Prepare call hierarchy entry points\n"
    "- incomingCalls: Find locations that call the current function\n"
    "- outgoingCalls: Find functions called by the current function\n\n"
    "Note: The operation field determines which LSP method is used. "
    "Hover is not supported. Results from gitignored files (node_modules, __pycache__, etc.) "
    "are automatically filtered out for navigation operations."
)

LSP_TOOL_DESCRIPTION_CN = (
    "LSP（Language Server Protocol）工具，为 AI Agent 提供代码导航功能。\n\n"
    "可用操作：\n"
    "- goToDefinition: 跳转到符号定义位置\n"
    "- findReferences: 查找符号的所有引用位置\n"
    "- documentSymbol: 获取文档中的所有符号（大纲）\n"
    "- workspaceSymbol: 在整个工作区搜索符号\n"
    "- goToImplementation: 查找接口/抽象方法的具体实现\n"
    "- prepareCallHierarchy: 准备调用层次结构入口\n"
    "- incomingCalls: 查找调用当前函数的所有位置\n"
    "- outgoingCalls: 查找当前函数调用的所有函数\n\n"
    "注意：operation 字段决定使用哪个 LSP 方法。不支持 hover。"
    "导航操作的结果会自动过滤掉位于 gitignored 目录（如 node_modules、__pycache__ 等）中的条目。"
)

DESCRIPTION: Dict[str, str] = {
    "cn": LSP_TOOL_DESCRIPTION_CN,
    "en": LSP_TOOL_DESCRIPTION_EN,
}

# ---------------------------------------------------------------------------
# Tool parameters (bilingual)
# ---------------------------------------------------------------------------
PARAMS: Dict[str, Dict[str, str]] = {
    "operation": {
        "cn": "LSP 操作类型",
        "en": "LSP operation type",
    },
    "file_path": {
        "cn": "文件路径（绝对或相对于工作区根目录）",
        "en": "File path (absolute or relative to workspace root)",
    },
    "line": {
        "cn": "行号（1-indexed，LSP 内部使用 0-indexed）",
        "en": "Line number (1-indexed; LSP internally uses 0-indexed)",
    },
    "character": {
        "cn": "列号（1-indexed，LSP 内部使用 0-indexed）",
        "en": "Column number (1-indexed; LSP internally uses 0-indexed)",
    },
    "query": {
        "cn": "搜索查询字符串（仅 workspaceSymbol 使用）",
        "en": "Search query string (used by workspaceSymbol only)",
    },
    "include_declaration": {
        "cn": "findReferences 是否包含声明位置",
        "en": "Whether findReferences includes the declaration location",
    },
}


def get_lsp_input_params(language: str = "cn") -> Dict[str, Any]:
    """Return the full JSON Schema for LSP tool input parameters."""
    lang = language if language in ("cn", "en") else "cn"
    return {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": [
                    "goToDefinition",
                    "findReferences",
                    "documentSymbol",
                    "workspaceSymbol",
                    "goToImplementation",
                    "prepareCallHierarchy",
                    "incomingCalls",
                    "outgoingCalls",
                ],
                "description": PARAMS["operation"][lang],
            },
            "file_path": {
                "type": "string",
                "description": PARAMS["file_path"][lang],
            },
            "line": {
                "type": "integer",
                "minimum": 1,
                "description": PARAMS["line"][lang],
            },
            "character": {
                "type": "integer",
                "minimum": 1,
                "description": PARAMS["character"][lang],
            },
            "query": {
                "type": "string",
                "description": PARAMS["query"][lang],
            },
            "include_declaration": {
                "type": "boolean",
                "description": PARAMS["include_declaration"][lang],
            },
        },
        "required": ["operation", "file_path"],
    }


class LspToolMetadataProvider(ToolMetadataProvider):
    """LSP tool metadata provider for prompts sections tool registration."""

    def get_name(self) -> str:
        return "lsp"

    def get_description(self, language: str = "cn") -> str:
        return DESCRIPTION.get(language, DESCRIPTION["cn"])

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return get_lsp_input_params(language)
