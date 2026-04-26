# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class ToolOutput(BaseModel):
    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None
    extra_metadata: Dict[str, Any] = Field(default_factory=dict)
