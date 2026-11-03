# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Dimension-based token pricing for image content blocks.

Providers bill images by their dimensions, not by their base64 payload. Counting
an image data URL as text (the character-based fallback) under-reported a
full-size screenshot by an order of magnitude, so any estimator that falls back
to characters must add these visual tokens on top of the text estimate.

The estimation order mirrors the TiktokenCounter policy:

* producer-stamped dimensions (``width``/``height``/``dimensions``) win;
* otherwise sniff the header of the base64 payload (PNG/JPEG/GIF);
* otherwise fall back to :data:`DEFAULT_IMAGE_PLACEHOLDER_TOKENS`;
* ``detail: low`` is priced flat and the ``TIKTOKEN_IMAGE_PLACEHOLDER_TOKENS``
  env var overrides every branch.
"""

from __future__ import annotations

import base64
import math
import os
from typing import Any, Optional, Tuple

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

_IMAGE_BLOCK_TYPES = frozenset({"image_url", "image", "input_image"})


def is_image_block(part: Any) -> bool:
    """Return whether one content block carries an image.

    Covers the OpenAI/Anthropic/Responses shapes this repo emits:
    ``{"type": "image_url"}``, ``{"type": "image"}``, ``{"type": "input_image"}``
    and the legacy key-presence variants. Keep this set aligned with
    ``ReActAgent._content_block_is_image`` so stripping, the request image
    budget and the visual token estimate all see the same blocks.
    """

    if not isinstance(part, dict):
        return False
    if part.get("type") in _IMAGE_BLOCK_TYPES:
        return True
    return "image_url" in part or "image" in part or "input_image" in part


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
    if image_url is None:
        image_url = part.get("input_image")
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


def estimate_image_tokens(width: int, height: int) -> int:
    """Pixel-dimension based visual token estimate (tile / patch formulas)."""
    return max(_openai_tile_tokens(width, height), _claude_patch_tokens(width, height))


def estimate_image_block_tokens(part: Any) -> int:
    """Estimate provider-billed visual tokens for one image content block.

    Non-image blocks return 0 so callers can price mixed content without
    pre-filtering every part.
    """
    if not is_image_block(part):
        return 0
    override = _image_placeholder_override()
    if override is not None:
        return override
    if _detail_from_image_part(part) == "low":
        return _LOW_DETAIL_TOKENS
    dimensions = _image_block_dimensions(part)
    if dimensions is None:
        return DEFAULT_IMAGE_PLACEHOLDER_TOKENS
    return estimate_image_tokens(*dimensions)


def estimate_content_image_tokens(content: Any) -> int:
    """Sum the visual tokens of every image block inside one message content.

    Text-only message contents (the common case) return 0 so callers can add
    this on top of a plain text estimate without branching.
    """
    if not isinstance(content, list):
        return 0
    return sum(
        estimate_image_block_tokens(part) for part in content if is_image_block(part)
    )


#: Backward-compatible aliases kept for callers that used the private names.
_image_block_tokens = estimate_image_block_tokens
_estimate_image_tokens = estimate_image_tokens


__all__ = [
    "DEFAULT_IMAGE_PLACEHOLDER_TOKENS",
    "estimate_content_image_tokens",
    "estimate_image_block_tokens",
    "estimate_image_tokens",
    "is_image_block",
]
