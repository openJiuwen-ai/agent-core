# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import hashlib
import json
import os
import threading
from collections import OrderedDict
from typing import List, Dict

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm import BaseMessage, AssistantMessage
from openjiuwen.core.foundation.tool import ToolInfo
from openjiuwen.core.context_engine.token.base import TokenCounter


_STABLE_TOKEN_CACHE_MAX_ENTRIES = 2048
_STABLE_TOKEN_COUNT_CACHE: "OrderedDict[tuple[str, bytes], int]" = OrderedDict()
_STABLE_TOKEN_COUNT_CACHE_LOCK = threading.Lock()


def _static_assembly_cache_enabled() -> bool:
    return os.getenv("JIUWENSWARM_STATIC_ASSEMBLY_CACHE", "").strip().lower() in {
        "1", "true", "yes", "on",
    }


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

    __slots__ = ("_enc", "_encoding_cache_key", "_model", "_fallback_warning_printed")

    def __init__(self, model: str = "gpt-4") -> None:
        self._model = model
        enc_name = self._MODEL2ENC.get(model, "cl100k_base")
        try:
            import tiktoken
            self._enc = tiktoken.get_encoding(enc_name)
            self._encoding_cache_key = enc_name
            self._fallback_warning_printed = False
        except Exception:
            self._enc = None
            self._encoding_cache_key = f"fallback:{enc_name}"
            self._fallback_warning_printed = False

    def _count_stable_text(self, text: str) -> int:
        """Count immutable prompt material without retaining its plaintext.

        System prompt prefixes and tool schemas are repeated across isolated
        sessions.  The cache key contains only the tokenizer identity and a
        SHA-256 digest; user-visible content is never retained process-wide.
        """
        if not _static_assembly_cache_enabled():
            return self.count(text)
        digest = hashlib.sha256(text.encode("utf-8", errors="surrogatepass")).digest()
        key = (self._encoding_cache_key, digest)
        with _STABLE_TOKEN_COUNT_CACHE_LOCK:
            cached = _STABLE_TOKEN_COUNT_CACHE.get(key)
            if cached is not None:
                _STABLE_TOKEN_COUNT_CACHE.move_to_end(key)
                return cached

        count = self.count(text)
        with _STABLE_TOKEN_COUNT_CACHE_LOCK:
            existing = _STABLE_TOKEN_COUNT_CACHE.get(key)
            if existing is not None:
                _STABLE_TOKEN_COUNT_CACHE.move_to_end(key)
                return existing
            _STABLE_TOKEN_COUNT_CACHE[key] = count
            while len(_STABLE_TOKEN_COUNT_CACHE) > _STABLE_TOKEN_CACHE_MAX_ENTRIES:
                _STABLE_TOKEN_COUNT_CACHE.popitem(last=False)
        return count

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
            if msg.role == "system":
                total += self._count_stable_text(piece)
            else:
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
            total += self._count_stable_text(piece)

        # Consistent with count_messages, reserve 3 tokens for the assistant
        return total + 3
