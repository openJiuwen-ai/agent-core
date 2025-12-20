#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import re
import json
from ast import literal_eval
from typing import Optional, List, Dict, Any

from openjiuwen.core.common.logging import logger
from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.utils.prompt.template.template import Template
from openjiuwen.core.utils.llm.messages import BaseMessage, AIMessage
from openjiuwen.agent_builder.tune.base import Case, EvaluatedCase


class TuneUtils:
    @staticmethod
    def validate_digital_parameter(param: float, param_name: str, lower: float, upper: float):
        if param < lower or param > upper:
            raise JiuWenBaseException(
                StatusCode.AGENT_BUILDER_AGENT_PARAMS_ERROR.code,
                StatusCode.AGENT_BUILDER_AGENT_PARAMS_ERROR.errmsg.format(
                    error_msg=f"{param_name} should be between {lower} and {upper}"
                )
            )

    @staticmethod
    def get_input_string_from_case(case: Case):
        messages_content = []
        for message in case.messages:
            if isinstance(message, AIMessage) and message.tool_calls:
                content = "".join(json.dumps(tool_call.model_dump()) for tool_call in message.tool_calls)
            else:
                content = message.content
            messages_content.append(f"[{message.role}]: {content}")
        input_string = "\n".join(messages_content)
        if case.variables:
            input_string += f"\nvariables: {str(case.variables)}\n"
        return input_string

    @staticmethod
    def get_output_string_from_message(message: BaseMessage):
        if isinstance(message, AIMessage) and message.tool_calls:
            return "".join("".join(json.dumps(tool_call.model_dump(include={"name", "arguments"}))
                                   for tool_call in message.tool_calls))
        return message.content

    @staticmethod
    def get_content_string_from_template(template: Template):
        return "\n".join(msg.content for msg in template.to_messages())

    @staticmethod
    def parse_json_from_llm_response(json_like_string: str) -> Optional[Dict[str, Any]]:
        pattern = r"```json(.*?)```"
        match = re.search(pattern, json_like_string, re.DOTALL)
        if not match:
            logger.warning("Failed to extract json string from response")
            return None

        matched_json_string = match.group(1).strip()
        try:
            json_data = json.loads(matched_json_string)
        except json.decoder.JSONDecodeError:
            logger.warning("Failed to decode json string")
            return None
        return json_data

    @staticmethod
    def parse_list_from_llm_response(list_like_string: str) -> Optional[List[Any]]:
        pattern = r"```list(.*?)```"
        match = re.search(pattern, list_like_string, re.DOTALL)
        if not match:
            logger.warning("Failed to extract list string from response")
            return None

        matched_list_string = match.group(1).strip()
        try:
            list_data = literal_eval(matched_list_string)
        except Exception:
            logger.warning("Failed to convert list string to python list")
            return None
        if not isinstance(list_data, list):
            logger.warning("Parsed data is not a list-type")

        return list_data

    @staticmethod
    def convert_cases_to_examples(cases: List[Case | EvaluatedCase]) -> str:
        if not cases:
            return ""
        examples_list = [
            f"example {i + 1}:\n" \
            f"[question]: {TuneUtils._convert_dict_to_string(case.inputs)}\n" \
            f"[expected answer]: {TuneUtils._convert_dict_to_string(case.label)}"
            for i, case in enumerate(cases)
        ]
        return "\n".join(examples_list)

    @staticmethod
    def _convert_dict_to_string(data: Dict) -> str:
        return " | ".join(f"{key}:{value}" for key, value in data.items())