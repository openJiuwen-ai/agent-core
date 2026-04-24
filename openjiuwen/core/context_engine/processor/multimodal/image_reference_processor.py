# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Inject image references into the outgoing model context window.

The processor keeps persisted conversation history textual. When a model
context window is materialized, it resolves image paths or image URLs found in
user/tool messages and adds OpenAI-compatible multimodal user messages only to
that temporary window.
"""
from __future__ import annotations

import asyncio
import base64
import mimetypes
import os
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Literal

from pydantic import BaseModel, Field

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.logging import logger
from openjiuwen.core.context_engine.base import ContextWindow, ModelContext
from openjiuwen.core.context_engine.context_engine import ContextEngine
from openjiuwen.core.context_engine.processor.base import ContextEvent, ContextProcessor
from openjiuwen.core.foundation.llm import BaseMessage, ToolMessage, UserMessage
from openjiuwen.core.sys_operation import SysOperation


_IMAGE_EXTENSIONS = {
    ".png": "png",
    ".jpg": "jpeg",
    ".jpeg": "jpeg",
    ".webp": "webp",
    ".gif": "gif",
    ".bmp": "bmp",
}
_EXT_PATTERN = r"(?:png|jpe?g|webp|gif|bmp)"

_REMOTE_IMAGE_RE = re.compile(
    rf"https?://[^\s\"'<>]+?\.{_EXT_PATTERN}(?:\?[^\s\"'<>]*)?",
    re.IGNORECASE,
)
_FILE_URL_RE = re.compile(
    rf"file:///[^\s\"'<>]+?\.{_EXT_PATTERN}",
    re.IGNORECASE,
)
_WINDOWS_PATH_RE = re.compile(
    rf"(?:[A-Za-z]:|\\\\[^\\/:*?\"<>|\r\n]+\\[^\\/:*?\"<>|\r\n]+)"
    rf"[\\/][^\r\n\"'<>|]*?\.{_EXT_PATTERN}",
    re.IGNORECASE,
)
_POSIX_PATH_RE = re.compile(
    rf"/(?:[^\s\"'<>|]+/)*[^\s\"'<>|]*?\.{_EXT_PATTERN}",
    re.IGNORECASE,
)
_DATA_IMAGE_URL_RE = re.compile(
    rf"data:image/{_EXT_PATTERN};base64,[A-Za-z0-9+/=\r\n]+",
    re.IGNORECASE,
)
_OMITTED_DATA_URL = "[image data URL omitted; injected as multimodal context]"


class ImageReferenceProcessorConfig(BaseModel):
    """Configuration for resolving image references in model context windows."""

    scan_user_messages: bool = Field(default=True)
    """Resolve image paths/URLs mentioned directly by user messages."""

    scan_tool_messages: bool = Field(default=True)
    """Resolve image paths/URLs found in recent tool messages."""

    max_images: int = Field(default=4, ge=1, le=20)
    """Maximum number of resolved images injected per context window."""

    max_image_bytes: int = Field(default=10 * 1024 * 1024, ge=1)
    """Maximum local or downloaded image size accepted for data URL injection."""

    allowed_roots: list[str] = Field(default_factory=list)
    """Optional local path roots. Empty means sys_operation/default filesystem policy applies."""

    remote_url_policy: Literal["direct", "download", "disabled"] = Field(default="direct")
    """How remote image URLs are handled: pass through, download as data URL, or ignore."""

    remote_timeout: float = Field(default=10.0, gt=0)
    """Timeout in seconds for remote image download when remote_url_policy='download'."""

    recent_tool_messages: int = Field(default=4, ge=1, le=50)
    """Number of latest tool messages scanned for image paths."""

    synthetic_tool_context_text: str = Field(
        default=(
            "[Runtime image context]\n"
            "Recent tool results referenced these image files. Inspect the attached images directly."
        )
    )
    """Text used for the temporary synthetic user message generated from tool results."""


@dataclass(frozen=True)
class _ImageRef:
    raw: str
    source: str
    is_remote: bool = False
    is_data_url: bool = False


@dataclass(frozen=True)
class _ResolvedImage:
    raw: str
    source: str
    model_url: str
    label: str


@ContextEngine.register_processor()
class ImageReferenceProcessor(ContextProcessor):
    """Temporarily expand image references into multimodal user messages."""

    def __init__(self, config: ImageReferenceProcessorConfig):
        super().__init__(config)

    async def trigger_get_context_window(
            self,
            context: ModelContext,  # noqa: ARG002
            context_window: ContextWindow,
            **kwargs: Any,  # noqa: ARG002
    ) -> bool:
        if self.config.scan_user_messages:
            for message in context_window.context_messages:
                if isinstance(message, UserMessage) and self._message_text(message):
                    if self._extract_image_refs(self._message_text(message)):
                        return True
        if self.config.scan_tool_messages:
            for message in self._recent_tool_messages(context_window.context_messages):
                if self._extract_image_refs(self._message_text(message)):
                    return True
        return False

    async def on_get_context_window(
            self,
            context: ModelContext,  # noqa: ARG002
            context_window: ContextWindow,
            **kwargs: Any,
    ) -> tuple[ContextEvent | None, ContextWindow]:
        sys_operation = kwargs.get("sys_operation")
        resolved_count = 0
        seen_sources: set[str] = set()
        new_messages: list[BaseMessage] = []

        if self.config.scan_user_messages:
            for message in context_window.context_messages:
                if not isinstance(message, UserMessage):
                    new_messages.append(message)
                    continue

                text = self._message_text(message)
                refs = self._extract_image_refs(text)
                if not refs:
                    new_messages.append(message)
                    continue

                limit = self.config.max_images - resolved_count
                resolved = await self._resolve_refs(refs, sys_operation, seen_sources, limit)
                if not resolved:
                    new_messages.append(message)
                    continue

                resolved_count += len(resolved)
                new_messages.append(self._build_user_image_message(text, resolved))
        else:
            new_messages = list(context_window.context_messages)

        tool_refs: list[_ImageRef] = []
        if self.config.scan_tool_messages:
            tool_refs = self._extract_refs_from_tool_messages(new_messages)
            new_messages = self._sanitize_tool_data_urls(new_messages)

        if self.config.scan_tool_messages and resolved_count < self.config.max_images:
            limit = self.config.max_images - resolved_count
            resolved = await self._resolve_refs(tool_refs, sys_operation, seen_sources, limit)
            if resolved:
                new_messages.append(self._build_tool_image_context_message(resolved))

        context_window.context_messages = new_messages
        return ContextEvent(event_type=self.processor_type()), context_window

    def load_state(self, state: dict[str, Any]) -> None:  # noqa: ARG002
        return

    def save_state(self) -> dict[str, Any]:
        return {}

    @property
    def config(self) -> ImageReferenceProcessorConfig:
        return self._config

    @staticmethod
    def _message_text(message: BaseMessage) -> str:
        content = getattr(message, "content", "")
        return content if isinstance(content, str) else ""

    def _extract_refs_from_tool_messages(self, messages: list[BaseMessage]) -> list[_ImageRef]:
        refs: list[_ImageRef] = []
        for message in self._recent_tool_messages(messages):
            refs.extend(self._extract_image_refs(self._message_text(message)))
        return refs

    def _recent_tool_messages(self, messages: list[BaseMessage]) -> list[ToolMessage]:
        tool_messages = [message for message in messages if isinstance(message, ToolMessage)]
        return tool_messages[-self.config.recent_tool_messages:]

    def _extract_image_refs(self, text: str) -> list[_ImageRef]:
        if not text:
            return []

        refs: list[_ImageRef] = []
        refs.extend(
            _ImageRef(
                raw=match.group(0),
                source=match.group(0),
                is_data_url=True,
            )
            for match in _DATA_IMAGE_URL_RE.finditer(text)
        )
        for regex, is_remote in (
                (_REMOTE_IMAGE_RE, True),
                (_FILE_URL_RE, False),
                (_WINDOWS_PATH_RE, False),
                (_POSIX_PATH_RE, False),
        ):
            refs.extend(
                _ImageRef(
                    raw=match.group(0),
                    source=self._normalize_source(match.group(0), is_remote=is_remote),
                    is_remote=is_remote,
                )
                for match in regex.finditer(text)
            )
        return self._dedupe_refs(refs)

    @staticmethod
    def _dedupe_refs(refs: Iterable[_ImageRef]) -> list[_ImageRef]:
        result: list[_ImageRef] = []
        seen: set[str] = set()
        for ref in refs:
            key = ref.source.lower()
            if key in seen:
                continue
            seen.add(key)
            result.append(ref)
        return result

    @staticmethod
    def _normalize_source(source: str, *, is_remote: bool) -> str:
        value = source.strip().strip("`'\"),.;")
        if is_remote:
            return value
        if value.lower().startswith("file:///"):
            parsed = urllib.parse.urlparse(value)
            path = urllib.request.url2pathname(parsed.path)
            if re.match(r"^/[A-Za-z]:/", path):
                path = path[1:]
            return path
        if re.match(r"^[A-Za-z]:\\\\", value):
            value = value.replace("\\\\", "\\")
        return value

    async def _resolve_refs(
            self,
            refs: list[_ImageRef],
            sys_operation: Any,
            seen_sources: set[str],
            limit: int,
    ) -> list[_ResolvedImage]:
        resolved: list[_ResolvedImage] = []
        if limit <= 0:
            return resolved

        for ref in refs:
            if len(resolved) >= limit:
                break
            key = ref.source.lower()
            if key in seen_sources:
                continue
            seen_sources.add(key)
            image = await self._resolve_ref(ref, sys_operation)
            if image is not None:
                resolved.append(image)
        return resolved

    async def _resolve_ref(self, ref: _ImageRef, sys_operation: Any) -> _ResolvedImage | None:
        try:
            if ref.is_data_url:
                model_url = ref.source
            elif ref.is_remote:
                model_url = await self._resolve_remote_url(ref.source)
            else:
                model_url = await self._resolve_local_path(ref.source, sys_operation)
        except Exception as exc:
            logger.warning(
                "Failed to resolve image reference '%s': %s",
                self._safe_label(ref.source),
                exc,
            )
            return None

        if not model_url:
            return None
        return _ResolvedImage(
            raw=ref.raw,
            source=ref.source,
            model_url=model_url,
            label=self._safe_label(ref.source),
        )

    async def _resolve_remote_url(self, url: str) -> str | None:
        if self.config.remote_url_policy == "disabled":
            return None
        if self.config.remote_url_policy == "direct":
            return url

        def _download() -> tuple[bytes, str]:
            request = urllib.request.Request(url, headers={"User-Agent": "OpenJiuWen/1.0"})
            with urllib.request.urlopen(request, timeout=self.config.remote_timeout) as response:
                content_type = response.headers.get_content_type()
                raw = response.read(self.config.max_image_bytes + 1)
            return raw, content_type

        raw, content_type = await asyncio.to_thread(_download)
        if len(raw) > self.config.max_image_bytes:
            raise ValueError("remote image exceeds max_image_bytes")
        if not content_type.startswith("image/"):
            content_type = self._mime_type_from_path(url)
        return self._data_url(raw, content_type)

    async def _resolve_local_path(self, path: str, sys_operation: Any) -> str | None:
        if not self._is_allowed_local_path(path):
            return None
        raw = await self._read_local_bytes(path, sys_operation)
        if not raw:
            return None
        if len(raw) > self.config.max_image_bytes:
            raise ValueError("local image exceeds max_image_bytes")
        return self._data_url(raw, self._mime_type_from_path(path))

    def _is_allowed_local_path(self, path: str) -> bool:
        if not self.config.allowed_roots:
            return True
        try:
            resolved_path = Path(path).expanduser().resolve()
        except OSError:
            return False
        for root in self.config.allowed_roots:
            try:
                resolved_root = Path(root).expanduser().resolve()
                if resolved_path == resolved_root or resolved_root in resolved_path.parents:
                    return True
            except OSError:
                continue
        return False

    async def _read_local_bytes(self, path: str, sys_operation: Any) -> bytes:
        if isinstance(sys_operation, SysOperation):
            res = await sys_operation.fs().read_file(
                path,
                mode="bytes",
                chunk_size=self.config.max_image_bytes + 1,
            )
            if res.code != StatusCode.SUCCESS.code:
                raise ValueError(res.message)
            content = getattr(res.data, "content", b"") if res.data is not None else b""
            if not isinstance(content, bytes):
                raise ValueError("image content is not bytes")
            return content

        file_path = Path(path).expanduser()
        size = await asyncio.to_thread(os.path.getsize, file_path)
        if size > self.config.max_image_bytes:
            raise ValueError("local image exceeds max_image_bytes")
        return await asyncio.to_thread(file_path.read_bytes)

    @staticmethod
    def _mime_type_from_path(path: str) -> str:
        parsed_path = urllib.parse.urlparse(path).path if "://" in path else path
        suffix = Path(parsed_path).suffix.lower()
        if suffix in _IMAGE_EXTENSIONS:
            return f"image/{_IMAGE_EXTENSIONS[suffix]}"
        guessed, _ = mimetypes.guess_type(path)
        return guessed if guessed and guessed.startswith("image/") else "image/png"

    @staticmethod
    def _data_url(raw: bytes, mime_type: str) -> str:
        encoded = base64.b64encode(raw).decode("ascii")
        if mime_type.startswith("data:"):
            return f"{mime_type};base64,{encoded}"
        return f"data:{mime_type};base64,{encoded}"

    @staticmethod
    def _safe_label(source: str) -> str:
        if source.lower().startswith("data:image/"):
            return "embedded image"
        parsed = urllib.parse.urlparse(source)
        if parsed.scheme in {"http", "https"}:
            name = Path(parsed.path).name
            return name or parsed.netloc
        return Path(source).name or source

    def _build_user_image_message(self, original_text: str, images: list[_ResolvedImage]) -> UserMessage:
        text = original_text
        for index, image in enumerate(images, start=1):
            text = text.replace(image.raw, f"[image {index}: {image.label}]")
        return UserMessage(content=self._build_content_parts(text, images))

    def _build_tool_image_context_message(self, images: list[_ResolvedImage]) -> UserMessage:
        lines = [self.config.synthetic_tool_context_text]
        lines.extend(
            f"{index}. {self._display_source(image)}"
            for index, image in enumerate(images, start=1)
        )
        return UserMessage(content=self._build_content_parts("\n".join(lines), images))

    @staticmethod
    def _display_source(image: _ResolvedImage) -> str:
        if image.source.lower().startswith("data:image/"):
            return image.label
        return image.source

    @staticmethod
    def _sanitize_tool_data_urls(messages: list[BaseMessage]) -> list[BaseMessage]:
        sanitized_messages: list[BaseMessage] = []
        for message in messages:
            if not isinstance(message, ToolMessage) or not isinstance(message.content, str):
                sanitized_messages.append(message)
                continue
            content = _DATA_IMAGE_URL_RE.sub(_OMITTED_DATA_URL, message.content)
            if content == message.content:
                sanitized_messages.append(message)
                continue
            sanitized_messages.append(
                ToolMessage(
                    content=content,
                    tool_call_id=message.tool_call_id,
                    name=message.name,
                )
            )
        return sanitized_messages

    @staticmethod
    def _build_content_parts(text: str, images: list[_ResolvedImage]) -> list[dict[str, Any]]:
        content: list[dict[str, Any]] = [{"type": "text", "text": text}]
        for image in images:
            content.append({"type": "image_url", "image_url": {"url": image.model_url}})
        return content
