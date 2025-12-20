#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved

import json
import re
import secrets
from typing import List, Union

from openjiuwen.agent.config.base import AgentConfig
from openjiuwen.core.agent.controller.config.reasoner_config import IntentDetectionConfig
from openjiuwen.core.agent.controller.constants import IntentDetectionConstants
from openjiuwen.core.agent.controller.utils import ReasonerUtils
from openjiuwen.core.agent.message.message import Message
from openjiuwen.core.agent.task.task import Task, TaskType, TaskInput
from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.common.logging import logger
from openjiuwen.core.common.security.exception_utils import ExceptionUtils
from openjiuwen.core.context_engine.engine import ContextEngine
from openjiuwen.core.runtime.runtime import Runtime
from openjiuwen.core.common.security.user_config import UserConfig
from openjiuwen.core.utils.llm.messages import BaseMessage


class IntentDetection:
    """IntentDetection - Intent detection module for message intent recognition and task generation"""

    def __init__(self, intent_config: IntentDetectionConfig, agent_config: AgentConfig,
                context_engine: ContextEngine, runtime: Runtime):
        """
        Initialize IntentDetection
        
        Args:
            intent_config: IntentDetection config
            agent_config: Agent config
            context_engine: Context engine
            runtime: Runtime environment
        """
        self.intent_config = intent_config
        self.agent_config = agent_config
        self.context_engine = context_engine
        self.runtime = runtime

    async def process_message(self, message: Message) -> List[Task]:
        """
        Process message, detect intent and generate tasks
        
        Args:
            message: Input message
            
        Returns:
            List[Task]: Generated task list
        """
        # 1. Detect intent
        llm_inputs = self._prepare_detection_input(message)
        session_id = self.runtime.session_id()
        if UserConfig.is_sensitive():
            logger.info(f"[%s] <LLM Input>", session_id)
        else:
            logger.info(f"[%s] <LLM Input>: %s", session_id, llm_inputs)
        
        # 2. Call LLM for intent detection
        llm_output = await self._invoke_llm_get_output(llm_inputs)
        if UserConfig.is_sensitive():
            logger.info(f"[%s] <LLM Output>", session_id)
        else:
            logger.info(f"[%s] <LLM Output>: %s", session_id, llm_output)
        detected_intent_id = self._parse_intent_from_output(llm_output)
        
        # 3. Create tasks from intent
        tasks = self._generate_tasks_from_intent(detected_intent_id, message)
        return tasks

    def _generate_tasks_from_intent(
        self, intent_id: str, message: Message
    ) -> List[Task]:
        """
        Create task objects
        
        Create tasks from detected intent:
        1. Map intent to task type
        2. Create task instance
        3. Return task list
        
        Note: If agent_config has no workflows, use intent_id as target_name
        """
        tasks = []
        session_id = self.runtime.session_id()
        task_unique_id = f"{session_id}_intent_{intent_id}_{secrets.token_hex(4)}"
        
        if intent_id == IntentDetectionConstants.DEFAULT_CLASS:
            # No match, return empty task list
            return tasks
        
        # If no workflows, use intent_id as target
        workflows = getattr(self.agent_config, 'workflows', None) or []
        if not workflows:
            task_input = TaskInput(target_id=intent_id, target_name=intent_id, arguments=message.content)
            task = Task(
                agent_id=self.agent_config.id,
                task_id=task_unique_id,
                task_type=TaskType.WORKFLOW,
                input=task_input
            )
            tasks.append(task)
            logger.info(
                f"[%s] success to create task for intent (direct): %s",
                session_id, intent_id
            )
            return tasks
        
        # Match workflow when workflows exist
        for workflow in workflows:
            if workflow.id == intent_id:
                task_input = TaskInput(target_id=workflow.id, target_name=workflow.name, arguments=message.content)
                task = Task(
                    agent_id=self.agent_config.id,
                    task_id=task_unique_id,
                    task_type=TaskType.WORKFLOW,
                    input=task_input
                )
                tasks.append(task)
                logger.info(
                    f"[%s] success to create task for intent: %s",
                    session_id, intent_id
                )
                break
        return tasks

    def _parse_intent_from_output(self, llm_output: str) -> str:
        """
        Parse intent from LLM output
        
        Extract intent from LLM output:
        1. Parse intent label from output
        2. Return workflow id or category name
        
        Note: If agent_config has no workflows, return category name directly
        """
        detected_intent_id = ""
        session_id = self.runtime.session_id()
        try:
            cleaned = re.sub(r'^\s*```json\s*|\s*```\s*$', '', llm_output.strip(), flags=re.IGNORECASE)
            cleaned = re.sub(r"^\s*'''json\s*|\s*'''\s*$", '', cleaned, flags=re.IGNORECASE)
            output_data = json.loads(cleaned, strict=False)
            detected_class_number = int(output_data.get('result', ''))
            if (detected_class_number <= 0 or
                    detected_class_number > len(self.intent_config.category_list)):
                # Unknown intent
                logger.warning("get unknown class")
            else:
                detected_intent_name = (
                    self.intent_config.category_list[detected_class_number - 1]
                )
                
                # If no workflows, return category name directly
                workflows = getattr(self.agent_config, 'workflows', None) or []
                if not workflows:
                    logger.info(
                        f"[%s] get intent (direct category): %s",
                        session_id, detected_intent_name
                    )
                    return detected_intent_name
                
                # Match workflow when workflows exist
                # Prefer description match, fallback to name match
                for workflow in workflows:
                    workflow_label = (
                        workflow.description if workflow.description else workflow.name
                    )
                    if workflow_label == detected_intent_name:
                        detected_intent_id = workflow.id
                        logger.info(
                            f"[%s] get intent: %s", session_id, detected_intent_id
                        )
                        break
                return detected_intent_id
        except Exception as e:
            if UserConfig.is_sensitive():
                logger.error("failed to parse JSON from LLM output")
            else:
                logger.error(
                    "failed to parse JSON from LLM output, error: %s", str(e)
                )
            raise

        return IntentDetectionConstants.DEFAULT_CLASS

    async def _invoke_llm_get_output(self, llm_inputs: Union[List[BaseMessage], str]) -> str:
        try:
            model = ReasonerUtils.get_model(self.agent_config.model, self.runtime)
            llm_output = await model.ainvoke(self.agent_config.model.model_info.model_name, llm_inputs)
            llm_output_content = llm_output.content.strip()
        except Exception as e:
            ExceptionUtils.raise_exception(StatusCode.CONTROLLER_INVOKE_LLM_FAILED, str(e), e)

        return llm_output_content

    def _prepare_detection_input(self, message: Message) -> str:
        """
        Prepare intent detection input
        
        Combine current message and history as LLM input:
        1. Format current message
        2. Format history
        3. Combine as LLM input
        """
        category_list = "分类0：意图不明\n" + "\n".join(f"分类{i+1}：{c}" for i, c in enumerate(self.intent_config.category_list))
        current_inputs = {}
        current_inputs.update({
            IntentDetectionConstants.USER_PROMPT: self.intent_config.user_prompt,
            IntentDetectionConstants.CATEGORY_LIST: category_list,
            IntentDetectionConstants.DEFAULT_CLASS: self.intent_config.default_class,
            IntentDetectionConstants.ENABLE_HISTORY: self.intent_config.enable_history,
            IntentDetectionConstants.ENABLE_INPUT: self.intent_config.enable_input,
            IntentDetectionConstants.EXAMPLE_CONTENT: "\n\n".join(self.intent_config.example_content),
            IntentDetectionConstants.CHAT_HISTORY_MAX_TURN: self.intent_config.chat_history_max_turn,
            IntentDetectionConstants.CHAT_HISTORY: ""
        })

        # Update chat history
        if self.intent_config.enable_history:
            chat_history = ReasonerUtils.get_chat_history(self.context_engine, self.runtime,
                                                          self.intent_config.chat_history_max_turn)
            chat_history_str = ""
            for history in chat_history:
                chat_history_str += "{}: {}\n".format(
                    IntentDetectionConstants.ROLE_MAP.get(history.role, "用户"),
                    history.content
                )
            current_inputs.update({IntentDetectionConstants.CHAT_HISTORY: chat_history_str})

        # Process current input
        if self.intent_config.enable_input:
            current_inputs.update({IntentDetectionConstants.INPUT: message.content.get_query() or ""})
        llm_inputs = self.intent_config.intent_detection_template.format(current_inputs).to_messages()
        return llm_inputs
