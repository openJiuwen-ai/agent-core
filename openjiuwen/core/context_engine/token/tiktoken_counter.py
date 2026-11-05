# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import json
import threading
from typing import List, Optional

from openjiuwen.core.common.logging import logger
from openjiuwen.core.context_engine.content_sanitize import sanitize_content_for_text, sanitize_value_for_text
from openjiuwen.core.context_engine.token.base import TokenCounter
from openjiuwen.core.context_engine.token.encode_guard import CountCache, count_with_guard, estimate_tokens
from openjiuwen.core.context_engine.token.image_tokens import (
    DEFAULT_IMAGE_PLACEHOLDER_TOKENS,  # noqa: F401 - re-exported for existing callers
    _image_block_tokens,  # noqa: F401 - backward-compatible private alias
    estimate_image_block_tokens,
    is_image_block,
)
from openjiuwen.core.foundation.llm import AssistantMessage, BaseMessage
from openjiuwen.core.foundation.tool import ToolInfo

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
    A token counter powered by tiktoken.

    Known OpenAI model names use the encoding resolved by tiktoken. Unknown
    model names deliberately use the project's default ``cl100k_base``
    encoding and expose ``tiktoken_fallback`` metadata instead of pretending
    that the count is model-native.

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

    __slots__ = (
        "_enc",
        "_model",
        "_fallback_warning_printed",
        "_count_cache",
        "_base_measurement",
        "measurement_source",
        "measurement_tokenizer",
        "measurement_fallback_reason",
        "measurement_fallback_tokenizer_model",
    )

    def __init__(
        self,
        model: str = "gpt-4",
        *,
        default_encoding: str = "cl100k_base",
        source_override: str | None = None,
        fallback_reason: str | None = None,
        fallback_tokenizer_model: str | None = None,
    ) -> None:
        self._model = model
        self._count_cache = CountCache()
        self.measurement_source = source_override or "tiktoken"
        self.measurement_tokenizer = None
        self.measurement_fallback_reason = fallback_reason
        self.measurement_fallback_tokenizer_model = fallback_tokenizer_model
        try:
            import tiktoken

            # Share the warm-up lock so concurrent first loads (including
            # non-default encodings like o200k_base) stay single-flight and
            # do not block the event loop with parallel vocabulary downloads.
            with _ENCODING_LOAD_LOCK:
                if model in self._MODEL2ENC:
                    self._enc = tiktoken.get_encoding(self._MODEL2ENC[model])
                else:
                    try:
                        self._enc = tiktoken.encoding_for_model(model)
                    except KeyError:
                        self._enc = tiktoken.get_encoding(default_encoding)
                        if source_override is None:
                            self.measurement_source = "tiktoken_fallback"
                        if self.measurement_fallback_reason is None:
                            self.measurement_fallback_reason = "model_not_registered"
            self.measurement_tokenizer = getattr(self._enc, "name", default_encoding)
            self._fallback_warning_printed = False
        except Exception:
            self._enc = None
            self.measurement_source = "string_length_fallback"
            self.measurement_tokenizer = "unicode_codepoints"
            if self.measurement_fallback_reason is None:
                self.measurement_fallback_reason = "tiktoken_unavailable"
            self._fallback_warning_printed = False
        self._base_measurement = (
            self.measurement_source,
            self.measurement_tokenizer,
            self.measurement_fallback_reason,
        )

    # ------------------------------------------------------------------
    # Core interfaces
    # ------------------------------------------------------------------
    def count(self, text: str, *, model: str = "", **kwargs) -> int:
        if self._enc is None:
            if not self._fallback_warning_printed:
                self._fallback_warning_printed = True
                logger.warning("Tiktoken initialization failed, using the character-based token estimate.")
            return estimate_tokens(text)
        try:
            count = count_with_guard(
                text,
                cache=self._count_cache,
                encode=lambda value: len(self._enc.encode(value, disallowed_special=())),
            )
        except UnicodeEncodeError:
            # Deliberately uncached: this estimate must not be replayed as an exact
            # count, and the attribution below belongs to this count only.
            logger.warning(
                f"Tiktoken encoding failed for text (len={len(text)}), using the character-based token estimate."
            )
            self.measurement_source = "string_length_fallback"
            self.measurement_tokenizer = "unicode_codepoints"
            self.measurement_fallback_reason = "text_encoding_failed"
            return estimate_tokens(text)
        self.measurement_source, self.measurement_tokenizer, self.measurement_fallback_reason = self._base_measurement
        return count

    def count_messages(self, messages: List[BaseMessage], *, model: str = "", **kwargs) -> int:
        if not messages:
            return 0
        total = 0
        for msg in messages:
            if isinstance(msg.content, list):
                combined_text, block_tokens = self._count_content_blocks(msg.content, model=model, **kwargs)
                total += block_tokens
                piece = f"<|start|>{msg.role}\n{combined_text}<|end|>"
            else:
                # A data URL embedded in plain string content is the same
                # ballooning bug through a side door — sanitize before counting.
                piece = f"<|start|>{msg.role}\n{sanitize_content_for_text(msg.content)}<|end|>"
            total += self.count(piece, model=model, **kwargs)
            if isinstance(msg, AssistantMessage):
                dict_msg = msg.model_dump()
                # count tool calls
                tool_calls = dict_msg.get("tool_calls")
                if tool_calls:
                    total += self.count(json.dumps(dict_msg["tool_calls"], ensure_ascii=False), model=model, **kwargs)
        return total + 3

    def _count_content_blocks(self, blocks: list, *, model: str = "", **kwargs) -> tuple:
        """Split multimodal block-list content into countable text and non-text tokens.

        Image blocks are priced by their dimensions (tile formula), never by
        their payload: counting a base64 ``data:`` URL as text would report
        tens of thousands of tokens for an image a provider bills at ~1.5k.
        Images whose dimensions cannot be read cost the flat fallback, and the
        ``TIKTOKEN_IMAGE_PLACEHOLDER_TOKENS`` env var overrides both. Unknown
        block types are counted from their compact JSON form.
        """
        text_parts: List[str] = []
        block_tokens = 0
        for part in blocks:
            if isinstance(part, dict):
                ptype = part.get("type")
                if is_image_block(part):
                    block_tokens += estimate_image_block_tokens(part)
                elif ptype == "text" or "text" in part:
                    text_parts.append(str(part.get("text") or ""))
                else:
                    sanitized = sanitize_value_for_text(part)
                    compact = json.dumps(sanitized, ensure_ascii=False, separators=(",", ":"))
                    block_tokens += self.count(compact, model=model, **kwargs)
            elif isinstance(part, str):
                text_parts.append(part)
        return "\n".join(text_parts), block_tokens

    def count_tools(self, tools: List[ToolInfo], *, model: str = "", **kwargs) -> int:
        if not tools:
            return 0
        total = 0
        for idx, tool in enumerate(tools):
            function_obj = {
                "name": tool.name,
                "description": tool.description or "",
                "parameters": tool.parameters,  # 期望是 JSON Schema dict
            }
            json_str = json.dumps(function_obj, ensure_ascii=False, separators=(",", ":"))

            # message format：functions.{name}:{index}
            piece = f"<|start|>functions.{tool.name}:{idx}\n{json_str}<|end|>"
            total += self.count(piece)

        # Consistent with count_messages, reserve 3 tokens for the assistant
        return total + 3
