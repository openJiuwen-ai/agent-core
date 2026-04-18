# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from __future__ import annotations

from typing import Any, Dict, List, Optional

from openjiuwen.core.operator import (
    Operator,
    LLMCallOperator,
    ToolCallOperator,
)
from openjiuwen.core.single_agent.agents.react_agent import (
    ReActAgent,
    ReActAgentConfig,
)
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


class ReActAgentEvolve(ReActAgent):
    """ReActAgent variant for self-evolution training.

    Extends ReActAgent with operator parameter synchronization.
    Operators' parameter updates are propagated back to agent's config
    via callbacks. Execution logic is fully inherited from ReActAgent.

    Usage:
        1. Create ReActAgentEvolve with desired configuration
        2. Run trainer.train(agent=evolve_agent, ...)
        3. Operators contain optimized parameters, can be accessed via get_operators()
    """

    def __init__(
        self,
        card: AgentCard,
    ):
        """Initialize ReActAgentEvolve

        Args:
            card: Agent card (required)
        """
        super().__init__(card)
        self._init_operators()

    def _init_operators(self) -> None:
        """Initialize operators with parameter update callbacks.

        Operators are parameter proxies - they don't contain execution logic.
        Agent execution is inherited from ReActAgent.
        Parameter changes are synced back to agent config via callbacks.
        """
        # LLMCallOperator: manages prompt templates
        system_prompt = getattr(self._config, "prompt_template", []) or []
        self._llm_op = LLMCallOperator(
            system_prompt=system_prompt,
            user_prompt="{{query}}",
            freeze_system_prompt=False,
            freeze_user_prompt=True,
            operator_id="react_llm",
            on_parameter_updated=self._on_llm_parameter_updated,
        )

        # ToolCallOperator: manages tool descriptions
        tool_descriptions = self._extract_tool_descriptions()
        self._tool_op = ToolCallOperator(
            operator_id="react_tool",
            descriptions=tool_descriptions,
            on_parameter_updated=self._on_tool_parameter_updated,
        )

    def get_operators(self) -> Dict[str, Operator]:
        """Return operators for self-evolution framework.

        This is the primary interface for the self-evolution framework.
        The trainer gets Operators via this method and updates parameters
        via set_parameter() or load_state().

        Returns:
            Dict mapping operator_id to Operator instance.
        """
        ops: Dict[str, Operator] = {}

        if self._tool_op is not None:
            ops[self._tool_op.operator_id] = self._tool_op

        if self._llm_op is not None:
            ops[self._llm_op.operator_id] = self._llm_op

        return ops

    def _on_llm_parameter_updated(self, target: str, value: Any) -> None:
        """Callback: sync LLM parameter changes to agent config.

        Called by LLMCallOperator when set_parameter() or load_state()
        updates a parameter. Updates _config.prompt_template with new
        system prompt.

        Args:
            target: Name of the updated parameter
            value: New parameter value (list of messages or single string)
        """
        if target != "system_prompt":
            return

        # Handle both list and string types for system_prompt
        if isinstance(value, list):
            content = value
        elif isinstance(value, str):
            content = [{"role": "system", "content": value}]
        else:
            return

        self._config.prompt_template = content

    def _extract_tool_descriptions(self) -> Dict[str, str]:
        """Extract current tool descriptions from ability_manager.

        Returns:
            Dict mapping tool_name to description
        """
        descriptions: Dict[str, str] = {}
        for ability in self.ability_manager.list():
            tool_info = ability.tool_info()
            if tool_info.name:
                descriptions[tool_info.name] = tool_info.description
        return descriptions

    def _on_tool_parameter_updated(self, target: str, value: Any) -> None:
        """Callback: sync tool description changes to ability_manager.

        Called by ToolCallOperator when set_parameter() or load_state()
        updates a parameter. Updates ability_manager's ToolCard descriptions.

        Args:
            target: Name of the updated parameter
            value: New parameter value (Dict[tool_name, description])
        """
        if target != "tool_description" or not isinstance(value, dict):
            return

        # Update ToolCard descriptions in ability_manager
        for tool_name, description in value.items():
            tool_card = self.ability_manager.get(tool_name)
            if tool_card is not None and hasattr(tool_card, "description"):
                tool_card.description = description

__all__ = [
    "ReActAgentEvolve",
]
