"""LSP tool metadata for bilingual tool registration in prompts sections."""

from __future__ import annotations

from typing import Any, Dict

from openjiuwen.harness.prompts.tools.base import (
    ToolMetadataProvider,
)

# ---------------------------------------------------------------------------
# Tool description (bilingual)
# ---------------------------------------------------------------------------
LSP_TOOL_DESCRIPTION_EN = (
    "Interact with Language Server Protocol (LSP) servers to get code intelligence features.\n\n"
    "Supported operations:\n"
    "- goToDefinition: Find where a symbol is defined\n"
    "- findReferences: Find all references to a symbol\n"
    "- documentSymbol: Get all symbols (functions, classes, variables) in a document\n"
    "- workspaceSymbol: Search for symbols across the entire workspace\n"
    "- goToImplementation: Find implementations of an interface or abstract method\n"
    "- prepareCallHierarchy: Get call hierarchy item at a position (functions/methods)\n"
    "- incomingCalls: Find all functions/methods that call the function at a position\n"
    "- outgoingCalls: Find all functions/methods called by the function at a position\n\n"
    "Note: Hover (hover information) is not currently supported.\n\n"
    "All operations require:\n"
    "- filePath: The file to operate on (absolute or relative to workspace root)\n"
    "- line: The line number (1-based, as shown in editors)\n"
    "- character: The character offset (1-based, as shown in editors)\n\n"
    "workspaceSymbol does not require line or character parameters; query is used instead.\n\n"
    "Results from gitignored files (node_modules, __pycache__, etc.) are automatically filtered out "
    "for navigation operations.\n\n"
    "Large files (exceeding 10MB) are not sent to the LSP server.\n\n"
    "Note: LSP servers must be configured for the file type. "
    "If no server is available, an error will be returned."
)

LSP_TOOL_DESCRIPTION_CN = (
    "通过 Language Server Protocol (LSP) 服务器获取代码智能功能（如定义跳转、引用查找等）。\n\n"
    "支持的操作：\n"
    "- goToDefinition: 查找符号的定义位置\n"
    "- findReferences: 查找符号的所有引用\n"
    "- documentSymbol: 获取文档中的所有符号（函数、类、变量等）\n"
    "- workspaceSymbol: 在整个工作区搜索符号\n"
    "- goToImplementation: 查找接口或抽象方法的具体实现\n"
    "- prepareCallHierarchy: 获取光标位置的调用层次结构条目\n"
    "- incomingCalls: 查找所有调用当前函数的函数/方法\n"
    "- outgoingCalls: 查找当前函数调用的所有函数/方法\n\n"
    "注意：hover（悬停信息）操作暂不支持。\n\n"
    "所有操作均需要：\n"
    "- filePath: 要操作的文件（绝对路径或相对于工作区根目录的路径）\n"
    "- line: 行号（1-indexed，编辑器中显示的行号）\n"
    "- character: 列号（1-indexed，编辑器中显示的字符偏移）\n\n"
    "workspaceSymbol 不需要 line 和 character 参数，而是使用 query 参数进行搜索。\n\n"
    "导航操作的结果会自动过滤掉位于 gitignored 目录（如 node_modules、__pycache__ 等）中的条目。\n\n"
    "大文件（超过 10MB）不会被发送到 LSP 服务器。\n\n"
    "注意：必须为文件类型配置对应的 LSP 服务器。如果没有可用的服务器，将返回错误。"
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
        "cn": "LSP 操作类型，可选值：goToDefinition、findReferences、documentSymbol、workspaceSymbol、"
              "goToImplementation、prepareCallHierarchy、incomingCalls、outgoingCalls",
        "en": "LSP operation type. Options: goToDefinition, findReferences, documentSymbol, "
              "workspaceSymbol, goToImplementation, prepareCallHierarchy, incomingCalls, outgoingCalls",
    },
    "file_path": {
        "cn": "文件路径（绝对路径或相对于工作区根目录的路径）",
        "en": "The absolute or relative path to the file",
    },
    "line": {
        "cn": "行号（1-indexed，编辑器中显示的行号）",
        "en": "The line number (1-based, as shown in editors)",
    },
    "character": {
        "cn": "列号（1-indexed，默认为 1）",
        "en": "The character offset (1-based, as shown in editors; defaults to 1)",
    },
    "query": {
        "cn": "搜索查询字符串；为空时返回所有可用符号（仅 workspaceSymbol 使用）",
        "en": "Search query string; when empty, returns all available symbols (used by workspaceSymbol only)",
    },
    "include_declaration": {
        "cn": "为 true 时，结果中包含符号的定义位置（默认 true）",
        "en": "When true, the declaration location itself is included in the results (default: true)",
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
