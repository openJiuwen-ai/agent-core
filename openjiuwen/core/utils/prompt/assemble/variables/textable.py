#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import re

from openjiuwen.core.common.logging import logger
from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.utils.prompt.assemble.variables.variable import Variable

TEMPLATE_VARIABLE_PLACEHOLDER_PATTERN = r"\{\{([^{}]*)\}\}"


class TextableVariable(Variable):
    """Variable class for processing string-type placeholders"""
    def __init__(self, text: str, name: str = "default"):
        clean_text = text
        placeholders = []
        input_keys = []
        placeholder_matches = re.finditer(TEMPLATE_VARIABLE_PLACEHOLDER_PATTERN, text)
        for match in placeholder_matches:
            placeholder = match.group(1).strip()
            if len(placeholder) == 0:
                raise JiuWenBaseException(
                    error_code=StatusCode.PROMPT_ASSEMBLER_VARIABLE_INIT_ERROR.code,
                    message="Placeholders cannot be empty string"
                )
            if placeholder not in placeholders:
                placeholders.append(placeholder)
            input_key = placeholder.split(".")[0]
            if input_key not in input_keys:
                input_keys.append(input_key)
            clean_text = clean_text.replace(match.group(0), "{{" + placeholder + "}}")
        self.text = clean_text
        self.placeholders = placeholders
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
                    error_code=StatusCode.PROMPT_ASSEMBLER_VARIABLE_INIT_ERROR.code,
                    message=f"Error parsing the placeholder `{placeholder}`."
                ) from e
            if not isinstance(value, (str, int, float, bool)):
                logger.info(f"Converting non-string value `{placeholder}` using str()."
                            f" Please check if the style is describe.")
            formatted_text = formatted_text.replace("{{" + placeholder + "}}", str(value))
        self.value = formatted_text
