# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from __future__ import annotations
from typing import Optional

from openjiuwen.core.foundation.tool import Tool
from openjiuwen.harness.prompts.tools import build_tool_card


class AskUserTool(Tool):
    def __init__(self, language: str = "cn", agent_id: Optional[str] = None):
        super().__init__(
            build_tool_card(
                name="ask_user",
                tool_id="ask_user",
                language=language,
                agent_id=agent_id,
            )
        )

    async def invoke(self, query, **kwargs):
        return {}

    async def stream(self, query, **kwargs):
        yield {}