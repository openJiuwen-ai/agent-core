import re
from typing import List, Any, Union
import copy

from openjiuwen.core.common.logging import prompt_logger, LogEventType
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.foundation.prompt.assemble.variables.variable import Variable


class DictableVariable(Variable):
    """Variable class for processing dict or list type placeholders recursively.

    An empty placeholder (for example ``{{}}``) is treated as literal text
    with a warning instead of raising an error.
    """

    def __init__(self, data: Union[dict, list], name: str = "default", prefix: str = "{{", suffix: str = "}}"):
        self.data = data
        self.name = name
        self.prefix = prefix
        self.suffix = suffix
        self.pattern = re.compile(re.escape(prefix) + r"([^{}]*?)" + re.escape(suffix))

        placeholders = []
        self._scan_placeholders(self.data, placeholders)

        input_keys = []
        for placeholder in placeholders:
            input_key = placeholder.split(".")[0]
            if input_key not in input_keys:
                input_keys.append(input_key)

        self.placeholders = placeholders
        self.input_keys = input_keys
        super().__init__(name, input_keys=input_keys)

    def _scan_placeholders(self, obj: Any, placeholders: List[str]):
        if isinstance(obj, str):
            for match in self.pattern.finditer(obj):
                placeholder = match.group(1).strip()
                if len(placeholder) == 0:
                    prompt_logger.warning(
                        "Empty placeholder found; treating as literal text",
                        metadata={"name": self.name, "placeholder": match.group(0)}
                    )
                    continue
                if placeholder not in placeholders:
                    placeholders.append(placeholder)
        elif isinstance(obj, list):
            for item in obj:
                self._scan_placeholders(item, placeholders)
        elif isinstance(obj, dict):
            for value in obj.values():
                self._scan_placeholders(value, placeholders)

    def update(self, **kwargs):
        """Recursively replace placeholders in the dict/list structure and update `self.value`"""
        data_copy = copy.deepcopy(self.data)
        self.value = self._recursive_format(data_copy, kwargs)
        return self.value

    def _recursive_format(self, obj: Any, kwargs: dict) -> Any:
        if isinstance(obj, list):
            return [self._recursive_format(item, kwargs) for item in obj]

        if isinstance(obj, dict):
            return {k: self._recursive_format(v, kwargs) for k, v in obj.items()}

        if not isinstance(obj, str):
            return obj

        # Split the string into ordered segments (literal text vs placeholder)
        # so that rendering only touches literal text and never rewrites the
        # substituted values.
        parts = []
        cursor = 0
        for match in self.pattern.finditer(obj):
            placeholder = match.group(1).strip()
            if len(placeholder) == 0:
                # cursor not advanced: empty placeholder retained as literal text
                continue
            parts.append(obj[cursor:match.start()])
            value = kwargs
            try:
                for node in placeholder.split("."):
                    if isinstance(value, dict):
                        value = value.get(node)
                    else:
                        value = getattr(value, node)
            except Exception as e:
                raise build_error(
                    StatusCode.PROMPT_ASSEMBLER_VARIABLE_INIT_FAILED,
                    error_msg=f"error parsing the placeholder `{placeholder}`",
                    cause=e
                ) from e

            if value is None:
                prompt_logger.warning(
                    "Placeholder resolved to None; keeping original placeholder text",
                    metadata={"name": self.name, "placeholder": placeholder}
                )
                parts.append(obj[match.start():match.end()])
                cursor = match.end()
                continue

            if not isinstance(value, (str, int, float, bool)):
                prompt_logger.info(
                    "Converting non-string value using str()."
                    "Please check if the style is describe.",
                    type_event=LogEventType.AGENT_START,
                    input_data=kwargs,
                    output_data=None,
                    metadata={"placeholder": placeholder}
                )

            parts.append(str(value))
            cursor = match.end()
        parts.append(obj[cursor:])
        return "".join(parts)
