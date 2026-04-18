# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Enhanced PowerShellTool with command semantics, smart truncation, and security."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import (
    Any,
    AsyncIterator,
    Dict,
    Optional,
)

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.logging import sys_operation_logger
from openjiuwen.core.foundation.tool.base import Tool
from openjiuwen.core.sys_operation import SysOperation
from openjiuwen.harness.prompts.sections.tools import build_tool_card
from openjiuwen.harness.tools.base_tool import ToolOutput
from openjiuwen.harness.tools.powershell._output import (
    persist_large_output,
    truncate_output,
)
from openjiuwen.harness.tools.powershell._permission import (
    check_permission,
    PermissionConfig,
    PermissionMode,
)
from openjiuwen.harness.tools.powershell._security import (
    check_injection,
    get_destructive_warning,
)
from openjiuwen.harness.tools.powershell._semantics import (
    interpret_exit_code,
    is_silent,
)


@dataclass(frozen=True)
class _PowerShellInputs:
    """Parsed and clamped inputs for a PowerShellTool invocation."""

    command: str
    timeout: int
    workdir: str
    background: bool
    max_output_chars: int
    description: str


class PowerShellTool(Tool):
    """PowerShell command executor with truncation, permissions, and security checks."""

    def __init__(
        self,
        operation: SysOperation,
        language: str = "cn",
        permission_mode: str = "auto",
        deny_patterns: list[str] | None = None,
        allow_patterns: list[str] | None = None,
        agent_id: Optional[str] = None,
        **_kwargs: Any,
    ) -> None:
        super().__init__(build_tool_card("powershell", "PowerShellTool", language, agent_id=agent_id))
        self._operation = operation
        self._permission = PermissionConfig(
            mode=PermissionMode(permission_mode),
            deny_patterns=PermissionConfig.compile_patterns(deny_patterns),
            allow_patterns=PermissionConfig.compile_patterns(allow_patterns),
        )

    @staticmethod
    def _parse_inputs(inputs: Dict[str, Any]) -> _PowerShellInputs:
        """Parse and clamp tool inputs."""
        return _PowerShellInputs(
            command=(inputs.get("command") or "").strip(),
            timeout=max(1, min(int(inputs.get("timeout", 30)), 300)),
            workdir=inputs.get("workdir", ""),
            background=bool(inputs.get("background", False)),
            max_output_chars=max(200, min(int(inputs.get("max_output_chars", 8000)), 20000)),
            description=inputs.get("description", ""),
        )

    async def invoke(self, inputs: Dict[str, Any], **kwargs: Any) -> ToolOutput:
        from openjiuwen.core.sys_operation.cwd import get_cwd, get_workspace

        p = self._parse_inputs(inputs)

        if not p.command:
            return ToolOutput(success=False, error="command cannot be empty")

        current_cwd = get_cwd()
        workspace_cwd = get_workspace()
        resolved_cwd = p.workdir or workspace_cwd or current_cwd

        if os.getenv("OPENJIUWEN_BASH_STRICT") == "1":
            guard = self._guard(p)
            if guard is not None:
                return guard

        warning = get_destructive_warning(p.command)

        if p.description:
            sys_operation_logger.debug("PowerShellTool: %s - %s", p.description, p.command)

        if p.background:
            res = await self._operation.shell().execute_cmd_background(
                p.command,
                cwd=resolved_cwd,
                shell_type="powershell",
            )
            if res.code != StatusCode.SUCCESS.code:
                return ToolOutput(success=False, error=res.message)
            return ToolOutput(success=True, data={"pid": res.data.pid, "status": "started"})

        res = await self._operation.shell().execute_cmd(
            p.command,
            cwd=resolved_cwd,
            timeout=p.timeout,
            shell_type="powershell",
        )
        if res.code != StatusCode.SUCCESS.code:
            return ToolOutput(success=False, error=res.message)

        exit_code = res.data.exit_code if res.data else -1
        stdout = (res.data.stdout or "") if res.data else ""
        stderr = (res.data.stderr or "") if res.data else ""

        meaning = interpret_exit_code(p.command, exit_code, stdout, stderr)
        silent = is_silent(p.command)

        persisted_path: str | None = None
        persisted_size: int | None = None
        if len(stdout) + len(stderr) > p.max_output_chars:
            persisted_path, persisted_size = persist_large_output(stdout, stderr)

        return ToolOutput(
            success=not meaning.is_error,
            data={
                "stdout": truncate_output(stdout, p.max_output_chars),
                "stderr": truncate_output(stderr, p.max_output_chars),
                "exit_code": exit_code,
                "return_code_interpretation": meaning.message,
                "no_output_expected": silent,
                "destructive_warning": warning,
                "persisted_output_path": persisted_path,
                "persisted_output_size": persisted_size,
            },
            error=truncate_output(stderr, p.max_output_chars) if meaning.is_error else None,
        )

    async def stream(self, inputs: Dict[str, Any], **kwargs: Any) -> AsyncIterator[ToolOutput]:
        from openjiuwen.core.sys_operation.cwd import get_cwd, get_workspace

        p = self._parse_inputs(inputs)

        if not p.command:
            yield ToolOutput(success=False, error="command cannot be empty")
            return

        current_cwd = get_cwd()
        workspace_cwd = get_workspace()
        resolved_cwd = p.workdir or workspace_cwd or current_cwd

        if os.getenv("OPENJIUWEN_BASH_STRICT") == "1":
            guard = self._guard(p)
            if guard is not None:
                yield guard
                return

        warning = get_destructive_warning(p.command)

        if p.description:
            sys_operation_logger.debug("PowerShellTool(stream): %s - %s", p.description, p.command)

        start = time.monotonic()
        accumulated_stdout = ""
        accumulated_stderr = ""
        final_exit_code = -1

        async for chunk in self._operation.shell().execute_cmd_stream(
            p.command,
            cwd=resolved_cwd,
            timeout=p.timeout,
            shell_type="powershell",
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

        meaning = interpret_exit_code(p.command, final_exit_code, accumulated_stdout, accumulated_stderr)
        silent = is_silent(p.command)
        yield ToolOutput(
            success=not meaning.is_error,
            data={
                "stdout": truncate_output(accumulated_stdout, p.max_output_chars),
                "stderr": truncate_output(accumulated_stderr, p.max_output_chars),
                "exit_code": final_exit_code,
                "return_code_interpretation": meaning.message,
                "no_output_expected": silent,
                "destructive_warning": warning,
                "elapsed_time_seconds": round(time.monotonic() - start, 2),
            },
            error=truncate_output(accumulated_stderr, p.max_output_chars) if meaning.is_error else None,
        )

    def _guard(self, p: _PowerShellInputs):
        sec = check_injection(p.command)
        if sec.blocked:
            return ToolOutput(success=False, error=sec.reason)

        perm = check_permission(p.command, self._permission)
        if not perm.allowed:
            return ToolOutput(success=False, error=perm.reason)
        return None
