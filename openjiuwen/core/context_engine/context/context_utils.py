# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import Optional, List, Dict, Any
import json
import uuid

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.context_engine.base import ContextWindow
from openjiuwen.core.foundation.llm import BaseMessage, ToolMessage, AssistantMessage


CONTEXT_MESSAGE_ID_KEY = "context_message_id"
DEFAULT_CONTEXT_MAX_TOKENS = 200000

MODEL_DEFAULT_CONTEXT_WINDOW_TOKENS: Dict[str, int] = {
    # GLM
    "glm-5": 200000,
    "glm-4-long": 200000,
    "glm-4": 128000,
    "glm-4-9b-chat-1m": 1048576,
    # OpenAI GPT
    "gpt-5.4": 1100000,
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
    "gpt-4-turbo": 128000,
    "gpt-3.5-turbo": 16384,
    # DeepSeek
    "deepseek-v3": 128000,
    "deepseek-chat": 65536,
    # Anthropic Claude
    "claude-opus-4.6": 1000000,
    "claude-sonnet-4.6": 1000000,
    "claude-haiku-4.6": 200000,
    # Google Gemini
    "gemini-3.1-pro": 2000000,
    "gemini-2.5-pro": 1000000,
    "gemini-2.5-flash": 1000000,
    # Meta Llama
    "llama-4-maverick": 1000000,
    "llama-4-scout": 10000000,
    # Qwen
    "qwen-max": 32000,
    "qwen-plus": 131072,
    "qwen-turbo": 8192,
    "qwen-long": 1000000,
}


class ContextUtils:
    """
    Utility helper functions for manipulating and parsing conversation contexts.
    All methods are static and stateless.
    """

    @staticmethod
    def validate_messages(messages: BaseMessage | List[BaseMessage]) -> None:
        if isinstance(messages, BaseMessage):
            return
        if isinstance(messages, list):
            for msg in messages:
                if not isinstance(msg, BaseMessage):
                    raise build_error(
                        StatusCode.CONTEXT_MESSAGE_INVALID,
                        error_msg="messages should be a BaseMessage or a list of BaseMessage"
                    )
            return
        raise build_error(
            StatusCode.CONTEXT_MESSAGE_INVALID,
            error_msg="messages should be a BaseMessage or a list of BaseMessage"
        )

    @staticmethod
    def ensure_context_message_ids(messages: List[BaseMessage]) -> List[BaseMessage]:
        for msg in messages:
            metadata = getattr(msg, "metadata", None)
            if not isinstance(metadata, dict):
                metadata = {}
                setattr(msg, "metadata", metadata)
            if not metadata.get(CONTEXT_MESSAGE_ID_KEY):
                metadata[CONTEXT_MESSAGE_ID_KEY] = uuid.uuid4().hex
        return messages

    @staticmethod
    def validate_and_fix_context_window(context_window: ContextWindow) -> None:
        messages: List[BaseMessage] = context_window.context_messages
        if not messages:
            return

        first_non_tool = 0
        while first_non_tool < len(messages) and isinstance(messages[first_non_tool], ToolMessage):
            first_non_tool += 1

        if first_non_tool == len(messages):
            context_window.context_messages = []
            return

        if first_non_tool > 0:
            context_window.context_messages = messages[first_non_tool:]

    @staticmethod
    def resolve_context_max(
        model_name: Optional[str] = None,
        fallback_context_window_tokens: Optional[int] = None,
        model_context_window_tokens: Optional[Dict[str, int]] = None,
    ) -> int:
        """Resolve the maximum context window size in tokens.

        Priority order:
        1. Explicit ``fallback_context_window_tokens`` if set and positive.
        2. Look up ``model_name`` in ``model_context_window_tokens`` dict.
        3. Look up ``model_name`` in the built-in ``MODEL_DEFAULT_CONTEXT_WINDOW_TOKENS`` dict.
        4. Return ``DEFAULT_CONTEXT_MAX_TOKENS`` (200000).
        """
        if isinstance(fallback_context_window_tokens, int) and fallback_context_window_tokens > 0:
            return fallback_context_window_tokens

        if isinstance(model_name, str) and model_name:
            if model_context_window_tokens:
                value = model_context_window_tokens.get(model_name)
                if isinstance(value, int) and value > 0:
                    return value
            builtin = MODEL_DEFAULT_CONTEXT_WINDOW_TOKENS.get(model_name)
            if isinstance(builtin, int) and builtin > 0:
                return builtin

        return DEFAULT_CONTEXT_MAX_TOKENS

    @staticmethod
    def is_compression_processor(processor: Any) -> bool:
        processor_type = processor.processor_type().lower()
        module_name = processor.__class__.__module__.lower()
        return (
            "compressor" in processor_type
            or "compact" in processor_type
            or ".processor.compressor." in module_name
        )

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

    @staticmethod
    def format_reloaded_messages(
            offload_handle: str,
            messages: List[BaseMessage]
    ):
        """
        Format a list of reloaded messages into a human-readable string for LLM consumption.

        This method creates a structured text representation of messages that were
        previously offloaded and have now been retrieved. The formatted output
        includes the offload handle for traceability and each message serialized
        as JSON for structured parsing by the model.

        Args:
            offload_handle: The unique identifier of the offloaded content being
                restored. Used to correlate the reloaded content with its original
                offload marker (e.g., [[OFFLOAD: handle=xxx, type=...]]).
            messages: List of BaseMessage objects that have been retrieved from
                external storage and need to be presented back to the LLM.

        Returns:
            A formatted string containing the handle reference and serialized
            messages, suitable for injection back into the conversation context.
        """
        formatted_content = f"reload messages with handle={offload_handle}:\n"
        for i, msg in enumerate(messages, 1):
            formatted_content += f"message {i}: "
            formatted_content += json.dumps(msg.model_dump(), ensure_ascii=False)
            if i != len(messages):
                formatted_content += "\n"
        return formatted_content

    @staticmethod
    def find_all_dialogue_round(messages: List[BaseMessage]) -> List[List[Optional[int]]]:
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
                has_tool_calls = (
                    msg.role == "assistant"
                    and hasattr(msg, "tool_calls")
                    and msg.tool_calls
                )

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

    @staticmethod
    def find_last_n_dialogue_round(
            messages: List[BaseMessage],
            n: int
    ) -> int:
        """
        Find the starting index of the n-th conversation round from the end.

        A round is defined as: user message → next assistant message without tool_calls.
        Incomplete rounds (no final assistant) still count as one round.

        Args:
            messages: List of BaseMessage objects
            n: Which round from the end (1 = last round, 2 = second-to-last, etc.)

        Returns:
            Starting index of the target round in the messages list

        Raises:
            ValueError: If n <= 0 or fewer than n rounds exist
        """
        rounds = ContextUtils.find_all_dialogue_round(messages)
        if not rounds:
            return -1
        target_round = rounds[min(n, len(rounds)) - 1]
        return target_round[0]

    @staticmethod
    def tool_call_matches_id(tool_call: Any, tool_call_id: str) -> bool:
        """Check if a tool_call object matches a given tool_call_id."""
        if isinstance(tool_call, dict):
            return tool_call.get("id") == tool_call_id
        return getattr(tool_call, "id", None) == tool_call_id

    @staticmethod
    def extract_tool_name(tool_call: Any) -> Optional[str]:
        """Extract the tool name from a tool_call object (dict or object)."""
        if isinstance(tool_call, dict):
            function = tool_call.get("function")
            if isinstance(function, dict):
                function_name = function.get("name")
                if isinstance(function_name, str) and function_name:
                    return function_name
            name = tool_call.get("name")
            return name if isinstance(name, str) and name else None
        function = getattr(tool_call, "function", None)
        function_name = getattr(function, "name", None) if function is not None else None
        if isinstance(function_name, str) and function_name:
            return function_name
        name = getattr(tool_call, "name", None)
        return name if isinstance(name, str) and name else None

    @staticmethod
    def resolve_tool_call_from_message(
        message: BaseMessage,
        context_messages: List[BaseMessage],
    ) -> Optional[Any]:
        """Look up the tool_call object that corresponds to a tool message by traversing context backwards.

        Args:
            message: ToolMessage to look up.
            context_messages: Context message list.

        Returns:
            The matching tool_call object, or None if not found.
        """
        if not isinstance(message, ToolMessage):
            return None
        tool_call_id = getattr(message, "tool_call_id", None)
        if not tool_call_id:
            return None
        for context_message in reversed(context_messages):
            if not isinstance(context_message, AssistantMessage):
                continue
            tool_calls = getattr(context_message, "tool_calls", None) or []
            for tool_call in tool_calls:
                if ContextUtils.tool_call_matches_id(tool_call, tool_call_id):
                    return tool_call
        return None

    @staticmethod
    def resolve_tool_name_from_message(
        message: BaseMessage,
        context_messages: List[BaseMessage],
    ) -> Optional[str]:
        """Look up the tool name that corresponds to a tool message by traversing context backwards.

        Args:
            message: ToolMessage to look up.
            context_messages: Context message list.

        Returns:
            Tool name string, or None if not found.
        """
        tool_call = ContextUtils.resolve_tool_call_from_message(message, context_messages)
        if not tool_call:
            return None
        return ContextUtils.extract_tool_name(tool_call)

    @staticmethod
    def estimate_tokens(content: Any) -> int:
        """估计内容的 token 数，使用字符数 // 3 的粗略估算。"""
        if isinstance(content, str):
            return max(len(content) // 3, 1)
        try:
            return max(len(json.dumps(content, ensure_ascii=False)) // 3, 1)
        except (TypeError, ValueError):
            return max(len(str(content)) // 3, 1)

    @staticmethod
    def estimate_message_tokens(message: BaseMessage) -> int:
        """估计单条消息的 token 数。"""
        content = getattr(message, "content", "")
        return ContextUtils.estimate_tokens(content)
