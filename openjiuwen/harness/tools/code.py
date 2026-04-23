# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import os
from typing import Dict, Any, AsyncIterator, Optional

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.foundation.tool.base import Tool
from openjiuwen.core.sys_operation import SysOperation
from openjiuwen.harness.prompts.tools import build_tool_card
from openjiuwen.harness.tools.base_tool import ToolOutput


class CodeTool(Tool):

    def __init__(self, operation: SysOperation, language: str = "cn", agent_id: Optional[str] = None):
        super().__init__(build_tool_card("code", "CodeTool", language, agent_id=agent_id))
        self.operation = operation

    @staticmethod
    def _resolve_timeout(raw_value: Any, default: int = 300) -> int:
        """Parse and validate a timeout value."""
        try:
            timeout = int(raw_value)
        except (TypeError, ValueError):
            timeout = default
        try:
            max_timeout = int(os.getenv("CODE_TOOL_MAX_TIMEOUT_SECONDS") or "3600")
        except ValueError:
            max_timeout = 3600
        max_timeout = max(1, max_timeout)
        return max(1, min(timeout, max_timeout))

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        code = inputs.get("code")
        language = inputs.get("language", "python")
        timeout = self._resolve_timeout(inputs.get("timeout", 300))

        res = await self.operation.code().execute_code(code, language=language, timeout=timeout)
        if res.code != StatusCode.SUCCESS.code:
            return ToolOutput(success=False, error=res.message)

        return ToolOutput(
            success=(res.data.exit_code == 0) if res.data else False,
            data={
                "stdout": res.data.stdout if res.data else "",
                "stderr": res.data.stderr if res.data else "",
                "exit_code": res.data.exit_code if res.data else -1
            },
            error=res.data.stderr if res.data and res.data.exit_code != 0 else None
        )

    async def stream(self, inputs: Dict[str, Any], **kwargs) -> AsyncIterator[Any]:
        pass
