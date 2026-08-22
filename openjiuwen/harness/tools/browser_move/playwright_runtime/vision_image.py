# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Screenshot post-processing for ``browser_vision``.

Payload size is controlled capture-side first — JPEG quality plus the page's own
viewport — so Pillow is only a secondary clamp and stays an optional import.
``browser_move`` must not inherit the ``mobile-gui`` extra just to look at a page;
without Pillow the capture is passed through unchanged.
"""

from __future__ import annotations

import base64
import binascii
import io
from dataclasses import dataclass
from typing import Optional

from openjiuwen.core.common.logging import logger

DEFAULT_MAX_DIMENSION = 1280
_RESIZED_JPEG_QUALITY = 70


@dataclass(frozen=True)
class PreparedScreenshot:
    """A capture ready to enter model context."""

    base64_jpeg: str
    width: Optional[int]
    height: Optional[int]
    downscaled: bool

    @property
    def data_url(self) -> str:
        """The capture as a data URL, ready for an ``image_url`` content block."""
        return f"data:image/jpeg;base64,{self.base64_jpeg}"

    @property
    def approx_bytes(self) -> int:
        """Decoded size of the JPEG, for logging and budget checks."""
        return len(self.base64_jpeg) * 3 // 4


def prepare_screenshot(
    image_base64: str,
    *,
    max_dimension: int = DEFAULT_MAX_DIMENSION,
) -> PreparedScreenshot:
    """Clamp a base64 JPEG to ``max_dimension`` on its longest side.

    Returns the input untouched when Pillow is unavailable, when the image is
    already small enough, or when decoding fails: a slightly oversized screenshot
    is worth more to the model than no screenshot at all.
    """
    cleaned = str(image_base64 or "").strip()
    if not cleaned:
        return PreparedScreenshot(base64_jpeg="", width=None, height=None, downscaled=False)

    try:
        from PIL import Image  # noqa: PLC0415 — optional dependency, imported on use
    except ImportError:
        logger.debug("[browser_vision] Pillow not installed; sending the capture unscaled")
        return PreparedScreenshot(base64_jpeg=cleaned, width=None, height=None, downscaled=False)

    try:
        raw = base64.b64decode(cleaned, validate=True)
        with Image.open(io.BytesIO(raw)) as img:
            width, height = img.size
            if max(width, height) <= max_dimension:
                return PreparedScreenshot(
                    base64_jpeg=cleaned,
                    width=width,
                    height=height,
                    downscaled=False,
                )

            ratio = max_dimension / float(max(width, height))
            target = (max(1, int(width * ratio)), max(1, int(height * ratio)))
            resized = img.convert("RGB").resize(target, Image.LANCZOS)

            buffer = io.BytesIO()
            resized.save(buffer, format="JPEG", quality=_RESIZED_JPEG_QUALITY)
            encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    except (OSError, ValueError) as exc:
        logger.warning("[browser_vision] could not downscale the capture (%s); sending it unscaled", exc)
        return PreparedScreenshot(base64_jpeg=cleaned, width=None, height=None, downscaled=False)

    return PreparedScreenshot(
        base64_jpeg=encoded,
        width=target[0],
        height=target[1],
        downscaled=True,
    )


__all__ = [
    "DEFAULT_MAX_DIMENSION",
    "PreparedScreenshot",
    "prepare_screenshot",
]
