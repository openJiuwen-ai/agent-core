# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from abc import ABC, abstractproperty
from enum import Enum
from typing import Union, List

from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.core.foundation.tool.utils.callable_schema_extractor import CallableSchemaExtractor
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

    def list_tools(self) -> List[ToolCard]:
        pass

    def _generate_tool_cards(self, method_names: List[str]) -> List[ToolCard]:
        """Generate tool cards based on method names.
        
        Args:
            method_names: List of method names to be exposed as tools.
        
        Returns:
            List of ToolCard objects.
        """
        tool_cards = []
        for method_name in method_names:
            if hasattr(self, method_name):
                method = getattr(self, method_name)
                tool_cards.append(
                    ToolCard(
                        name=method_name,
                        description=CallableSchemaExtractor.extract_function_description(method),
                        input_params=CallableSchemaExtractor.generate_schema(method)
                    )
                )
        return tool_cards
