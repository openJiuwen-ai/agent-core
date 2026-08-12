# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Provider-neutral content parts for :class:`BaseMessage`.
   Instead of taking opaque dictionaries that cannot be verified by pydantic,
   :class:`ContentPart` provides known interfaces for both text and image inputs
   and can be easily extended to deal with other modals such as audio and video

.. code-block:: text

    producer -> ContentPart -> client render -> provider wire format

Nothing in this module talks to a provider. Translation *out* belongs to the
model clients; translation *in* is :func:`normalize_content_part`, which is
deliberately total: it never raises, and returns unrecognized input untouched.
"""

from __future__ import annotations

import base64
import binascii
import math
from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BaseModel, Field, TypeAdapter, model_validator
from pydantic import ValidationError as PydanticValidationError

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import raise_error
from openjiuwen.core.common.logging import llm_logger

# Fallback billing estimate for an image whose pixel dimensions are unknown.
# Also the placeholder ``TiktokenCounter`` bills such an image at, where it is
# overridable via ``TIKTOKEN_IMAGE_PLACEHOLDER_TOKENS``.
DEFAULT_IMAGE_TOKENS = 1445

_DATA_URL_PREFIX = "data:"
_BASE64_MARKER = ";base64,"


class TextPart(BaseModel):
    """A plain-text span of message content."""

    type: Literal["text"] = "text"
    text: str


class ImagePart(BaseModel):
    """An image carried either inline (``data``) or by reference (``url``).

    Exactly one of ``data``/``url`` must be set.
    """

    type: Literal["image"] = "image"
    mime_type: str
    data: Optional[str] = None
    """Base64 payload **without** the ``data:<mime>;base64,`` prefix."""
    url: Optional[str] = None
    detail: Literal["auto", "low", "high"] = "auto"
    width: Optional[int] = None
    height: Optional[int] = None

    @model_validator(mode="after")
    def _require_exactly_one_source(self) -> "ImagePart":
        if bool(self.data) == bool(self.url):
            raise ValueError("ImagePart requires exactly one of 'data' or 'url'")
        return self

    @classmethod
    def from_data_url(cls, url: str, **kwargs: Any) -> "ImagePart":
        if not isinstance(url, str) or not url.startswith(_DATA_URL_PREFIX):
            raise_error(
                StatusCode.MODEL_INVOKE_PARAM_ERROR,
                error_msg=f"expected a 'data:' URL, got: {str(url)[:32]!r}",
            )

        head, marker, payload = url.partition(_BASE64_MARKER)
        if not marker:
            raise_error(
                StatusCode.MODEL_INVOKE_PARAM_ERROR,
                error_msg="data URL is not base64-encoded, expected a ';base64,' marker",
            )

        mime_type = head.removeprefix(_DATA_URL_PREFIX)
        if not mime_type:
            raise_error(
                StatusCode.MODEL_INVOKE_PARAM_ERROR,
                error_msg="data URL is missing its media type",
            )

        try:
            base64.b64decode(payload, validate=True)
        except ValueError as exc:
            raise_error(
                StatusCode.MODEL_INVOKE_PARAM_ERROR,
                error_msg="data URL payload is not valid base64",
                cause=exc,
            )

        return cls(mime_type=mime_type, data=payload, **kwargs)

    @classmethod
    def from_data_url_unchecked(cls, url: str, **kwargs: Any) -> "ImagePart":
        body = url.removeprefix(_DATA_URL_PREFIX)
        mime_type, marker, payload = body.partition(_BASE64_MARKER)
        if marker and payload:
            return cls(mime_type=mime_type or "image/png", data=payload, **kwargs)

        # No base64 marker: ``mime_type`` above swallowed the whole body, so
        # re-read it as the media type only — up to the first ';' or ','.
        media_type = body.split(";", 1)[0].split(",", 1)[0]
        return cls(mime_type=media_type or "image/png", url=url, **kwargs)

    def to_data_url(self) -> str:
        """Render back to a ``data:`` URL. Requires inline ``data``."""
        if not self.data:
            raise_error(
                StatusCode.MODEL_INVOKE_PARAM_ERROR,
                error_msg="cannot build a data URL from a url-backed ImagePart",
            )
        return f"{_DATA_URL_PREFIX}{self.mime_type}{_BASE64_MARKER}{self.data}"

    def estimated_tokens(self, provider: str) -> int:
        """Estimate what this image costs on ``provider``.

        Falls back to :data:`DEFAULT_IMAGE_TOKENS` whenever the provider is
        unknown or ``width``/``height`` are absent — dimensions are optional
        because most producers only ever see the encoded bytes.
        """
        if self.width is None or self.height is None:
            return DEFAULT_IMAGE_TOKENS

        estimator = _TOKEN_ESTIMATORS.get(provider.strip().lower())
        if estimator is None:
            return DEFAULT_IMAGE_TOKENS
        return estimator(self)


ContentPart = Annotated[
    Union[TextPart, ImagePart],
    Field(discriminator="type"),
]

_CONTENT_PART_ADAPTER: TypeAdapter[Union[TextPart, ImagePart]] = TypeAdapter(ContentPart)


# ---------------------------------------------------------------------------
# Per-provider token estimation
# ---------------------------------------------------------------------------

_OPENAI_LOW_DETAIL_TOKENS = 85
_OPENAI_TILE_TOKENS = 170
_OPENAI_TILE_PX = 512
_OPENAI_MAX_EDGE_PX = 2048
_OPENAI_SHORT_EDGE_PX = 768

_ANTHROPIC_PX_PER_TOKEN = 750
_ANTHROPIC_MAX_TOKENS = 1600


def _openai_tokens(part: ImagePart) -> int:
    """OpenAI vision pricing: a base cost plus one charge per 512px tile."""
    if part.detail == "low":
        return _OPENAI_LOW_DETAIL_TOKENS

    width, height = part.width, part.height
    longest = max(width, height)
    if longest > _OPENAI_MAX_EDGE_PX:
        scale = _OPENAI_MAX_EDGE_PX / longest
        width, height = int(width * scale), int(height * scale)

    shortest = min(width, height)
    if shortest > _OPENAI_SHORT_EDGE_PX:
        scale = _OPENAI_SHORT_EDGE_PX / shortest
        width, height = int(width * scale), int(height * scale)

    tiles = math.ceil(width / _OPENAI_TILE_PX) * math.ceil(height / _OPENAI_TILE_PX)
    return _OPENAI_LOW_DETAIL_TOKENS + _OPENAI_TILE_TOKENS * tiles


def _anthropic_tokens(part: ImagePart) -> int:
    """Anthropic vision pricing: roughly ``width * height / 750``."""
    return min(
        math.ceil(part.width * part.height / _ANTHROPIC_PX_PER_TOKEN),
        _ANTHROPIC_MAX_TOKENS,
    )


_TOKEN_ESTIMATORS = {
    "openai": _openai_tokens,
    "anthropic": _anthropic_tokens,
}


# ---------------------------------------------------------------------------
# Normalization: the several inbound dialects -> ContentPart
# ---------------------------------------------------------------------------


def normalize_content_part(item: Any) -> Any:
    """Best-effort conversion of one raw content item into a :data:`ContentPart`.

    Recognized inputs:

    * an existing ``TextPart``/``ImagePart`` — returned as-is
    * ``str`` — becomes a ``TextPart``
    * ``{"type": "text", "text": ...}`` — the OpenAI text block
    * ``{"type": "image_url", "image_url": {"url": ...}}`` — the OpenAI image
      block, as emitted by ``react_agent`` and the mobile GUI skill runner
    * ``{"type": "image", "data_url": ..., "mime_type": ...}`` — the shape
      ``harness/tools/filesystem.py`` attaches to ``read_file`` results

    Anything else is returned unchanged instead of raising.
    """
    if isinstance(item, (TextPart, ImagePart)):
        return item
    if isinstance(item, str):
        return TextPart(text=item)
    if isinstance(item, dict):
        return _part_from_dict(item)
    llm_logger.debug("content item of type %s is not a known content part", type(item).__name__)
    return item


def _part_from_dict(item: dict) -> Any:
    # Canonical shape first, so a part that was serialized with ``model_dump``
    # is read back as the same part. Without this, persisting through
    # ``session/vcs`` and reloading would silently downgrade an ImagePart to an
    # opaque dict, since no inbound dialect matches its own dump shape.
    part = _part_from_canonical_dict(item)
    if part is not None:
        return part

    item_type = item.get("type")

    if item_type == "text" and isinstance(item.get("text"), str):
        return TextPart(text=item["text"])

    if item_type == "image_url":
        part = _image_from_openai_block(item)
        if part is not None:
            return part

    if item_type == "image" and isinstance(item.get("data_url"), str):
        part = _image_from_data_url(item["data_url"], **_stamped_dimensions(item))
        if part is not None:
            return part

    llm_logger.debug("content dict is not a known content part, preserved verbatim: %s", sorted(item))
    return item


def _part_from_canonical_dict(item: dict) -> Optional[Any]:
    """Validate against :data:`ContentPart` itself, or ``None`` if it does not fit."""
    try:
        return _CONTENT_PART_ADAPTER.validate_python(item)
    except PydanticValidationError:
        return None


def _image_from_openai_block(item: dict) -> Optional[ImagePart]:
    image_url = item.get("image_url")
    if not isinstance(image_url, dict):
        return None

    url = image_url.get("url")
    if not isinstance(url, str) or not url:
        return None

    detail = image_url.get("detail")
    extra: dict[str, Any] = {"detail": detail} if detail in ("auto", "low", "high") else {}
    extra.update(_stamped_dimensions(item))

    if url.startswith(_DATA_URL_PREFIX):
        return _image_from_data_url(url, **extra)
    return ImagePart(mime_type=_mime_type_from_url(url), url=url, **extra)


def _stamped_dimensions(item: dict) -> dict[str, int]:
    """Extract image dimensions when available."""
    width, height = item.get("width"), item.get("height")
    if all(isinstance(x, int) and x > 0 for x in [width, height]):
        return {"width": width, "height": height}

    value = item.get("dimensions")
    if isinstance(value, str) and "x" in value.lower():
        left, _, right = value.lower().partition("x")
        value = [left, right]
    if isinstance(value, (list, tuple)) and len(value) == 2:
        try:
            width, height = int(value[0]), int(value[1])
        except (TypeError, ValueError):
            return {}
        if width > 0 and height > 0:
            return {"width": width, "height": height}
    return {}


def _image_from_data_url(url: str, **kwargs: Any) -> Optional[ImagePart]:
    """Parse a data URL, degrading to ``None`` instead of raising."""
    try:
        return ImagePart.from_data_url(url, **kwargs)
    except Exception as exc:  # noqa: BLE001 — normalization must not raise
        llm_logger.debug("failed to parse image data URL, preserving verbatim: %s", exc)
        return None


def _mime_type_from_url(url: str) -> str:
    """Guess a media type from a remote URL's extension, defaulting to PNG."""
    suffix = url.rsplit(".", 1)[-1].split("?", 1)[0].lower()
    return _URL_SUFFIX_MIME_TYPES.get(suffix, "image/png")


_URL_SUFFIX_MIME_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    "bmp": "image/bmp",
}


__all__ = [
    "ContentPart",
    "DEFAULT_IMAGE_TOKENS",
    "ImagePart",
    "TextPart",
    "normalize_content_part",
]
