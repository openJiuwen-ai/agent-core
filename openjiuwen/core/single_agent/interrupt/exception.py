# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Tool interrupt exception definitions."""

from typing import Optional

from openjiuwen.core.session.interaction.base import AgentInterrupt
from openjiuwen.core.single_agent.interrupt.response import InterruptRequest
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall


class ToolInterruptException(AgentInterrupt):
    """Exception raised when a tool needs user confirmation.

    Attributes:
        request: The interrupt request containing confirmation message and schema
        tool_call: The ToolCall object that was intercepted (optional, set by Rail)
    """

    def __init__(
            self,
            request: InterruptRequest,
            tool_call: Optional["ToolCall"] = None,
    ):
        self.request = request
        self.tool_call = tool_call
        super().__init__(str(request.message))
