# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import hashlib
import json
import os
import tempfile
import threading
import time
from typing import List, Optional

from openjiuwen.core.common.logging import context_engine_logger
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
#: stall **forever** on silently black-holed networks (E-1125: requests hang
#: 2h until the relay watchdog kills them). The lock keeps concurrent loaders
#: single-flight, and every waiter takes it with a timeout so a stuck holder
#: degrades callers to character-based estimation instead of hanging them.
_ENCODING_LOAD_LOCK = threading.Lock()

#: How long ``__init__``/warm-up may wait for the load lock before falling
#: back. The holder's critical section is bounded by ``_ensure_vocab_cached``
#: (download timeout + local read), so a healthy first load fits comfortably.
_ENCODING_LOAD_TIMEOUT_S = 20.0

#: (connect, read) timeout for the pre-flight vocabulary download. tiktoken's
#: internal ``requests.get`` has no timeout and cannot be configured, so we
#: download the vocab ourselves into its cache and let ``get_encoding`` hit
#: the cache instead of the network.
_VOCAB_DOWNLOAD_TIMEOUT_S = (5.0, 10.0)

#: After a failed vocab load, skip retrying for this long (process-wide) so
#: each new session does not re-pay the lock/download timeout on networks
#: where the vocab source is unreachable.
_VOCAB_COOLDOWN_S = 300.0

#: Monotonic deadline until which vocab loads are skipped (negative cache).
#: 0.0 means "no known failure". GIL makes float read/write atomic enough for
#: this advisory state; races only cause a redundant download or a skipped
#: retry, both benign.
_vocab_unavailable_until = 0.0

#: Vocab blob URLs for the encodings this module actually uses. Keys mirror
#: ``TiktokenCounter._MODEL2ENC`` values plus the default encoding.
_VOCAB_URLS = {
    "cl100k_base": "https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken",
    "o200k_base": "https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken",
}

#: Background warm-up thread handle (see :func:`start_background_warm_up`).
_warm_up_thread: Optional[threading.Thread] = None
_warm_up_lock = threading.Lock()

#: Default encoding used when a model name has no explicit mapping.
_DEFAULT_ENCODING_NAME = "cl100k_base"


def _vocab_cooldown_active() -> bool:
    return time.monotonic() < _vocab_unavailable_until


def _note_vocab_failure(reason: str) -> None:
    """Enter (or confirm) the cooldown window; log once per window."""
    global _vocab_unavailable_until
    now = time.monotonic()
    if _vocab_unavailable_until <= now:
        # Routed through context_engine_logger: the generic `logger` has no
        # file mapping in the SDK logging bootstrap, which made earlier
        # fallback warnings invisible in sidecar logs.
        context_engine_logger.warning(
            "tiktoken vocabulary load failed (%s); falling back to character-based "
            "token estimation for %ss before retrying",
            reason,
            int(_VOCAB_COOLDOWN_S),
        )
    _vocab_unavailable_until = now + _VOCAB_COOLDOWN_S


def _note_vocab_success() -> None:
    global _vocab_unavailable_until
    _vocab_unavailable_until = 0.0


def _vocab_cache_dir() -> Optional[str]:
    """Mirror ``tiktoken.load.read_file_cached``'s cache-dir resolution so the
    prefill lands exactly where ``get_encoding`` will look it up:
    TIKTOKEN_CACHE_DIR -> DATA_GYM_CACHE_DIR -> <tempdir>/data-gym-cache.
    An empty string means "caching disabled" in tiktoken; no prefill then
    (the bounded lock still protects other waiters in that setup)."""
    if "TIKTOKEN_CACHE_DIR" in os.environ:
        cache_dir = os.environ["TIKTOKEN_CACHE_DIR"]
    elif "DATA_GYM_CACHE_DIR" in os.environ:
        cache_dir = os.environ["DATA_GYM_CACHE_DIR"]
    else:
        cache_dir = os.path.join(tempfile.gettempdir(), "data-gym-cache")
    if cache_dir == "":
        return None
    return cache_dir


def _ensure_vocab_cached(encoding_name: str) -> None:
    """Pre-download the BPE vocab with a timeout into tiktoken's cache.

    After this succeeds, ``tiktoken.get_encoding`` finds the vocab locally and
    never touches the network, so its unbounded internal ``requests.get``
    becomes unreachable. Unknown encodings are left to tiktoken (callers are
    still protected by the load-lock timeout). Raises on download failure so
    callers take their fallback path.
    """
    url = _VOCAB_URLS.get(encoding_name)
    if url is None:
        return
    cache_dir = _vocab_cache_dir()
    if cache_dir is None:
        return
    cache_path = os.path.join(cache_dir, hashlib.sha1(url.encode()).hexdigest())
    if os.path.exists(cache_path):
        return
    import requests

    resp = requests.get(url, timeout=_VOCAB_DOWNLOAD_TIMEOUT_S)
    resp.raise_for_status()
    os.makedirs(cache_dir, exist_ok=True)
    tmp_path = f"{cache_path}.{os.getpid()}.tmp"
    with open(tmp_path, "wb") as f:
        f.write(resp.content)
    os.replace(tmp_path, cache_path)


def warm_up_default_encoding() -> None:
    """Load the default tiktoken encoding in the caller's thread.

    Intended to run on a background thread: the first load in a process may
    download the vocabulary synchronously, and doing that on the event loop
    stalls every WebSocket/heartbeat on it. Failures never break startup; the
    cooldown negative-cache keeps later sessions from re-paying the timeout,
    and the first real use retries after the cooldown window.
    """
    if _vocab_cooldown_active():
        return
    try:
        import tiktoken

        if not _ENCODING_LOAD_LOCK.acquire(timeout=_ENCODING_LOAD_TIMEOUT_S):
            _note_vocab_failure("warmup_lock_timeout")
            return
        try:
            _ensure_vocab_cached(_DEFAULT_ENCODING_NAME)
            tiktoken.get_encoding(_DEFAULT_ENCODING_NAME)
        finally:
            _ENCODING_LOAD_LOCK.release()
        _note_vocab_success()
    except Exception as exc:
        _note_vocab_failure(f"warmup_failed:{type(exc).__name__}")


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
            if _vocab_cooldown_active():
                raise RuntimeError("tiktoken vocab unavailable (recent failure cooldown)")

            import tiktoken

            # Share the warm-up lock so concurrent first loads (including
            # non-default encodings like o200k_base) stay single-flight and
            # do not block the event loop with parallel vocabulary downloads.
            # The acquire is bounded: a stuck holder (e.g. a black-holed
            # download) degrades us to char estimation instead of hanging.
            if not _ENCODING_LOAD_LOCK.acquire(timeout=_ENCODING_LOAD_TIMEOUT_S):
                _note_vocab_failure("init_lock_timeout")
                raise RuntimeError(f"encoding load lock not acquired within {_ENCODING_LOAD_TIMEOUT_S}s")
            try:
                # Resolve the encoding name first and preflight its vocab on
                # every branch. ``encoding_for_model`` internally calls
                # ``get_encoding``, whose unbounded ``requests.get`` would run
                # on this very lock-holding thread (the bounded acquire only
                # protects waiters), so no branch may reach tiktoken's
                # downloader directly.
                if model in self._MODEL2ENC:
                    enc_name = self._MODEL2ENC[model]
                else:
                    try:
                        enc_name = tiktoken.encoding_name_for_model(model)
                    except KeyError:
                        enc_name = default_encoding
                        if source_override is None:
                            self.measurement_source = "tiktoken_fallback"
                        if self.measurement_fallback_reason is None:
                            self.measurement_fallback_reason = "model_not_registered"
                _ensure_vocab_cached(enc_name)
                self._enc = tiktoken.get_encoding(enc_name)
            finally:
                _ENCODING_LOAD_LOCK.release()
            self.measurement_tokenizer = getattr(self._enc, "name", default_encoding)
            self._fallback_warning_printed = False
            _note_vocab_success()
        except Exception as exc:
            # Any load failure (cooldown, lock timeout, download error) must
            # never propagate: construction runs on the request path and a
            # raise here would hang/break the session exactly like the hang
            # this guard exists to prevent.
            if not _vocab_cooldown_active():
                _note_vocab_failure(f"init_failed:{type(exc).__name__}")
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
                context_engine_logger.warning(
                    "Tiktoken initialization failed, using the character-based token estimate."
                )
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
            context_engine_logger.warning(
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
