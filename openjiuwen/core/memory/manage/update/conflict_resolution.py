# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import json
from typing import List, Tuple
from openjiuwen.core.memory.prompt.conflict_resolution import CONFLICT_RESOLUTION_PROMPT
from openjiuwen.core.foundation.llm import Model, JsonOutputParser
from openjiuwen.core.memory.manage.mem_model.memory_unit import ConflictType
from openjiuwen.core.common.logging import memory_logger
from openjiuwen.core.common.logging.events import LogEventType


def _get_message(old_messages: List[str], new_message: str) -> list[dict]:
    new_msg_input = {
        "id": "0",
        "text": new_message,
        "event": "operation"
    }

    old_msg_input = []
    for index, old_message in enumerate(old_messages, start=1):
        old_msg_input.append({
            "id": str(index),
            "text": old_message,
            "event": "operation"
        })
    user_input = {
        "new_message": new_msg_input,
        "old_messages": old_msg_input,
    }
    user_message = (f"现在开始：请根据设定的规则处理以下输入并生成输出：\n"
                    f"```json"
                    f"{json.dumps(user_input, ensure_ascii=False)}"
                    f"```")

    return [
        {"role": "system", "content": CONFLICT_RESOLUTION_PROMPT},
        {"role": "user", "content": user_message}
    ]


class ConflictResolution:
    def __init__(self):
        pass

    @staticmethod
    async def check_conflict(
            old_messages: List[str],
            new_message: str,
            base_chat_model: Tuple[str, Model],
            retries: int = 3
    ) -> list[dict]:
        """
        Check for conflicts between old messages and a new message.

        Args:
            old_messages (List[str]): List of old messages.
            new_message (str): The new message to check against old messages.
            base_chat_model (Tuple[str, Model]): The chat model to use for processing.
            retries (int, optional): Number of retries for the operation. Defaults to 3.

        Returns:
            list[dict]: A list of dictionaries representing the conflict resolution results.
        """
        if len(old_messages) == 0 or not base_chat_model:
            memory_logger.debug(
                "No need to check conflict",
                event_type=LogEventType.MEMORY_PROCESS,
                metadata={"msg_len": len(old_messages)}
            )
            return [
                {
                    "id": "0",
                    "text": new_message,
                    "event": ConflictType.ADD.value,
                }
            ]
        if new_message in old_messages:
            memory_logger.debug(
                "New message found in old messages",
                event_type=LogEventType.MEMORY_PROCESS,
                metadata={"new_message": new_message, "old_messages": old_messages}
            )
            return [
                {
                    "id": "0",
                    "text": new_message,
                    "event": ConflictType.NONE.value,
                }
            ]
        model_name, model_client = base_chat_model
        messages = _get_message(old_messages, new_message)
        memory_logger.debug(
            "Start checking conflict",
            event_type=LogEventType.MEMORY_PROCESS,
            metadata={"input_messages": messages}
        )
        parser = JsonOutputParser()
        for attempt in range(retries):
            try:
                response = await model_client.invoke(model=model_name, messages=messages)
                result = await parser.parse(str(response.content).strip().replace("'", '"'))
                memory_logger.debug(
                    "Succeed to check conflict",
                    event_type=LogEventType.MEMORY_PROCESS,
                    metadata={"result": result}
                )
                if not isinstance(result, dict):
                    continue
                output = []
                if "new_message" in result:
                    output.append(result["new_message"])
                if "old_messages" in result and isinstance(result["old_messages"], list):
                    output.extend(result["old_messages"])
                if output:
                    return output
            except json.JSONDecodeError as e:
                if attempt <= retries - 1:
                    continue
                memory_logger.error(
                    "Categories model output format error",
                    event_type=LogEventType.MEMORY_PROCESS,
                    exception=str(e)
                )
        return []
