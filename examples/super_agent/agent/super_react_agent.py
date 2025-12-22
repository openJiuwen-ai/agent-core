"""
Super ReAct Agent
Enhanced ReAct Agent with custom context management
Supports both main single_agent and sub-single_agent execution with the same class
"""

import inspect
from typing import Dict, Any, AsyncIterator, List, Optional

from examples.super_agent.agent.utils import (
    process_input,
)

from examples.super_agent.agent.prompt_templates import (
    get_task_instruction_prompt
)

from examples.super_agent.agent.context_manager import (
    ContextManager
)
from examples.super_agent.agent.o3_handler import O3Handler
from examples.super_agent.agent.super_config import SuperAgentConfig
from examples.super_agent.agent.tool_call_handler import (
    ToolCallHandler
)
from examples.super_agent.llm.openrouter_llm import (
    OpenRouterLLM,
    ContextLimitError
)
from openjiuwen.core.single_agent import BaseAgent
from openjiuwen.core.common.logging import logger
from openjiuwen.core.runner import Runner
from openjiuwen.core.session import Session
from openjiuwen.core.foundation.llm import AIMessage
from openjiuwen.core.foundation.tool import Tool
from openjiuwen.core.foundation.tool import LocalFunction, ToolCard
from openjiuwen.core.protocols.mcp import McpServerConfig
from openjiuwen.core.workflow import Workflow


def _make_mcp_call_coroutine(server_name: str, tool_name: str):
    """
    为某个 MCP 工具生成一个 coroutine 函数：
    - 入参是工具的参数（**kwargs）
    - 内部通过 Runner.run_tool 调用真正的 MCP 工具
    """
    async def _wrapper(**kwargs):
        tool_id = f"{server_name}.{tool_name}"  # 例如：browser-use-server.browser_navigate
        tool = Runner.resource_mgr.get_tool(tool_id)
        result = await tool.invoke(kwargs)

        # Test 里约定：如果返回 dict 且有 "result" 字段，就用它
        if isinstance(result, dict) and "result" in result:
            return result["result"]
        return result

    return _wrapper

class SuperReActAgent(BaseAgent):
    """
    Enhanced ReAct Agent with custom context management:
    - Custom context management (no ContextEngine dependency)
    - Task logging
    - O3 integration
    - Context limit handling
    - Sub-single_agent support
    - Main single_agent and sub-single_agent use the same class with different instances
    """

    def __init__(
        self,
        agent_config: SuperAgentConfig,
        workflows: List[Workflow] = None,
        tools: List[Tool] = None
    ):
        """
        Initialize Super ReAct Agent

        Args:
            agent_config: Super single_agent configuration
            workflows: List of workflows
            tools: List of tools
        """
        # Call parent init
        super().__init__(agent_config)

        # Store single_agent-specific config
        self._agent_config: SuperAgentConfig = agent_config

        # LLM instance (OpenRouter) - create eagerly for context manager
        model_config = agent_config.model
        self._llm = OpenRouterLLM(
            api_key=model_config.model_info.api_key,
            api_base=model_config.model_info.api_base,
            model_name=model_config.model_info.model_name,
            timeout=model_config.model_info.timeout
        )

        # Custom context manager (replaces ContextEngine)
        # Pass LLM for summary generation with retry logic
        self._context_manager = ContextManager(
            llm=self._llm,
            max_history_length=agent_config.constrain.reserved_max_chat_rounds * 2
        )

        # O3 handler (for hints and final answer extraction)
        self._o3_handler: Optional[O3Handler] = None
        if agent_config.enable_o3_hints or agent_config.enable_o3_final_answer:
            if agent_config.o3_api_key:
                self._o3_handler = O3Handler(
                    api_key=agent_config.o3_api_key,
                    enable_message_ids=True
                )

        # Add tools and workflows through BaseAgent interface
        if tools:
            self.add_tools(tools)
        if workflows:
            self.add_workflows(workflows)

        # Sub-single_agent instances (for main single_agent only)
        self._sub_agents: Dict[str, "SuperReActAgent"] = {}

        # Tool call handler
        self._tool_call_handler = ToolCallHandler(
            sub_agents=self._sub_agents
        )

    def _get_llm(self) -> OpenRouterLLM:
        """Get LLM instance (always available after __init__)"""
        return self._llm

    async def _call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """
        调用一个工具：
        - 从 self._tools 里找到 LocalFunction
        - 支持 func 是同步函数或 async 函数
        - 如果 func 返回的是 coroutine（awaitable），自动 await
        """
        tool = self._tools.get(tool_name)
        if tool is None:
            raise ValueError(f"Tool '{tool_name}' is not registered in SuperReActAgent")

        func = getattr(tool, "func", None)
        if func is None:
            raise RuntimeError(f"Tool '{tool_name}' has no 'func' defined")

        try:
            result = func(**(arguments or {}))
            if inspect.isawaitable(result):
                result = await result
            return result
        except Exception as e:
            raise RuntimeError(f"Error while executing tool '{tool_name}': {e}") from e

    async def _register_mcp_server_as_local_tools(
        self,
        server_name: str,
        client_type: str,
        params
    ):
        """
        注册一个 MCP server（SSE / stdio ），并把该 server 上所有 tools
        映射成 LocalFunction，返回 List[LocalFunction]，可以直接传给 SuperReActAgent.
        """
        # 注册 MCP server
        server_cfg = McpServerConfig(
            server_name=server_name,
            params=params,
            client_type=client_type,
        )
        ok_list = await Runner.resource_mgr.add_mcp_server([server_cfg])
        if not ok_list or not ok_list[0].is_ok():
            raise RuntimeError(f"Failed to add MCP server: {server_name}")

        # 用 Runner.list_tools 拿到工具列表（McpToolInfo）
        tool_infos = await Runner.resource_mgr.get_mcp_tool_infos(server_name=server_name)

        local_tools = []
        for info in tool_infos:
            schema = getattr(info, "schema", {}) or {}

            async_func = _make_mcp_call_coroutine(server_name, info.name)

            mcp_local_tool = LocalFunction(
                card=ToolCard(
                    name=info.name,
                    description=getattr(info, "description", "") or f"MCP tool {info.name} from {server_name}",
                    parameters=schema,
                ),
                func=async_func,
            )

            local_tools.append(mcp_local_tool)

        return local_tools

    async def create_mcp_tools(self, server_name: str, client_type: str, params) -> List[LocalFunction]:
        """Utility method to create MCP tools based on server type and params"""
        return await self._register_mcp_server_as_local_tools(
            server_name=server_name,
            client_type=client_type,
            params=params,
        )

    def register_sub_agent(self, agent_name: str, sub_agent: "SuperReActAgent"):
        """
        Register a sub-single_agent instance and add it as a tool

        Args:
            agent_name: Name of the sub-single_agent (should start with 'single_agent-' for automatic routing)
            sub_agent: SuperReActAgent instance to register
        """
        # Register sub-single_agent in the handler's registry
        self._sub_agents[agent_name] = sub_agent

        # Delegate tool creation to ToolCallHandler
        sub_agent_tool = self._tool_call_handler.create_sub_agent_tool(agent_name, sub_agent)

        # Add the tool to this single_agent's tools
        self.add_tools([sub_agent_tool])

        logger.info(f"Registered sub-single_agent '{agent_name}' as tool")

    def _format_tool_calls_for_message(self, tool_calls) -> List[Dict]:
        """Format tool calls for message history"""
        return ToolCallHandler.format_tool_calls_for_message(tool_calls)

    async def call_model(
        self,
        user_input: str,
        session: Session,
        is_first_call: bool = False,
        step_id: int = 0
    ) -> AIMessage:
        """
        Call LLM for reasoning

        Args:
            user_input: User input or tool result
            session: Session instance
            is_first_call: Whether this is the first call (adds user message)
            step_id: Step ID for logging

        Returns:
            AIMessage: LLM output
        """
        # If first call, add user message to context
        if is_first_call:
            self._context_manager.add_user_message(user_input)

        # Get chat history from context manager
        chat_history = self._context_manager.get_history()

        # Format messages with prompt template
        messages = []
        for prompt in self._agent_config.prompt_template:
            messages.append(prompt)

        # Add chat history
        messages.extend(chat_history)

        # Get tool definitions from session
        # tools = session.get_tool_info()
        
        # === 从 session 拿到所有工具 ===
        all_tools = session.get_tool_info()
        tools = all_tools

        # === 计算当前 single_agent 允许使用的工具名集合 ===
        allowed_tool_names: set[str] = set()

        try:
            agent_cfg = session.get_agent_config()
        except Exception as e:
            agent_cfg = None
            logger.warning(f"Failed to get single_agent config from session: {e}")

        if agent_cfg is not None:
            cfg_tools = getattr(agent_cfg, "tools", None)
            if cfg_tools:
                # cfg_tools 例如 ["tool-vqa", "tool-reading", "tool-code", ...]
                allowed_tool_names.update(cfg_tools)

        # 兜底：用自身 _agent_config.tools（BaseAgent.add_tools 已经维护）
        cfg_tools_self = getattr(self._agent_config, "tools", None)
        if cfg_tools_self:
            allowed_tool_names.update(cfg_tools_self)

        # === 根据 allowed_tool_names 从 all_tools 里筛 ===
        if allowed_tool_names:
            filtered_tools = []
            for t in all_tools:
                # ToolInfo.name 是真正暴露给 LLM 的 function 名
                tool_name = None
                fn = getattr(t, "function", None)
                if fn is not None:
                    tool_name = getattr(fn, "name", None)

                # 做一个兜底
                if not tool_name and hasattr(t, "name"):
                    tool_name = getattr(t, "name")

                if tool_name in allowed_tool_names:
                    filtered_tools.append(t)

            tools = filtered_tools
            logger.info(
                f"[SuperReActAgent] Filtered tools for single_agent {self._agent_config.id}: "
                f"{[getattr(t, 'name', None) for t in tools]}"
            )
        else:
            # 如果没有任何限制配置，就退回到“全量工具”行为，保证兼容性
            logger.warning(
                f"[SuperReActAgent] No tool whitelist found for single_agent {self._agent_config.id}, "
                f"exposing all {len(all_tools)} tools to LLM"
            )
            tools = all_tools

        # Call LLM
        llm = self._get_llm()
        llm_output = await llm.ainvoke(
            model_name=self._agent_config.model.model_info.model_name,
            messages=messages,
            tools=[tool.model_dump() for tool in tools]
        )

        # Save AI message to context
        tool_calls_formatted = self._format_tool_calls_for_message(llm_output.tool_calls)
        self._context_manager.add_assistant_message(
            llm_output.content or "",
            tool_calls=tool_calls_formatted
        )

        return llm_output

    async def _execute_tool_call(
        self,
        tool_call,
        session: Session
    ) -> Any:
        """
        Execute a single tool call

        Args:
            tool_call: Tool call object from LLM
            session: Session instance

        Returns:
            Tool execution result
        """
        return await self._tool_call_handler.execute_tool_call(tool_call, session)

    async def invoke(self, inputs: Dict, session: Session = None) -> Dict:
        """
        Synchronous invoke - complete ReAct loop

        Args:
            inputs: Input dict {"query": usr_question, "file_path": usr_file}
            session: Optional session (creates one if not provided)

        Returns:
            Result dict with 'output' and 'result_type'
        """
        # Prepare session
        session_created = False

        if session is None:
            session = await self._session.pre_run(session_id="default", inputs=inputs)
            session_created = True

        try:
            user_input = inputs.get("query", "")
            if not user_input:
                return {"output": "No query provided", "result_type": "error"}

            file_path  = inputs.get("file_path", None)
            # 1 11.27: Process inputs of GAIA
            user_input = process_input(task_description=user_input, task_file_name=file_path)

            # Extract O3 hints if enabled (main single_agent only)
            o3_notes = ""
            if self._agent_config.enable_o3_hints and self._agent_config.agent_type == "main":
                if self._o3_handler:
                    try:
                        o3_hints = await self._o3_handler.extract_hints(user_input)
                        if o3_hints:
                            o3_notes = f"\n\nBefore you begin, please review the following preliminary notes highlighting subtle or easily misunderstood points in the question, which might help you avoid common pitfalls during your analysis (for reference only; these may not be exhaustive):\n\n{o3_hints}"
                    except Exception as e:
                        logger.warning(f"O3 hints extraction failed: {e}")
                        o3_notes = ""

            # 2 11.27: add input prompt
            user_input = get_task_instruction_prompt(task_description=user_input, o3_notes=o3_notes, use_skill=True)
            logger.info(f"complete_user_inputs: {user_input}")

            # ReAct loop
            iteration = 0
            max_iteration = self._agent_config.constrain.max_iteration
            max_tool_calls_per_turn = self._agent_config.max_tool_calls_per_turn
            is_first_call = True
            task_failed = False

            # O3 metadata (populated if O3 final answer extraction succeeds)
            o3_metadata = None

            while iteration < max_iteration:
                iteration += 1
                logger.info(f"ReAct iteration {iteration} ({self._agent_config.agent_type})")

                try:
                    # Call model
                    llm_output = await self.call_model(
                        user_input,
                        session,
                        is_first_call=is_first_call,
                        step_id=iteration
                    )
                    is_first_call = False

                    # Check for tool calls
                    if not llm_output.tool_calls:
                        logger.info("No tool calls, task completed")
                        break

                    # Execute tool calls
                    num_calls = len(llm_output.tool_calls)
                    if num_calls > max_tool_calls_per_turn:
                        logger.warning(
                            f"Too many tool calls ({num_calls}), processing only first {max_tool_calls_per_turn}"
                        )

                    # Execute all tool calls and collect results (don't add to context yet)
                    for tool_call in llm_output.tool_calls[:max_tool_calls_per_turn]:
                        tool_name = tool_call.name
                        logger.info(f"Executing tool: {tool_name}")

                        try:
                            result = await self._execute_tool_call(tool_call, session)
                            logger.info(f"Tool {tool_name} completed")

                            # Add tool result to context immediately after execution
                            self._context_manager.add_tool_message(
                                tool_call.id,
                                str(result)
                            )
                        except Exception as tool_error:
                            logger.error(f"Tool {tool_name} failed: {tool_error}")
                            # Add error as tool result so conversation can continue
                            self._context_manager.add_tool_message(
                                tool_call.id,
                                f"Error executing tool: {str(tool_error)}"
                            )
                            raise  # Re-raise to trigger task_failed

                    # Check context limits (if enabled)
                    if self._agent_config.enable_context_limit_retry:
                        llm = self._get_llm()
                        # Simple prompt for context space estimation
                        temp_summary = f"Summarize the task: {inputs.get('query', '')}"

                        chat_history = self._context_manager.get_history()

                        if not llm.ensure_summary_context(chat_history, temp_summary):
                            logger.warning("Context limit reached, triggering summary")
                            task_failed = True
                            break

                except ContextLimitError:
                    logger.warning("Context limit exceeded during execution")
                    task_failed = True
                    break

                except Exception as e:
                    logger.error(f"Error during iteration {iteration}: {e}")
                    task_failed = True
                    break

            # Check if max iterations reached
            if iteration >= max_iteration:
                logger.warning(f"Max iterations ({max_iteration}) reached")
                task_failed = True

            # Generate summary using context manager
            summary = await self._context_manager.generate_summary(
                task_description=inputs.get("query", ""),
                task_failed=task_failed,
                system_prompts=self._agent_config.prompt_template,
                agent_type=self._agent_config.agent_type
            )

            # O3 final answer extraction (main single_agent only)
            if (self._agent_config.enable_o3_final_answer and
                self._agent_config.agent_type == "main" and
                self._o3_handler and
                not task_failed):
                try:
                    # Get answer type
                    answer_type = await self._o3_handler.get_answer_type(inputs.get("query", ""))
                    logger.info(f"O3 answer type detected: {answer_type}")

                    # Extract final answer with type-specific formatting
                    o3_extracted_answer, confidence = await self._o3_handler.extract_final_answer(
                        answer_type=answer_type,
                        task_description=inputs.get("query", ""),
                        summary=summary
                    )

                    # Extract boxed answer for logging
                    boxed_answer = self._o3_handler.extract_boxed_answer(o3_extracted_answer)

                    # Add O3 response to message history (like Miroflow does)
                    # This preserves the O3 analysis in the conversation context
                    self._context_manager.add_assistant_message(
                        f"O3 extracted final answer:\n{o3_extracted_answer}",
                        tool_calls=[]
                    )

                    # Concatenate original summary and O3 answer as final result
                    summary = (
                        f"{summary}\n\n"
                        f"------------------------------------------O3 Extracted Answer:------------------------------------------\n"
                        f"{o3_extracted_answer}"
                    )

                    logger.info(
                        f"O3 final answer extraction completed - "
                        f"Answer type: {answer_type}, "
                        f"Confidence: {confidence}/100, "
                        f"Boxed answer: {boxed_answer}"
                    )

                    # Store O3 metadata for return
                    o3_metadata = {
                        "answer_type": answer_type,
                        "confidence": confidence,
                        "boxed_answer": boxed_answer,
                        "full_response": o3_extracted_answer
                    }

                except Exception as e:
                    logger.warning(f"O3 final answer extraction failed after retries: {str(e)}")
                    # Continue using original summary

            # Build result dict
            result = {
                "output": summary,
                "result_type": "error" if task_failed else "answer"
            }

            # Add O3 metadata if available
            if o3_metadata:
                result["o3_metadata"] = o3_metadata

            return result

        finally:
            if session_created:
                await session.post_run()

    async def stream(self, inputs: Dict, session: Session = None) -> AsyncIterator[Any]:
        """Streaming invoke - delegates to invoke for now"""
        result = await self.invoke(inputs, session)
        yield result
