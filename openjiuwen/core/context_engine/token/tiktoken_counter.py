# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import base64
import json
import math
import os
from typing import List, Optional, Tuple

from openjiuwen.core.common.logging import logger
from openjiuwen.core.context_engine.content_sanitize import sanitize_content_for_text, sanitize_value_for_text
from openjiuwen.core.context_engine.token.base import TokenCounter
from openjiuwen.core.foundation.llm import AssistantMessage, BaseMessage
from openjiuwen.core.foundation.tool import ToolInfo

# Providers charge tokens per image by its dimensions, not its payload size.
# Counting the base64 data URL as text instead balloons the estimate by orders
# of magnitude and triggers premature context compression. When an image's
# dimensions cannot be read, this flat fallback applies: the OpenAI high-detail
# cost of a typical full-height phone screenshot (85 + 8 tiles x 170).
DEFAULT_IMAGE_PLACEHOLDER_TOKENS = 1445

# The estimate is provider-agnostic by design (model-name sniffing is brittle
# under router aliases): it takes the larger of the two dominant billing
# formulas, so budgeting errs slightly high rather than under-reserving.
# OpenAI high-detail: 85 + 170 per 512px tile after provider downscaling.
_IMAGE_BASE_TOKENS = 85
_IMAGE_TILE_TOKENS = 170
# Anthropic-style patch pricing: one token per 28x28px patch, capped.
_CLAUDE_PATCH_SIZE = 28
_CLAUDE_MAX_IMAGE_TOKENS = 1568
# OpenAI 'detail: low' requests are billed flat regardless of size.
_LOW_DETAIL_TOKENS = 85
# Decode at most this many base64 chars (~64 KB) hunting for header metadata;
# enough to skip a large EXIF segment without paying to decode a whole payload.
_IMAGE_HEADER_B64_CHARS = 87360


def _image_placeholder_override() -> Optional[int]:
    """Explicit operator calibration; when set it wins over estimation."""
    raw = os.getenv("TIKTOKEN_IMAGE_PLACEHOLDER_TOKENS")
    if raw is None or not raw.strip():
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def _png_dimensions(data: bytes) -> Optional[Tuple[int, int]]:
    if len(data) >= 24 and data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
        width = int.from_bytes(data[16:20], "big")
        height = int.from_bytes(data[20:24], "big")
        if width and height:
            return width, height
    return None


def _gif_dimensions(data: bytes) -> Optional[Tuple[int, int]]:
    if len(data) >= 10 and data[:6] in (b"GIF87a", b"GIF89a"):
        width = int.from_bytes(data[6:8], "little")
        height = int.from_bytes(data[8:10], "little")
        if width and height:
            return width, height
    return None


def _jpeg_dimensions(data: bytes) -> Optional[Tuple[int, int]]:
    if len(data) < 4 or data[:2] != b"\xff\xd8":
        return None
    i = 2
    while i + 9 <= len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        # Standalone markers carry no length segment.
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        # SOFn frames hold the dimensions (C4/C8/CC are not frame headers).
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            height = data[i + 5] * 256 + data[i + 6]
            width = data[i + 7] * 256 + data[i + 8]
            if width and height:
                return width, height
            return None
        segment_length = data[i + 2] * 256 + data[i + 3]
        if segment_length < 2:
            return None
        i += 2 + segment_length
    return None


def _image_block_base64(part: dict) -> Optional[str]:
    """Pull the base64 payload out of the common image block shapes.

    Remote references (an http(s) ``image_url`` or an Anthropic ``source`` with
    ``type == "url"``) carry no payload to sniff; they return None here and the
    caller prices them at the flat fallback unless producer-stamped dimensions
    are present.
    """
    image_url = part.get("image_url")
    url = image_url.get("url") if isinstance(image_url, dict) else image_url
    if isinstance(url, str) and url.startswith("data:"):
        _, _, payload = url.partition("base64,")
        return payload or None
    source = part.get("source")
    if isinstance(source, dict) and isinstance(source.get("data"), str):
        return source["data"]
    return None


def _is_positive_int(value: object) -> bool:
    return isinstance(value, int) and value > 0


def _explicit_dimensions(part: dict) -> Optional[Tuple[int, int]]:
    """Producer-stamped dimensions: cheaper and more exact than payload sniffing.

    Accepted shapes: integer ``width``/``height`` keys, or ``dimensions`` as
    ``"WxH"`` / ``[W, H]``. Rails that resize screenshots know the final size
    and can annotate it here so counting never touches the payload.
    """
    width = part.get("width")
    height = part.get("height")
    if _is_positive_int(width) and _is_positive_int(height):
        return width, height
    value = part.get("dimensions")
    if isinstance(value, str) and "x" in value.lower():
        left, _, right = value.lower().partition("x")
        try:
            width, height = int(left), int(right)
        except ValueError:
            return None
        return (width, height) if width > 0 and height > 0 else None
    if isinstance(value, (list, tuple)) and len(value) == 2:
        try:
            width, height = int(value[0]), int(value[1])
        except (TypeError, ValueError):
            return None
        return (width, height) if width > 0 and height > 0 else None
    return None


def _image_block_dimensions(part: dict) -> Optional[Tuple[int, int]]:
    explicit = _explicit_dimensions(part)
    if explicit is not None:
        return explicit
    payload = _image_block_base64(part)
    if not payload:
        return None
    try:
        header = base64.b64decode(payload[:_IMAGE_HEADER_B64_CHARS])
    except ValueError:
        # binascii.Error subclasses ValueError, so malformed base64 lands here.
        return None
    for parse in (_png_dimensions, _jpeg_dimensions, _gif_dimensions):
        dimensions = parse(header)
        if dimensions is not None:
            return dimensions
    return None


def _detail_from_image_part(part: dict) -> str:
    image_url = part.get("image_url")
    if isinstance(image_url, dict):
        detail = image_url.get("detail")
        if isinstance(detail, str):
            return detail.lower()
    detail = part.get("detail")
    if isinstance(detail, str):
        return detail.lower()
    return "auto"


def _openai_tile_tokens(width: int, height: int) -> int:
    """OpenAI high-detail algorithm: fit into 2048px, shortest side to 768, tile by 512."""
    w, h = float(width), float(height)
    longest = max(w, h)
    if longest > 2048:
        w, h = w * 2048 / longest, h * 2048 / longest
    shortest = min(w, h)
    if shortest > 768:
        w, h = w * 768 / shortest, h * 768 / shortest
    tiles = math.ceil(w / 512) * math.ceil(h / 512)
    return _IMAGE_BASE_TOKENS + _IMAGE_TILE_TOKENS * tiles


def _claude_patch_tokens(width: int, height: int) -> int:
    tokens = math.ceil(width / _CLAUDE_PATCH_SIZE) * math.ceil(height / _CLAUDE_PATCH_SIZE)
    return min(tokens, _CLAUDE_MAX_IMAGE_TOKENS)


def _estimate_image_tokens(width: int, height: int) -> int:
    return max(_openai_tile_tokens(width, height), _claude_patch_tokens(width, height))


def _image_block_tokens(part: dict) -> int:
    override = _image_placeholder_override()
    if override is not None:
        return override
    if _detail_from_image_part(part) == "low":
        return _LOW_DETAIL_TOKENS
    dimensions = _image_block_dimensions(part)
    if dimensions is None:
        return DEFAULT_IMAGE_PLACEHOLDER_TOKENS
    return _estimate_image_tokens(*dimensions)


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
        self.measurement_source = source_override or "tiktoken"
        self.measurement_tokenizer = None
        self.measurement_fallback_reason = fallback_reason
        self.measurement_fallback_tokenizer_model = fallback_tokenizer_model
        try:
            import tiktoken

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

    # ------------------------------------------------------------------
    # Core interfaces
    # ------------------------------------------------------------------
    def count(self, text: str, *, model: str = "", **kwargs) -> int:
        if self._enc is not None:
            try:
                return len(self._enc.encode(text, disallowed_special=()))
            except UnicodeEncodeError:
                logger.warning(
                    f"Tiktoken encoding failed for text (len={len(text)}), "
                    "using three-character Unicode length fallback."
                )
                self.measurement_source = "string_length_fallback"
                self.measurement_tokenizer = "unicode_codepoints"
                self.measurement_fallback_reason = "text_encoding_failed"
                return len(text) // 3
        if not self._fallback_warning_printed:
            self._fallback_warning_printed = True
            logger.warning(
                "Tiktoken initialization failed, using three-character Unicode length fallback for token counting."
            )
        return len(text) // 3

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
                if ptype in ("image_url", "image") or "image_url" in part or "image" in part:
                    block_tokens += _image_block_tokens(part)
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
