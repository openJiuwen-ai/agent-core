# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import List, Any
from openjiuwen.core.utils.llm.messages import BaseMessage, AIMessage, HumanMessage, ToolMessage
from openjiuwen.core.context_engine.engine import ContextEngine
from openjiuwen.core.runtime.runtime import Runtime
from openjiuwen.core.common.logging import logger
from openjiuwen.core.common.security.user_config import UserConfig
from openjiuwen.agent.config.base import AgentConfig


class MessageUtils:
    """Message utilities for adding and retrieving messages"""

    @staticmethod
    def should_add_user_message(query: str, context_engine: ContextEngine, runtime: Runtime) -> bool:
        """Check if user message should be added
        
        Args:
            query: User input
            context_engine: Context engine
            runtime: Runtime instance
        
        Returns:
            bool: Whether to add user message
        """
        agent_context = context_engine.get_agent_context(runtime.session_id())
        last_message = agent_context.get_latest_message()

        if not last_message:
            return True

        if last_message.role == 'tool':
            logger.info("post-tool-call request")
            return True

        if last_message.role == 'user' and last_message.content == query:
            logger.info("Skipping duplicate user message")
            return False

        return True

    @staticmethod
    def add_user_message(query: Any, context_engine: ContextEngine, runtime: Runtime):
        """Add user message to chat history
        
        Args:
            query: User input
            context_engine: Context engine
            runtime: Runtime instance
        """
        if MessageUtils.should_add_user_message(query, context_engine, runtime):
            agent_context = context_engine.get_agent_context(runtime.session_id())
            user_message = HumanMessage(content=query)
            agent_context.add_message(user_message)
            if UserConfig.is_sensitive():
                logger.info(f"Added user message")
            else:
                logger.info(f"Added user message: {query}")

    @staticmethod
    def add_ai_message(ai_message: AIMessage, context_engine: ContextEngine, runtime: Runtime):
        """Add AI message to chat history
        
        Args:
            ai_message: AI message object
            context_engine: Context engine
            runtime: Runtime instance
        """
        if ai_message:
            agent_context = context_engine.get_agent_context(runtime.session_id())
            agent_context.add_message(ai_message)

    @staticmethod
    def add_tool_message(tool_message: ToolMessage, context_engine: ContextEngine, runtime: Runtime):
        """Add tool message to chat history
        
        Args:
            tool_message: Tool message object
            context_engine: Context engine
            runtime: Runtime instance
        """
        if tool_message:
            agent_context = context_engine.get_agent_context(runtime.session_id())
            agent_context.add_message(tool_message)

    @staticmethod
    def add_workflow_message(message: BaseMessage, workflow_id: str,
                            context_engine: ContextEngine, runtime: Runtime):
        """Add message to workflow chat history
        
        Args:
            message: Message object
            workflow_id: Workflow ID
            context_engine: Context engine
            runtime: Runtime instance
        """
        workflow_context = context_engine.get_workflow_context(
            workflow_id=workflow_id,
            session_id=runtime.session_id()
        )
        workflow_context.add_message(message)

    @staticmethod
    def get_chat_history(context_engine: ContextEngine, runtime: Runtime, config: AgentConfig) -> List[BaseMessage]:
        """Get chat history by max conversation rounds(Split each round of conversation by HumanMessage)
        
        Args:
            context_engine: Context engine
            runtime: Runtime instance
            config: Agent config
        
        Returns:
            List[BaseMessage]: Chat history message list
        """
        agent_context = context_engine.get_agent_context(runtime.session_id())
        chat_history = agent_context.get_messages()
        max_rounds = config.constrain.reserved_max_chat_rounds
        if not chat_history or max_rounds <= 0:
            return [] if max_rounds <= 0 else chat_history

        rounds_count = 0
        start_idx = 0
        for i in range(len(chat_history) - 1, -1, -1):
            if chat_history[i].role == 'user':
                rounds_count += 1
                start_idx = i
                if rounds_count == max_rounds:
                    logger.info(
                        f"Reaching max conversation rounds {max_rounds}, only return the last max_rounds rounds")
                    break

        # make sure all chat history begin with HumanMessage
        result = chat_history[start_idx:]
        if not result:
            return []

        # search backward for the first HumanMessage
        if result[0].role != 'user':
            first_human_idx = next(
                (i for i, msg in enumerate(result) if msg.role == 'user'), -1
            )
            if first_human_idx == -1:
                logger.warning("No HumanMessage found in chat history, returning empty list")
                return []
            result = result[first_human_idx:]
            logger.info(f"Trimmed {first_human_idx} non-user messages from the start of chat history")
        return result

