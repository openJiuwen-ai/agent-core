# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Image-input modality probing for harness agents."""

from __future__ import annotations

import base64
import struct
import zlib
from typing import Any, Iterable, List, Optional

from openjiuwen.core.common.logging import logger

_IMAGE_INPUT_SCAN_MAX_DEPTH = 8
_IMAGE_INPUT_UNSUPPORTED_ERROR_CODES = (
    "invalid_image_input",
    "image_input_unsupported",
    "unsupported_content_type",
    "unsupported_image",
    "unsupported_image_input",
    "unsupported_message_content_type",
)
_IMAGE_INPUT_UNSUPPORTED_ERROR_PATTERNS = (
    "no endpoints found that support image input",
    "does not accept images",
    "does not support image",
    "doesn't accept images",
    "doesn't support image",
    "do not support image",
    "image input is not supported",
    "image input not supported",
    "image_url is not supported",
    "images are not supported",
    "multimodal input is not supported",
    "not support image input",
    "unsupported image",
    "vision is not supported",
)


def _make_red_png_b64() -> str:
    """Generate a small red PNG, base64-encoded."""

    def _chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    width = height = 32
    red_row = b"\x00" + (b"\xff\x00\x00" * width)
    ihdr = _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    idat = _chunk(b"IDAT", zlib.compress(red_row * height))
    iend = _chunk(b"IEND", b"")
    png = b"\x89PNG\r\n\x1a\n" + ihdr + idat + iend
    return base64.b64encode(png).decode()


DUMMY_IMAGE_B64: str = _make_red_png_b64()


def _iter_exception_error_values(value: Any, depth: int = 0) -> Iterable[str]:
    if depth > _IMAGE_INPUT_SCAN_MAX_DEPTH or value is None:
        return

    if isinstance(value, str):
        yield value
        return

    if isinstance(value, (int, float)):
        yield str(value)
        return

    if isinstance(value, dict):
        for child in value.values():
            yield from _iter_exception_error_values(child, depth + 1)
        return

    if isinstance(value, (list, tuple)):
        for child in value:
            yield from _iter_exception_error_values(child, depth + 1)
        return


def _extract_exception_error_values(exc: BaseException) -> List[str]:
    values = [str(exc)]

    for attr in ("code", "status_code", "message", "body"):
        attr_value = getattr(exc, attr, None)
        values.extend(_iter_exception_error_values(attr_value))

    response = getattr(exc, "response", None)
    if response is not None:
        values.extend(
            _iter_exception_error_values(
                getattr(response, "status_code", None)
            )
        )
        json_fn = getattr(response, "json", None)
        if callable(json_fn):
            try:
                values.extend(_iter_exception_error_values(json_fn()))
            except (TypeError, ValueError):
                pass
        text = getattr(response, "text", None)
        values.extend(_iter_exception_error_values(text))

    return values


def is_image_modality_rejection(exc: BaseException) -> bool:
    """Return True if *exc* is a deterministic client-side rejection of the image."""
    seen: set[int] = set()
    values: list[str] = []
    current: Optional[BaseException] = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        values.extend(_extract_exception_error_values(current))
        current = current.__cause__ or current.__context__

    lowered_values = [value.lower() for value in values if value]
    for value in lowered_values:
        normalized = value.replace("-", "_").replace(" ", "_")
        for code in _IMAGE_INPUT_UNSUPPORTED_ERROR_CODES:
            if normalized == code or normalized.endswith(f"_{code}"):
                return True

    text = "\n".join(lowered_values)
    for pattern in _IMAGE_INPUT_UNSUPPORTED_ERROR_PATTERNS:
        if pattern in text:
            return True

    return False


async def probe_image_support(llm) -> Optional[bool]:
    """Detect whether *llm* accepts native image input.

    Returns:
        True if the model named the color it was shown, False if it responded
        without naming it or deterministically rejected the image (e.g. a 404
        "no endpoints found that support image input"), and None if the call
        failed for some other reason (timeout, auth, rate limit, 5xx) and the
        result is therefore inconclusive and should not be cached.
    """
    try:
        response = await llm.invoke(
            [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{DUMMY_IMAGE_B64}",
                            },
                        },
                        {
                            "type": "text",
                            "text": "What color is this image? Reply with one word.",
                        },
                    ],
                }
            ],
            max_tokens=1024,
            temperature=0,
        )
    except Exception as exc:
        if is_image_modality_rejection(exc):
            logger.info(
                "[ImageModalityProbe] model rejected image input; treating read_file "
                "image multimodal as unsupported: %s",
                exc,
            )
            return False
        logger.warning("[ImageModalityProbe] image modality probe call failed: %s", exc)
        return None

    content = response.content if isinstance(response.content, str) else str(response.content)
    return "red" in content.lower()
