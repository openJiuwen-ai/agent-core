# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Dict, Any, Type, Union, Optional
from pydantic import BaseModel, Field



class ToolInfo(BaseModel):
    type: str = Field(default="function")
    name: str = Field(default="")
    description: str = Field(default="")
    parameters: Union[Dict[str, Any], Type[BaseModel]] = Field(default_factory=dict)


class ToolTimeoutResult(BaseModel):
    """Structured result emitted by ``AbilityManager`` when a tool call
    exhausts its retry budget after repeated timeouts.

    Placed in ``core/foundation/tool/schema`` so both core and harness
    layers can reference it without circular imports.
    """
    success: bool = False
    data: Optional[Any] = None
    error: Optional[str] = None


class McpToolInfo(ToolInfo):
    server_name: str