# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Any, Optional

from pydantic import BaseModel


class ToolCall(BaseModel):
    """
    Tool Call
    Attributes:
        id: Tool call ID
        type: Tool call type
        name: Tool name
        arguments: Tool arguments
        index: Tool call index, used to distinguish multiple tool calls
        response_item_id: Optional provider response item ID for protocols
            that distinguish response item IDs from tool call IDs.
        extra_content: Optional opaque provider metadata that must be preserved
            when a tool call is sent back in conversation history.
    """
    id: Optional[str]
    type: str
    name: str
    arguments: str
    index: Optional[int] = None
    response_item_id: Optional[str] = None
    extra_content: Optional[dict[str, Any]] = None
