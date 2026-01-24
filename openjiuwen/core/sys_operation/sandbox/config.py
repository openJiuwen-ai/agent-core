# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from typing import Dict, Any

from pydantic import BaseModel, Field


class SandboxGatewayConfig(BaseModel):
    """Remote sandbox gateway connection configuration."""

    gateway_url: str = Field(default="", description="Remote sandbox gateway service endpoint")
    params: Dict[str, Any] = Field(default_factory=dict, description="Global request parameters")
    auth_headers: Dict[str, str] = Field(default_factory=dict, description="Authentication HTTP headers")
    auth_query_params: Dict[str, str] = Field(default_factory=dict, description="Authentication query parameters")
