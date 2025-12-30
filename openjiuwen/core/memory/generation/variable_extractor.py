# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import json
from typing import Any, Tuple
from openjiuwen.core.utils.llm.base import BaseModelClient
from openjiuwen.core.utils.llm.messages import BaseMessage
from openjiuwen.core.utils.llm.output_parser.json_output_parser import JsonOutputParser
from openjiuwen.core.memory.config.config import MemoryConfig
from openjiuwen.core.memory.generation.common import build_model_input
from openjiuwen.core.memory.generation.memory_info import (
    ExtractedData,
    ExtractedDataType
)

from openjiuwen.core.memory.prompt.variable_extractor import EXTRACT_VARIABLES_PROMPT

from openjiuwen.core.common.logging import logger


class ComprehensionExtractor:
    def __init__(self):
        pass

    @staticmethod
    async def extract(
            messages: list[BaseMessage],
            history_summary: BaseMessage,
            base_chat_model: Tuple[str, BaseModelClient],
            config: MemoryConfig
    ) -> list[ExtractedData]:
        """Extract variables from the given messages using LLM.
        
        Args:
            messages (list[BaseMessage]): The current messages to extract variables from.
            history_summary (BaseMessage): The summary of historical messages.
            base_chat_model (BaseModelClient): The chat model to use for extraction.
            config (MemoryConfig): Configuration for the extraction process.
        
        Returns:
            list[ExtractedData]: A list of extracted data objects.
        """
        if config.mem_variables is None or len(config.mem_variables) == 0:
            logger.info("Memory variables not set.")
            return []
        variables_dict = {
            "variables_description": "",
            "variables_user": set()
        }
        variables_output_format = "{"
        cnt = 0
        for key in config.mem_variables:
            description = config.mem_variables[key]
            variables_dict["variables_user"].add(key)
            variables_dict["variables_description"] += f"{key}({description}),"
            if cnt != 0:
                variables_output_format += ","
            variables_output_format += f'"{key}": ' + '{"value": "string"}'
            cnt += 1
        variables_output_format += "}"

        sys_message = EXTRACT_VARIABLES_PROMPT.format(
            variables=variables_dict["variables_description"],
            variables_output_format=variables_output_format
        )

        history = history_summary.content if isinstance(history_summary.content, str) else ""
        model_input = build_model_input(
            messages=messages,
            history_messages=history,
            prompt=sys_message
        )
        logger.debug(f"Start to extract variables, input: {model_input}")
        model_name, model_client = base_chat_model
        response = await model_client.ainvoke(
            model_name,
            model_input
        )
        logger.debug(f"Succeed to call llm, content: {response.content}")
        # Parse response
        extract_result = []
        try:
            parser = JsonOutputParser()
            response = await parser.parse(response.content)
            if not response:
                logger.error(f"Failed to extract variables, response None")
                return []
            for key, value in response.items():
                key = str(key).strip()
                if not ComprehensionExtractor._check_value(value):
                    continue
                value = str(value.get("value", "")).strip()
                if len(value) > 0 and value.lower() != "null":
                    if key in variables_dict["variables_user"]:
                        extract_result.append(
                            ExtractedData(
                                type=ExtractedDataType.USER,
                                key=key,
                                value=value
                            )
                        )
            logger.debug(f"Succeed to extract variables, result: {extract_result}")
            return extract_result
        except Exception as e:
            logger.error(f"Failed to extract variables, with error: {str(e)}")
            return []

    @staticmethod
    def _check_value(value: Any) -> bool:
        if (value is None or not isinstance(value, dict) or value.get("value", "") is None
                or value.get("value", "").lower() == "none"):
            return False
        return True
