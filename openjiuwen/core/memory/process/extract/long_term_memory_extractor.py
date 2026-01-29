# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import json
from typing import Dict, Any
from openjiuwen.core.memory.manage.mem_model.memory_unit import MemoryType
from openjiuwen.core.foundation.llm import JsonOutputParser
from openjiuwen.core.memory.process.extract.common import build_model_input
from openjiuwen.core.memory.process.extract.common import ExtractMemoryParams
from openjiuwen.core.memory.prompt.user_profile_extractor import (USER_PROFILE_JSON_FORMAT,
                                                                  USER_PROFILE_MULTI_USER_PROMPT)
from openjiuwen.core.memory.prompt.long_term_memory_extractor import LONG_TERM_MEMORY_EXTRACTOR_PROMPT
from openjiuwen.core.common.logging import memory_logger
from openjiuwen.core.common.logging.events import LogEventType


def handle_user_profile_prompt(sys_prompt: str, index: int, last_one: bool, user_define: Dict[str, str] = None) -> str:
    user_profile_prompt = ""
    user_profile_json_format = ""
    if index > 0:
        user_define_description = ""
        user_define_format = ""
        if user_define and len(user_define) > 0:
            for key in user_define.keys():
                value = user_define[key]
                user_define_description += f"    *   **{key}:** {value}等相关信息\n"
                user_define_format += f',\n    "{key}": []'
        user_profile_prompt = USER_PROFILE_MULTI_USER_PROMPT.format(
            index=index,
            user_define_description=user_define_description,
        )
        user_profile_json_format = USER_PROFILE_JSON_FORMAT.format(
            user_define_format=user_define_format,
            comma='' if last_one else ',',
        )
    sys_prompt = sys_prompt.replace("{USER_PROFILE_PROMPT}", user_profile_prompt)
    sys_prompt = sys_prompt.replace("{USER_PROFILE_JSON_FORMAT}", user_profile_json_format)
    return sys_prompt


def get_message(user_define: Dict[str, str] = None, categories: list[str] | None = None) -> str:
    sys_prompt = LONG_TERM_MEMORY_EXTRACTOR_PROMPT
    index = 1
    if "user_profile" in categories:
        sys_prompt = handle_user_profile_prompt(sys_prompt, index, index == len(categories), user_define)
        index += 1
    else:
        sys_prompt = handle_user_profile_prompt(sys_prompt, 0, index == len(categories))
    return sys_prompt


class LongTermMemoryExtractor:
    def __init__(self) -> None:
        pass

    @staticmethod
    async def extract_long_term_memory(
            categories: list[str],
            extract_memory_paras: ExtractMemoryParams,
            timestamp: str,
            user_define: dict[str, str] = None,
            retries: int = 3
    ) -> Dict[str, Any]:
        if not isinstance(categories, list) or len(categories) == 0:
            return {}
        for category in categories:
            try:
                MemoryType(category)
            except ValueError:
                memory_logger.warning(
                    "Invalid category, remove category",
                    event_type=LogEventType.MEMORY_PROCESS,
                    metadata={"category": category}
                )
                categories.remove(category)
        if len(categories) == 0:
            return {}
        sys_prompt = get_message(user_define, categories)
        model_input = build_model_input(
            extract_memory_paras.messages,
            extract_memory_paras.history_messages,
            sys_prompt,
            timestamp
        )
        model_name, model_client = extract_memory_paras.base_chat_model
        parser = JsonOutputParser()
        for attempt in range(retries):
            try:
                response = await model_client.invoke(
                    model=model_name,
                    messages=model_input
                )
                result = await parser.parse(response.content)
                if isinstance(result, dict):
                    return result
            except json.JSONDecodeError as e:
                if attempt < retries - 1:
                    continue
                memory_logger.error(
                    "Long term memory extractor model output format error",
                    event_type=LogEventType.MEMORY_PROCESS,
                    exception=str(e)
                )
        return {}
