# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import copy
from typing import List, Dict, Any, Optional

from openjiuwen.agent.common.enum import TaskType
from openjiuwen.agent.config.base import AgentConfig
from openjiuwen.core.agent.task import Task, TaskInput
from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.common.security.exception_utils import ExceptionUtils
from openjiuwen.core.common.security.json_utils import JsonUtils
from openjiuwen.core.stream.base import OutputSchema
from openjiuwen.core.utils.llm.messages import BaseMessage, AIMessage, HumanMessage, ToolMessage
from openjiuwen.core.utils.tool.schema import ToolCall
from openjiuwen.core.utils.prompt.template.template import Template
from openjiuwen.core.runtime.interaction.interactive_input import InteractiveInput
from openjiuwen.core.context_engine.engine import ContextEngine
from openjiuwen.core.runtime.runtime import Runtime
from openjiuwen.core.common.logging import logger
from openjiuwen.core.common.security.user_config import UserConfig
from openjiuwen.core.common.utlis.hash_util import generate_key
from openjiuwen.core.utils.llm.model_utils.model_factory import ModelFactory
from openjiuwen.core.component.common.configs.model_config import ModelConfig
from openjiuwen.core.agent.message.message import Message
from openjiuwen.core.workflow.base import WorkflowOutput


class MessageHandlerUtils:

    @staticmethod
    def format_llm_inputs(
            inputs: Any,
            chat_history: List[BaseMessage],
            config: AgentConfig,
            keywords: Optional[dict]=None
    ) -> List[BaseMessage]:
        if isinstance(inputs, InteractiveInput):
            user_fields = {}
        elif isinstance(inputs, dict):
            user_fields = copy.deepcopy(inputs)
        else:
            user_fields = {"query": inputs}

        if keywords:
            user_fields.update(keywords)

        system_prompt = (Template(
            name=config.prompt_template_name,
            content=config.prompt_template
        ).format(user_fields).to_messages())

        return MessageHandlerUtils.concat_system_prompt_with_chat_history(system_prompt, chat_history)

    @staticmethod
    def concat_system_prompt_with_chat_history(system_prompt: List[BaseMessage],
                                               chat_history: List[BaseMessage]) -> List[BaseMessage]:
        result_messages = []

        if not chat_history or chat_history[0].role != "system":
            result_messages.extend(system_prompt)

        result_messages.extend(chat_history)

        return result_messages

    @staticmethod
    def parse_llm_output(response: BaseMessage, config: AgentConfig) -> List[Task]:
        """Parse LLM output, return task list"""
        return MessageHandlerUtils.create_tasks_from_tool_calls(
            response.tool_calls, config
        )

    @staticmethod
    def create_tasks_from_tool_calls(
            tool_calls: List[ToolCall],
            config: AgentConfig
    ) -> List[Task]:
        if not tool_calls:
            return []

        result = []
        for tool_call in tool_calls:
            tool_name = tool_call.name
            for workflow in config.workflows:
                if workflow.name == tool_name:
                    task_type = TaskType.WORKFLOW
                    target_id = f"{workflow.id}_{workflow.version}"
                    arguments = {}
                    try:
                        arguments = JsonUtils.safe_json_loads(tool_call.arguments)
                    except Exception as e:
                        if UserConfig.is_sensitive():
                            logger.error("LLM Agent parse tool call workflow's arguments error")
                            ExceptionUtils.raise_exception(StatusCode.CONTROLLER_PARSE_TOOL_CALL_ERROR,
                                                           "LLM-generated workflow arguments are invalid", e)
                        else:
                            logger.error(f"LLM Agent parse tool call workflow({tool_name})'s arguments error: "
                                         f"{tool_call.arguments}")
                            ExceptionUtils.raise_exception(StatusCode.CONTROLLER_PARSE_TOOL_CALL_ERROR,
                                                           f"LLM-generated workflow ({tool_name}) arguments "
                                                           f"are invalid: {tool_call.arguments}", e)

                    result.append(Task(
                        task_id=tool_call.id,
                        input=TaskInput(
                            target_id=target_id,
                            target_name=tool_name,
                            arguments=arguments
                        ),
                        task_type=task_type
                    ))
                    break
            for plugin in config.plugins:
                if plugin.name == tool_name:
                    task_type = TaskType.PLUGIN
                    arguments = {}
                    try:
                        arguments = JsonUtils.safe_json_loads(tool_call.arguments)
                    except Exception as e:
                        if UserConfig.is_sensitive():
                            logger.error("LLM Agent parse tool call plugin's arguments error")
                            ExceptionUtils.raise_exception(StatusCode.CONTROLLER_PARSE_TOOL_CALL_ERROR,
                                                           "LLM-generated plugin arguments are invalid", e)
                        else:
                            logger.error(f"LLM Agent parse tool call plugin({tool_name})'s arguments error: "
                                         f"{tool_call.arguments}")
                            ExceptionUtils.raise_exception(StatusCode.CONTROLLER_PARSE_TOOL_CALL_ERROR,
                                                           f"LLM-generated plugin ({tool_name}) arguments "
                                                           f"are invalid: {tool_call.arguments}", e)
                    result.append(Task(
                        task_id=tool_call.id,
                        input=TaskInput(
                            target_name=tool_name,
                            arguments=arguments
                        ),
                        task_type=task_type
                    ))
                    break
        if not result:
            raise JiuWenBaseException(
                error_code=StatusCode.TOOL_NOT_FOUND_ERROR.code,
                message=StatusCode.TOOL_NOT_FOUND_ERROR.errmsg
            )
        return result

    @staticmethod
    def determine_task_type(tool_name: str, config: AgentConfig) -> TaskType:
        for workflow in config.workflows:
            if tool_name == workflow.name:
                return TaskType.WORKFLOW

        for plugin in config.plugins:
            if tool_name == plugin.name:
                return TaskType.PLUGIN

        raise JiuWenBaseException(StatusCode.TOOL_NOT_FOUND_ERROR.code, f"not find tool call type: {tool_name}")

    @staticmethod
    def is_interaction_result(exec_result: Any) -> bool:
        return (isinstance(exec_result, dict) and
                exec_result.get("error") and
                isinstance(exec_result.get("value"), list))

    @staticmethod
    def create_interrupt_result(e, tool_name: str) -> Dict[str, Any]:
        return {
            "error": True,
            "value": e.message,
            "tool_name": tool_name
        }

    @staticmethod
    def validate_execution_inputs(exec_result: Any, sub_task_result: Any) -> bool:
        return exec_result is not None

    @staticmethod
    def should_add_user_message(query: str, context_engine: ContextEngine, runtime: Runtime) -> bool:
        agent_context = context_engine.get_agent_context(runtime.session_id())
        last_message = agent_context.get_latest_message()

        if not last_message:
            return True

        if last_message.role == 'tool':
            logger.info("Skipping user message - post-tool-call request")
            return False

        if last_message.role == 'user' and last_message.content == query:
            logger.info("Skipping duplicate user message")
            return False

        return True

    @staticmethod
    def add_user_message(query: Any, context_engine: ContextEngine, runtime: Runtime):
        if MessageHandlerUtils.should_add_user_message(query, context_engine, runtime):
            agent_context = context_engine.get_agent_context(runtime.session_id())
            user_message = HumanMessage(content=query)
            agent_context.add_message(user_message)
            if UserConfig.is_sensitive():
                logger.info(f"Added user message")
            else:
                logger.info(f"Added user message: {query}")

    @staticmethod
    def add_ai_message(ai_message: AIMessage, context_engine: ContextEngine, runtime: Runtime):
        if ai_message:
            agent_context = context_engine.get_agent_context(runtime.session_id())
            agent_context.add_message(ai_message)

    @staticmethod
    def add_tool_result(message: Message, context_engine: ContextEngine, runtime: Runtime):
        if message:
            agent_context = context_engine.get_agent_context(runtime.session_id())
            tool_result = message.content.task_result.output
            if isinstance(tool_result, OutputSchema):
                payload = tool_result.payload
                if isinstance(payload, dict):
                    tool_result = payload.get("output", "")
            elif isinstance(tool_result, WorkflowOutput):
                tool_result = tool_result.result
            content = JsonUtils.safe_json_dumps(tool_result, str(tool_result), ensure_ascii=False)
            tool_message = ToolMessage(content=content,
                                       tool_call_id=message.context.task_id)
            agent_context.add_message(tool_message)

    @staticmethod
    def get_chat_history(context_engine: ContextEngine, runtime: Runtime, config: AgentConfig) -> List[BaseMessage]:
        agent_context = context_engine.get_agent_context(runtime.session_id())
        chat_history = agent_context.get_messages()
        max_rounds = config.constrain.reserved_max_chat_rounds
        return chat_history[-2 * max_rounds:]

    @staticmethod
    def filter_inputs(schema: dict, user_data: dict) -> dict:
        """Filter and validate user input, extract fields by schema"""
        if not schema:
            return {}

        required_fields = {
            k for k, v in schema.items()
            if isinstance(v, dict) and v.get("required") is True
        }

        filtered = {}
        for k in schema:
            if k not in user_data:
                if k in required_fields:
                    raise KeyError(f"missing required parameter: {k}")
                continue
            filtered[k] = user_data[k]

        return filtered

    @staticmethod
    def add_workflow_message_to_chat_history(message: BaseMessage, workflow_id: str,
                                             context_engine: ContextEngine, runtime: Runtime):
        """Add message to workflow chat history"""
        workflow_context = context_engine.get_workflow_context(
            workflow_id=workflow_id,
            session_id=runtime.session_id()
        )
        workflow_context.add_message(message)


class ReasonerUtils:
    @staticmethod
    def get_chat_history(context_engine: ContextEngine, runtime: Runtime,
                         chat_history_max_turn: int) -> List[BaseMessage]:
        """Get history by max conversation rounds"""
        agent_context = context_engine.get_agent_context(runtime.session_id())
        chat_history = agent_context.get_messages()
        return chat_history[-2 * chat_history_max_turn:]

    @staticmethod
    def get_model(model_config: ModelConfig, runtime: Runtime):
        """Get model instance by config"""
        model_id = generate_key(
            model_config.model_info.api_key,
            model_config.model_info.api_base,
            model_config.model_provider
        )

        model = runtime.get_model(model_id=model_id)

        if model is None:
            model = ModelFactory().get_model(
                model_provider=model_config.model_provider,
                **model_config.model_info.model_dump(exclude=['model_name', 'streaming'])
            )
            runtime.add_model(model_id=model_id, model=model)

        return model
