# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Tool domain optimizer base class: fixes domain=tool, default_targets=[tool_description].
ToolCallOperator exposes only tool descriptions (tool_description).
Subclasses implement _backward / _update.
"""

from typing import Dict, List, TYPE_CHECKING

from openjiuwen.agent_evolving.optimizer.base import BaseOptimizer

if TYPE_CHECKING:
    from openjiuwen.core.operator.base import Operator


class ToolOptimizerBase(BaseOptimizer):
    """
    Tool dimension optimizer base class: optimizes tunables exposed by ToolCallOperator
    (tool descriptions only).

    ToolCallOperator exposes single 'tool_description' tunable when tool_registry is set.
    set_parameter('tool_description', value) expects value as Dict[tool_name, description_str].
    """

    domain: str = "tool"

    def default_targets(self) -> List[str]:
        """Default: tool_description (tool-description self-evolution)."""
        return ["tool_description"]

    def filter_operators(self, operators: Dict[str, "Operator"], targets: List[str]) -> Dict[str, "Operator"]:
        """Filter Operators exposing tool tunables; logs warning for missing targets."""
        return super().filter_operators(operators, targets)
