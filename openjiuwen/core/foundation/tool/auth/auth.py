# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from typing import Dict, Any, Optional
from dataclasses import dataclass


@dataclass
class ToolAuthConfig:
    """Tool authentication configuration"""
    auth_type: str  # Authentication type: ssl, header_and_query, etc.
    config: Dict[str, Any]  # Authentication configuration parameters
    tool_type: str  # Tool type: restful_api, mcp, etc.
    tool_id: Optional[str] = None  # Tool ID


@dataclass
class ToolAuthResult:
    """Tool authentication result"""
    success: bool  # Whether authentication was successful
    auth_data: Dict[str, Any]  # Authentication data (such as headers, ssl config, credentials, etc.)
    message: str = ""  # Authentication message
    error: Optional[Exception] = None  # Authentication error (if any)