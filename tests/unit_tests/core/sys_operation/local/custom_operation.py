# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from abc import abstractmethod
from typing import List

from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.core.sys_operation.base import BaseOperation, OperationMode
from openjiuwen.core.sys_operation.registry import operation


class BaseCalculatorOperation(BaseOperation):
    """Base calculator operation for arithmetic calculations."""

    def list_tools(self) -> List[ToolCard]:
        method_names = ["add"]
        return self._generate_tool_cards(method_names)

    @abstractmethod
    async def add(self, a: int, b: int) -> int:
        """Add two numbers.

        Args:
            a: First number
            b: Second number

        Returns:
            Sum of the two numbers
        """
        pass


@operation(name="calculator", mode=OperationMode.LOCAL, description="Calculator operations")
class LocalCalculatorOperation(BaseCalculatorOperation):
    """Local implementation of calculator operations."""

    async def add(self, a: int, b: int) -> int:
        """Add two numbers.

        Args:
            a: First number
            b: Second number

        Returns:
            Sum of the two numbers
        """
        return a + b
