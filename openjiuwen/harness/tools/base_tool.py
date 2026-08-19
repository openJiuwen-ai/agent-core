# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from typing import Any, Optional

from pydantic import BaseModel


class ToolOutput(BaseModel):
    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None
    extracted_content: Optional[str] = None
    include_extracted_content_only_once: bool = False
    long_term_memory: Optional[str] = None
