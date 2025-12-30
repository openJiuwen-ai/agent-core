# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import re
import copy
from typing import Union, List, Dict, Optional

from pydantic import BaseModel, Field


from openjiuwen.core.common.logging import logger
from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.utils.llm.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage, ToolMessage
from openjiuwen.core.utils.prompt.assemble.assembler import Assembler
from openjiuwen.core.utils.prompt.assemble.variables.textable import TEMPLATE_VARIABLE_PLACEHOLDER_PATTERN


message_map = {
    "user": HumanMessage,
    "assistant": AIMessage,
    "system": SystemMessage,
    "tool": ToolMessage
}


class Template(BaseModel):
    """
    template data

    """
    name: str = Field(default='')
    content: Union[List[Dict], List[BaseMessage], str]
    filters: Optional[dict] = Field(default=None)

    def to_messages(self) -> List[BaseMessage]:
        """Return Template as a list of Messages."""
        messages = []
        if self.content is None or len(self.content) == 0:
            self.content = []
            return messages

        if isinstance(self.content, str):
            messages.append(HumanMessage(content=self.content))
            return messages

        for msg in self.content:
            if isinstance(msg, BaseMessage):
                messages.append(copy.deepcopy(msg))
            elif isinstance(msg, dict):
                message_cls = message_map.get(msg.get("role", ""))
                if message_cls:
                    messages.append(message_cls(**msg))
            else:
                raise JiuWenBaseException(
                    error_code=StatusCode.PROMPT_TEMPLATE_INCORRECT_ERROR.code,
                    message=f"Template type must be in str, list[dict] or list[BaseMessage]."
                )
        self._validate_template_content_assembled()
        return messages

    def format(self, keywords: dict[str, str] = None) -> "Template":
        """format prompt"""
        assembler = Assembler(copy.deepcopy(self.content))
        input_keys = assembler.input_keys
        format_dict = {}
        for key in input_keys:
            if keywords and keywords.get(key) is not None:
                format_dict[key] = keywords.get(key)
        content = assembler.assemble(**format_dict)
        return Template(name=self.name, content=content, filters=self.filters)

    def _validate_template_content_assembled(self):
        if isinstance(self.content, str):
            placeholder_matches = re.findall(TEMPLATE_VARIABLE_PLACEHOLDER_PATTERN, self.content)
            if placeholder_matches:
                logger.warning(f"template content has not assembled "
                            f"with variable placeholders: {len(placeholder_matches)}")
            return
        for message in self.content:
            content = message.content if isinstance(message, BaseMessage) else message.get('content', '')
            placeholder_matches = re.findall(TEMPLATE_VARIABLE_PLACEHOLDER_PATTERN, content)
            if placeholder_matches:
                logger.warning(f"template content has not assembled "
                            f"with variable placeholders: {len(placeholder_matches)}")
        return
