# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import json
from typing import List, Tuple
from openjiuwen.core.foundation.llm import BaseMessage, JsonOutputParser, Model
from openjiuwen.core.common.logging import logger
from openjiuwen.core.memory.process.extract.common import build_model_input
from openjiuwen.core.memory.prompt.categorizer import CATEGORIZATION_PROMPT


class Categorizer:
    def __init__(self) -> None:
        pass

    @staticmethod
    async def get_categories(
            messages: List[BaseMessage],
            history_messages: List[BaseMessage],
            base_chat_model: Tuple[str, Model],
            retries: int = 3
    ) -> List[str]:
        model_input = build_model_input(
            messages,
            history_messages,
            CATEGORIZATION_PROMPT,
        )
        model_name, model_client = base_chat_model
        logger.debug(f"Start to get categories, input: {model_input}")
        parser = JsonOutputParser()
        for attempt in range(retries):
            try:
                response = await model_client.invoke(model=model_name, messages=model_input)
                categories = await parser.parse(response.content)
                logger.debug(f"Succeed to get categories, result: {categories}")
                if isinstance(categories, dict) and "categories" in categories.keys():
                    return categories["categories"]
            except json.JSONDecodeError as e:
                if attempt < retries - 1:
                    continue
                logger.error(f"categories model output format error: {e.msg}")
        return []
