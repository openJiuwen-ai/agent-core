# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""TaskTool implementation for subagent delegation."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, AsyncIterator, List, Optional


if TYPE_CHECKING:
    from openjiuwen.deepagents.deep_agent import DeepAgent

from openjiuwen.deepagents.schema.config import SubAgentConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.tool import Input, Output, Tool, ToolCard
from openjiuwen.core.session.agent import Session
from openjiuwen.deepagents.tools.base_tool import ToolOutput
from openjiuwen.deepagents.prompts.sections.tools.task_tool import (
    GENERAL_PURPOSE_AGENT_DESC,
)
from openjiuwen.deepagents.prompts.sections.tools import build_tool_card


class TaskTool(Tool):
    """Tool for delegating tasks to ephemeral subagents with isolated context.

    This tool creates a new subagent instance, assigns it an independent
    session to prevent context pollution, and returns the subagent's
    final output after task completion.
    """

    def __init__(
        self,
        card: ToolCard,
        parent_agent: "DeepAgent",
        language: str = "cn",
    ):
        """Initialize TaskTool.

        Args:
            card: Tool metadata card.
            parent_agent: Parent DeepAgent instance used to clone config
                and create subagents.
            language: Language for prompts ('cn' or 'en').
        """
        super().__init__(card)
        self.parent_agent = parent_agent
        self.language = language

    async def invoke(self, inputs: Input, **kwargs) -> ToolOutput:
        """Execute task by delegating to a subagent.

        Args:
            inputs: input_params containing subagent_type and task description.
            **kwargs: Additional parameters, including 'session' for parent session context.

        Returns:
            subagent's final result.

        Raises:
            ToolError: If subagent creation or execution fails.
        """
        parent_session = kwargs.get("session", None)
        if not isinstance(parent_session, Session):
            raise build_error(
                StatusCode.TOOL_TASK_TOOL_INVOKED,
                reason="TaskTool requires a valid session in kwargs",
            )

        # Parse inputs
        if isinstance(inputs, dict):
            subagent_type = inputs.get("subagent_type")
            task_description = inputs.get("task_description")
        else:
            raise build_error(
                StatusCode.TOOL_TASK_TOOL_INVOKED,
                reason=f"Invalid inputs type: {type(inputs)}",
            )

        if not subagent_type or not task_description:
            raise build_error(
                StatusCode.TOOL_TASK_TOOL_INVOKED,
                reason="Both 'subagent_type' and 'task' are required",
            )

        logger.info(f"[TaskTool] Creating subagent: {subagent_type}, parent_session={parent_session.get_session_id()}")

        # Create subagent instance
        subagent = self.parent_agent.create_subagent(subagent_type)

        # Create isolated session_id for subagent
        parent_session_id = parent_session.get_session_id()
        sub_session_id = f"{parent_session_id}_sub_{subagent_type}_{uuid.uuid4().hex[:8]}"

        logger.info(f"[TaskTool] Invoking subagent with isolated session: {sub_session_id}, query: {task_description}")

        try:
            # Invoke subagent with isolated session_id
            result = await subagent.invoke({"query": task_description, "conversation_id": sub_session_id})

            output = result.get("output", "")

            return ToolOutput(success=True, data={"output": output}, error=None)

        except Exception as e:
            logger.error(f"[TaskTool] Subagent: {subagent_type} execution failed, error={e}")
            raise build_error(
                StatusCode.TOOL_TASK_TOOL_INVOKED,
                reason=f"Subagent {subagent_type} execution failed: {e}",
            ) from e

    async def stream(self, inputs: Input, **kwargs) -> AsyncIterator[Output]:
        pass


def create_task_tool(
    parent_agent: "DeepAgent",
    available_agents: str,
    language: str = "cn",
) -> List[Tool]:
    """Create TaskTool instance for the given parent agent.

    Args:
        parent_agent: Parent DeepAgent instance.
        available_agents: Formatted string describing available subagent types.
        language: Language for tool parameters ('cn' or 'en').

    Returns:
        List containing a single TaskTool instance.
    """
    # 使用统一的 build_tool_card，传递格式化参数
    card = build_tool_card(
        name="task_tool",
        tool_id="task_tool",
        language=language,
        format_args={"available_agents": available_agents},
    )

    return [TaskTool(card=card, parent_agent=parent_agent, language=language)]


__all__ = [
    "TaskTool",
    "create_task_tool",
]
