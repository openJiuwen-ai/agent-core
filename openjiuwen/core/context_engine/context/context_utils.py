# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import threading
import time
import uuid
from typing import Any, Dict, List, Optional

import requests

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.context_engine.base import ContextWindow
from openjiuwen.core.context_engine.content_sanitize import sanitize_content_for_text
from openjiuwen.core.foundation.llm import AssistantMessage, BaseMessage, ToolMessage

CONTEXT_MESSAGE_ID_KEY = "context_message_id"
CONTEXT_USAGE_STALE_KEY = "context_usage_stale"
DEFAULT_CONTEXT_MAX_TOKENS = 200000
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
OPENROUTER_MODEL_CACHE_TTL_SECONDS = 3600
_OPENROUTER_MODEL_CONTEXT_WINDOW_TOKENS: Dict[str, int] = {}
_OPENROUTER_MODEL_CONTEXT_WINDOW_TOKENS_FETCHED_AT = 0.0
_OPENROUTER_MODEL_CONTEXT_WINDOW_TOKENS_LOCK = threading.Lock()
_OPENROUTER_PREFETCH_LOCK = threading.Lock()
_OPENROUTER_PREFETCH_THREAD: Optional[threading.Thread] = None

MODEL_DEFAULT_CONTEXT_WINDOW_TOKENS: Dict[str, int] = {
    # GLM
    "glm-5.2": 1048576,
    "glm-5.1": 204800,
    "glm-5": 204800,
    "glm-5-turbo": 202752,
    "glm-4.7": 204800,
    "glm-4.7-flash": 202752,
    "glm-4.7-flashx": 200000,
    "glm-4.6": 204800,
    "glm-4.5": 131072,
    "glm-4.5-air": 131072,
    "glm-4-long": 1000000,
    "glm-4": 128000,
    "glm-4-9b-chat-1m": 1048576,
    # OpenAI GPT
    "gpt-5.6-luna": 1050000,
    "gpt-5.6-luna-pro": 1050000,
    "gpt-5.6-sol": 1050000,
    "gpt-5.6-sol-pro": 1050000,
    "gpt-5.6-terra": 1050000,
    "gpt-5.6-terra-pro": 1050000,
    "gpt-5.5": 1050000,
    "gpt-5.5-pro": 1050000,
    "gpt-5.4": 1050000,
    "gpt-5.4-pro": 1050000,
    "gpt-5.4-mini": 400000,
    "gpt-5.4-nano": 400000,
    "gpt-5.2": 400000,
    "gpt-5.1": 400000,
    "gpt-5": 400000,
    "gpt-5-pro": 400000,
    "gpt-5-mini": 400000,
    "gpt-5-nano": 400000,
    "gpt-4.1": 1047576,
    "gpt-4.1-mini": 1047576,
    "gpt-4.1-nano": 1047576,
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
    "gpt-4-turbo": 128000,
    "gpt-3.5-turbo": 16385,
    # OpenAI o-series
    "o1": 200000,
    "o3": 200000,
    "o3-mini": 200000,
    "o3-pro": 200000,
    "o4-mini": 200000,
    # DeepSeek
    "deepseek-v4-pro": 1048576,
    "deepseek-v4-flash": 1048576,
    "deepseek-v3.2": 163840,
    "deepseek-v3.1-terminus": 163840,
    "deepseek-r1": 163840,
    "deepseek-v3": 128000,
    "deepseek-chat": 163840,
    # Anthropic Claude
    "claude-opus-5": 1000000,
    "claude-opus-4-8": 1000000,
    "claude-opus-4.8": 1000000,
    "claude-opus-4-7": 1000000,
    "claude-opus-4.7": 1000000,
    "claude-opus-4-6": 1000000,
    "claude-opus-4.6": 1000000,
    "claude-opus-4-5": 200000,
    "claude-opus-4.5": 200000,
    "claude-sonnet-5": 1000000,
    "claude-sonnet-4-6": 1000000,
    "claude-sonnet-4.6": 1000000,
    "claude-sonnet-4-5": 1000000,
    "claude-sonnet-4.5": 1000000,
    "claude-haiku-4-5": 200000,
    "claude-haiku-4.5": 200000,
    # Google Gemini
    "gemini-3.6-flash": 1048576,
    "gemini-3.5-flash": 1048576,
    "gemini-3.5-flash-lite": 1048576,
    "gemini-3.1-pro-preview": 1048576,
    "gemini-3.1-flash-lite": 1048576,
    "gemini-3-pro-preview": 1048576,
    "gemini-3-flash-preview": 1048576,
    "gemini-2.5-pro": 1048576,
    "gemini-2.5-flash": 1048576,
    "gemini-2.5-flash-lite": 1048576,
    # Meta Llama
    "llama-4-maverick": 1048576,
    "llama-4-scout": 10000000,  # 10M context window (Meta official spec)
    # Qwen
    "qwen3.7-max": 1000000,
    "qwen3.7-plus": 1000000,
    "qwen3.7-flash": 1000000,
    "qwen3.6-max-preview": 262144,
    "qwen3.6-plus": 1000000,
    "qwen3.6-flash": 1000000,
    "qwen3.5-plus": 1000000,
    "qwen3.5-flash": 1000000,
    "qwen3-max": 262144,
    "qwen3-max-thinking": 262144,
    "qwen3-coder-plus": 1000000,
    "qwen3-coder-flash": 1000000,
    "qwen3-coder-next": 262144,
    "qwen-max": 262144,
    "qwen-plus": 1000000,
    "qwen-flash": 1000000,
    "qwen-turbo": 8192,
    "qwen-long": 1000000,
    # Moonshot Kimi
    "kimi-k3": 1048576,
    "kimi-k2.7-code": 262144,
    "kimi-k2.6": 262144,
    "kimi-k2.5": 262144,
    "kimi-k2-thinking": 262144,
    "kimi-k2-0905": 262144,
    "kimi-k2": 131072,
    # MiniMax
    "MiniMax-M3": 1048576,
    "MiniMax-M2.7": 204800,
    "MiniMax-M2.7-highspeed": 204800,
    "MiniMax-M2.5": 204800,
    "MiniMax-M2.5-highspeed": 204800,
    "MiniMax-M2.1": 204800,
    "MiniMax-M2": 204800,
    "MiniMax-M1": 1000000,
    # xAI Grok
    "grok-4.20": 2000000,
    "grok-4.20-multi-agent": 2000000,
    "grok-4.5": 500000,
    "grok-4.3": 1000000,
    "grok-4.3-latest": 1000000,
    "grok-latest": 500000,
}


class ContextUtils:
    """
    Utility helper functions for manipulating and parsing conversation contexts.
    All methods are static and stateless.
    """

    @staticmethod
    def _get_positive_int_by_model_name(mapping: Optional[Dict[str, int]], model_name: str) -> Optional[int]:
        if not mapping:
            return None

        value = mapping.get(model_name)
        if isinstance(value, int) and value > 0:
            return value

        normalized_model_name = model_name.lower()
        for candidate_model_name, candidate_value in mapping.items():
            if not isinstance(candidate_model_name, str):
                continue
            if candidate_model_name.lower() != normalized_model_name:
                continue
            if isinstance(candidate_value, int) and candidate_value > 0:
                return candidate_value

        return None

    @staticmethod
    def _parse_openrouter_model(model: Any) -> Optional[tuple[str, int]]:
        if not isinstance(model, dict):
            return None

        model_id = model.get("id")
        context_length = model.get("context_length")
        if not isinstance(model_id, str):
            return None
        if not isinstance(context_length, int) or context_length <= 0:
            return None

        return model_id, context_length

    @staticmethod
    def _parse_openrouter_model_context_window_tokens(models: List[Any]) -> Dict[str, int]:
        """Parse OpenRouter model IDs and add unambiguous aliases without the provider prefix."""
        fetched_tokens = {}
        alias_tokens = {}
        ambiguous_aliases = set()
        for model in models:
            parsed_model = ContextUtils._parse_openrouter_model(model)
            if parsed_model is None:
                continue

            model_id, context_length = parsed_model
            fetched_tokens[model_id] = context_length
            if "/" not in model_id:
                continue

            alias = model_id.split("/", 1)[1]
            if alias in alias_tokens:
                ambiguous_aliases.add(alias)
            else:
                alias_tokens[alias] = context_length

        fetched_tokens.update(
            {alias: context_length for alias, context_length in alias_tokens.items() if alias not in ambiguous_aliases}
        )
        return fetched_tokens

    @staticmethod
    def _run_openrouter_prefetch(timeout: float) -> None:
        """Body of the background prefetch thread; never propagates failures."""
        try:
            ContextUtils.fetch_openrouter_model_context_window_tokens(timeout)
        except Exception:
            logger.debug(
                "OpenRouter context window prefetch failed, keeping built-in values",
                exc_info=True,
            )

    @staticmethod
    def prefetch_openrouter_model_context_window_tokens(timeout: float = 3.0) -> None:
        """Warm the OpenRouter context-window cache without blocking the caller.

        The fetch is a ~600KB cross-region HTTPS download; awaiting it on the
        context-creation path costs every process its first turn (measured at
        ~2.2s). Callers that only need context windows *eventually* schedule it
        here instead: the current call falls back to the built-in table, and a
        later context picks up the fetched values once the background thread
        lands them.

        No-ops when the cache is still fresh or a prefetch is already in flight.
        The worker is a daemon thread so it never holds up interpreter exit.

        Args:
            timeout: Per-request timeout handed to the underlying fetch.
        """
        global _OPENROUTER_PREFETCH_THREAD

        fetched_at = _OPENROUTER_MODEL_CONTEXT_WINDOW_TOKENS_FETCHED_AT
        if fetched_at and (time.monotonic() - fetched_at) < OPENROUTER_MODEL_CACHE_TTL_SECONDS:
            return

        with _OPENROUTER_PREFETCH_LOCK:
            in_flight = _OPENROUTER_PREFETCH_THREAD
            if in_flight is not None and in_flight.is_alive():
                return
            _OPENROUTER_PREFETCH_THREAD = threading.Thread(
                target=ContextUtils._run_openrouter_prefetch,
                args=(timeout,),
                name="openrouter-context-window-prefetch",
                daemon=True,
            )
            _OPENROUTER_PREFETCH_THREAD.start()

    @staticmethod
    def fetch_openrouter_model_context_window_tokens(timeout: float = 3.0) -> Dict[str, int]:
        """Fetch OpenRouter model context windows with a process-wide TTL cache.

        Blocking. Prefer :meth:`prefetch_openrouter_model_context_window_tokens`
        on any path that a user request waits on.
        """
        global _OPENROUTER_MODEL_CONTEXT_WINDOW_TOKENS
        global _OPENROUTER_MODEL_CONTEXT_WINDOW_TOKENS_FETCHED_AT

        now = time.monotonic()
        cache_age = now - _OPENROUTER_MODEL_CONTEXT_WINDOW_TOKENS_FETCHED_AT
        if _OPENROUTER_MODEL_CONTEXT_WINDOW_TOKENS_FETCHED_AT and cache_age < OPENROUTER_MODEL_CACHE_TTL_SECONDS:
            return dict(_OPENROUTER_MODEL_CONTEXT_WINDOW_TOKENS)

        with _OPENROUTER_MODEL_CONTEXT_WINDOW_TOKENS_LOCK:
            now = time.monotonic()
            cache_age = now - _OPENROUTER_MODEL_CONTEXT_WINDOW_TOKENS_FETCHED_AT
            if _OPENROUTER_MODEL_CONTEXT_WINDOW_TOKENS_FETCHED_AT and cache_age < OPENROUTER_MODEL_CACHE_TTL_SECONDS:
                return dict(_OPENROUTER_MODEL_CONTEXT_WINDOW_TOKENS)

            try:
                response = requests.get(OPENROUTER_MODELS_URL, timeout=timeout)
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise ValueError("response payload must be an object")
                models = payload.get("data")
                if not isinstance(models, list):
                    raise ValueError("response data must be a list")

                fetched_tokens = ContextUtils._parse_openrouter_model_context_window_tokens(models)
                if not fetched_tokens:
                    raise ValueError("response does not contain valid model context windows")

                _OPENROUTER_MODEL_CONTEXT_WINDOW_TOKENS = fetched_tokens
            except (requests.RequestException, ValueError, TypeError) as exc:
                logger.warning(
                    f"failed to fetch OpenRouter model context windows, use cached or built-in values: {exc}"
                )
            finally:
                _OPENROUTER_MODEL_CONTEXT_WINDOW_TOKENS_FETCHED_AT = now

        return dict(_OPENROUTER_MODEL_CONTEXT_WINDOW_TOKENS)

    @staticmethod
    def build_model_context_window_tokens(
        model_context_window_tokens: Optional[Dict[str, int]] = None,
        *,
        enable_openrouter_model_context_window_tokens: bool = False,
        openrouter_request_timeout: float = 3.0,
    ) -> Dict[str, int]:
        """Build explicit model context windows and optionally warm the OpenRouter cache.

        The warm-up is scheduled in the background: this runs while a context is
        being constructed for a live request, so it must not wait on the network.
        """
        if enable_openrouter_model_context_window_tokens:
            ContextUtils.prefetch_openrouter_model_context_window_tokens(openrouter_request_timeout)
        return dict(model_context_window_tokens) if model_context_window_tokens else {}

    @staticmethod
    def validate_messages(messages: BaseMessage | List[BaseMessage]) -> None:
        if isinstance(messages, BaseMessage):
            return
        if isinstance(messages, list):
            for msg in messages:
                if not isinstance(msg, BaseMessage):
                    raise build_error(
                        StatusCode.CONTEXT_MESSAGE_INVALID,
                        error_msg="messages should be a BaseMessage or a list of BaseMessage",
                    )
            return
        raise build_error(
            StatusCode.CONTEXT_MESSAGE_INVALID, error_msg="messages should be a BaseMessage or a list of BaseMessage"
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
    def invalidate_usage_metadata(messages: List[BaseMessage]) -> None:
        """Mark retained model usage as stale after existing context is rewritten."""
        for message in messages:
            if not isinstance(message, AssistantMessage) or message.usage_metadata is None:
                continue
            metadata = getattr(message, "metadata", None)
            if not isinstance(metadata, dict):
                metadata = {}
                message.metadata = metadata
            metadata[CONTEXT_USAGE_STALE_KEY] = True

    @staticmethod
    def has_valid_usage_metadata(message: BaseMessage) -> bool:
        """Return whether an assistant usage record still describes its prefix."""
        metadata = getattr(message, "metadata", None)
        usage_is_stale = metadata.get(CONTEXT_USAGE_STALE_KEY, False) if isinstance(metadata, dict) else False
        return (
            isinstance(message, AssistantMessage)
            and message.usage_metadata is not None
            and message.usage_metadata.total_tokens > 0
            and not bool(usage_is_stale)
        )

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
        4. Look up ``model_name`` in cached OpenRouter metadata.
        5. Return ``DEFAULT_CONTEXT_MAX_TOKENS`` (200000).
        """
        if isinstance(fallback_context_window_tokens, int) and fallback_context_window_tokens > 0:
            return fallback_context_window_tokens

        if isinstance(model_name, str) and model_name:
            value = ContextUtils._get_positive_int_by_model_name(model_context_window_tokens, model_name)
            if value is not None:
                return value

            builtin = ContextUtils._get_positive_int_by_model_name(MODEL_DEFAULT_CONTEXT_WINDOW_TOKENS, model_name)
            if builtin is not None:
                return builtin

            openrouter = ContextUtils._get_positive_int_by_model_name(
                _OPENROUTER_MODEL_CONTEXT_WINDOW_TOKENS,
                model_name,
            )
            if openrouter is not None:
                return openrouter

        return DEFAULT_CONTEXT_MAX_TOKENS

    @staticmethod
    def is_compression_processor(processor: Any) -> bool:
        processor_type = processor.processor_type().lower()
        module_name = processor.__class__.__module__.lower()
        return "compressor" in processor_type or "compact" in processor_type or ".processor.compressor." in module_name

    @staticmethod
    def is_offload_processor(processor: Any) -> bool:
        processor_type = processor.processor_type().lower()
        module_name = processor.__class__.__module__.lower()
        return "offload" in processor_type or ".processor.offloader." in module_name

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

        return messages[:start_index] + target_messages + messages[end_index + 1:]  # fmt: skip

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

    @staticmethod
    def find_last_n_dialogue_round(messages: List[BaseMessage], n: int) -> int:
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
        """估计内容的 token 数，使用字符数 // 4 的粗略估算。

        ``// 4`` 与 ``TiktokenCounter`` 自身 fallback 一致，且更接近真实
        token 量级，可与按真实 context window 标定的阈值对齐。
        """
        text = sanitize_content_for_text(content)
        return max(len(text) // 4, 1)

    @staticmethod
    def estimate_message_tokens(message: BaseMessage) -> int:
        """估计单条消息的 token 数。"""
        content = getattr(message, "content", "")
        return ContextUtils.estimate_tokens(content)
