# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared image shrink pipeline for every image entry point.

User uploads and ``read_file`` image results both go through these helpers, so
the model always receives pixels that were prepared the same way:

* a copy that already fits (bytes and longest side) is used as-is;
* otherwise the image is scaled down with the aspect ratio preserved and
  encoded with quality tried from high to low — the first tier that fits wins;
* only an in-memory copy is produced; the source file is never modified, so a
  sharper render can always be regenerated from the original.
"""

from __future__ import annotations

import base64
import io
from pathlib import Path
from typing import Any, Optional, Tuple

DEFAULT_MAX_BYTES = 10 * 1024 * 1024
DEFAULT_MAX_DIMENSION = 1536

FORMAT_TO_MIME = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "WEBP": "image/webp",
    "GIF": "image/gif",
}

EXT_TO_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}

SUPPORTED_INLINE_MIME_TYPES = frozenset(FORMAT_TO_MIME.values())

_JPEG_QUALITY_STEPS = (95, 85, 70, 50, 30, 10)
_WEBP_QUALITY_STEPS = (95, 85, 70, 50, 30, 10)
_SCALE_STEP = 0.85
_MIN_DIMENSION = 32


def build_data_uri(data: bytes, mime_type: str) -> str:
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def detect_mime_type(raw: bytes, suffix: str = "") -> str:
    """Detect the image MIME type from the payload, falling back to the extension."""

    try:
        from PIL import Image
    except ImportError:
        return EXT_TO_MIME.get(suffix.lower(), "")
    try:
        with Image.open(io.BytesIO(raw)) as image:
            return FORMAT_TO_MIME.get(image.format or "", "") or EXT_TO_MIME.get(suffix.lower(), "")
    except Exception:
        return EXT_TO_MIME.get(suffix.lower(), "")


def shrink_image_bytes(
    raw: bytes,
    mime_type: str,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_dimension: int = DEFAULT_MAX_DIMENSION,
) -> Optional[Tuple[bytes, str]]:
    """Return ``(bytes, mime_type)`` for a copy that fits the inline budget.

    ``None`` is returned when the payload cannot be decoded or cannot be made to
    fit (the caller decides whether to fall back to a text-only description).
    """

    if not raw:
        return None
    if mime_type not in SUPPORTED_INLINE_MIME_TYPES:
        return None

    try:
        from PIL import Image
    except ImportError:
        if len(raw) <= max_bytes:
            return raw, mime_type
        return None

    try:
        with Image.open(io.BytesIO(raw)) as image:
            image.load()
            width, height = image.size
            if len(raw) <= max_bytes and max(width, height) <= max_dimension:
                return raw, mime_type

            resized = _resize_to_max_dimension(_prepare_for_resize(image), max_dimension)
            encoded = _encode_within_bytes(resized, mime_type, max_bytes)
            if encoded is not None:
                return encoded, mime_type

            while max(resized.size) > _MIN_DIMENSION:
                next_dimension = int(max(resized.size) * _SCALE_STEP)
                resized = _resize_to_max_dimension(resized, next_dimension)
                encoded = _encode_within_bytes(resized, mime_type, max_bytes)
                if encoded is not None:
                    return encoded, mime_type
    except Exception:
        return None
    return None


def shrink_image_to_data_uri(
    path: str,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_dimension: int = DEFAULT_MAX_DIMENSION,
) -> str:
    """Shrink ``path`` into a base64 ``data:`` URI; empty string on failure."""

    file_path = Path(path)
    if not file_path.is_file():
        return ""
    try:
        raw = file_path.read_bytes()
    except OSError:
        return ""
    if not raw:
        return ""
    mime_type = detect_mime_type(raw, file_path.suffix)
    result = shrink_image_bytes(
        raw,
        mime_type,
        max_bytes=max_bytes,
        max_dimension=max_dimension,
    )
    if result is None:
        return ""
    data, used_mime = result
    return build_data_uri(data, used_mime)


def _prepare_for_resize(image: Any) -> Any:
    if image.mode in ("RGB", "RGBA", "L"):
        return image
    if image.mode in ("LA", "PA") or (image.mode == "P" and "transparency" in image.info):
        return image.convert("RGBA")
    return image.convert("RGB")


def _resize_to_max_dimension(image: Any, max_dimension: int) -> Any:
    from PIL import Image

    width, height = image.size
    longest = max(width, height)
    if longest <= max_dimension:
        return image
    ratio = max_dimension / longest
    size = (max(1, round(width * ratio)), max(1, round(height * ratio)))
    return image.resize(size, Image.Resampling.LANCZOS)


def _encode_within_bytes(image: Any, mime_type: str, max_bytes: int) -> Optional[bytes]:
    if mime_type == "image/png":
        encoded = _save_image(image, format="PNG", optimize=True)
        return encoded if len(encoded) <= max_bytes else None
    if mime_type == "image/jpeg":
        rgb = image.convert("RGB")
        for quality in _JPEG_QUALITY_STEPS:
            encoded = _save_image(rgb, format="JPEG", quality=quality, optimize=True)
            if len(encoded) <= max_bytes:
                return encoded
        return None
    if mime_type == "image/webp":
        for quality in _WEBP_QUALITY_STEPS:
            encoded = _save_image(image, format="WEBP", quality=quality)
            if len(encoded) <= max_bytes:
                return encoded
        return None
    if mime_type == "image/gif":
        encoded = _save_image(image.convert("RGB"), format="GIF", optimize=True)
        return encoded if len(encoded) <= max_bytes else None
    return None


def _save_image(image: Any, *, format: str, **kwargs: Any) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format=format, **kwargs)
    return buf.getvalue()
