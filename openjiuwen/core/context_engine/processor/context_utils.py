# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import Optional, List, Dict, Any


from openjiuwen.core.foundation.llm import BaseMessage


class ContextUtils:
    """
    Utility helper functions for manipulating and parsing conversation contexts.
    All methods are static and stateless.
    """

    @staticmethod
    def find_last_ai_message_without_tool_call(
        messages: List[BaseMessage],
    ) -> Optional[int]:
        """
        Return the index of the most-recent **assistant** message that
        **does not** contain any tool-call field.

        Search is performed backwards (newest → oldest).
        If no qualifying message exists, return None.
        """
        if not messages:
            return None

        for idx in range(len(messages) - 1, -1, -1):
            msg = messages[idx]
            if msg.role == "assistant":
                if not getattr(msg, "tool_call", None):
                    return idx
        return None

    @staticmethod
    def replace_messages(
        messages: List[BaseMessage],
        target_messages: List[BaseMessage],
        start_index: int,
        end_index: int,
    ) -> List[BaseMessage]:
        """
        Return a **new** list where the slice
        `messages[start_index : end_index + 1]`
        is replaced by `target_messages`.

        Works for single or multiple message replacement.
        Raises IndexError if indices are out of range or inverted.
        """
        if start_index < 0 or end_index >= len(messages) or start_index > end_index:
            raise IndexError("Invalid start/end index")

        return messages[:start_index] + target_messages + messages[end_index + 1:]
