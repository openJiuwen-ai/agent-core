# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import re

from openjiuwen.core.common.logging import prompt_logger, LogEventType
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.foundation.prompt.assemble.variables.variable import Variable


class TextableVariable(Variable):
    """Variable class for processing string-type placeholders.

    An empty placeholder (for example ``{{}}``) is treated as literal text
    with a warning instead of raising an error.
    """
    def __init__(self, text: str, name: str = "default", prefix: str = "{{", suffix: str = "}}"):
        self.text = text
        self.name = name
        self.prefix = prefix
        self.suffix = suffix

        pattern = re.compile(re.escape(prefix) + r"([^{}]*?)" + re.escape(suffix))
        placeholders = []
        # Split the template into ordered segments (literal text vs placeholder)
        # so that rendering only touches literal text and never rewrites the
        # substituted values.
        segments = []
        cursor = 0
        for match in pattern.finditer(text):
            placeholder = match.group(1).strip()
            if len(placeholder) == 0:
                prompt_logger.warning(
                    "Empty placeholder found; treating as literal text",
                    metadata={"name": name, "placeholder": match.group(0)}
                )
                # cursor not advanced: empty placeholder retained as literal text
                continue
            segments.append(("text", text[cursor:match.start()]))
            segments.append(("variable", placeholder))
            cursor = match.end()
            if placeholder not in placeholders:
                placeholders.append(placeholder)
        segments.append(("text", text[cursor:]))

        input_keys = []
        for placeholder in placeholders:
            input_key = placeholder.split(".")[0]
            if input_key not in input_keys:
                input_keys.append(input_key)

        self.placeholders = placeholders
        self.input_keys = input_keys
        self._segments = segments
        super().__init__(name, input_keys=input_keys)

    def update(self, **kwargs):
        """Replace placeholders in the text with passed-in key-values and update `self.value`

        Args:
            **kwargs: arguments passed in as key-value pairs for updating the variable.
        """
        parts = []
        for kind, payload in self._segments:
            if kind == "text":
                parts.append(payload)
                continue
            value = kwargs
            try:
                for node in payload.split("."):
                    if isinstance(value, dict):
                        value = value.get(node)
                    else:
                        value = getattr(value, node)
            except Exception as e:
                raise build_error(
                    StatusCode.PROMPT_ASSEMBLER_VARIABLE_INIT_FAILED,
                    error_msg=f"error parsing the placeholder `{payload}`",
                    cause=e
                ) from e
            if value is None:
                prompt_logger.warning(
                    "Placeholder resolved to None; keeping original placeholder text",
                    metadata={"name": self.name, "placeholder": payload}
                )
                parts.append(f"{self.prefix}{payload}{self.suffix}")
                continue
            if not isinstance(value, (str, int, float, bool)):
                prompt_logger.info(
                    "Converting non-string value using str()."
                    "Please check if the style is describe.",
                    type_event=LogEventType.AGENT_START,
                    input_data=kwargs,
                    output_data=self.value,
                    metadata={"placeholder": payload}
                    )
            parts.append(str(value))
        formatted_text = "".join(parts)
        self.value = formatted_text
