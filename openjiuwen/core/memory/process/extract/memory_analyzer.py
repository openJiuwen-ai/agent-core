# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import json
from typing import List, Tuple
from pydantic import BaseModel, Field

from openjiuwen.core.foundation.llm import Model, JsonOutputParser
from openjiuwen.core.foundation.llm.schema.message import BaseMessage
from openjiuwen.core.memory.config.config import AgentMemoryConfig
from openjiuwen.core.memory.prompt.memory_analyzer import (MEMORY_ANALYZER_PROMPT,
                                                           VARIABLES_DESCRIPTION_TEMPLATE_PROMPT,
                                                           SUMMARY_TEMPLATE_PROMPT, USER_PROFILE_CATEGORY)
from openjiuwen.core.common.logging import memory_logger
from openjiuwen.core.common.logging.events import LogEventType

MEMORY_CATEGORIES_SCOPE = [
    "user_profile"
]


class VariableResult(BaseModel):
    variable_key: str = Field(default="", description="variable key")
    variable_value: str = Field(default="", description="variable value")


class MemoryAnalyzerResult(BaseModel):
    categories: List[str] = Field(default=[])
    variables: List[VariableResult] = Field(default=[])
    summary: str = Field(default="")


def _get_system_prompt():
    sys_prompt = MEMORY_ANALYZER_PROMPT
    memory_categories_scope = "["
    index = 0
    if 'user_profile' in MEMORY_CATEGORIES_SCOPE:
        memory_categories_scope += "`user_profile`, "
        index += 1
        user_profile_category = USER_PROFILE_CATEGORY.replace("INDEX", str(index))
    else:
        user_profile_category = ""
    sys_prompt = sys_prompt.replace("USER_PROFILE_PROMPT", user_profile_category)

    if index > 0:
        memory_categories_scope = memory_categories_scope[:-2]
    memory_categories_scope += "]"
    sys_prompt = sys_prompt.replace("MEMORY_CATEGORIES_SCOPE", memory_categories_scope)
    return sys_prompt


class MemoryAnalyzer:
    def __init__(self):
        pass

    @staticmethod
    async def analyze(
            messages: List[BaseMessage],
            history_messages: List[BaseMessage],
            base_chat_model: Tuple[str, Model],
            memory_config: AgentMemoryConfig,
            retries: int = 3
    ) -> MemoryAnalyzerResult | None:
        if len(messages) == 0:
            memory_logger.warning(
                "No messages to analyze",
                event_type=LogEventType.MEMORY_PROCESS,
                metadata={"messages_len": len(messages)}
            )
            return None
        need_summary, raw_summary = MemoryAnalyzer._check_summary(
            messages=messages,
            summary_max_length_threshold=memory_config.summary_config.threshold
        )
        model_input = MemoryAnalyzer._build_model_input(
            messages=messages,
            history_messages=history_messages,
            memory_config=memory_config,
            need_summary=need_summary,
            raw_summary_len=len(raw_summary),
        )

        model_name, model_client = base_chat_model
        parser = JsonOutputParser()
        for attempt in range(retries):
            try:
                response = await model_client.invoke(
                    model=model_name,
                    messages=model_input
                )
                res = await parser.parse(response.content)
                analyze_result = MemoryAnalyzerResult.model_validate(res)
                if not need_summary:
                    analyze_result.summary = raw_summary
                return analyze_result
            except json.JSONDecodeError as e:
                if attempt < retries - 1:
                    continue
                memory_logger.error(
                    "Categories model output format error",
                    event_type=LogEventType.MEMORY_PROCESS,
                    exception=str(e)
                )

        return MemoryAnalyzerResult()

    @staticmethod
    def _build_model_input(
            messages: List[BaseMessage],
            history_messages: List[BaseMessage],
            memory_config: AgentMemoryConfig,
            need_summary: bool,
            raw_summary_len: int
    ) -> List:
        variables_description, variables_output_format = MemoryAnalyzer._build_variable_prompt(memory_config)
        if need_summary:
            summary_max_token = max(int(raw_summary_len * memory_config.summary_config.fraction),
                                    memory_config.summary_config.max_token)
            memory_logger.debug(
                "Building model input to analyze the memories contained in the messages",
                event_type=LogEventType.MEMORY_PROCESS,
                metadata={"summary_max_token": summary_max_token, "raw_summary_len": raw_summary_len,
                          "fraction": memory_config.summary_config.fraction,
                          "config_max_token": memory_config.summary_config.max_token}
            )
            summary_description, summary_output_format = \
                MemoryAnalyzer._build_summary_prompt(
                    max_message_token=summary_max_token,
                    memory_config=memory_config
                )
        else:
            summary_description, summary_output_format = "", ""

        sys_prompt = _get_system_prompt()
        sys_prompt = sys_prompt.replace("VARIABLES_DESCRIPTION_TEMPLATE", variables_description)
        sys_prompt = sys_prompt.replace("VARIABLES_OUTPUT_TEMPLATE", variables_output_format)
        sys_prompt = sys_prompt.replace("SUMMARY_TEMPLATE", summary_description)
        sys_prompt = sys_prompt.replace("SUMMARY_OUTPUT_TEMPLATE", summary_output_format)
        model_input = [{
            "role": "system",
            "content": sys_prompt
        }]
        user_input = ""
        history = ""
        conversation = ""
        for msg in history_messages:
            if msg.name:
                history += f"{msg.name}: {msg.content}\n"
            else:
                history += f"{msg.role}: {msg.content}\n"
        for msg in messages:
            conversation += f"{msg.role}: {msg.content}\n"
        if history != "":
            user_input += (f"如果当前输入与历史消息有关联，可参考历史消息，历史消息如下：\n"
                           f"<historical_messages>{history}</historical_messages>\n")
        user_input += f"现在开始：请根据设定的规则处理以下输入并生成出输出：\n<current_messages>{conversation}</current_messages>\n"
        model_input.append({
            "role": "user",
            "content": user_input
        })
        return model_input

    @staticmethod
    def _build_variable_prompt(
            memory_config: AgentMemoryConfig
    ) -> Tuple[str, str]:
        if len(memory_config.mem_variables) == 0:
            return "", ""

        variables_description = []
        variables_output_format = []
        for param in memory_config.mem_variables:
            variables_description.append({
                "variable_key": param.name,
                "variable_value": param.description
            })

            variables_output_format.append({
                "variable_key": param.name,
                "variable_value": ""
            })

        variables_description_json = json.dumps(variables_description, ensure_ascii=False)
        variables_output_format_json = json.dumps(variables_output_format, ensure_ascii=False)
        return VARIABLES_DESCRIPTION_TEMPLATE_PROMPT.replace("VARIABLES_DEFINE_TEMPLATE",
                                                             variables_description_json), \
            ",\n\"variables\":" + variables_output_format_json

    @staticmethod
    def _build_summary_prompt(
            max_message_token: int,
            memory_config: AgentMemoryConfig
    ) -> Tuple[str, str]:
        step_num = 2 if len(memory_config.mem_variables) == 0 else 3

        summary_prompt = SUMMARY_TEMPLATE_PROMPT.format(
            step_num=step_num,
            max_message_token=max_message_token
        )
        summary_output_format = ',\n\"summary\": ""'
        return summary_prompt, summary_output_format

    @staticmethod
    def _check_summary(
            summary_max_length_threshold: int,
            messages: List[BaseMessage],
    ) -> Tuple[bool, str]:
        messages_content_length = 0
        raw_summary = ""
        for msg in messages:
            messages_content_length += len(msg.content)
            raw_summary += f"{msg.role}: {msg.content}\n"
        if messages_content_length >= summary_max_length_threshold:
            return True, raw_summary
        return False, raw_summary
