#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import json
from typing import List

from jiuwen.core.common.logging import logger
from jiuwen.core.utils.llm.messages import BaseMessage


class FormatUtils:
    @staticmethod
    def create_llm_inputs(system_prompt: List[BaseMessage], chat_history: List[BaseMessage]) -> List[BaseMessage]:
        result_messages = []

        if not chat_history or chat_history[0].role != "system":
            result_messages.extend(system_prompt)

        result_messages.extend(chat_history)

        return result_messages

    @staticmethod
    def json_loads(arguments: str) -> dict:
        result = dict()
        try:
            result = json.loads(arguments, strict=False)
        except json.JSONDecodeError:
            logger.error(f"JSON parser error.")
        return result