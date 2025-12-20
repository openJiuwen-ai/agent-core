#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, List, Dict, Union

from pydantic import BaseModel, Field, ConfigDict, ValidationError

from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.common.logging import logger
from openjiuwen.core.common.security.exception_utils import ExceptionUtils
from openjiuwen.core.common.security.user_config import UserConfig
from openjiuwen.core.component.base import ComponentConfig, WorkflowComponent
from openjiuwen.core.component.common.configs.model_config import ModelConfig
from openjiuwen.core.context_engine.base import Context
from openjiuwen.core.graph.executable import Executable, Input, Output
from openjiuwen.core.runtime.base import ComponentExecutable
from openjiuwen.core.runtime.runtime import Runtime
from openjiuwen.core.utils.llm.base import BaseModelClient, BaseModelInfo
from openjiuwen.core.utils.llm.messages import BaseMessage, HumanMessage
from openjiuwen.core.utils.llm.model_utils.model_factory import ModelFactory
from openjiuwen.core.utils.prompt.template.template import Template

START_STR = "start"
END_STR = "end"
USER_INTERACT_STR = "user_interact"

SUB_PLACEHOLDER_PATTERN = r'\{\{([^}]*)\}\}'
CONTINUE_ASK_STATEMENT = "请您提供{non_extracted_key_fields_names}相关的信息"
WORKFLOW_CHAT_HISTORY = "workflow_chat_history"
TEMPLATE_NAME = "questioner"
QUESTIONER_STATE_KEY = "questioner_state"

QUESTIONER_SYSTEM_TEMPLATE = """\
你是一个信息收集助手，你需要根据指定的参数收集用户的信息，然后提交到系统。
请注意：不要使用任何工具、不用理会问题的具体含义，并保证你的输出仅有 JSON 格式的结果数据。
请严格遵循如下规则：
  1. 让我们一步一步思考。
  2. 用户输入中没有提及的参数提取为 null，并直接向询问用户没有明确提供的参数。
  3. 通过用户提供的对话历史以及当前输入中提取 {{required_name}}，不要追问任何其他信息。
  4. 参数收集完成后，将收集到的信息通过 JSON 的方式展示给用户。

## Specified Parameters
{{required_params_list}}

## Constraints
{{extra_info}}

## Examples
{{example}}
"""

QUESTIONER_USER_TEMPLATE = """\
对话历史
{{dialogue_history}}

请充分考虑以上对话历史及用户输入，正确提取最符合约束要求的 JSON 格式参数。
"""


def questioner_default_template():
    return [
        {"role": "system", "content": QUESTIONER_SYSTEM_TEMPLATE},
        {"role": "user", "content": QUESTIONER_USER_TEMPLATE},
    ]


class ExecutionStatus(Enum):
    START = START_STR
    USER_INTERACT = USER_INTERACT_STR
    END = END_STR


class QuestionerEvent(Enum):
    START_EVENT = START_STR
    END_EVENT = END_STR
    USER_INTERACT_EVENT = USER_INTERACT_STR


class ResponseType(Enum):
    ReplyDirectly = "reply_directly"


class FieldInfo(BaseModel):
    field_name: str
    description: str
    cn_field_name: str = Field(default="")
    required: bool = Field(default=False)
    default_value: Any = Field(default="")


@dataclass
class QuestionerConfig(ComponentConfig):
    model: Optional[ModelConfig] = field(default=None)
    response_type: str = field(default=ResponseType.ReplyDirectly.value)
    question_content: str = field(default="")
    extract_fields_from_response: bool = field(default=True)
    field_names: List[FieldInfo] = field(default_factory=list)
    max_response: int = field(default=3)
    with_chat_history: bool = field(default=False)
    chat_history_max_rounds: int = field(default=5)
    extra_prompt_for_fields_extraction: str = field(default="")
    example_content: str = field(default="")


@dataclass
class QuestionerDefaultConfig:
    prompt_template: List[Dict] = field(default_factory=questioner_default_template)


class QuestionerInput(BaseModel):
    model_config = ConfigDict(extra='allow')   # Allow any extra fields
    query: Union[str, dict, None] = Field(default="")


class OutputCache(BaseModel):
    user_response: Union[str, dict] = Field(default="")
    question: str = Field(default="")
    key_fields: dict = Field(default_factory=dict)


class QuestionerOutput(BaseModel):
    user_response: Union[str, dict] = Field(default="")
    question: str = Field(default="")
    model_config = ConfigDict(extra='allow')  # Allow any extra fields


class QuestionerState(BaseModel):
    response_num: int = Field(default=0)
    user_response: Union[str, dict] = Field(default="")
    question: str = Field(default="")
    extracted_key_fields: Dict[str, Any] = Field(default_factory=dict)
    status: ExecutionStatus = Field(default=ExecutionStatus.START)

    @classmethod
    def deserialize(cls, raw_state: dict):
        state = cls.model_validate(raw_state)
        return state.handle_event(QuestionerEvent(state.status.value))

    def serialize(self) -> dict:
        return self.model_dump()

    def handle_event(self, event: QuestionerEvent):
        if event == QuestionerEvent.START_EVENT:
            return QuestionerStartState.from_state(self)
        if event == QuestionerEvent.USER_INTERACT_EVENT:
            return QuestionerInteractState.from_state(self)
        if event == QuestionerEvent.END_EVENT:
            return QuestionerEndState.from_state(self)
        return self

    def is_undergoing_interaction(self):
        return self.status in [ExecutionStatus.USER_INTERACT]

    def is_fresh_state(self):
        return self.status == ExecutionStatus.START and self.response_num == 0


class QuestionerStartState(QuestionerState):
    @classmethod
    def from_state(cls, questioner_state: QuestionerState):
        return cls(response_num=questioner_state.response_num,
                   user_response=questioner_state.user_response,
                   question=questioner_state.question,
                   extracted_key_fields=questioner_state.extracted_key_fields,
                   status=ExecutionStatus.START)

    def handle_event(self, event: QuestionerEvent):
        if event == QuestionerEvent.USER_INTERACT_EVENT:
            return QuestionerInteractState.from_state(self)
        if event == QuestionerEvent.END_EVENT:
            return QuestionerEndState.from_state(self)
        return self


class QuestionerInteractState(QuestionerState):
    status: ExecutionStatus = Field(default=ExecutionStatus.USER_INTERACT)

    @classmethod
    def from_state(cls, questioner_state: QuestionerState):
        return cls(response_num=questioner_state.response_num,
                   user_response=questioner_state.user_response,
                   question=questioner_state.question,
                   extracted_key_fields=questioner_state.extracted_key_fields,
                   status=ExecutionStatus.USER_INTERACT)

    def handle_event(self, event: QuestionerEvent):
        if event == QuestionerEvent.END_EVENT:
            return QuestionerEndState.from_state(self)
        return self


class QuestionerEndState(QuestionerState):
    status: ExecutionStatus = Field(default=ExecutionStatus.END)

    @classmethod
    def from_state(cls, questioner_state: QuestionerState):
        return cls(response_num=questioner_state.response_num,
                   user_response=questioner_state.user_response,
                   question=questioner_state.question,
                   extracted_key_fields=questioner_state.extracted_key_fields,
                   status=ExecutionStatus.END)

    def handle_event(self, event: QuestionerEvent):
        if event == QuestionerEvent.START_EVENT:
            return QuestionerState().handle_event(event)  # loop back to STRAT state
        return self


class QuestionerUtils:
    @staticmethod
    def format_template(template: str, user_fields: dict):
        def replace(match):
            key = match.group(1)
            return str(user_fields.get(key))

        try:
            result = re.sub(SUB_PLACEHOLDER_PATTERN, replace, template)
            return result
        except (KeyError, TypeError, AttributeError):
            return ""

    @staticmethod
    def get_latest_k_rounds_chat(chat_messages, rounds):
        return chat_messages[-rounds * 2 - 1:]

    @staticmethod
    def format_continue_ask_question(non_extracted_key_fields: List[FieldInfo]):
        non_extracted_key_fields_names = list()
        for param in non_extracted_key_fields:
            non_extracted_key_fields_names.append(param.cn_field_name or param.description)
        result = ", ".join(non_extracted_key_fields_names)
        return CONTINUE_ASK_STATEMENT.format(non_extracted_key_fields_names=result)

    @staticmethod
    def format_questioner_output(output_cache: OutputCache) -> Dict:
        output = QuestionerOutput(**output_cache.key_fields)
        output.user_response = output_cache.user_response
        output.question = output_cache.question
        return output.model_dump(exclude_defaults=True)

    @staticmethod
    def validate_inputs(inputs):
        try:
            return QuestionerInput.model_validate(inputs)
        except ValidationError as e:
            ExceptionUtils.raise_exception(StatusCode.QUESTIONER_COMPONENT_USER_INPUT_ERROR,
                                           ExceptionUtils.format_validation_error(e))

    @staticmethod
    def is_valid_value(input_value):
        if input_value is None:
            return False
        if input_value in ("", {}, []):
            return False
        if isinstance(input_value, str):
            value = input_value.strip().lower()
            return value not in ("null", "none")
        return True


class QuestionerDirectReplyHandler:
    def __init__(self):
        self._config = None
        self._model = None
        self._state = None
        self._prompt = None
        self._query = ""

    def config(self, config: QuestionerConfig):
        self._config = config
        return self

    def model(self, model: BaseModelClient):
        self._model = model
        return self

    def state(self, state: QuestionerState):
        self._state = state
        return self

    def get_state(self):
        return self._state

    def prompt(self, prompt):
        self._prompt = prompt
        return self

    async def handle(self, inputs: Input, runtime: Runtime, context):
        if self._state.status == ExecutionStatus.START:
            return self._handle_start_state(inputs, runtime, context)
        if self._state.status == ExecutionStatus.USER_INTERACT:
            return await self._handle_user_interact_state(inputs, runtime, context)
        if self._state.status == ExecutionStatus.END:
            return self._handle_end_state(inputs, runtime, context)
        return dict()

    def _handle_start_state(self, inputs, runtime, context):
        questioner_input = QuestionerUtils.validate_inputs(inputs)
        output = OutputCache()
        self._query = questioner_input.query or ""
        chat_history = self._get_latest_chat_history(context)
        if self._is_set_question_content():
            user_fields = questioner_input.model_dump(exclude={'query'})
            output.question = QuestionerUtils.format_template(self._config.question_content, user_fields)
            self._update_questioner_states_question(output.question)
            self._state = self._state.handle_event(QuestionerEvent.USER_INTERACT_EVENT)
            return QuestionerUtils.format_questioner_output(output)

        if self._need_extract_fields():
            is_continue_ask = self._initial_extract_from_chat_history(chat_history, output)
            event = QuestionerEvent.USER_INTERACT_EVENT if is_continue_ask else QuestionerEvent.END_EVENT
            if is_continue_ask:
                self._update_questioner_states_question(output.question)
            self._state = self._state.handle_event(event)
        else:
            ExceptionUtils.raise_exception(StatusCode.QUESTIONER_COMPONENT_EMPTY_QUESTION_IN_DIRECT_REPLY)
        return QuestionerUtils.format_questioner_output(output)

    async def _handle_user_interact_state(self, inputs, runtime: Runtime, context):
        await self._get_latest_human_feedback(runtime)
        output = OutputCache(question=self._state.question, user_response=self._query)

        chat_history = self._get_latest_chat_history(context)
        user_response = chat_history[-1].content if chat_history else ""

        if self._is_set_question_content() and not self._need_extract_fields():
            output.user_response = user_response
            self._state = self._state.handle_event(QuestionerEvent.END_EVENT)
            return QuestionerUtils.format_questioner_output(output)

        if self._need_extract_fields():
            is_continue_ask = self._repeat_extract_from_chat_history(chat_history, output)
            event = QuestionerEvent.USER_INTERACT_EVENT if is_continue_ask else QuestionerEvent.END_EVENT
            if is_continue_ask:
                self._update_questioner_states_question(output.question)
            self._state = self._state.handle_event(event)
        else:
            ExceptionUtils.raise_exception(StatusCode.QUESTIONER_COMPONENT_EMPTY_QUESTION_IN_DIRECT_REPLY)
        return QuestionerUtils.format_questioner_output(output)

    def _handle_end_state(self, inputs, runtime, context):
        output = QuestionerOutput(**self._state.extracted_key_fields)
        output.user_response = self._state.user_response
        output.question = self._state.question
        return output.model_dump(exclude_defaults=True)

    def _is_set_question_content(self):
        return isinstance(self._config.question_content, str) and len(self._config.question_content) > 0

    def _need_extract_fields(self):
        return (self._config.extract_fields_from_response and
                len(self._config.field_names) > len(self._state.extracted_key_fields))

    def _initial_extract_from_chat_history(self, chat_history, output: OutputCache) -> bool:
        self._invoke_llm_and_parse_result(chat_history, output)

        self._update_param_default_value(output)
        self._update_state_of_key_fields(output.key_fields)

        return self._check_if_continue_ask(output)

    def _repeat_extract_from_chat_history(self, chat_history, output: OutputCache) -> bool:
        self._invoke_llm_and_parse_result(chat_history, output)

        self._update_param_default_value(output)
        self._update_state_of_key_fields(output.key_fields)

        return self._check_if_continue_ask(output)

    def _get_latest_chat_history(self, context) -> List:
        result = list()
        if self._config.with_chat_history and context:
            raw_chat_history = context.get_messages()
            if raw_chat_history:
                result = QuestionerUtils.get_latest_k_rounds_chat(raw_chat_history,
                                                                  self._config.chat_history_max_rounds)
        if not result or result[-1].role in ["assistant"]:
            # make sure content is Union[str, List[Union[str, Dict]]]
            content = self._query
            if isinstance(content, dict):
                content = [content]  # wrap dict in list
            result.append(HumanMessage(role="user", content=content))
        return result

    def _build_llm_inputs(self, chat_history: list = None) -> List[BaseMessage]:
        prompt_template_input = self._create_prompt_template_keywords(chat_history)
        formatted_template: Template = self._prompt.format(prompt_template_input)
        return formatted_template.to_messages()

    def _create_prompt_template_keywords(self, chat_history: List[BaseMessage]):
        params_list, required_name_list = list(), list()
        for param in self._config.field_names:
            params_list.append(f"{param.field_name}: {param.description}")
            if param.required:
                required_name_list.append(param.cn_field_name or param.description)
        required_name_str = "、".join(required_name_list) + f"{len(required_name_list)}个必要信息"
        all_param_str = "\n".join(params_list)
        dialogue_history_str = "\n".join([f"{_.role}：{_.content}" for _ in chat_history])

        return dict(required_name=required_name_str, required_params_list=all_param_str,
                    extra_info=self._config.extra_prompt_for_fields_extraction, example=self._config.example_content,
                    dialogue_history=dialogue_history_str)

    def _invoke_llm_for_extraction(self, llm_inputs: List[BaseMessage]):
        response = ""

        if UserConfig.is_sensitive():
            logger.info("Invoke llm for extraction")
        else:
            logger.info(f"Invoke llm for extraction, inputs = {llm_inputs}")

        try:
            response = self._model.invoke(
                model_name=self._config.model.model_info.model_name, messages=llm_inputs).content
        except Exception as e:
            ExceptionUtils.raise_exception(StatusCode.QUESTIONER_COMPONENT_INVOKE_LLM_ERROR,
                                           "Failed to invoke llm for extraction", e)

        if UserConfig.is_sensitive():
            logger.info("Success to invoke llm for extraction")
        else:
            logger.info(f"Success to invoke llm for extraction, outputs = {response}")

        result = dict()
        try:
            cleaned = re.sub(r'^\s*```json\s*|\s*```\s*$', '', response.strip(), flags=re.IGNORECASE)
            cleaned = re.sub(r"^\s*'''json\s*|\s*'''\s*$", '', cleaned, flags=re.IGNORECASE)
            result = json.loads(cleaned, strict=False)
        except json.JSONDecodeError as _:
            logger.error(f"Failed to parse json from llm response")
            return result

        if not isinstance(result, dict):
            ExceptionUtils.raise_exception(StatusCode.QUESTIONER_COMPONENT_PARSE_LLM_RESPONSE_ERROR,
                                           "Failed to parse json from llm response")
        result = {k: v for k, v in result.items() if QuestionerUtils.is_valid_value(v)}
        return result

    def _filter_non_extracted_key_fields(self) -> List[FieldInfo]:
        result = []
        for item in self._config.field_names:
            if item.required and item.field_name not in self._state.extracted_key_fields:
                result.append(item)
        return result

    def _update_state_of_key_fields(self, key_fields):
        for k, v in key_fields.items():
            if v:
                self._state.extracted_key_fields.update({k: v})

    def _update_param_default_value(self, output: OutputCache):
        result = dict()
        extracted_key_fields = self._state.extracted_key_fields
        for param in self._config.field_names:
            param_name = param.field_name
            default_value = param.default_value
            if default_value and param_name not in extracted_key_fields:
                result.update({param_name: default_value})
        output.key_fields.update(result)

    def _increment_state_of_response_num(self):
        self._state.response_num += 1

    def _exceed_max_response(self):
        return self._state.response_num >= self._config.max_response

    def _check_if_continue_ask(self, output: OutputCache):
        is_continue_ask = False
        non_extracted_key_fields: List[FieldInfo] = self._filter_non_extracted_key_fields()
        if non_extracted_key_fields:
            if not self._exceed_max_response():
                output.question = QuestionerUtils.format_continue_ask_question(non_extracted_key_fields)
                is_continue_ask = True
            else:
                ExceptionUtils.raise_exception(StatusCode.QUESTIONER_COMPONENT_EXCEED_MAX_RESPONSE)
        if is_continue_ask:
            output.key_fields.clear()
        else:
            output.key_fields.update(self._state.extracted_key_fields)
        return is_continue_ask

    def _invoke_llm_and_parse_result(self, chat_history, output):
        llm_inputs = self._build_llm_inputs(chat_history=chat_history)
        extracted_key_fields = self._invoke_llm_for_extraction(llm_inputs)
        for k, v in extracted_key_fields.items():
            if v:
                output.key_fields.update({k: v})

        self._update_state_of_key_fields(extracted_key_fields)

    async def _get_latest_human_feedback(self, runtime):
        for _ in range(self._state.response_num + 1):
            self._query = await runtime.interact(self._state.question)  # keep the last question, in case of no feedback
        self._increment_state_of_response_num()

    def _update_questioner_states_question(self, question):
        self._state.question = question


class QuestionerExecutable(ComponentExecutable):
    def __init__(self, config: QuestionerConfig):
        super().__init__()
        self._validate_config(config)
        self._config = config
        self._default_config = QuestionerDefaultConfig()
        self._llm = self._create_llm_instance()
        self._prompt: Template = self._init_prompt()
        self._state = None

    @staticmethod
    def _load_state_from_runtime(runtime: Runtime) -> QuestionerState:
        questioner_state = runtime.get_state()
        state_dict = questioner_state.get(QUESTIONER_STATE_KEY) if isinstance(questioner_state, dict) else None
        if state_dict:
            return QuestionerState.deserialize(state_dict)
        return QuestionerState()

    @staticmethod
    def _store_state_to_runtime(state: QuestionerState, runtime: Runtime):
        state_dict = state.serialize()
        runtime.update_state({QUESTIONER_STATE_KEY: state_dict})

    @staticmethod
    def _validate_max_response_num_config(max_response_num: int):
        if max_response_num <= 0:
            ExceptionUtils.raise_exception(StatusCode.QUESTIONER_COMPONENT_CONFIG_ERROR,
                                           "max response must be greater than 0")

    @staticmethod
    def _validate_extract_key_fields_config(if_extract: bool, extract_key_fields: List[FieldInfo]):
        if if_extract and not extract_key_fields:
            ExceptionUtils.raise_exception(StatusCode.QUESTIONER_COMPONENT_CONFIG_ERROR,
                                           "extracted key fields cannot be empty")
        for item in extract_key_fields:
            if not item.field_name:
                ExceptionUtils.raise_exception(StatusCode.QUESTIONER_COMPONENT_CONFIG_ERROR,
                                           "extracted key field name cannot be empty")

    @staticmethod
    def _validate_response_type_config(response_type: str):
        response_type_values = [member.value for member in ResponseType]
        if response_type not in response_type_values:
            ExceptionUtils.raise_exception(StatusCode.QUESTIONER_COMPONENT_CONFIG_ERROR,
                                           f"response type {response_type} is invalid")

    def state(self, state: QuestionerState):
        self._state = state
        return self

    async def invoke(self, inputs: Input, runtime: Runtime, context: Context) -> Output:
        state_from_runtime = self._load_state_from_runtime(runtime)
        if state_from_runtime.is_undergoing_interaction():
            current_state = state_from_runtime  # recover state from runtime
        else:
            current_state = QuestionerState()  # create new state

        current_state = current_state.handle_event(QuestionerEvent.START_EVENT)

        invoke_result = dict()
        if self._config.response_type == ResponseType.ReplyDirectly.value:
            invoke_result = await self._handle_questioner_direct_reply_safe(
                inputs, runtime, context, current_state
            )
            # handler might update state
            current_state = invoke_result.pop('_state', current_state)

        self._store_state_to_runtime(current_state, runtime)

        if current_state.is_undergoing_interaction():
            await runtime.interact(invoke_result.get("question", ""))

        return invoke_result

    def _create_llm_instance(self) -> BaseModelClient:
        if isinstance(self._config.model.model_info, BaseModelInfo):
            kwargs = self._config.model.model_info.model_dump(exclude={'model_name', 'streaming'})
            return ModelFactory().get_model(model_provider=self._config.model.model_provider, **kwargs)
        else:
            return ModelFactory().get_model(model_provider=self._config.model.model_provider,
                                            api_base=self._config.model.model_info.api_base,
                                            api_key=self._config.model.model_info.api_key)

    def _init_prompt(self) -> Template:
        return Template(content=self._default_config.prompt_template)

    async def _handle_questioner_direct_reply(self, inputs: Input, runtime: Runtime, context):
        handler = (QuestionerDirectReplyHandler()
                   .config(self._config).model(self._llm).state(self._state).prompt(self._prompt))
        result = await handler.handle(inputs, runtime, context)
        self._state = handler.get_state()
        return result

    async def _handle_questioner_direct_reply_safe(
            self, inputs: Input, runtime: Runtime, context, current_state: QuestionerState
    ):
        """并发安全版本：使用传入的 state 而不是实例变量"""
        handler = (QuestionerDirectReplyHandler()
                   .config(self._config).model(self._llm).state(current_state).prompt(self._prompt))
        result = await handler.handle(inputs, runtime, context)
        # return updated state, let caller manage
        result['_state'] = handler.get_state()
        return result

    def _validate_config(self, config: QuestionerConfig):
        self._validate_response_type_config(config.response_type)
        self._validate_extract_key_fields_config(config.extract_fields_from_response, config.field_names)
        self._validate_max_response_num_config(config.max_response)


class QuestionerComponent(WorkflowComponent):
    def __init__(self, questioner_comp_config: QuestionerConfig = None):
        super().__init__()
        self._questioner_config = questioner_comp_config
        self._executable = None

    def to_executable(self) -> Executable:
        return QuestionerExecutable(self._questioner_config).state(QuestionerState())
