# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""PA-14 guarded adapters for the existing Harness workspace read tools."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Mapping
from typing import Any

from openjiuwen.core.foundation.tool import Tool
from openjiuwen.core.foundation.tool.schema import ToolOutput

from ..schema.execution import ExecutionContext
from ..workspace.access import WorkspaceAccessPort, validate_read_scope
from ..workspace.path_policy import (
    ResolvedWorkspacePath,
    WorkspacePathError,
    observe_version,
    resolve_read_path,
)

_SAFE_ERRORS = {
    "forbidden": "workspace path is not readable",
    "unknown": "workspace authorization is unavailable",
    "not_found": "workspace entry was not found",
    "unavailable": "workspace read is unavailable",
}
_PATH_METADATA_KEYS = frozenset({"file_path", "path", "source_path", "target_path"})


class _PersistentWorkspaceReadTool(Tool):
    """Common authorization and result handling for one read-only tool."""

    _path_key: str
    _default_path: str | None = None

    def __init__(
        self,
        *,
        base_tool: Any,
        execution: ExecutionContext,
        workspace_access: WorkspaceAccessPort,
    ) -> None:
        card = base_tool.card.model_copy(deep=True)
        if self._path_key == "file_path":
            card.description = card.description.replace(
                "file_path 必须是绝对路径。",
                "file_path 必须是办公空间内的逻辑相对路径；不接受绝对路径或包含 `..` 的路径段。",
            ).replace(
                "file_path must be an absolute path.",
                "file_path must be a workspace-relative path; absolute paths and `..` path segments are not allowed.",
            )
            input_params = card.input_params
            if isinstance(input_params, dict):
                properties = input_params.get("properties")
                if isinstance(properties, dict):
                    path_param = properties.get("file_path")
                    if isinstance(path_param, dict):
                        path_description = path_param.get("description", "")
                        path_param["description"] = path_description.replace(
                            "要读取的文件绝对路径",
                            "办公空间内文件的逻辑相对路径（不接受绝对路径或包含 `..` 的路径段）",
                        ).replace(
                            "The absolute path to the file to read",
                            "Workspace-relative path for the file to read "
                            "(absolute paths and `..` path segments are not accepted)",
                        )
        super().__init__(card)
        self._base_tool = base_tool
        self._execution = execution
        self._workspace_access = workspace_access

    async def invoke(self, inputs: Mapping[str, Any] | None, **kwargs: Any) -> ToolOutput:
        requested_path = self._get_requested_path(inputs)
        resolved, failure = await self._authorize(requested_path)
        if failure is not None:
            return failure
        assert resolved is not None

        # The authorization decision is already known at this point. A missing
        # authorized target is different from a denied target, and is safe to
        # classify without exposing the host path.
        if not self._target_exists(resolved.path):
            return self._failure("not_found")

        delegated_inputs = dict(inputs or {})
        delegated_inputs[self._path_key] = os.fspath(resolved.path)
        try:
            output = await self._base_tool.invoke(delegated_inputs, **kwargs)
        except Exception:
            return self._failure(self._backend_failure_status(resolved))
        if not isinstance(output, ToolOutput):
            return self._failure("unavailable")
        if not output.success:
            return self._failure(self._backend_failure_status(resolved))
        return self._decorate_success(output, resolved, delegated_inputs)

    async def stream(self, inputs: Mapping[str, Any] | None, **kwargs: Any) -> AsyncIterator[ToolOutput]:
        """Expose the same guard for callers that use the Tool stream API."""
        yield await self.invoke(inputs, **kwargs)

    def render_for_llm(self, output: Any) -> str:
        renderer = getattr(self._base_tool, "render_for_llm", None)
        if callable(renderer):
            return renderer(output)
        return super().render_for_llm(output)

    def _get_requested_path(self, inputs: Mapping[str, Any] | None) -> Any:
        if not isinstance(inputs, Mapping):
            return None
        value = inputs.get(self._path_key, self._default_path)
        return value

    async def _authorize(
        self,
        requested_path: Any,
    ) -> tuple[ResolvedWorkspacePath | None, ToolOutput | None]:
        try:
            scope = await self._workspace_access.resolve_read_scope(self._execution)
            scope = validate_read_scope(scope, self._execution)
            return resolve_read_path(scope, requested_path), None
        except WorkspacePathError as exc:
            return None, self._failure(exc.status)
        except Exception:
            # A host lookup or an identity mismatch must fail closed. The
            # caller receives no host exception, reason, or path detail.
            return None, self._failure("unknown")

    @staticmethod
    def _target_exists(path: Any) -> bool:
        try:
            return bool(path.exists())
        except OSError:
            return False

    def _backend_failure_status(self, resolved: ResolvedWorkspacePath) -> str:
        return "not_found" if not self._target_exists(resolved.path) else "unavailable"

    @staticmethod
    def _failure(status: str) -> ToolOutput:
        safe_status = status if status in _SAFE_ERRORS else "unknown"
        return ToolOutput(success=False, data={"status": safe_status}, error=_SAFE_ERRORS[safe_status])

    def _decorate_success(
        self,
        output: ToolOutput,
        resolved: ResolvedWorkspacePath,
        inputs: Mapping[str, Any],
    ) -> ToolOutput:
        if isinstance(output.data, Mapping):
            data = dict(output.data)
        elif output.data is None:
            data = {}
        else:
            data = {"content": output.data}

        data = _logicalize_path_metadata(data, resolved.path, resolved.relative_path)
        data[self._path_key] = resolved.relative_path
        data["source_refs"] = [resolved.source_ref]
        data["version"] = observe_version(resolved.path)
        partial = self._is_partial(inputs)
        data["status"] = "partial" if partial else "ok"
        data["completeness"] = "partial" if partial else "complete"
        data["next_cursor"] = None
        updates: dict[str, Any] = {"data": data, "error": None}
        for field_name in ("extracted_content", "long_term_memory"):
            field_value = getattr(output, field_name, None)
            if isinstance(field_value, str):
                updates[field_name] = _logicalize_text(field_value, resolved.path, resolved.relative_path)
        return output.model_copy(update=updates)

    def _is_partial(self, inputs: Mapping[str, Any]) -> bool:
        return bool(inputs.get("offset", 0)) or inputs.get("limit") is not None


class PersistentWorkspaceReadTool(_PersistentWorkspaceReadTool):
    """Authorized wrapper around Harness ``ReadFileTool``."""

    _path_key = "file_path"


class PersistentWorkspaceListTool(_PersistentWorkspaceReadTool):
    """Authorized wrapper around Harness ``ListDirTool``."""

    _path_key = "path"
    _default_path = "."

    def _is_partial(self, inputs: Mapping[str, Any]) -> bool:
        # ListDirTool has no paging contract yet; preserve the complete result
        # shape until a bounded list API is added.
        return False


def _logicalize_path_metadata(value: Any, absolute_path: Any, relative_path: str, key: str | None = None) -> Any:
    """Remove backend absolute paths from structured tool metadata only."""
    if isinstance(value, Mapping):
        return {
            item_key: _logicalize_path_metadata(item_value, absolute_path, relative_path, str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        return [_logicalize_path_metadata(item, absolute_path, relative_path, key) for item in value]
    if isinstance(value, tuple):
        return tuple(_logicalize_path_metadata(item, absolute_path, relative_path, key) for item in value)
    if key == "content" and isinstance(value, str):
        return _logicalize_text(value, absolute_path, relative_path)
    if key not in _PATH_METADATA_KEYS or not isinstance(value, str):
        return value
    try:
        if os.path.normcase(os.path.abspath(value)) == os.path.normcase(os.fspath(absolute_path)):
            return relative_path
    except (TypeError, ValueError, OSError):
        return relative_path if value == os.fspath(absolute_path) else value
    return value


def _logicalize_text(value: str, absolute_path: Any, relative_path: str) -> str:
    """Replace backend spellings in tool-generated text metadata."""
    absolute = os.fspath(absolute_path)
    for spelling in {absolute, absolute.replace("\\", "/")}:
        value = value.replace(spelling, relative_path)
    return value


__all__ = [
    "PersistentWorkspaceListTool",
    "PersistentWorkspaceReadTool",
]
