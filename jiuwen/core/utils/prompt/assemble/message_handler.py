#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import json
import re

from jiuwen.core.common.exception.exception import JiuWenBaseException
from jiuwen.core.common.exception.status_code import StatusCode

MESSAGE_VALIDATION_SCHEMA = {
    "system": {
        "role": str,
        "content": str
    },
    "assistant": {
        "role": str,
        "content": (type(None), str),
        "function_call": (type(None), dict)
    },
    "user": {
        "role": str,
        "content": str
    },
    "function": {
        "role": str,
        "content": str,
        "name": str
    }
}

EXTRA_VALIDATION_SCHEMA = {
    "function_call": {
        "name": str,
        "arguments": str
    }
}


def validate(data: dict, schema: dict):
    """validate data"""
    if len(set(data.keys()) - set(schema.keys())) > 0:
        raise JiuWenBaseException(
            error_code=StatusCode.PROMPT_ASSEMBLER_TEMPLATE_FORMAT_ERROR.code,
            message="Failed validate the data against the schema."
        )
    for name, data_type in schema.items():
        if not isinstance(data.get(name), data_type):
            raise JiuWenBaseException(
                error_code=StatusCode.PROMPT_ASSEMBLER_TEMPLATE_FORMAT_ERROR.code,
                message=f"Failed validate the data against the schema."
            )
        if name in EXTRA_VALIDATION_SCHEMA and data.get(name) is not None:
            validate(data.get(name), EXTRA_VALIDATION_SCHEMA.get(name))


def padding_message(message_prefix, message_content, validation_schema):
    """message padding"""
    key_role = "role"
    key_content = "content"
    message = {
        key_role: message_prefix,
        key_content: message_content
    }
    extra_fields_matches = list(re.finditer(r'`\*(name|function_call)\*`', message_content))
    for field_index, field_match in enumerate(extra_fields_matches):
        field_name = field_match.group(1)
        field_start = field_match.end()
        if field_index < len(extra_fields_matches) - 1:
            field_end = extra_fields_matches[field_index + 1].start()
        else:
            field_end = len(message_content)
        field_content = message_content[field_start:field_end].strip()
        try:
            data_type = validation_schema.get(field_name)
            if (isinstance(data_type, tuple) and dict in data_type) or dict == data_type:
                field_content = json.loads(field_content)
        except Exception as e:
            raise JiuWenBaseException(
                error_code=StatusCode.PROMPT_ASSEMBLER_TEMPLATE_FORMAT_ERROR.code,
                message=f"Errors occur when parsing field `{field_name}` into dict."
            ) from e
        message[field_name] = field_content
        validate(message, MESSAGE_VALIDATION_SCHEMA.get(message_prefix))
        if field_index == 0:
            message[key_content] = message_content[:field_match.start()].strip()
            if len(message[key_content]) == 0:
                message[key_content] = None
    return message

