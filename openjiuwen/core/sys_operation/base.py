# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from enum import Enum
from typing import Union

from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.core.sys_operation.config import LocalWorkConfig, SandboxGatewayConfig


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
