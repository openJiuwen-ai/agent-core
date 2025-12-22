# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Dict, Any

from pydantic import BaseModel, Field


class McpServerConfig(BaseModel):
    server_name: str
    server_path: str
    client_type: str = 'sse'
    params: Dict[str, Any] = Field(default_factory=dict)
    auth_headers: dict = Field(default_factory=dict)
    auth_query_params: Dict[str, str] = Field(default_factory=dict)


NO_TIMEOUT = -1
