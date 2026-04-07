# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Enhanced BashTool with command semantics, smart truncation, and security."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, AsyncIterator, Dict, Optional

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.logging import sys_operation_logger
from openjiuwen.core.foundation.tool.base import Tool
from openjiuwen.core.sys_operation import SysOperation
from openjiuwen.harness.prompts.sections.tools import build_tool_card
from openjiuwen.harness.tools.base_tool import ToolOutput

from openjiuwen.harness.tools.bash._output import persist_large_output, truncate_output
from openjiuwen.harness.tools.bash._permission import (
    PermissionConfig,
    PermissionMode,
    check_permission,
)
from openjiuwen.harness.tools.bash._security import check_injection, get_destructive_warning
from openjiuwen.harness.tools.bash._semantics import interpret_exit_code, is_silent

_VALID_SHELL_TYPES = frozenset({"auto", "cmd", "powershell", "bash", "sh"})


class BashTool(Tool):
    """Shell command executor with semantic exit-code interpretation,
    smart output truncation, and injection detection."""

    def __init__(
        self,
        operation: SysOperation,
        language: str = "cn",
        workspace: str | None = None,
        permission_mode: str = "auto",
        deny_patterns: list[str] | None = None,
        allow_patterns: list[str] | None = None,
    ) -> None:
        super().__init__(build_tool_card("bash", "BashTool", language))
        self._operation = operation
        self._workspace: Path | None = Path(workspace).resolve() if workspace else None
        self._permission = PermissionConfig(
            mode=PermissionMode(permission_mode),
            deny_patterns=PermissionConfig.compile_patterns(deny_patterns),
            allow_patterns=PermissionConfig.compile_patterns(allow_patterns),
        )

    # ── workspace sandbox ─────────────────────────────────────

    def _get_workspace(self) -> Path | None:
        if self._workspace:
            return self._workspace
        wd = self._operation.work_dir
        return Path(wd).resolve() if wd else None

    def _resolve_workdir(self, workdir: str) -> Path | None:
        """Resolve workdir and enforce sandbox.

        Returns None if the resolved path escapes the workspace.
        """
        workspace = self._get_workspace()
        if workspace is None:
            return Path(workdir).resolve() if workdir else Path.cwd()

        candidate = Path(workdir) if workdir else workspace
        if not candidate.is_absolute():
            candidate = workspace / candidate
        candidate = candidate.resolve()
        try:
            candidate.relative_to(workspace)
        except ValueError:
            return None
        return candidate

    # ── input parsing ─────────────────────────────────────────

    @staticmethod
    def _parse_inputs(inputs: Dict[str, Any]) -> tuple[str, int, str, bool, int, str, str]:
        """Parse and clamp tool inputs.

        Returns:
            (command, timeout, workdir, background, max_output_chars, shell_type, description)
        """
        command = (inputs.get("command") or "").strip()
        timeout = max(1, min(int(inputs.get("timeout", 30)), 300))
        workdir = inputs.get("workdir", "")
        background = bool(inputs.get("background", False))
        max_output_chars = max(200, min(int(inputs.get("max_output_chars", 8000)), 20000))
        shell_type = inputs.get("shell_type", "auto")
        if shell_type not in _VALID_SHELL_TYPES:
            shell_type = "auto"
        description = inputs.get("description", "")
        return command, timeout, workdir, background, max_output_chars, shell_type, description

    # ── invoke ────────────────────────────────────────────────

    async def invoke(self, inputs: Dict[str, Any], **kwargs: Any) -> ToolOutput:
        command, timeout, workdir, background, max_chars, shell_type, description = self._parse_inputs(inputs)

        if not command:
            return ToolOutput(success=False, error="command cannot be empty")

        resolved_cwd = self._resolve_workdir(workdir)
        if resolved_cwd is None:
            return ToolOutput(success=False, error="workdir is outside workspace sandbox")

        # tool-layer injection check (supplements sys_operation safety)
        sec = check_injection(command)
        if sec.blocked:
            return ToolOutput(success=False, error=sec.reason)

        # permission pipeline (deny/allow patterns + mode enforcement)
        perm = check_permission(command, self._permission)
        if not perm.allowed:
            return ToolOutput(success=False, error=perm.reason)

        warning = get_destructive_warning(command)

        if description:
            sys_operation_logger.debug("BashTool: %s — %s", description, command)

        # ── background execution ──────────────────────────────
        if background:
            res = await self._operation.shell().execute_cmd_background(
                command, cwd=str(resolved_cwd), shell_type=shell_type,
            )
            if res.code != StatusCode.SUCCESS.code:
                return ToolOutput(success=False, error=res.message)
            return ToolOutput(success=True, data={"pid": res.data.pid, "status": "started"})

        # ── normal execution ──────────────────────────────────
        res = await self._operation.shell().execute_cmd(
            command, cwd=str(resolved_cwd), timeout=timeout, shell_type=shell_type,
        )
        if res.code != StatusCode.SUCCESS.code:
            return ToolOutput(success=False, error=res.message)

        exit_code = res.data.exit_code if res.data else -1
        stdout = (res.data.stdout or "") if res.data else ""
        stderr = (res.data.stderr or "") if res.data else ""

        meaning = interpret_exit_code(command, exit_code, stdout, stderr)
        silent = is_silent(command)

        # persist large outputs to disk before truncation
        persisted_path: str | None = None
        persisted_size: int | None = None
        if len(stdout) + len(stderr) > max_chars:
            persisted_path, persisted_size = persist_large_output(stdout, stderr)

        return ToolOutput(
            success=not meaning.is_error,
            data={
                "stdout": truncate_output(stdout, max_chars),
                "stderr": truncate_output(stderr, max_chars),
                "exit_code": exit_code,
                "return_code_interpretation": meaning.message,
                "no_output_expected": silent,
                "destructive_warning": warning,
                "persisted_output_path": persisted_path,
                "persisted_output_size": persisted_size,
            },
            error=truncate_output(stderr, max_chars) if meaning.is_error else None,
        )

    # ── stream ────────────────────────────────────────────────

    async def stream(self, inputs: Dict[str, Any], **kwargs: Any) -> AsyncIterator[ToolOutput]:
        command, timeout, workdir, _background, max_chars, shell_type, description = self._parse_inputs(inputs)

        if not command:
            yield ToolOutput(success=False, error="command cannot be empty")
            return

        resolved_cwd = self._resolve_workdir(workdir)
        if resolved_cwd is None:
            yield ToolOutput(success=False, error="workdir is outside workspace sandbox")
            return

        sec = check_injection(command)
        if sec.blocked:
            yield ToolOutput(success=False, error=sec.reason)
            return

        perm = check_permission(command, self._permission)
        if not perm.allowed:
            yield ToolOutput(success=False, error=perm.reason)
            return

        warning = get_destructive_warning(command)

        if description:
            sys_operation_logger.debug("BashTool(stream): %s — %s", description, command)

        start = time.monotonic()
        accumulated_stdout = ""
        accumulated_stderr = ""
        final_exit_code: int = -1

        async for chunk in self._operation.shell().execute_cmd_stream(
            command, cwd=str(resolved_cwd), timeout=timeout, shell_type=shell_type,
        ):
            if chunk.code != StatusCode.SUCCESS.code:
                yield ToolOutput(success=False, error=chunk.message)
                return

            data = chunk.data
            elapsed = round(time.monotonic() - start, 2)

            if data.exit_code is not None:
                final_exit_code = data.exit_code

            text = data.text or ""
            stream_type = data.type or "stdout"
            if stream_type == "stderr":
                accumulated_stderr += text
            else:
                accumulated_stdout += text

            yield ToolOutput(
                success=True,
                data={
                    "text": text,
                    "type": stream_type,
                    "chunk_index": data.chunk_index,
                    "exit_code": data.exit_code,
                    "elapsed_time_seconds": elapsed,
                },
            )

        # final summary after stream ends
        meaning = interpret_exit_code(command, final_exit_code, accumulated_stdout, accumulated_stderr)
        silent = is_silent(command)
        yield ToolOutput(
            success=not meaning.is_error,
            data={
                "stdout": truncate_output(accumulated_stdout, max_chars),
                "stderr": truncate_output(accumulated_stderr, max_chars),
                "exit_code": final_exit_code,
                "return_code_interpretation": meaning.message,
                "no_output_expected": silent,
                "destructive_warning": warning,
                "elapsed_time_seconds": round(time.monotonic() - start, 2),
            },
            error=truncate_output(accumulated_stderr, max_chars) if meaning.is_error else None,
        )
