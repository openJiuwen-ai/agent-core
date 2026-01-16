# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import re
import logging

from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.foundation.prompt.assemble.variables.variable import Variable

logger = logging.getLogger(__name__)


class TextableVariable(Variable):
    """Variable class for processing string-type placeholders"""
    def __init__(self, text: str, name: str = "default", prefix: str = "{{", suffix: str = "}}"):
        self.text = text
        self.name = name
        self.prefix = prefix
        self.suffix = suffix

        pattern = re.compile(re.escape(prefix) + r"([^{}]*?)" + re.escape(suffix))
        placeholders = []
        for match in pattern.finditer(text):
            placeholder = match.group(1).strip()
            if len(placeholder) == 0:
                raise JiuWenBaseException(
                    error_code=StatusCode.PROMPT_ASSEMBLER_VARIABLE_INIT_FAILED.code,
                    message=StatusCode.PROMPT_ASSEMBLER_VARIABLE_INIT_FAILED.errmsg.format(
                        error_msg="placeholders cannot be empty string"
                    )
                )
            if placeholder not in placeholders:
                placeholders.append(placeholder)

        input_keys = []
        for placeholder in placeholders:
            input_key = placeholder.split(".")[0]
            if input_key not in input_keys:
                input_keys.append(input_key)

        self.placeholders = placeholders
        self.input_keys = input_keys
        super().__init__(name, input_keys=input_keys)

    def update(self, **kwargs):
        """Replace placeholders in the text with passed-in key-values and update `self.value`

        Args:
            **kwargs: arguments passed in as key-value pairs for updating the variable.
        """
        formatted_text = self.text
        for placeholder in self.placeholders:
            value = kwargs
            try:
                for node in placeholder.split("."):
                    if isinstance(value, dict):
                        value = value.get(node)
                    else:
                        value = getattr(value, node)
            except Exception as e:
                raise JiuWenBaseException(
                    error_code=StatusCode.PROMPT_ASSEMBLER_VARIABLE_INIT_FAILED.code,
                    message=StatusCode.PROMPT_ASSEMBLER_VARIABLE_INIT_FAILED.errmsg.format(
                        error_msg=f"error parsing the placeholder `{placeholder}`"
                    )
                ) from e
            if not isinstance(value, (str, int, float, bool)):
                logger.info(f"Converting non-string value `{placeholder}` using str()."
                            f" Please check if the style is describe.")
            placeholder_str = f"{self.prefix}{placeholder}{self.suffix}"
            formatted_text = formatted_text.replace(placeholder_str, str(value))
        self.value = formatted_text