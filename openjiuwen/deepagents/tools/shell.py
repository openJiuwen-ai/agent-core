# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from pathlib import Path
from typing import Dict, Any, AsyncIterator, Optional

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.foundation.tool.base import Tool
from openjiuwen.core.sys_operation import SysOperation
from openjiuwen.deepagents.prompts.sections.tools import build_tool_card
from openjiuwen.deepagents.tools.base_tool import ToolOutput


def _clip_text(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return f"{value[:max_chars]}\n...[truncated]"


class BashTool(Tool):

    def __init__(self, operation: SysOperation, language: str = "cn", workspace: Optional[str] = None):
        super().__init__(build_tool_card("bash", "BashTool", language))
        self.operation = operation
        self._workspace: Optional[Path] = Path(workspace).resolve() if workspace else None

    def _get_workspace(self) -> Optional[Path]:
        if self._workspace:
            return self._workspace
        wd_val = self.operation.work_dir
        if wd_val:
            return Path(wd_val).resolve()
        return None

    def _resolve_workdir(self, workdir: str) -> Optional[Path]:
        """Resolve workdir and enforce sandbox. Returns None if path escapes workspace."""
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

    _VALID_SHELL_TYPES = {"auto", "cmd", "powershell", "bash", "sh"}

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        command = (inputs.get("command") or "").strip()
        timeout = max(1, min(int(inputs.get("timeout", 30)), 300))
        workdir = inputs.get("workdir", "")
        background = bool(inputs.get("background", False))
        max_output_chars = max(200, min(int(inputs.get("max_output_chars", 8000)), 20000))
        shell_type = inputs.get("shell_type", "auto")
        if shell_type not in self._VALID_SHELL_TYPES:
            shell_type = "auto"

        if not command:
            return ToolOutput(success=False, error="command cannot be empty")

        resolved_cwd = self._resolve_workdir(workdir)
        if resolved_cwd is None:
            return ToolOutput(success=False, error="workdir is outside workspace sandbox")

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
