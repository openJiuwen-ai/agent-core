# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Dialogue-round boundaries shared by window truncation and PTL recovery.

This module is deliberately dependency-free (only the message schema) so the
PTL capability and ``ContextUtils`` can both use it without importing the
context-engine package graph.
"""

from __future__ import annotations

from typing import List, Optional

from openjiuwen.core.foundation.llm import BaseMessage


def find_all_dialogue_round(
    messages: List[BaseMessage],
) -> List[List[Optional[int]]]:
    """
    Build all dialogue round boundaries by scanning messages from end to start.

    A round is defined as: user message → next assistant message without tool_calls.
    Incomplete rounds (no final assistant) still count as one round.

    Args:
        messages: List of BaseMessage objects

    Returns:
        List of rounds, each round is [user_idx, assistant_idx].
        assistant_idx may be None for incomplete rounds.
        Order is from newest round to oldest (index 0 = last round).
    """
    rounds: List[List[Optional[int]]] = []
    i = len(messages) - 1

    def find_contiguous_user_group_start(user_idx: int) -> int:
        while user_idx - 1 >= 0 and messages[user_idx - 1].role == "user":
            user_idx -= 1
        return user_idx

    while i >= 0:
        # Find the closing assistant of this round (may not exist)
        assistant_idx = None
        round_end = i

        # Skip any trailing non-assistant messages (shouldn't happen in valid data)
        while i >= 0 and messages[i].role != "assistant":
            i -= 1

        if i >= 0:
            # Found assistant, check if it has tool_calls
            msg = messages[i]
            # tool_calls indicated by content type or metadata (adapt as needed)
            has_tool_calls = msg.role == "assistant" and hasattr(msg, "tool_calls") and msg.tool_calls

            if not has_tool_calls:
                # This assistant closes a round
                assistant_idx = i

            # Move to find the user that started this round
            i -= 1
        else:
            # No assistant found in this remaining prefix. Treat the latest
            # user plus any following messages as an incomplete round.
            i = round_end

        # Now find the user message that starts this round
        while i >= 0 and messages[i].role != "user":
            i -= 1

        if i < 0:
            # No user found, incomplete round at start
            break

        found_user_idx = i
        user_idx = find_contiguous_user_group_start(found_user_idx)

        if not rounds:
            # Find incomplete round
            for last_round_index in range(len(messages) - 1, found_user_idx, -1):
                if messages[last_round_index].role == "user":
                    rounds.append([find_contiguous_user_group_start(last_round_index), None])
                    break

        rounds.append([user_idx, assistant_idx])
        i = user_idx - 1  # Continue before the contiguous user-message group

    return rounds


__all__ = ["find_all_dialogue_round"]
