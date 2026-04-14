# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
#
# DEPRECATED: Use openjiuwen.harness.tools.bash.BashTool instead.
# This module is kept for backward compatibility only.
#
import os
from pathlib import Path
from typing import Dict, Any, AsyncIterator, Optional

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.foundation.tool.base import Tool
from openjiuwen.core.sys_operation import SysOperation
from openjiuwen.harness.prompts.sections.tools import build_tool_card
from openjiuwen.harness.tools.base_tool import ToolOutput


def _clip_text(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return f"{value[:max_chars]}\n...[truncated]"


class BashTool(Tool):

    def __init__(self, operation: SysOperation, language: str = "cn",
                 workspace: Optional[str] = None, agent_id: Optional[str] = None, **_kwargs):
        super().__init__(build_tool_card("bash", "BashTool", language, agent_id=agent_id))
        self.operation = operation

    _VALID_SHELL_TYPES = {"auto", "cmd", "powershell", "bash", "sh"}

    @staticmethod
    def _resolve_timeout(raw_value: Any, default: int = 300) -> int:
        """Parse and validate a timeout value."""
        try:
            timeout = int(raw_value)
        except (TypeError, ValueError):
            timeout = default
        try:
            max_timeout = int(os.getenv("BASH_TOOL_MAX_TIMEOUT_SECONDS") or "3600")
        except ValueError:
            max_timeout = 3600
        max_timeout = max(1, max_timeout)
        return max(1, min(timeout, max_timeout))

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        command = (inputs.get("command") or "").strip()
        timeout = self._resolve_timeout(inputs.get("timeout", 300))
        workdir = inputs.get("workdir", "")
        background = bool(inputs.get("background", False))
        max_output_chars = max(200, min(int(inputs.get("max_output_chars", 8000)), 20000))
        shell_type = inputs.get("shell_type", "auto")
        if shell_type not in self._VALID_SHELL_TYPES:
            shell_type = "auto"

        if not command:
            return ToolOutput(success=False, error="command cannot be empty")

        from openjiuwen.core.sys_operation.cwd import get_cwd
        resolved_cwd = workdir or get_cwd()

        if background:
            res = await self.operation.shell().execute_cmd_background(
                command, cwd=str(resolved_cwd), shell_type=shell_type
            )
            if res.code != StatusCode.SUCCESS.code:
                return ToolOutput(success=False, error=res.message)
            return ToolOutput(success=True, data={"pid": res.data.pid, "status": "started"})

        res = await self.operation.shell().execute_cmd(
            command, cwd=str(resolved_cwd), timeout=timeout, shell_type=shell_type
        )
        if res.code != StatusCode.SUCCESS.code:
            return ToolOutput(success=False, error=res.message)
        exit_code = res.data.exit_code if res.data else -1
        stdout = _clip_text((res.data.stdout or "") if res.data else "", max_output_chars)
        stderr = _clip_text((res.data.stderr or "") if res.data else "", max_output_chars)
        return ToolOutput(
            success=(exit_code == 0),
            data={"stdout": stdout, "stderr": stderr, "exit_code": exit_code},
            error=stderr if exit_code != 0 else None,
        )

    async def stream(self, inputs: Dict[str, Any], **kwargs) -> AsyncIterator[Any]:
        pass
