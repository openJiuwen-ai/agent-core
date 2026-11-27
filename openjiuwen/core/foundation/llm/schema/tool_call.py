# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import json
from typing import Any, Optional

from pydantic import BaseModel, field_validator


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
    """
    id: Optional[str]
    type: str
    name: str
    arguments: str
    index: Optional[int] = None
    response_item_id: Optional[str] = None

    @field_validator("arguments", mode="before")
    @classmethod
    def _normalize_object_arguments(cls, value: Any) -> Any:
        """兼容 OpenAI 兼容网关返回对象/数组形式的 arguments。"""
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        return value
