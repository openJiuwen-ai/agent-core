# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import json
from typing import List, Dict, Tuple, Any

from openjiuwen.core.common.logging import logger
from openjiuwen.core.memory.process.extract.common import build_model_input
from openjiuwen.core.memory.prompt.user_profile_extractor import USER_PROFILE_EXTRACTOR_PROMPT
from openjiuwen.core.foundation.llm import BaseMessage, JsonOutputParser, Model


def _get_message(user_define: Dict[str, str] = None) -> str:
    if user_define and len(user_define) > 0:
        user_define_description = ""
        user_define_format = ""
        for key in user_define.keys():
            value = user_define[key]
            user_define_description += f"    *   **{key}:** {value}等相关信息\n"
            user_define_format += f',\n    "{key}": []'
        sym_prompt = USER_PROFILE_EXTRACTOR_PROMPT.format(
            user_define_description=user_define_description,
            user_define_format=user_define_format
        )
    else:
        sym_prompt = USER_PROFILE_EXTRACTOR_PROMPT.format(
            user_define_description="",
            user_define_format=""
        )
    return sym_prompt


class UserProfileExtractor:
    def __init__(self) -> None:
        pass

    @staticmethod
    async def get_user_profile(
            messages: List[BaseMessage],
            history_messages: List[BaseMessage],
            base_chat_model: Tuple[str, Model],
            user_define: Dict[str, str] = None,
            retries: int = 3
    ) -> Dict[str, Any]:
        sym_prompt = _get_message(user_define)
        model_input = build_model_input(
            messages,
            history_messages,
            sym_prompt
        )
        logger.debug(f"Start to get user profile, input: {model_input}")
        model_name, model_client = base_chat_model
        parser = JsonOutputParser()
        for attempt in range(retries):
            try:
                response = await model_client.invoke(model=model_name, messages=model_input)
                result = await parser.parse(response.content)
                logger.debug(f"Succeed to get user profile, result: {result}")
                if isinstance(result, dict):
                    return result
            except json.JSONDecodeError as e:
                if attempt < retries - 1:
                    continue
                logger.error(f"user profile extractor model output format error: {e.msg}")
        return {}
