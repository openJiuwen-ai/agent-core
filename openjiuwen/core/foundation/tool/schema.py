# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
from typing import Dict, Any, Type, Union, Optional
from pydantic import BaseModel, Field



class ToolInfo(BaseModel):
    type: str = Field(default="function")
    name: str = Field(default="")
    description: str = Field(default="")
    parameters: Union[Dict[str, Any], Type[BaseModel]] = Field(default_factory=dict)


class ToolOutput(BaseModel):
    """Standard structured result returned by tool ``invoke``.

    Program consumers (rails, events, logs) read the structured fields. The
    text the model sees is produced by ``Tool.render_for_llm``, which by
    default takes ``data["content"]`` on success and ``error`` on failure.

    Placed in ``core/foundation/tool/schema`` so core tool execution can render
    it by type while harness tools keep importing it from their own package.
    """

    success: bool
    data: Any | None = None
    error: str | None = None
    extracted_content: str | None = None
    include_extracted_content_only_once: bool = False
    long_term_memory: str | None = None


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