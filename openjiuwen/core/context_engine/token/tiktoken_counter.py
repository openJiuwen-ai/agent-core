# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import json
import threading
from typing import List, Dict, Optional

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm import BaseMessage, AssistantMessage
from openjiuwen.core.foundation.tool import ToolInfo
from openjiuwen.core.context_engine.token.base import TokenCounter

#: Serializes first-use loads of tiktoken encodings. The very first
#: ``tiktoken.get_encoding`` in a process downloads the BPE vocabulary file
#: over HTTPS through a blocking ``requests.get`` without timeout, which can
#: stall for tens of seconds; the lock keeps concurrent loaders single-flight.
_ENCODING_LOAD_LOCK = threading.Lock()

#: Background warm-up thread handle (see :func:`start_background_warm_up`).
_warm_up_thread: Optional[threading.Thread] = None
_warm_up_lock = threading.Lock()

#: Default encoding used when a model name has no explicit mapping.
_DEFAULT_ENCODING_NAME = "cl100k_base"


def warm_up_default_encoding() -> None:
    """Load the default tiktoken encoding in the caller's thread.

    Intended to run on a background thread: the first load in a process may
    download the vocabulary synchronously, and doing that on the event loop
    stalls every WebSocket/heartbeat on it. Failures are swallowed so the
    warm-up can never break startup; the first real use simply retries.
    """
    try:
        import tiktoken

        with _ENCODING_LOAD_LOCK:
            tiktoken.get_encoding(_DEFAULT_ENCODING_NAME)
    except Exception:
        logger.debug("tiktoken encoding warm-up failed; first use will retry", exc_info=True)


def start_background_warm_up() -> None:
    """Start a one-shot daemon thread that pre-loads the default encoding.

    Idempotent and single-flight: repeated calls while the warm-up is still
    running are no-ops, and once loaded the encoding stays in tiktoken's
    process-wide cache, so every later ``get_encoding`` is instant.
    """
    global _warm_up_thread

    with _warm_up_lock:
        if _warm_up_thread is not None and _warm_up_thread.is_alive():
            return
        _warm_up_thread = threading.Thread(
            target=warm_up_default_encoding,
            name="tiktoken-encoding-warmup",
            daemon=True,
        )
        _warm_up_thread.start()


class TiktokenCounter(TokenCounter):
    """
    A fast and exact token counter powered by tiktoken.
    Supports all publicly released OpenAI models (gpt-3.5-turbo, gpt-4, gpt-4o, ...).
    Thread-safe: tiktoken.Encoding objects are stateless and reusable.
    """

    # Mapping from user-friendly model names to tiktoken encoding names
    _MODEL2ENC = {
        "gpt-3.5-turbo": "cl100k_base",
        "gpt-4": "cl100k_base",
        "gpt-4-turbo": "cl100k_base",
        "gpt-4o": "o200k_base",
        "gpt-4o-mini": "o200k_base",
        "text-embedding-ada-002": "cl100k_base",
        "text-embedding-3-small": "cl100k_base",
        "text-embedding-3-large": "cl100k_base",
    }

    __slots__ = ("_enc", "_model", "_fallback_warning_printed")

    def __init__(self, model: str = "gpt-4") -> None:
        self._model = model
        enc_name = self._MODEL2ENC.get(model, _DEFAULT_ENCODING_NAME)
        try:
            import tiktoken
            with _ENCODING_LOAD_LOCK:
                self._enc = tiktoken.get_encoding(enc_name)
            self._fallback_warning_printed = False
        except Exception:
            self._enc = None
            self._fallback_warning_printed = False

    # ------------------------------------------------------------------
    # Core interfaces
    # ------------------------------------------------------------------
    def count(self, text: str, *, model: str = "", **kwargs) -> int:
        if self._enc is not None:
            try:
                return len(self._enc.encode(text, disallowed_special=()))
            except UnicodeEncodeError:
                logger.warning(
                    f"Tiktoken encoding failed for text (len={len(text)}), using len//4 fallback."
                )
                return len(text) // 4
        if not self._fallback_warning_printed:
            self._fallback_warning_printed = True
            logger.warning(
                "Tiktoken initialization failed, using len(text)//4 as fallback for token counting. "
            )
        return len(text) // 4

    def count_messages(self, messages: List[BaseMessage], *, model: str = "", **kwargs) -> int:
        if not messages:
            return 0
        total = 0
        for msg in messages:
            piece = f"<|start|>{msg.role}\n{msg.content}<|end|>"
            total += self.count(piece, model=model, **kwargs)
            if isinstance(msg, AssistantMessage):
                dict_msg = msg.model_dump()
                # count tool calls
                tool_calls = dict_msg.get("tool_calls")
                if tool_calls:
                    total += self.count(json.dumps(dict_msg["tool_calls"], ensure_ascii=False), model=model, **kwargs)
        return total + 3

    def count_tools(self, tools: List[ToolInfo], *, model: str = "", **kwargs) -> int:
        if not tools:
            return 0
        total = 0
        for idx, tool in enumerate(tools):
            function_obj = {
                "name": tool.name,
                "description": tool.description or "",
                "parameters": tool.parameters  # 期望是 JSON Schema dict
            }
            json_str = json.dumps(function_obj, ensure_ascii=False, separators=(",", ":"))

            # message format：functions.{name}:{index}
            piece = f"<|start|>functions.{tool.name}:{idx}\n{json_str}<|end|>"
            total += self.count(piece)

        # Consistent with count_messages, reserve 3 tokens for the assistant
        return total + 3