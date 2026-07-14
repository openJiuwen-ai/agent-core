# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import re
import urllib.parse
from pathlib import Path
from typing import FrozenSet, Iterator

_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp"})
_VIDEO_EXTENSIONS = frozenset({".mp4", ".mov", ".webm", ".mkv"})

_MARKDOWN_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\((.*?)\)")
_MARKDOWN_LINK_RE = re.compile(r"(?<!!)\[([^\]]*)\]\((.*?)\)")
_HTML_MEDIA_SRC_RE = re.compile(
    r"<(?:video|img)\b[^>]*?\bsrc=(['\"])(.*?)\1",
    re.IGNORECASE,
)
_BARE_MEDIA_PATH_RE = re.compile(
    r"(?<![\w./])"
    r"([\w./-]+\.(?:png|jpe?g|gif|webp|mp4|mov|webm|mkv))"
    r"(?![\w.])",
    re.IGNORECASE,
)


def _extension_for_markdown_url(raw_url: str) -> str:
    decoded = urllib.parse.unquote((raw_url or "").strip())
    path_part = decoded.split("?", 1)[0].split("#", 1)[0]
    return Path(path_part.replace("\\", "/")).suffix.lower()


def _iter_markdown_image_urls(text: str) -> Iterator[str]:
    for match in _MARKDOWN_IMAGE_RE.finditer(text or ""):
        yield (match.group(2) or "").strip()


def _iter_markdown_link_urls(text: str) -> Iterator[str]:
    for match in _MARKDOWN_LINK_RE.finditer(text or ""):
        yield (match.group(2) or "").strip()


def _iter_html_media_src_urls(text: str) -> Iterator[str]:
    for match in _HTML_MEDIA_SRC_RE.finditer(text or ""):
        yield (match.group(2) or "").strip()


def _iter_bare_media_paths(text: str) -> Iterator[str]:
    for match in _BARE_MEDIA_PATH_RE.finditer(text or ""):
        yield (match.group(1) or "").strip()


def _iter_media_reference_urls(text: str) -> Iterator[str]:
    """Yield candidate media paths/URLs from common markdown and HTML forms."""
    yield from _iter_markdown_image_urls(text)
    yield from _iter_markdown_link_urls(text)
    yield from _iter_html_media_src_urls(text)
    yield from _iter_bare_media_paths(text)


def _has_media_reference(text: str, extensions: FrozenSet[str]) -> bool:
    return any(
        _extension_for_markdown_url(url) in extensions
        for url in _iter_media_reference_urls(text)
        if url
    )


def markdown_has_image_reference(text: str) -> bool:
    """Return True when markdown/HTML contains an image media reference."""
    return _has_media_reference(text, _IMAGE_EXTENSIONS)


def markdown_has_video_reference(text: str) -> bool:
    """Return True when markdown/HTML contains a video media reference."""
    return _has_media_reference(text, _VIDEO_EXTENSIONS)


__all__ = [
    "markdown_has_image_reference",
    "markdown_has_video_reference",
]
