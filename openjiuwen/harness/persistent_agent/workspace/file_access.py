# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Persistent workspace file access and write protection."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from contextvars import ContextVar, Token
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.foundation.llm.schema.message import ToolMessage
from openjiuwen.core.foundation.tool import Tool
from openjiuwen.core.foundation.tool.schema import ToolOutput
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.harness.tools.filesystem import EditFileTool, ListDirTool, ReadFileTool, WriteFileTool

from ..schema.execution import ExecutionContext
from ..tools.workspace_read import PersistentWorkspaceListTool, PersistentWorkspaceReadTool
from .access import WorkspaceAccessPort, WorkspaceWriteAccessPort, validate_write_scope
from .path_policy import WorkspacePathError, observe_version, resolve_write_path


@dataclass(frozen=True, slots=True)
class WorkspaceWritePreflight:
    """Internal handoff from the file authorization check to the writer."""

    tool_name: str
    relative_path: str
    source_ref: str
    backend_path: Path
    target_exists: bool
    observed_version: str | None
    expected_content_sha256: str | None
    create_if_absent: bool


_CURRENT_WRITE_PREFLIGHT: ContextVar[WorkspaceWritePreflight | None] = ContextVar(
    "persistent_workspace_write_preflight",
    default=None,
)


def _bind_write_preflight(preflight: WorkspaceWritePreflight) -> Token:
    return _CURRENT_WRITE_PREFLIGHT.set(preflight)


def _reset_write_preflight(token: Token) -> None:
    _CURRENT_WRITE_PREFLIGHT.reset(token)


def _current_write_preflight() -> WorkspaceWritePreflight | None:
    return _CURRENT_WRITE_PREFLIGHT.get()


_SAFE_FAILURES = {
    "forbidden": "workspace path is not writable",
    "unknown": "workspace write outcome is unknown",
    "not_found": "workspace entry was not found",
    "version_conflict": "workspace file version conflict; re-read it before retrying",
    "unavailable": "workspace write is unavailable",
}
_PATH_KEYS = frozenset({"file_path", "path", "source_path", "target_path"})
_DROP_SUCCESS_KEYS = frozenset({"original_file", "content"})


class WorkspaceWriteAdapter(Tool):
    """Keep the existing Harness writer behind the Persistent file boundary."""

    def __init__(self, base_tool: Any) -> None:
        super().__init__(base_tool.card)
        self._base_tool = base_tool

    async def invoke(self, inputs: Mapping[str, Any] | None, **kwargs: Any) -> ToolOutput:
        preflight = _current_write_preflight()
        if preflight is None or preflight.tool_name != self.card.name:
            return self._failure("unknown")

        delegated_inputs = dict(inputs or {})
        if delegated_inputs.get("file_path") != preflight.relative_path:
            return self._failure("unknown")
        delegated_inputs["file_path"] = os.fspath(preflight.backend_path)
        condition = self._condition(preflight)
        if condition is None:
            return self._failure("unknown")
        delegated_kwargs = dict(kwargs)
        delegated_kwargs["_persistent_conditional_write"] = condition

        # Exceptions intentionally escape.  PA-15 classifies a dispatched
        # exception as unknown instead of turning it into a retryable failure.
        output = await self._base_tool.invoke(delegated_inputs, **delegated_kwargs)
        if not isinstance(output, ToolOutput):
            return self._failure("unavailable")
        return self._sanitize_output(output, preflight)

    async def stream(self, inputs: Mapping[str, Any] | None, **kwargs: Any) -> AsyncIterator[ToolOutput]:
        yield await self.invoke(inputs, **kwargs)

    def render_for_llm(self, output: Any) -> str:
        renderer = getattr(self._base_tool, "render_for_llm", None)
        if callable(renderer):
            return renderer(output)
        return super().render_for_llm(output)

    @staticmethod
    def _failure(status: str) -> ToolOutput:
        safe_status = status if status in _SAFE_FAILURES else "unknown"
        return ToolOutput(success=False, data={"status": safe_status}, error=_SAFE_FAILURES[safe_status])

    def _sanitize_output(self, output: ToolOutput, preflight: WorkspaceWritePreflight) -> ToolOutput:
        if not output.success:
            status = output.data.get("status") if isinstance(output.data, Mapping) else None
            if status not in _SAFE_FAILURES:
                status = "failed"
            if status == "failed":
                return ToolOutput(success=False, data={"status": "failed"}, error="workspace write failed")
            return self._failure(status)

        data = output.data if isinstance(output.data, Mapping) else {}
        safe_data: dict[str, Any] = {"file_path": preflight.relative_path}
        for key, value in data.items():
            if key not in _DROP_SUCCESS_KEYS:
                safe_data[str(key)] = self._sanitize_value(value, preflight)
        safe_data["file_path"] = preflight.relative_path

        updates: dict[str, Any] = {"data": safe_data, "error": None}
        for field_name in ("extracted_content", "long_term_memory"):
            field_value = getattr(output, field_name, None)
            if isinstance(field_value, str):
                updates[field_name] = self._sanitize_text(field_value, preflight)
        return output.model_copy(update=updates)

    @staticmethod
    def _condition(preflight: WorkspaceWritePreflight) -> dict[str, Any] | None:
        if preflight.create_if_absent:
            return {"create_if_absent": True}
        if preflight.expected_content_sha256:
            return {"expected_content_sha256": preflight.expected_content_sha256}
        return None

    @classmethod
    def _sanitize_value(cls, value: Any, preflight: WorkspaceWritePreflight, key: str | None = None) -> Any:
        if isinstance(value, Mapping):
            return {
                str(item_key): cls._sanitize_value(item_value, preflight, str(item_key))
                for item_key, item_value in value.items()
                if item_key not in _DROP_SUCCESS_KEYS
            }
        if isinstance(value, list):
            return [cls._sanitize_value(item, preflight, key) for item in value]
        if isinstance(value, tuple):
            return tuple(cls._sanitize_value(item, preflight, key) for item in value)
        if key in _PATH_KEYS and isinstance(value, str):
            return cls._sanitize_path(value, preflight)
        if isinstance(value, str):
            return cls._sanitize_text(value, preflight)
        return value

    @staticmethod
    def _sanitize_path(value: str, preflight: WorkspaceWritePreflight) -> str:
        backend = os.fspath(preflight.backend_path)
        try:
            if os.path.normcase(os.path.abspath(value)) == os.path.normcase(os.path.abspath(backend)):
                return preflight.relative_path
        except (TypeError, ValueError, OSError):
            pass
        return preflight.relative_path if value == backend else value

    @staticmethod
    def _sanitize_text(value: str, preflight: WorkspaceWritePreflight) -> str:
        backend = os.fspath(preflight.backend_path)
        sanitized = value.replace(backend, preflight.relative_path)
        return sanitized.replace(backend.replace("\\", "/"), preflight.relative_path)


class PersistentWorkspaceReadRail(DeepAgentRail):
    """Register the PA-14 ``read_file`` and ``list_files`` abilities only."""

    priority = 100

    def __init__(
        self,
        execution: ExecutionContext,
        workspace_access: WorkspaceAccessPort,
        *,
        permission_profile: str = "default",
    ) -> None:
        super().__init__()
        self.execution = execution
        self.workspace_access = workspace_access
        self.permission_profile = permission_profile
        self.tools = []

    def init(self, agent) -> None:
        if self.tools:
            return
        if self.sys_operation is None:
            raise build_error(
                StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR,
                error_msg="workspace_read requires enable_sys_operation",
            )

        language = agent.system_prompt_builder.language
        agent_id = getattr(getattr(agent, "card", None), "id", None)
        read_tool = PersistentWorkspaceReadTool(
            base_tool=ReadFileTool(self.sys_operation, language, agent_id),
            execution=self.execution,
            workspace_access=self.workspace_access,
            permission_profile=self.permission_profile,
        )
        list_tool = PersistentWorkspaceListTool(
            base_tool=ListDirTool(self.sys_operation, language, agent_id),
            execution=self.execution,
            workspace_access=self.workspace_access,
        )
        self.tools = [read_tool, list_tool]
        for tool in self.tools:
            agent.ability_manager.add_ability(tool.card, tool)

    def uninit(self, agent) -> None:
        for tool in self.tools:
            name = getattr(tool.card, "name", None)
            if name and hasattr(agent, "ability_manager"):
                agent.ability_manager.remove_ability(name)
        self.tools = []


class PersistentWorkspaceWriteRail(DeepAgentRail):
    """Preflight and register guarded Persistent ``write_file``/``edit_file``."""

    # Permission/security rails normally run at 90 or above.  PA-16 runs after
    # those decisions but before the PA-15 intent rail (priority 5).
    priority = 80

    def __init__(
        self,
        execution: ExecutionContext,
        workspace_access: WorkspaceWriteAccessPort,
        *,
        permission_profile: str = "default",
    ) -> None:
        super().__init__()
        self.execution = execution
        self.workspace_access = workspace_access
        self.permission_profile = permission_profile
        self.tools = []

    def init(self, agent) -> None:
        if self.tools:
            return
        if self.sys_operation is None:
            raise build_error(
                StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR,
                error_msg="workspace_write requires enable_sys_operation",
            )

        language = agent.system_prompt_builder.language
        agent_id = getattr(getattr(agent, "card", None), "id", None)
        write_tool = WorkspaceWriteAdapter(
            WriteFileTool(
                self.sys_operation,
                language,
                agent_id,
                enable_conditional_writes=True,
            )
        )
        edit_tool = WorkspaceWriteAdapter(
            EditFileTool(
                self.sys_operation,
                language,
                agent_id,
                enable_conditional_writes=True,
            )
        )
        self.tools = [write_tool, edit_tool]
        for tool in self.tools:
            agent.ability_manager.add_ability(tool.card, tool)

    def uninit(self, agent) -> None:
        for tool in self.tools:
            name = getattr(tool.card, "name", None)
            if name and hasattr(agent, "ability_manager"):
                agent.ability_manager.remove_ability(name)
        self.tools = []

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        """Authorize, resolve, and observe a write before PA-15 records intent."""
        tool_name = getattr(ctx.inputs, "tool_name", None)
        if tool_name not in {"write_file", "edit_file"} or self._must_not_dispatch(ctx):
            return

        arguments = self._arguments(ctx)
        if arguments is None:
            self._block(ctx, "forbidden", "workspace write arguments are invalid")
            return
        requested_path = arguments.get("file_path")
        if not isinstance(requested_path, str):
            self._block(ctx, "forbidden", "workspace path is not writable")
            return

        try:
            scope = await self.workspace_access.resolve_write_scope(self.execution)
            scope = validate_write_scope(scope, self.execution, permission_profile=self.permission_profile)
            resolved = resolve_write_path(scope, requested_path)
        except WorkspacePathError as exc:
            self._block(ctx, exc.status, self._safe_error(exc.status))
            return
        except Exception:
            self._block(ctx, "unknown", self._safe_error("unknown"))
            return

        fs_operation = self._conditional_fs()
        if fs_operation is None:
            self._block(ctx, "unavailable", "workspace conditional write is unavailable")
            return

        try:
            target_exists = resolved.path.exists()
            target_is_dir = target_exists and resolved.path.is_dir()
        except OSError:
            self._block(ctx, "unknown", self._safe_error("unknown"))
            return
        if target_is_dir:
            self._block(ctx, "forbidden", "workspace path is not writable")
            return

        if tool_name == "edit_file":
            old_string = arguments.get("old_string")
            if not isinstance(old_string, str):
                self._block(ctx, "forbidden", "workspace edit arguments are invalid")
                return
            if old_string == "" and target_exists:
                self._block(ctx, "version_conflict", self._safe_error("version_conflict"))
                return
            if old_string != "" and not target_exists:
                self._block(ctx, "not_found", self._safe_error("not_found"))
                return

        expected_content_sha256 = None
        create_if_absent = not target_exists
        if target_exists:
            expected_content_sha256 = await self._expected_content_sha256(fs_operation, resolved.path)
            if expected_content_sha256 is None:
                self._block(ctx, "unavailable", "workspace conditional write is unavailable")
                return

        preflight = WorkspaceWritePreflight(
            tool_name=tool_name,
            relative_path=resolved.relative_path,
            source_ref=resolved.source_ref,
            backend_path=resolved.path,
            target_exists=target_exists,
            observed_version=observe_version(resolved.path) if target_exists else None,
            expected_content_sha256=expected_content_sha256,
            create_if_absent=create_if_absent,
        )
        old_token = ctx.extra.pop("_persistent_write_context_token", None)
        if old_token is not None:
            _reset_write_preflight(old_token)
        ctx.extra["_persistent_write_preflight"] = preflight
        ctx.extra["_persistent_write_context_token"] = _bind_write_preflight(preflight)
        ctx.extra["_persistent_tool_target_ref"] = resolved.source_ref
        ctx.extra["_persistent_tool_args_summary"] = self._args_summary(tool_name, arguments, resolved.relative_path)

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        """Remove the task-local physical-path handoff after dispatch completes."""
        token = ctx.extra.pop("_persistent_write_context_token", None)
        if token is not None:
            _reset_write_preflight(token)
        for key in (
            "_persistent_write_preflight",
            "_persistent_tool_target_ref",
            "_persistent_tool_args_summary",
        ):
            ctx.extra.pop(key, None)

    def _conditional_fs(self):
        try:
            fs_operation = self.sys_operation.fs()
        except Exception:
            return None
        return fs_operation if getattr(fs_operation, "supports_conditional_write", False) is True else None

    @staticmethod
    async def _expected_content_sha256(fs_operation, path) -> str | None:
        """Read the exact bytes used as the write CAS condition.

        The digest is captured through the configured SysOperation rather than
        by reading the host path directly.  This keeps the handoff valid for a
        provider whose filesystem is not the Python process' local filesystem.
        The final comparison still happens atomically inside that provider.
        """
        try:
            result = await fs_operation.read_file(os.fspath(path), mode="bytes")
        except Exception:
            return None
        if getattr(result, "code", None) != StatusCode.SUCCESS.code:
            return None
        content = getattr(getattr(result, "data", None), "content", None)
        if isinstance(content, str):
            content = content.encode("utf-8")
        if not isinstance(content, (bytes, bytearray)):
            return None
        return hashlib.sha256(bytes(content)).hexdigest()

    @staticmethod
    def _arguments(ctx: AgentCallbackContext) -> dict[str, Any] | None:
        arguments = getattr(ctx.inputs, "tool_args", None)
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except (TypeError, ValueError):
                return None
        return dict(arguments) if isinstance(arguments, Mapping) else None

    @staticmethod
    def _args_summary(tool_name: str, arguments: dict[str, Any], relative_path: str) -> str:
        if tool_name == "write_file":
            content = arguments.get("content")
            content_bytes = len(content.encode("utf-8", errors="replace")) if isinstance(content, str) else 0
            summary = {
                "operation": "write",
                "file_path": relative_path,
                "content_bytes": content_bytes,
            }
        else:
            old_string = arguments.get("old_string")
            new_string = arguments.get("new_string")
            summary = {
                "operation": "edit",
                "file_path": relative_path,
                "old_string_chars": len(old_string) if isinstance(old_string, str) else 0,
                "new_string_chars": len(new_string) if isinstance(new_string, str) else 0,
                "replace_all": bool(arguments.get("replace_all", False)),
            }
        return json.dumps(summary, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _must_not_dispatch(ctx: AgentCallbackContext) -> bool:
        if ctx.has_force_finish_request or ctx.extra.get("_skip_tool"):
            return True
        call = getattr(ctx.inputs, "tool_call", None)
        call_id = getattr(call, "id", None)
        skip_calls = ctx.extra.get("_skip_tool_calls")
        return isinstance(skip_calls, dict) and bool(skip_calls.get(call_id))

    @staticmethod
    def _safe_error(status: str) -> str:
        return {
            "forbidden": "workspace path is not writable",
            "unknown": "workspace write authorization is unavailable",
            "not_found": "workspace entry was not found",
            "version_conflict": "workspace file version conflict; re-read it before retrying",
            "unavailable": "workspace conditional write is unavailable",
        }.get(status, "workspace write is unavailable")

    def _block(self, ctx: AgentCallbackContext, status: str, error: str) -> None:
        call = getattr(ctx.inputs, "tool_call", None)
        call_id = getattr(call, "id", "")
        output = ToolOutput(success=False, data={"status": status}, error=error)
        ctx.extra["_skip_tool"] = True
        ctx.inputs.tool_result = output
        ctx.inputs.tool_msg = ToolMessage(content=error, tool_call_id=call_id)


__all__ = ["PersistentWorkspaceReadRail", "PersistentWorkspaceWriteRail", "WorkspaceWriteAdapter"]
