# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Base abstractions shared by all team tools."""

from abc import ABC
from typing import Any, AsyncIterator

from openjiuwen.core.foundation.tool.base import Tool, ToolCard


class TeamTool(Tool, ABC):
    """Base class for team tools.

    Subclasses override ``render_for_llm`` to control the text the model reads
    for their result; the structured ``ToolOutput`` stays with program
    consumers (events, logs). Without an override the core default rendering
    of ``Tool.render_for_llm`` applies.
    """

    async def stream(self, inputs: dict[str, Any], **kwargs) -> AsyncIterator[Any]:
        raise NotImplementedError("TeamTool does not support streaming")
