# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
ReActAgent - Minimal ReAct Agent (no interruption, no Controller)
"""

import json
import asyncio
from typing import Dict, Any, AsyncIterator, List

from pydantic import ValidationError

from openjiuwen.core.agent.agent import BaseAgent
from openjiuwen.agent.config.react_config import ReActAgentConfig
from openjiuwen.agent.common.schema import WorkflowSchema, PluginSchema
from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.runtime.runtime import Runtime, Workflow
from openjiuwen.core.stream.base import OutputSchema
from openjiuwen.core.utils.tool.base import Tool
from openjiuwen.core.component.common.configs.model_config import ModelConfig
from openjiuwen.core.utils.llm.model_utils.model_factory import ModelFactory
from openjiuwen.core.common.logging import logger
from openjiuwen.core.utils.llm.messages import AIMessage, ToolMessage
from openjiuwen.core.utils.prompt.template.template import Template
from openjiuwen.agent.utils import MessageUtils


class ReActAgent(BaseAgent):
    """ReAct Agent - Minimal implementation (no interruption, no Controller)
    """

    def __init__(
            self,
            agent_config: ReActAgentConfig
    ):
        """Initialize ReActAgent
        
        Args:
            agent_config: ReAct config
            workflows: Workflow list
            tools: Tool list
        """
        # Call parent init (BaseAgent creates runtime, context_engine, etc.)
        super().__init__(agent_config)

        # LLM instance (lazy creation)
        self._llm = None

    def _get_llm(self):
        """Get LLM instance"""
        if self._llm is None:
            self._llm = ModelFactory().get_model(
                model_provider=self.agent_config.model.model_provider,
                **self.agent_config.model.model_info.model_dump(exclude=['model_name', 'streaming'])
            )
        return self._llm

    async def call_model(self, user_input: str, runtime: Runtime, is_first_call: bool = False):
        """Call LLM for reasoning
        
        Args:
            user_input: User input or tool result
            runtime: Runtime instance
            is_first_call: Whether first call (first call needs to add user message)
        
        Returns:
            llm_output: LLM output (contains content and tool_calls)
        """
        # 1. If first call, add user message
        if is_first_call:
            MessageUtils.add_user_message(user_input, self.context_engine, runtime)

        # 2. Get chat history
        chat_history = MessageUtils.get_chat_history(
            self.context_engine, runtime, self.agent_config
        )

        # 3. Format prompt
        messages = []
        # Add system prompt
        try:
            system_prompt = Template(content=self.agent_config.prompt_template).to_messages()
            for prompt in system_prompt:
                prompt_dict = prompt.model_dump(exclude_none=True)
                messages.append(prompt_dict)
        except ValidationError as e:
            raise JiuWenBaseException(
                error_code=StatusCode.PROMPT_PARAMS_CHECK_ERROR.code,
                message=StatusCode.PROMPT_PARAMS_CHECK_ERROR.errmsg.format(msg=str(e))
            ) from e

        # Add chat history (need complete BaseMessage conversion)
        for msg in chat_history:
            # Use model_dump to export message completely, exclude None values
            msg_dict = msg.model_dump(exclude_none=True)
            messages.append(msg_dict)

        # 4. Get available tool info
        tools = runtime.get_tool_info()

        # 5. Call LLM
        llm = self._get_llm()
        llm_output = await llm.ainvoke(
            self.agent_config.model.model_info.model_name,
            messages,
            tools
        )

        # 6. Save AI response to chat history
        ai_message = AIMessage(
            content=llm_output.content,
            tool_calls=llm_output.tool_calls
        )
        MessageUtils.add_ai_message(ai_message, self.context_engine, runtime)

        return llm_output

    async def _execute_tool_call(self, tool_call, runtime: Runtime) -> Any:
        """Execute single tool call
        
        Args:
            tool_call: Tool call object returned by LLM
            runtime: Runtime instance
        
        Returns:
            Tool execution result
        """
        # Parse tool name and parameters
        tool_name = tool_call.name
        try:
            tool_args = json.loads(tool_call.arguments) if isinstance(tool_call.arguments, str) else tool_call.arguments
        except (json.JSONDecodeError, AttributeError):
            tool_args = {}

        # Get and execute tool
        tool = runtime.get_tool(tool_name)
        if not tool:
            raise ValueError(f"Tool not found: {tool_name}")

        result = await tool.ainvoke(tool_args)

        # Add tool result to chat history
        tool_message = ToolMessage(
            content=str(result),
            tool_call_id=tool_call.id
        )
        MessageUtils.add_tool_message(tool_message, self.context_engine, runtime)

        return result

    async def invoke(self, inputs: Dict, runtime: Runtime = None) -> Dict:
        """Sync call - Complete ReAct loop
        
        Args:
            inputs: Input data, must contain 'query' field
            runtime: Optional Runtime (if not provided, use BaseAgent's _runtime)
        
        Returns:
            Execution result
        """
        # 1. Prepare Runtime
        session_id = inputs.get("conversation_id", "default_session")
        runtime_created = False
        if runtime is None:
            # Use BaseAgent's _runtime, need to create task runtime
            runtime = await self._runtime.pre_run(session_id=session_id, inputs=inputs)
            runtime_created = True

        try:
            user_input = inputs.get("query", "")
            if not user_input:
                return {"output": "No query provided", "result_type": "error"}

            # 2. ReAct loop
            iteration = 0
            max_iteration = self.agent_config.constrain.max_iteration
            is_first_call = True

            while iteration < max_iteration:
                iteration += 1
                logger.info(f"ReAct iteration {iteration}")

                # 2.1 Call model for reasoning
                llm_output = await self.call_model(
                    user_input,
                    runtime,
                    is_first_call=is_first_call
                )
                is_first_call = False  # Set to False after first call

                # 2.2 If no tool calls, LLM thinks problem is solved
                if not llm_output.tool_calls:
                    logger.info("No tool calls, task completed")
                    return {
                        "output": llm_output.content,
                        "result_type": "answer"
                    }

                # 2.3 Execute tool calls (tool results already added to history in _execute_tool_call)
                for tool_call in llm_output.tool_calls:
                    tool_name = tool_call.name
                    logger.info(f"Executing tool: {tool_name}")
                    result = await self._execute_tool_call(tool_call, runtime)
                    logger.info(f"Tool {tool_name} completed with result: {result}")

            # 3. Exceeded max iteration count
            logger.warning(f"Exceeded max iteration {max_iteration}")
            return {
                "output": "Exceeded max iteration",
                "result_type": "error"
            }
        finally:
            # 4. Cleanup runtime (if we created it)
            if runtime_created:
                await runtime.post_run()

    async def stream(self, inputs: Dict, runtime: Runtime = None) -> AsyncIterator[Any]:
        """Stream call - minimal version
        
        Note:
            When external runtime is provided, data is written to it but not read
            from stream_iterator (to avoid nested read deadlock). External caller
            reads stream data from runtime.
        """
        # Prepare runtime
        session_id = inputs.get("conversation_id", "default_session")
        if runtime is None:
            # Use BaseAgent's _runtime, need to create task runtime
            agent_runtime = await self._runtime.pre_run(
                session_id=session_id, inputs=inputs
            )
            need_cleanup = True
            own_stream = True  # Owns stream lifecycle
        else:
            agent_runtime = runtime
            need_cleanup = False
            own_stream = False  # External owns stream lifecycle
            
            # Sync agent's tools to external runtime
            # When external runtime is provided, agent's tools need to be registered
            if hasattr(self, '_tools') and self._tools:
                tools_to_add = [(tool.name, tool) for tool in self._tools]
                agent_runtime.add_tools(tools_to_add)

        # Store final result for send_to_agent
        final_result_holder = {"result": None}

        async def stream_process():
            try:
                final_result = await self.invoke(inputs, agent_runtime)
                final_result_holder["result"] = final_result
                await agent_runtime.write_stream(OutputSchema(
                    type="answer",
                    index=0,
                    payload={"output": final_result, "result_type": "answer"}
                ))
            except Exception as e:
                logger.error(f"ReActAgent stream error: {e}")
            finally:
                # Cleanup runtime (if we created it)
                if need_cleanup:
                    await agent_runtime.post_run()

        task = asyncio.create_task(stream_process())

        if own_stream:
            # Read from stream_iterator only when owning stream
            # External caller reads if external runtime provided
            async for result in agent_runtime.stream_iterator():
                yield result

        await task

        # When own_stream=False, yield final result to send_to_agent
        # so send_to_agent can get agent's actual return value
        if not own_stream and final_result_holder["result"] is not None:
            yield final_result_holder["result"]


# ===== Factory Functions =====
def create_react_agent_config(
        agent_id: str,
        agent_version: str,
        description: str,
        model: ModelConfig,
        prompt_template: List[Dict]
) -> ReActAgentConfig:
    """Create ReAct Agent config
    
    Args:
        agent_id: Agent ID
        agent_version: Agent version
        description: Agent description
        model: Model config
        prompt_template: Prompt template
    
    Returns:
        ReActAgentConfig instance
    """
    return ReActAgentConfig(
        id=agent_id,
        version=agent_version,
        description=description,
        model=model,
        prompt_template=prompt_template
    )
