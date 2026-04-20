# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import AsyncIterator, Dict, Any
from openjiuwen.core.workflow.components.component import ComponentExecutable
from openjiuwen.core.context_engine import ModelContext
from openjiuwen.core.graph.executable import Input, Output
from openjiuwen.core.session.node import Session
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from .react_config import ReActAgentCompConfig


class ReActAgentCompExecutable(ComponentExecutable):
    def __init__(self, config: ReActAgentCompConfig):
        super().__init__()
        self.config = config
        # Lazy import to avoid circular dependency:
        # react_agent.py → base.py → ability_manager.py → workflow/__init__.py → react_executable.py
        from openjiuwen.core.single_agent.agents.react_agent import ReActAgent
        # Create a ReActAgent instance with a workflow-specific card
        self._react_agent = ReActAgent(
            card=AgentCard(
                id="react_agent_workflow_executable",
                name="ReAct Agent Workflow Executable",
                description="ReAct agent for workflow execution"
            )
        )
        self._react_agent.configure(config)

    @property
    def ability_manager(self):
        """Get the ability manager for adding tools/workflows/agents.
        
        This provides a public interface to manage agent capabilities.
        
        Returns:
            AbilityManager: The ability manager instance
        """
        return self._react_agent.ability_manager

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        """Execute ReAct loop synchronously with batch input/output."""
        try:
            # Execute the ReAct agent with the provided inputs
            result = await self._react_agent.invoke(inputs, session)
            return result
        except Exception as e:
            # Handle errors appropriately
            return {
                "output": f"Error in ReAct execution: {str(e)}",
                "result_type": "error"
            }

    async def stream(self, inputs: Input, session: Session, context: ModelContext) -> AsyncIterator[Output]:
        """Execute ReAct loop with streaming output."""
        try:
            # 流式执行 ReAct Agent
            # 对于工作流会话，此操作将数据写入 session.write_stream()，供工作流消费
            from openjiuwen.core.session.agent import create_agent_session
            from openjiuwen.core.session.stream import OutputSchema

            # 创建agent会话实例
            agent_session = create_agent_session(
                session_id=session.get_session_id(),
                card=self._react_agent.card
            )

            # Optional: Get data from workflow Optional: Get data from workflow global state (example comment)
            # shared_state = session.get_global_state("key")
            # inputs = inputs.update(shared_state)
            # Or synchronize the entire global state to the agent session
            # agent_session.update_global_state(session.get_global_state("key"))

            # 异步流式执行代理，并逐块返回结果
            async for chunk in self._react_agent.stream(inputs, agent_session):
                # 格式化输出块（若需要）
                # 如果块是输出架构实例，则提取其有效载荷
                if isinstance(chunk, OutputSchema):
                    # if type is llm_output and content exists, extract content; otherwise, use payload directly
                    if chunk.type == 'llm_output' and "content" in chunk.payload:
                        chunk = {"output": chunk.payload["content"]}
                    else:
                        chunk = chunk.payload
                yield chunk
        except Exception as e:
            # Handle errors appropriately
            yield {
                "type": "error",
                "payload": {"output": f"Error in ReAct streaming: {str(e)}", "result_type": "error"}
            }
