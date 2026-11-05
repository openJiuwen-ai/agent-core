# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shared helpers for ``read_file`` image messages kept in model context.

``ReActAgent`` tags each image ``UserMessage`` with ``IMAGE_METADATA_KEY``;
``ImageWindowOffloader`` folds old image blocks into text placeholders and
records them under ``FOLDED_METADATA_KEY``; compressors append an
``<image_refs>`` block so folded/compressed images stay recoverable by path.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Protocol

IMAGE_METADATA_KEY = "multimodal_images"
FOLDED_METADATA_KEY = "multimodal_images_folded"
READ_FILE_IMAGE_TEXT_PREFIX = "Image loaded from read_file: "
FOLDED_PLACEHOLDER_PREFIX = "[图片已折叠 "
IMAGE_REFS_OPEN = "<image_refs>"
IMAGE_REFS_CLOSE = "</image_refs>"

SUMMARY_STATUS_PENDING = "pending"
SUMMARY_STATUS_READY = "ready"
SUMMARY_STATUS_FAILED = "failed"

_IMAGE_KEY_LENGTH = 12
_READ_FILE_PATH_RE = re.compile(r"^Image loaded from read_file:\s*(.+?)\s*$", re.MULTILINE)
_IMAGE_REFS_BLOCK_RE = re.compile(
    re.escape(IMAGE_REFS_OPEN) + r"(.*?)" + re.escape(IMAGE_REFS_CLOSE),
    re.DOTALL,
)
_FOLDED_PATH_RE = re.compile(r"\[图片已折叠 path=(.+?)(?: size=\d+x\d+)?(?: 摘要=|，)")
_REF_FIELD_SEPARATOR = " | "


def image_key(data_url: str) -> str:
    """Stable short key of an image payload; same bytes give the same key."""
    return hashlib.sha1(data_url.encode("utf-8")).hexdigest()[:_IMAGE_KEY_LENGTH]


def _is_positive_size(width: Optional[int], height: Optional[int]) -> bool:
    """Whether ``width``/``height`` are both positive integers (a valid size)."""
    if not isinstance(width, int) or not isinstance(height, int):
        return False
    return width > 0 and height > 0


def build_image_entry(
    source_path: str,
    data_url: str,
    block_index: int,
    *,
    width: Optional[int] = None,
    height: Optional[int] = None,
) -> Dict[str, Any]:
    entry: Dict[str, Any] = {
        "source_path": source_path,
        "key": image_key(data_url),
        "block_index": block_index,
    }
    if _is_positive_size(width, height):
        entry["width"] = width
        entry["height"] = height
    return entry


def data_image_url(block: Any) -> Optional[str]:
    """Return the ``data:image/...`` URL of an ``image_url`` content block, else None."""
    if not isinstance(block, dict) or block.get("type") != "image_url":
        return None
    image_url = block.get("image_url")
    url = image_url.get("url") if isinstance(image_url, dict) else image_url
    if isinstance(url, str) and url.startswith("data:image/"):
        return url
    return None


@dataclass(frozen=True)
class ImageSummary:
    """Summary record supplied by an :class:`ImageSummaryProvider`."""

    status: str
    summary: str = ""
    width: Optional[int] = None
    height: Optional[int] = None


class ImageSummaryProvider(Protocol):
    """Looks up the summary of an image by key; must be cheap and non-blocking."""

    def lookup(self, key: str, *, session_id: Optional[str] = None) -> Optional[ImageSummary]:
        ...


_SUMMARY_PROVIDER: Optional[ImageSummaryProvider] = None


def set_image_summary_provider(provider: Optional[ImageSummaryProvider]) -> None:
    """Register the process-wide image summary provider (``None`` unregisters)."""
    global _SUMMARY_PROVIDER  # pylint: disable=global-statement
    _SUMMARY_PROVIDER = provider


def get_image_summary_provider() -> Optional[ImageSummaryProvider]:
    return _SUMMARY_PROVIDER


def lookup_image_summary(key: Optional[str], session_id: Optional[str]) -> Optional[ImageSummary]:
    provider = _SUMMARY_PROVIDER
    if provider is None or not key:
        return None
    try:
        return provider.lookup(key, session_id=session_id)
    except Exception:  # provider failures must never break the model call
        return None


def _size_text(width: Optional[int], height: Optional[int]) -> str:
    if not _is_positive_size(width, height):
        return ""
    return f"{width}x{height}"


def format_folded_placeholder(
    source_path: str,
    summary: Optional[ImageSummary] = None,
    *,
    width: Optional[int] = None,
    height: Optional[int] = None,
) -> str:
    if summary is not None:
        width = summary.width or width
        height = summary.height or height
    size = _size_text(width, height)
    head = f"{FOLDED_PLACEHOLDER_PREFIX}path={source_path}"
    if size and summary is not None:
        head += f" size={size}"
    if summary is None:
        return f"{head}，需要查看请调用 read_file 重新读取该路径]"
    if summary.status == SUMMARY_STATUS_READY and summary.summary:
        return (
            f"{head} 摘要={summary.summary}。摘要为自动生成的粗略描述，数字和细节请以原图为准，"
            "需要时调用 read_file 重新读取该路径]"
        )
    if summary.status == SUMMARY_STATUS_PENDING:
        label = "生成中"
    elif summary.status == SUMMARY_STATUS_FAILED:
        label = "生成失败"
    else:
        label = "无"
    return f"{head} 摘要={label}，需要查看请调用 read_file 重新读取该路径]"


def is_folded_placeholder_block(block: Any) -> bool:
    return (
        isinstance(block, dict)
        and block.get("type") == "text"
        and isinstance(block.get("text"), str)
        and block["text"].startswith(FOLDED_PLACEHOLDER_PREFIX)
    )


@dataclass
class ImageRef:
    path: str
    key: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None


def _metadata_entries(message: Any, metadata_key: str) -> List[Dict[str, Any]]:
    metadata = getattr(message, "metadata", None)
    if not isinstance(metadata, dict):
        return []
    entries = metadata.get(metadata_key)
    if not isinstance(entries, list):
        return []
    return [entry for entry in entries if isinstance(entry, dict) and entry.get("source_path")]


def _message_texts(message: Any) -> List[str]:
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return [content]
    if not isinstance(content, list):
        return []
    return [
        block["text"]
        for block in content
        if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str)
    ]


def _paths_from_refs_blocks(text: str) -> List[str]:
    paths: List[str] = []
    for block in _IMAGE_REFS_BLOCK_RE.findall(text):
        for line in block.splitlines():
            line = line.strip()
            if not line.startswith("- "):
                continue
            path = line[2:].split(_REF_FIELD_SEPARATOR, 1)[0].strip()
            if path:
                paths.append(path)
    return paths


def compressed_image_paths(messages: Iterable[Any]) -> List[str]:
    """Paths listed in ``<image_refs>`` blocks of earlier compression summaries."""
    paths: List[str] = []
    for message in messages:
        for text in _message_texts(message):
            if IMAGE_REFS_OPEN in text:
                paths.extend(_paths_from_refs_blocks(text))
    return paths


def collect_image_refs(messages: Iterable[Any]) -> List[ImageRef]:
    """Ordered, path-unique image references found in ``messages``.

    Sources: image metadata (live and folded), ``Image loaded from read_file``
    text, folded placeholders, and ``<image_refs>`` blocks of earlier summaries.
    """
    refs: Dict[str, ImageRef] = {}

    def _add(path: str, key: Optional[str] = None, width: Any = None, height: Any = None) -> None:
        path = path.strip()
        if not path:
            return
        ref = refs.get(path)
        if ref is None:
            ref = ImageRef(path=path)
            refs[path] = ref
        if key:
            ref.key = key
        if isinstance(width, int) and isinstance(height, int):
            ref.width, ref.height = width, height

    for message in messages:
        for metadata_key in (IMAGE_METADATA_KEY, FOLDED_METADATA_KEY):
            for entry in _metadata_entries(message, metadata_key):
                _add(
                    str(entry["source_path"]),
                    entry.get("key"),
                    entry.get("width"),
                    entry.get("height"),
                )
        for text in _message_texts(message):
            if IMAGE_REFS_OPEN in text:
                for path in _paths_from_refs_blocks(text):
                    _add(path)
            for path in _READ_FILE_PATH_RE.findall(text):
                _add(path)
            for path in _FOLDED_PATH_RE.findall(text):
                _add(path)
    return list(refs.values())


def render_image_refs_block(refs: List[ImageRef], *, session_id: Optional[str] = None) -> str:
    if not refs:
        return ""
    lines = [IMAGE_REFS_OPEN]
    for ref in refs:
        fields = [ref.path]
        summary = lookup_image_summary(ref.key, session_id)
        width = (summary.width if summary else None) or ref.width
        height = (summary.height if summary else None) or ref.height
        size = _size_text(width, height)
        if size:
            fields.append(f"size={size}")
        if summary is not None:
            if summary.status == SUMMARY_STATUS_READY and summary.summary:
                fields.append(f"摘要={summary.summary}")
            elif summary.status == SUMMARY_STATUS_PENDING:
                fields.append("摘要=生成中")
            elif summary.status == SUMMARY_STATUS_FAILED:
                fields.append("摘要=生成失败")
            else:
                fields.append("摘要=无")
        lines.append("- " + _REF_FIELD_SEPARATOR.join(fields))
    lines.append(IMAGE_REFS_CLOSE)
    return "\n".join(lines)


def strip_image_refs_blocks(text: str) -> str:
    return _IMAGE_REFS_BLOCK_RE.sub("", text).rstrip()


def append_image_refs_block(
    summary: str,
    messages: Iterable[Any],
    *,
    session_id: Optional[str] = None,
) -> str:
    """Append an ``<image_refs>`` block listing images referenced by ``messages``."""
    refs = collect_image_refs(messages)
    if not refs:
        return summary
    block = render_image_refs_block(refs, session_id=session_id)
    return f"{strip_image_refs_blocks(summary)}\n\n{block}"
