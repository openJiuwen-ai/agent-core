# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Bounded, non-consuming HTTP body previews for the host operation observer.

Multipart previews describe fields/files; they are not a second wire encoder.
Unknown/streaming/binary values are explicitly marked, never consumed to inspect
them. The preview is not a completeness claim or an authorization decision.
"""

from __future__ import annotations

import asyncio
import codecs
import inspect
import io
import json
import os
from collections.abc import Mapping
from typing import Any, Literal
from urllib.parse import urlencode

import aiohttp

MAX_PREVIEW_BYTES = 8192
UNAVAILABLE = "<binary/unavailable>"


class _Preview:
    def __init__(self) -> None:
        self.parts: list[str] = []
        self.remaining = MAX_PREVIEW_BYTES

    def append(self, value: str) -> None:
        # Slice characters first to avoid encoding an unbounded text value.
        encoded = value[: self.remaining].encode("utf-8", errors="replace")[: self.remaining]
        text = encoded.decode("utf-8", errors="ignore")
        self.parts.append(text)
        self.remaining -= len(text.encode("utf-8"))
        if len(encoded) > len(text.encode("utf-8")):
            self.remaining = 0  # Do not fill a partial UTF-8 character with later data.

    def text(self) -> str:
        return "".join(self.parts)


def _text(value: Any, limit: int) -> str:
    if isinstance(value, str):
        return value[:limit]
    if isinstance(value, (bytes, bytearray, memoryview)):
        prefix = bytes(value[:limit])
        try:
            # An incomplete UTF-8 character at the preview boundary is not binary.
            text = codecs.getincrementaldecoder("utf-8")().decode(prefix, final=len(value) < limit)
        except UnicodeDecodeError:
            return UNAVAILABLE
        if any(ord(char) < 32 and char not in "\t\n\r" for char in text):
            return UNAVAILABLE
        return text
    return UNAVAILABLE


def _stream_text(stream: Any, limit: int) -> str:
    # Only seekable streams can be inspected without consuming upload data.
    try:
        if any(
            inspect.iscoroutinefunction(getattr(stream, name, None)) for name in ("read", "seek", "tell", "seekable")
        ):
            return UNAVAILABLE
        if not stream.seekable():
            return UNAVAILABLE
        position = stream.tell()
        stream.seek(position)  # Check rewind support before the first read.
    except (AttributeError, OSError, TypeError, ValueError):
        return UNAVAILABLE
    try:
        try:
            return _text(stream.read(limit), limit)
        except (OSError, TypeError, ValueError):
            return UNAVAILABLE
    finally:
        # If restoration fails, stop before HTTP I/O instead of sending a body
        # whose prefix was consumed. Never silently swallow a failed rewind.
        stream.seek(position)


def _size(value: Any) -> int | None:
    if isinstance(value, str):
        return len(value.encode("utf-8"))
    if isinstance(value, memoryview):
        return value.nbytes
    if isinstance(value, (bytes, bytearray)):
        return len(value)
    try:
        if isinstance(value, io.BytesIO):
            with value.getbuffer() as view:
                return view.nbytes
        return os.fstat(value.fileno()).st_size
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def _filename(value: Any, fallback: Any) -> str:
    name = getattr(value, "name", None)
    return os.path.basename(name) if isinstance(name, str) else str(fallback)


def _file(preview: _Preview, field: Any, filename: Any, value: Any) -> None:
    if not preview.remaining:
        return
    metadata = {"field": str(field), "filename": filename, "size": _size(value)}
    preview.append("[file " + json.dumps(metadata, ensure_ascii=False) + "]\n")
    if preview.remaining:
        text = _stream_text(value, preview.remaining) if hasattr(value, "read") else _text(value, preview.remaining)
        preview.append(text)
        preview.append("\n")


def _pairs(value: Any):
    if isinstance(value, Mapping):
        return value.items()
    if isinstance(value, (list, tuple)) and all(isinstance(item, (list, tuple)) and len(item) == 2 for item in value):
        return value
    # Do not iterate generators/async iterables to construct a preview.
    return None


def _form(preview: _Preview, data: Any, client: str, *, charset: str = "utf-8") -> None:
    pairs = _pairs(data)
    if pairs is None:
        preview.append(UNAVAILABLE)
        return
    separator = ""
    for key, values in pairs:
        if not preview.remaining:
            break
        values = values if isinstance(values, (list, tuple)) else (values,)
        for value in values:
            if not preview.remaining:
                break
            if value is None and client == "requests":
                continue
            preview.append(separator)
            separator = "&"
            if hasattr(value, "read"):
                _file(preview, key, _filename(value, key), value)
            elif isinstance(value, (str, bytes, int, float, bool)) or value is None:
                # URL-encode a bounded field prefix, preserving duplicate keys.
                if isinstance(value, (str, bytes)):
                    value = value[: preview.remaining]
                if isinstance(key, (str, bytes)):
                    key = key[: preview.remaining]
                preview.append(urlencode([(key, value)], encoding=charset))
            else:
                preview.append(UNAVAILABLE)


def _multipart(preview: _Preview, files: Any, data: Any) -> None:
    if data:
        _form(preview, data, "requests")
        preview.append("\n")
    pairs = _pairs(files)
    if pairs is None:
        preview.append(UNAVAILABLE)
        return
    for field, item in pairs:
        if not preview.remaining:
            break
        filename, value = _filename(item, field), item
        if isinstance(item, (list, tuple)):
            if not 2 <= len(item) <= 4:
                preview.append(UNAVAILABLE)
                continue
            filename, value = item[:2]
        if value is not None:
            _file(preview, field, filename, value)


def _aiohttp_form(preview: _Preview, data: aiohttp.FormData) -> None:
    # FormData has no public non-consuming field iterator. Calling data() drains
    # multipart fields. Read this guarded layout only; fall back on API changes.
    fields = getattr(data, "_fields", None)
    if not isinstance(fields, list) or not fields:
        preview.append(UNAVAILABLE if data.is_multipart else "")
        return
    if not all(isinstance(item, tuple) and len(item) == 3 and isinstance(item[0], Mapping) for item in fields):
        preview.append(UNAVAILABLE)
        return
    if not data.is_multipart:
        _form(
            preview,
            [(options["name"], value) for options, _, value in fields],
            "aiohttp",
            charset=getattr(data, "_charset", None) or "utf-8",
        )
        return
    for options, _, value in fields:
        if not preview.remaining:
            break
        if "filename" in options or hasattr(value, "read"):
            _file(preview, options["name"], options.get("filename"), value)
        else:
            preview.append(str(options["name"]) + "=")
            preview.append(_text(value, preview.remaining))
            preview.append("\n")


def request_body_preview(kwargs: Mapping[str, Any], *, client: Literal["requests", "aiohttp"]) -> str:
    """Return at most 8192 UTF-8 bytes without modifying kwargs or consuming streams.

    Empty text means an empty body; unsupported values use UNAVAILABLE. File
    sizes are total bytes when knowable; content starts at the current cursor.
    """
    preview = _Preview()
    data = kwargs.get("data")
    files = kwargs.get("files") if client == "requests" else None
    if files:
        _multipart(preview, files, data)
    elif kwargs.get("json") is not None and (client == "aiohttp" or not data):
        for chunk in json.JSONEncoder(ensure_ascii=False).iterencode(kwargs["json"]):
            preview.append(chunk)
            if not preview.remaining:
                break
    elif data is None:
        pass
    elif isinstance(data, aiohttp.FormData):
        _aiohttp_form(preview, data)
    elif isinstance(data, (str, bytes, bytearray, memoryview)):
        preview.append(_text(data, preview.remaining))
    elif hasattr(data, "read"):
        if client == "aiohttp":
            _file(preview, "data", _filename(data, "data"), data)
        else:
            preview.append(_stream_text(data, preview.remaining))
    else:
        _form(preview, data, client)
    return preview.text()


async def async_request_body_preview(kwargs: Mapping[str, Any]) -> str:
    """Keep bounded file previews off the event loop, including cursor cleanup."""
    task = asyncio.create_task(asyncio.to_thread(request_body_preview, kwargs, client="aiohttp"))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # Finish restoring a file cursor before the caller can reuse its stream.
        completion = asyncio.gather(task, return_exceptions=True)
        while not completion.done():
            try:
                await asyncio.shield(completion)
            except asyncio.CancelledError:
                continue
        raise
