#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import json
from dataclasses import dataclass, field
from typing import List, Any, Dict, Optional, AsyncIterator, Union

from pydantic import ValidationError, Field, BaseModel

from jiuwen.core.common.enum.enum import WorkflowLLMResponseType, MessageRole
from jiuwen.core.common.exception.exception import JiuWenBaseException, InterruptException
from jiuwen.core.common.exception.status_code import StatusCode
from jiuwen.core.common.logging import logger
from jiuwen.core.utils.common.verify_utils import WorkflowLLMUtils, OutputFormatter, SchemaGenerator, ExceptionUtils
from jiuwen.core.component.base import ComponentConfig, WorkflowComponent
from jiuwen.core.context_engine.base import Context
from jiuwen.core.graph.executable import Input, Output
from jiuwen.core.runtime.base import ComponentExecutable
from jiuwen.core.runtime.runtime import Runtime
from jiuwen.core.utils.config.user_config import UserConfig
from jiuwen.core.utils.llm.base import BaseChatModel
from jiuwen.core.utils.llm.messages import SystemMessage, HumanMessage
from jiuwen.core.utils.llm.model_utils.model_factory import ModelFactory
from jiuwen.core.utils.prompt.template.template import Template
from jiuwen.core.utils.prompt.template.template_manager import TemplateManager

WORKFLOW_CHAT_HISTORY = "workflow_chat_history"
CHAT_HISTORY_MAX_TURN = 3
_ROLE = "role"
_CONTENT = "content"
ROLE_MAP = {"user": "用户", "assistant": "助手", "system": "系统"}
_SPAN = "span"
_WORKFLOW_DATA = "workflow_data"
_ID = "id"
_TYPE = "type"
_INSTRUCTION_NAME = "instruction_name"
_TEMPLATE_NAME = "template_name"

RESPONSE_FORMAT_TO_PROMPT_MAP = {
    WorkflowLLMResponseType.JSON.value: {
        _INSTRUCTION_NAME: "jsonInstruction",
        _TEMPLATE_NAME: "llm_json_formatting"
    },
    WorkflowLLMResponseType.MARKDOWN.value: {
        _INSTRUCTION_NAME: "markdownInstruction",
        _TEMPLATE_NAME: "llm_markdown_formatting"
    }
}


class LLMPromptFormatter:

    _DEFAULT_MARKDOWN_INSTRUCTION = (
        "Please return the answer in markdown format.\n"
        "- For headings, use number signs (#).\n"
        "- For list items, start with dashes (-).\n"
        "- To emphasize text, wrap it with asterisks (*).\n"
        "- For code or commands, surround them with backticks (`).\n"
        "- For quoted text, use greater than signs (>).\n"
        "- For links, wrap the text in square brackets [], followed by the URL in parentheses ().\n"
        "- For images, use square brackets [] for the alt text, followed by the image URL in parentheses ().\n"
        "The question is: ${query}."
    )

    _DEFAULT_JSON_INSTRUCTION = (
        "Carefully consider the user's question to ensure your answer is logical and makes sense.\n"
        "- Make sure your explanation is concise and easy to understand, not verbose.\n"
        "- Strictly return the answer in valid JSON format only, and "
        "\"DO NOT ADD ANY COMMENTS BEFORE OR AFTER IT\" to ensure it could be formatted "
        "as a JSON instance that conforms to the JSON schema below.\n"
        "Here is the JSON schema: ${json_schema}.\n"
        "The question is: ${query}."
    )

    @staticmethod
    def _find_last_user_index(history: List[Dict[str, Any]]) -> int | None:
        for idx in range(len(history) - 1, -1, -1):
            if history[idx].get("role") == "user":
                return idx
        return None

    @staticmethod
    def format_prompt(
            history: List[Dict[str, Any]],
            response_format: Dict[str, Any],
            output_config: dict,
    ) -> List[Dict[str, Any]]:
        res_type = response_format.get("type")
        if res_type == "text":
            return history

        last_user_idx = LLMPromptFormatter._find_last_user_index(history)
        if last_user_idx is None:
            return history
        query = history[last_user_idx]["content"]
        prompt = query

        if res_type == "markdown":
            instruction = (
                    response_format.get("markdownInstruction")
                    or LLMPromptFormatter._DEFAULT_MARKDOWN_INSTRUCTION
            )
            prompt = instruction.replace("${query}", query)

        elif res_type == "json":
            json_schema = SchemaGenerator.generate_json_schema(output_config)
            instruction = (
                    response_format.get("jsonInstruction")
                    or LLMPromptFormatter._DEFAULT_JSON_INSTRUCTION
            )
            prompt = (
                instruction
                .replace("${json_schema}", json.dumps(json_schema, ensure_ascii=False))
                .replace("${query}", query)
            )

        history[last_user_idx]["content"] = prompt
        return history


@dataclass
class LLMCompConfig(ComponentConfig):
    model: 'ModelConfig' = None
    template_content: List[Any] = field(default_factory=list)
    response_format: Dict[str, Any] = field(default_factory=dict)
    output_config: Dict[str, Any] = field(default_factory=dict)
    enable_history: bool = False


class ResponseFormatConfig(BaseModel):
    response_type: str = Field(pattern=r'^(text|markdown|json)$', alias="type")


class OutputParamConfig(BaseModel):
    param_type: str = Field(default="", alias="type")
    param_description: str = Field(default="", alias="description")
    param_required: bool = Field(default=False, alias="required")


class LLMExecutable(ComponentExecutable):
    def __init__(self, component_config: LLMCompConfig):
        super().__init__()
        self._validate_config(component_config)
        self._config: LLMCompConfig = component_config
        self._llm: Union[BaseChatModel, None] = None
        self._initialized: bool = False
        self._runtime = None
        self._context = None

    @staticmethod
    def _validate_template_content(template_content):
        if len(template_content) >= 1:
            try:
                for element in template_content:
                    if element.get(_ROLE, "") == "system":
                        SystemMessage.model_validate(element)
            except ValidationError as e:
                ExceptionUtils.raise_exception(StatusCode.LLM_COMPONENT_TEMPLATE_CONFIG_ERROR,
                                               "system message is invalid", e)

            if_contain_user_message = False
            for element in template_content:
                if element.get(_ROLE, "") == "user":
                    HumanMessage.model_validate(element)
                    if_contain_user_message = True
                if if_contain_user_message and element.get(_ROLE, "") == "system":
                    SystemMessage.model_validate(element)
                    ExceptionUtils.raise_exception(StatusCode.LLM_COMPONENT_TEMPLATE_CONFIG_ERROR,
                                            "system message must be before user message")
            if not if_contain_user_message:
                ExceptionUtils.raise_exception(StatusCode.LLM_COMPONENT_TEMPLATE_CONFIG_ERROR,
                                               "user message is required")
        else:
            ExceptionUtils.raise_exception(StatusCode.LLM_COMPONENT_TEMPLATE_CONFIG_ERROR,
                                           "template content is empty")

    @staticmethod
    def _validate_output_config(output_config):
        if not output_config:
            ExceptionUtils.raise_exception(StatusCode.LLM_COMPONENT_OUTPUT_CONFIG_ERROR,
                                           "output config is empty")
        for param, value in output_config.items():
            if not param:
                ExceptionUtils.raise_exception(StatusCode.LLM_COMPONENT_OUTPUT_CONFIG_ERROR,
                                    f"output config parameter {param} is empty")
            try:
                OutputParamConfig.model_validate(value)
            except ValidationError as e:
                if UserConfig.is_sensitive():
                    ExceptionUtils.raise_exception(StatusCode.LLM_COMPONENT_OUTPUT_CONFIG_ERROR,
                                         "output config parameter's config value is invalid")
                else:
                    ExceptionUtils.raise_exception(StatusCode.LLM_COMPONENT_OUTPUT_CONFIG_ERROR,
                                         f"output config parameter's config {value} is invalid", e)
    @staticmethod
    def _validate_response_format(response_format, output_config):
        response_type = ""
        try:
            response_type = ResponseFormatConfig.model_validate(response_format).response_type
        except ValidationError as e:
            ExceptionUtils.raise_exception(StatusCode.LLM_COMPONENT_RESPONSE_FORMAT_CONFIG_ERROR,
                                  f"response format {response_format} is invalid", e)

        if response_type in ["text", "markdown"] and len(output_config) != 1:
            ExceptionUtils.raise_exception(
                StatusCode.LLM_COMPONENT_RESPONSE_FORMAT_CONFIG_ERROR,
                "output config must contain exactly one parameter for text or markdown response type")

    async def invoke(self, inputs: Input, runtime: Runtime, context: Context) -> Output:
        self._set_runtime(runtime)
        self._set_context(context)
        model_inputs = self._prepare_model_inputs(inputs)
        if UserConfig.is_sensitive():
            logger.info("[%s] model inputs", self._runtime.executable_id())
        else:
            logger.info("[%s] model inputs %s", self._runtime.executable_id(), model_inputs)
        response = ""
        try:
            llm_response = await self._llm.ainvoke(
                model_name=self._config.model.model_info.model_name, messages=model_inputs)
            response = llm_response.content
        except Exception as e:
            ExceptionUtils.raise_exception(StatusCode.LLM_COMPONENT_INVOKE_LLM_ERROR,
                                           "Failed to invoke", e)
        if UserConfig.is_sensitive():
            logger.info("[%s] model outputs", self._runtime.executable_id())
        else:
            logger.info("[%s] model outputs %s", self._runtime.executable_id(), response)
        return self._create_output(response)

    async def stream(self, inputs: Input, runtime: Runtime, context: Context) -> AsyncIterator[Output]:
        self._set_runtime(runtime)
        self._set_context(context)
        response_format_type = self._get_response_format().get(_TYPE)
        try:
            if response_format_type == WorkflowLLMResponseType.JSON.value:
                async for out in self._invoke_for_json_format(inputs):
                    yield out
            else:
                async for out in self._stream_with_chunks(inputs):
                    yield out
        except Exception as e:
            if UserConfig.is_sensitive():
                ExceptionUtils.raise_exception(StatusCode.LLM_COMPONENT_INVOKE_LLM_ERROR,
                                               "Failed to stream", e)
            else:
                ExceptionUtils.raise_exception(StatusCode.LLM_COMPONENT_INVOKE_LLM_ERROR, str(e), e)

    async def interrupt(self, message: dict):
        raise InterruptException(
            error_code=StatusCode.CONTROLLER_INTERRUPTED_ERROR.code,
            message=json.dumps(message, ensure_ascii=False)
        )

    def _initialize_if_needed(self):
        if not self._initialized:
            try:
                self._llm = self._create_llm_instance()
                self._initialized = True
            except Exception as e:
                ExceptionUtils.raise_exception(StatusCode.LLM_COMPONENT_INIT_LLM_ERROR,
                                               "Failed to initialize llm if needed", e)

    def _create_llm_instance(self):
        return ModelFactory().get_model(model_provider=self._config.model.model_provider,
                                        api_base=self._config.model.model_info.api_base,
                                        api_key=self._config.model.model_info.api_key,
                                        timeout=self._config.model.model_info.timeout)

    def _build_user_prompt_content(self, inputs: dict) -> list[dict]:
        template_content_list = self._config.template_content
        user_prompt = [element for element in template_content_list if element.get(_ROLE, "") == MessageRole.USER.value]
        return Template(content=[user_prompt[0]]).format(inputs).content

    def _get_model_input(self, inputs: dict):
        system_prompt = self._build_system_prompt(inputs)
        user_prompt = self._build_user_prompt_content(inputs)
        all_prompts = self._insert_history_to_system_and_user_prompt(system_prompt, user_prompt)
        return LLMPromptFormatter.format_prompt(history=all_prompts,
                                                response_format=self._config.response_format,
                                                output_config=self._config.output_config)

    def _insert_history_to_system_and_user_prompt(self, system_prompt: list, user_prompt: list):
        original_history = system_prompt if isinstance(system_prompt, list) else []
        if self._context:
            chat_history = []
            chat_history_messages: list = self._context.get_messages()
            if chat_history_messages and self._config.enable_history:
                chat_history = [dict(role=message.role, content=message.content) for message in chat_history_messages]
            original_history.extend(chat_history)
        original_history.extend(user_prompt)
        return original_history

    def _get_response_format(self):
        try:
            response_format = self._config.response_format
            if not response_format:
                return {}

            format_type = response_format.get(_TYPE)
            if not format_type or format_type not in RESPONSE_FORMAT_TO_PROMPT_MAP:
                return response_format

            format_config = RESPONSE_FORMAT_TO_PROMPT_MAP[format_type]
            instruction_name = format_config.get(_INSTRUCTION_NAME)

            if response_format.get(instruction_name):
                return response_format

            instruction_content = self._get_instruction_from_template(format_config)
            if instruction_content:
                response_format[instruction_name] = instruction_content

            return response_format

        except Exception as e:
            ExceptionUtils.raise_exception(StatusCode.LLM_COMPONENT_ASSEMBLE_TEMPLATE_ERROR,
                                           "Failed to get response format", e)

    def _get_instruction_from_template(self, format_config: dict) -> Optional[str]:
        template_name = format_config.get(_TEMPLATE_NAME)
        try:
            if not template_name:
                return None
            filters = self._build_template_filters()

            template_manager = TemplateManager()
            template = template_manager.get(name=template_name, filters=filters)

            return getattr(template, "content", None) if template else None
        except Exception as e:
            return None

    def _build_template_filters(self) -> dict:
        filters = {}

        model_name = self._config.model.model_info.model_name
        if model_name:
            filters["model_name"] = model_name

        return filters

    def _create_output(self, llm_output) -> Output:
        try:
            formatted_res = OutputFormatter.format_response(llm_output,
                                                            self._config.response_format,
                                                            self._config.output_config)
            return formatted_res
        except JiuWenBaseException as e:
            if e.error_code == StatusCode.PROMPT_JSON_SCHEMA_ERROR.code:
                ExceptionUtils.raise_exception(StatusCode.LLM_COMPONENT_JSON_SCHEMA_OUTPUT_ERROR, error_msg=e.message)
            else:
                raise e

    def _set_runtime(self, runtime: Runtime):
        self._runtime = runtime

    def _set_context(self, context):
        self._context = context

    def _prepare_model_inputs(self, inputs):
        self._initialize_if_needed()
        return self._get_model_input(inputs)

    async def _invoke_for_json_format(self, inputs: Input) -> AsyncIterator[Output]:
        model_inputs = self._prepare_model_inputs(inputs)
        if UserConfig.is_sensitive():
            logger.info("[%s] model inputs", self._runtime.executable_id())
        else:
            logger.info("[%s] model inputs %s", self._runtime.executable_id(), model_inputs)
        llm_output = await self._llm.ainvoke(model_name=self._config.model.model_info.model_name, messages=model_inputs)  # 如果 invoke 是异步接口，要加 await
        llm_output_content = llm_output.content
        yield self._create_output(llm_output_content)

    async def _stream_with_chunks(self, inputs: Input) -> AsyncIterator[Output]:
        model_inputs = self._prepare_model_inputs(inputs)
        async for chunk in self._llm.astream(model_name=self._config.model.model_info.model_name, messages=model_inputs):
            content = WorkflowLLMUtils.extract_content(chunk)
            if content:
                formatted_res = OutputFormatter.format_response(content,
                                                                self._config.response_format,
                                                                self._config.output_config)
                stream_out = formatted_res
                yield stream_out

    def _build_system_prompt(self, inputs: dict):
        system_prompt = []
        for element in self._config.template_content:
            if element.get(_ROLE, "") == "system":
                system_prompt.append(element)
            else:
                break
        return Template(content=system_prompt).format(inputs).content

    def _validate_config(self, config: LLMCompConfig):
        self._validate_template_content(config.template_content)
        self._validate_response_format(config.response_format, config.output_config)
        self._validate_output_config(config.output_config)


class LLMComponent(WorkflowComponent):
    def __init__(self, component_config: Optional[LLMCompConfig] = None):
        super().__init__()
        self._executable = None
        self._config = component_config

    @property
    def executable(self) -> LLMExecutable:
        if self._executable is None:
            self._executable = self.to_executable()
        return self._executable

    def to_executable(self) -> LLMExecutable:
        return LLMExecutable(self._config)
