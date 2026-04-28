"""LSP tool class and executor for the harness layer."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

from pydantic import TypeAdapter

from openjiuwen.core.common.logging import tool_logger as logger
from openjiuwen.core.foundation.tool.base import Tool
from openjiuwen.core.sys_operation import SysOperation
from openjiuwen.harness.prompts.tools import build_tool_card
from openjiuwen.harness.tools.base_tool import ToolOutput
from openjiuwen.harness.tools.lsp_tool._formatter import format_result
from openjiuwen.harness.tools.lsp_tool._schemas import LspOperation, LspToolInput
from openjiuwen.harness.lsp.core.manager import LSPServerManager
from openjiuwen.harness.lsp.core.utils.constants import MAX_LSP_FILE_SIZE_BYTES
from openjiuwen.harness.lsp.core.utils.file_uri import path_to_file_uri
from openjiuwen.harness.lsp.core.utils.git_ignore import filter_git_ignored_locations


class LspTool(Tool):
    """
    LSP tool for the harness layer.

    Accepts the full discriminated union input (all 8 operation types),
    validates with Pydantic, and converts the result to ToolOutput.
    """

    def __init__(
        self,
        operation: Optional[SysOperation] = None,
        language: str = "cn",
        agent_id: Optional[str] = None,
        workspace: Optional[str] = None,
    ) -> None:
        super().__init__(build_tool_card("lsp", "LspTool", language, agent_id=agent_id))
        self.operation = operation
        self._language = language
        self._workspace: Optional[Path] = Path(workspace).resolve() if workspace else None

    def _get_workspace(self) -> Optional[Path]:
        """获取 workspace 路径，优先使用配置的 workspace，其次使用 operation.work_dir"""
        if self._workspace:
            return self._workspace
        if self.operation:
            wd_val = getattr(self.operation, "work_dir", None)
            if wd_val:
                return Path(wd_val).resolve()
        return None

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        try:
            workspace = self._get_workspace()
            result = await call_lsp_tool(inputs, workspace=workspace, operation=self.operation)
        except Exception as e:
            return ToolOutput(success=False, error=f"LSP tool error: {e}")

        if not result.get("success"):
            return ToolOutput(success=False, error=result.get("error", "Unknown error"))

        data = result.get("data", {})
        return ToolOutput(
            success=True,
            data={
                "operation": data.get("operation"),
                "result": data.get("result"),
                "file_path": data.get("file_path"),
            },
        )

    async def stream(self, inputs: Dict[str, Any], **kwargs) -> None:
        """Streaming not supported for LSP operations."""
        return  # pylint: disable=return-in-async-function


def build_lsp_tool() -> dict[str, Any]:
    """Build the LSP Tool definition for AI Agent registration."""
    return {
        "name": "lsp",
        "description": (
            "LSP (Language Server Protocol) tool providing code navigation features. "
            "Navigation: go to definition, find references, document symbols, workspace symbols, "
            "find implementations, call hierarchy. "
            "Returns typed, structured results."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": [op.value for op in LspOperation],
                    "description": "LSP operation type",
                },
                "file_path": {
                    "type": "string",
                    "description": "File path (absolute or relative to workspace root).",
                },
                "line": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Line number (1-indexed; LSP internally uses 0-indexed)",
                },
                "character": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Column number (1-indexed; LSP internally uses 0-indexed)",
                },
                "query": {
                    "type": "string",
                    "description": "Search query (used by workspaceSymbol only)",
                },
                "include_declaration": {
                    "type": "boolean",
                    "description": "Whether findReferences includes the declaration location",
                },
            },
            "required": ["operation", "file_path"],
        },
    }


async def call_lsp_tool(
    input_data: dict[str, Any],
    workspace: Optional[Path] = None,
    operation: Optional[SysOperation] = None,
) -> dict[str, Any]:  # noqa: ANN401
    """Execute an LSP tool call (with gitignore filtering).

    Args:
        input_data: The LSP tool input parameters.
        workspace: Optional workspace path for resolving relative paths and sandbox boundary.
        operation: Optional SysOperation instance for work_dir and sandbox validation.
    """
    try:
        validated = TypeAdapter(LspToolInput).validate_python(input_data)
    except Exception as e:
        return {"success": False, "error": f"Invalid input: {e}", "data": None}

    op = LspOperation(validated.operation) if isinstance(validated.operation, str) else validated.operation

    file_path_raw = getattr(validated, "file_path", "")
    file_path = _resolve_path(file_path_raw, workspace, operation)
    if not file_path:
        return {
            "success": False,
            "error": f"File path could not be resolved: {file_path_raw}",
            "data": None,
        }

    # 确定初始化使用的 cwd：优先使用 workspace，其次使用 operation.work_dir，最后使用 os.getcwd()
    init_cwd = _get_effective_cwd(workspace, operation)

    manager = LSPServerManager.get_instance()
    if not manager:
        # 懒加载模式下，自动触发初始化（由 Tool 调用触发补全）
        from openjiuwen.harness.lsp import InitializeOptions, initialize_lsp
        try:
            await initialize_lsp(InitializeOptions(cwd=init_cwd))
            manager = LSPServerManager.get_instance()
        except Exception as exc:
            logger.warning("LspTool: auto-init failed: %s", exc)

        if not manager:
            return {
                "success": False,
                "error": "LSP server manager not initialized and auto-init failed.",
                "data": None,
            }

    effective_file_path = file_path
    if op == LspOperation.WORKSPACE_SYMBOL and not validated.file_path:
        workspace_root = manager.get_workspace_root()
        if workspace_root:
            candidates = list(Path(workspace_root).rglob("*.py"))
            if candidates:
                effective_file_path = str(candidates[0])
            else:
                effective_file_path = workspace_root

    server = await manager.get_or_start_server(effective_file_path)
    if not server:
        return {
            "success": False,
            "error": f"No LSP server available for file type: {Path(file_path).suffix or '.py'}",
            "data": None,
        }

    try:
        content = _read_file_content(file_path)
        if content is not None:
            ext = Path(file_path).suffix.lower()
            language_id = server.config.extension_to_language.get(ext, server.name)
            await manager.open_file(file_path, language_id)
    except Exception as exc:
        logger.debug("Failed to open file for LSP didOpen (ignored): %s", exc)

    params = _build_lsp_params(validated, file_path)

    # callHierarchy/incomingCalls and outgoingCalls require a CallHierarchyItem
    # in the "item" field, not a textDocument+position pair.  Obtain the item
    # by running prepareCallHierarchy first, then swap the params.
    if op in {LspOperation.INCOMING_CALLS, LspOperation.OUTGOING_CALLS}:
        try:
            prepare_result = await server.send_request(
                method="textDocument/prepareCallHierarchy",
                params=params,
            )
        except Exception as e:
            return {"success": False, "error": f"prepareCallHierarchy failed: {e}", "data": None}

        if not prepare_result:
            return {
                "success": True,
                "data": {
                    "operation": str(validated.operation),
                    "result": "No call hierarchy item found at the given position.",
                    "file_path": file_path,
                },
            }

        call_item = prepare_result[0] if isinstance(prepare_result, list) else prepare_result
        params = {"item": call_item}

    try:
        result = await server.send_request(
            method=_operation_to_method(validated.operation),
            params=params,
        )
    except Exception as e:
        err_str = str(e)
        if "Unhandled method" in err_str or "-32601" in err_str:
            method_name = _operation_to_method(validated.operation)
            return {
                "success": False,
                "error": f"Operation not supported by this language server: {method_name}",
                "data": None,
            }
        return {"success": False, "error": f"LSP request failed: {e}", "data": None}

    if result and _needs_gitignore_filter(validated.operation):
        gitignore_cwd = init_cwd
        if isinstance(result, list):
            locations = _extract_locations(result, validated.operation)
        elif isinstance(result, dict):
            locations = [{"uri": result.get("uri", "")}]
        else:
            locations = []
        if locations:
            try:
                filtered = await filter_git_ignored_locations(locations, gitignore_cwd)
                result = _reapply_filtered_results(result, filtered, validated.operation)
            except Exception as exc:
                logger.debug("Git ignore filtering failed (ignored): %s", exc)

    try:
        formatted = format_result(validated.operation, result)
    except Exception as exc:
        logger.debug("Result formatting failed, falling back to str: %s", exc)
        formatted = str(result) if result else ""

    return {
        "success": True,
        "data": {
            "operation": str(validated.operation),
            "result": formatted,
            "file_path": file_path,
        },
    }


def _resolve_path(
    file_path: str,
    workspace: Optional[Path] = None,
    operation: Optional[SysOperation] = None,
) -> str | None:
    """解析文件路径，支持 sandbox 边界验证。

    Args:
        file_path: 原始文件路径（绝对或相对）。
        workspace: 可选的 workspace 根目录，用于解析相对路径和 sandbox 验证。
        operation: 可选的 SysOperation，用于获取 work_dir。

    Returns:
        解析后的绝对路径，如果路径在 sandbox 外则返回 None。
    """
    try:
        p = Path(file_path)
        if p.is_absolute():
            abs_path = str(p.resolve())
            # 校验 sandbox 边界
            if workspace:
                try:
                    Path(abs_path).relative_to(workspace)
                except ValueError:
                    logger.warning("LspTool: file path '%s' is outside workspace sandbox", abs_path)
                    return None
            return abs_path

        # 相对路径：优先使用 workspace，其次使用 operation.work_dir
        workspace_root = workspace
        if not workspace_root and operation:
            wd_val = getattr(operation, "work_dir", None)
            if wd_val:
                workspace_root = Path(wd_val).resolve()

        if workspace_root:
            resolved = (workspace_root / p).resolve()
            # 校验 sandbox 边界
            try:
                resolved.relative_to(workspace_root)
            except ValueError:
                logger.warning("LspTool: resolved path '%s' is outside workspace sandbox", resolved)
                return None
            return str(resolved)

        return str(Path.cwd() / p)
    except Exception:
        return None


def _get_effective_cwd(
    workspace: Optional[Path] = None,
    operation: Optional[SysOperation] = None,
) -> str:
    """获取有效的工作目录。

    优先级：workspace > operation.work_dir > os.getcwd()
    """
    if workspace:
        return str(workspace)
    if operation:
        wd_val = getattr(operation, "work_dir", None)
        if wd_val:
            return wd_val
    return os.getcwd()


def _read_file_content(file_path: str) -> str | None:
    try:
        size = os.path.getsize(file_path)
        if size > MAX_LSP_FILE_SIZE_BYTES:
            return None
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return None


def _build_lsp_params(validated: LspToolInput, resolved_file_path: str) -> dict[str, Any]:
    if hasattr(validated, "line") and hasattr(validated, "character"):
        position = {
            "line": validated.line - 1,
            "character": validated.character - 1,
        }
    else:
        position = {"line": 0, "character": 0}

    # 使用 resolved 绝对路径生成 URI，避免 pyright 因相对路径 + drive letter 构造出非法路径
    file_uri = path_to_file_uri(resolved_file_path)
    op = LspOperation(validated.operation) if isinstance(validated.operation, str) else validated.operation

    if op == LspOperation.FIND_REFERENCES:
        return {
            "textDocument": {"uri": file_uri},
            "position": position,
            "context": {"includeDeclaration": getattr(validated, "include_declaration", True)},
        }
    elif op == LspOperation.WORKSPACE_SYMBOL:
        return {"query": getattr(validated, "query", "")}
    else:
        return {
            "textDocument": {"uri": file_uri},
            "position": position,
        }


def _operation_to_method(operation: LspOperation) -> str:
    return {
        LspOperation.GO_TO_DEFINITION: "textDocument/definition",
        LspOperation.FIND_REFERENCES: "textDocument/references",
        LspOperation.DOCUMENT_SYMBOL: "textDocument/documentSymbol",
        LspOperation.WORKSPACE_SYMBOL: "workspace/symbol",
        LspOperation.GO_TO_IMPLEMENTATION: "textDocument/implementation",
        LspOperation.PREPARE_CALL_HIERARCHY: "textDocument/prepareCallHierarchy",
        LspOperation.INCOMING_CALLS: "callHierarchy/incomingCalls",
        LspOperation.OUTGOING_CALLS: "callHierarchy/outgoingCalls",
    }[LspOperation(operation)]


def _needs_gitignore_filter(operation: LspOperation) -> bool:
    op = LspOperation(operation) if isinstance(operation, str) else operation
    return op in {
        LspOperation.FIND_REFERENCES,
        LspOperation.GO_TO_DEFINITION,
        LspOperation.GO_TO_IMPLEMENTATION,
        LspOperation.WORKSPACE_SYMBOL,
    }


def _extract_locations(
    result: list[Any],
    operation: LspOperation,
) -> list[dict[str, Any]]:
    op = LspOperation(operation) if isinstance(operation, str) else operation
    if op == LspOperation.WORKSPACE_SYMBOL:
        return [
            {
                "uri": (
                    s.get("location", {}).get("uri")
                    if isinstance(s.get("location"), dict)
                    else s.get("uri", "")
                )
            }
            for s in result
            if s.get("location") or s.get("uri")
        ]
    else:
        return [
            {"uri": loc.get("uri") or loc.get("targetUri", "")}
            for loc in result
            if loc.get("uri") or loc.get("targetUri")
        ]


def _reapply_filtered_results(
    original: list[Any] | dict[str, Any],
    filtered: list[dict[str, Any]],
    operation: LspOperation,
) -> list[Any] | dict[str, Any]:
    filtered_uris = {loc["uri"] for loc in filtered if loc.get("uri")}
    op = LspOperation(operation) if isinstance(operation, str) else operation

    if isinstance(original, dict):
        uri = original.get("uri", "")
        if uri and uri not in filtered_uris:
            return {}
        return original
    else:
        if op == LspOperation.WORKSPACE_SYMBOL:
            filtered_results = []
            for s in original:
                location = s.get("location")
                if not location or (isinstance(location, dict)
                                    and location.get("uri") in filtered_uris):
                    filtered_results.append(s)
            return filtered_results
        else:
            filtered_results = []
            for item in original:
                uri = item.get("uri") or item.get("targetUri")
                if not uri or uri in filtered_uris:
                    filtered_results.append(item)
            return filtered_results
