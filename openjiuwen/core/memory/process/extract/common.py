# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from dataclasses import dataclass
from typing import List, Tuple
from openjiuwen.core.foundation.llm import BaseMessage
from openjiuwen.core.foundation.llm import Model
from openjiuwen.core.memory.prompt.common import HISTORY_INPUT, CONVERSATION_TIME_INPUT


def build_model_input(messages: List[BaseMessage],
                      history_messages: List[BaseMessage] | str,
                      prompt: str,
                      timestamp: str) -> List[dict]:
    history = ""
    if isinstance(history_messages, str):
        history = history_messages
    elif isinstance(history_messages, List):
        if history_messages and len(history_messages) > 0:
            for msg in history_messages:
                if msg.name:
                    history += f"{msg.name}: {msg.content}\n"
                else:
                    history += f"{msg.role}: {msg.content}\n"
    conversation = ""
    for msg in messages:
        if msg.name:
            conversation += f"{msg.name}: {msg.content}\n"
        else:
            conversation += f"{msg.role}: {msg.content}\n"
    model_input = [{
        "role": "system",
        "content": prompt
    }]

    user_input = ""
    if history != "":
        user_input += HISTORY_INPUT.replace("history_input", history)
    user_input += (CONVERSATION_TIME_INPUT.replace("conversation_input", conversation)
                   .replace("timestamp_input", timestamp))
    model_input.append({
        "role": "user",
        "content": user_input
    })
    return model_input


@dataclass
class ExtractMemoryParams:
    user_id: str
    scope_id: str
    messages: list[BaseMessage]
    history_messages: list[BaseMessage]
    base_chat_model: Tuple[str, Model]
