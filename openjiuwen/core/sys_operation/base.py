# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from abc import ABC
from enum import Enum
from typing import Union, TypeVar, Generic, Optional

from pydantic import BaseModel, Field

from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.core.sys_operation.local.config import LocalWorkConfig
from openjiuwen.core.sys_operation.sandbox.config import SandboxGatewayConfig

T = TypeVar('T')


class OperationMode(str, Enum):
    """Enum for operation mode."""
    LOCAL = "local"
    SANDBOX = "sandbox"


class BaseOperation:
    """BaseOperation for file, code, shell and so on."""

    def __init__(
            self,
            name: str,
            mode: OperationMode,
            description: str,
            run_config: Union[LocalWorkConfig, SandboxGatewayConfig]):
        self.name = name
        self.mode = mode
        self.description = description
        self._run_config = run_config

    def list_tools(self) -> list[ToolCard]:
        pass


class BaseResult(BaseModel, Generic[T], ABC):
    """BaseResult"""
    code: int = Field(..., description="Status code: 0 = success, non-0 = failure")
    message: str = Field(..., description="Message details")
    data: Optional[T] = Field(None, description="Business data (returned only on success)")

    class Config:
        arbitrary_types_allowed = True
