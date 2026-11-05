# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Fold old ``read_file`` images in context into path placeholders."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

from pydantic import BaseModel, Field

from openjiuwen.core.common.logging import context_engine_logger as logger
from openjiuwen.core.context_engine.base import ContextWindow, ModelContext
from openjiuwen.core.context_engine.context_engine import ContextEngine
from openjiuwen.core.context_engine.image_refs import (
    FOLDED_METADATA_KEY,
    IMAGE_METADATA_KEY,
    READ_FILE_IMAGE_TEXT_PREFIX,
    compressed_image_paths,
    data_image_url,
    format_folded_placeholder,
    get_image_summary_provider,
    image_key,
    is_folded_placeholder_block,
    lookup_image_summary,
)
from openjiuwen.core.context_engine.processor.base import ContextEvent, ContextProcessor
from openjiuwen.core.foundation.llm import BaseMessage

_MAX_REPORTED_REREAD_KEYS = 512


class ImageWindowOffloaderConfig(BaseModel):
    """Bound how many ``read_file`` images stay in context as pixels.

    Images of the current turn (after the last assistant message without
    ``tool_calls``) are kept up to ``keep_current_turn_max``; older turns keep
    the newest ``keep_history_k``; the same path keeps only its newest copy;
    and the kept images' base64 bytes stay under ``max_total_base64_bytes``.
    Images the model has not seen yet are never folded.
    """

    enabled: bool = Field(default=True)
    keep_current_turn_max: int = Field(default=8, ge=0)
    keep_history_k: int = Field(default=2, ge=0)
    max_total_base64_bytes: int = Field(default=600_000, gt=0)


@dataclass(frozen=True)
class _LiveImage:
    msg_idx: int
    block_idx: int
    path: str
    key: str
    nbytes: int
    width: Optional[int] = None
    height: Optional[int] = None

    @property
    def loc(self) -> Tuple[int, int]:
        return self.msg_idx, self.block_idx


def _metadata(message: BaseMessage) -> Dict[str, Any]:
    metadata = getattr(message, "metadata", None)
    return metadata if isinstance(metadata, dict) else {}


def _entries_by_block(message: BaseMessage, metadata_key: str) -> Dict[int, Dict[str, Any]]:
    entries = _metadata(message).get(metadata_key)
    if not isinstance(entries, list):
        return {}
    return {
        entry["block_index"]: entry
        for entry in entries
        if isinstance(entry, dict) and isinstance(entry.get("block_index"), int)
    }


def _preceding_read_file_path(content: List[Any], block_idx: int) -> Optional[str]:
    if block_idx <= 0:
        return None
    block = content[block_idx - 1]
    if not isinstance(block, dict) or block.get("type") != "text":
        return None
    text = block.get("text")
    if isinstance(text, str) and text.startswith(READ_FILE_IMAGE_TEXT_PREFIX):
        return text[len(READ_FILE_IMAGE_TEXT_PREFIX):].strip() or None
    return None


def _scan_live_images(messages: List[BaseMessage]) -> List[_LiveImage]:
    """Unfolded ``read_file`` images, oldest first. Untraceable images are left alone."""
    images: List[_LiveImage] = []
    for msg_idx, message in enumerate(messages):
        if getattr(message, "role", None) != "user":
            continue
        content = getattr(message, "content", None)
        if not isinstance(content, list):
            continue
        entries = _entries_by_block(message, IMAGE_METADATA_KEY)
        for block_idx, block in enumerate(content):
            url = data_image_url(block)
            if url is None:
                continue
            entry = entries.get(block_idx, {})
            path = entry.get("source_path") or _preceding_read_file_path(content, block_idx)
            if not path:
                continue
            images.append(
                _LiveImage(
                    msg_idx=msg_idx,
                    block_idx=block_idx,
                    path=str(path),
                    key=str(entry.get("key") or image_key(url)),
                    nbytes=len(url),
                    width=entry.get("width"),
                    height=entry.get("height"),
                )
            )
    return images


def _last_assistant_index(messages: List[BaseMessage], *, without_tool_calls: bool) -> int:
    for idx in range(len(messages) - 1, -1, -1):
        message = messages[idx]
        if getattr(message, "role", None) != "assistant":
            continue
        if without_tool_calls and getattr(message, "tool_calls", None):
            continue
        return idx
    return -1


def _folded_paths(messages: List[BaseMessage]) -> Set[str]:
    paths: Set[str] = set()
    for message in messages:
        for entry in _entries_by_block(message, FOLDED_METADATA_KEY).values():
            if entry.get("source_path"):
                paths.add(str(entry["source_path"]))
    return paths


@ContextEngine.register_processor()
class ImageWindowOffloader(ContextProcessor):
    """Keep a bounded window of ``read_file`` images; fold the rest into path placeholders.

    Folding rewrites stored context in ``on_add_messages`` (and on active
    compression). Summaries from a registered image summary provider are only
    rendered into the outgoing window copy, never stored.
    """

    # Image folding / summary rendering is context-window management, not
    # compression; triggering it must not surface "compressing context"
    # indicators (frontends map every compression_state event to that state).
    suppress_state_events = True

    def __init__(self, config: ImageWindowOffloaderConfig):
        super().__init__(config)
        self._folded_total = 0
        self._reread_total = 0
        self._reread_after_compression_total = 0
        self._reported_reread_keys: Set[str] = set()

    @property
    def config(self) -> ImageWindowOffloaderConfig:
        return self._config

    def load_state(self, state: Dict[str, Any]) -> None:
        return

    def save_state(self) -> Dict[str, Any]:
        return {}

    # ------------------------------------------------------------------
    # ADD: fold stored context
    # ------------------------------------------------------------------
    async def trigger_add_messages(
        self,
        context: ModelContext,
        messages_to_add: List[BaseMessage],
        **kwargs: Any,
    ) -> bool:
        if not self.config.enabled:
            return False
        if not any(getattr(message, "role", None) in ("user", "assistant") for message in messages_to_add):
            return False
        return bool(_scan_live_images(context.get_messages() + messages_to_add))

    async def on_add_messages(
        self,
        context: ModelContext,
        messages_to_add: List[BaseMessage],
        **kwargs: Any,
    ) -> Tuple[ContextEvent | None, List[BaseMessage]]:
        stored = context.get_messages()
        context_size = len(stored)
        all_messages = stored + list(messages_to_add)
        if messages_to_add:
            self._record_rereads(all_messages, context_size)

        updated, modified = self._fold(all_messages)
        if not modified:
            return None, messages_to_add

        context.set_messages(updated[:context_size])
        event = ContextEvent(event_type=self.processor_type(), messages_to_modify=modified)
        return event, updated[context_size:]

    # ------------------------------------------------------------------
    # GET: render summaries into the window copy; fold on active compression
    # ------------------------------------------------------------------
    async def trigger_get_context_window(
        self,
        context: ModelContext,
        context_window: ContextWindow,
        **kwargs: Any,
    ) -> bool:
        if not self.config.enabled:
            return False
        messages = context_window.context_messages
        # Rendering summaries into the window copy never needs a re-trigger (it
        # is idempotent and cheap), so only a pending fold or a foldable window
        # claims this GET.
        if get_image_summary_provider() is not None and _folded_paths(messages):
            return True
        return bool(self._plan_folds(messages, _scan_live_images(messages)))

    async def on_get_context_window(
        self,
        context: ModelContext,
        context_window: ContextWindow,
        **kwargs: Any,
    ) -> Tuple[ContextEvent | None, ContextWindow]:
        if kwargs.get("force"):
            updated, modified = self._fold(list(context_window.context_messages))
            if not modified:
                return None, context_window
            context_window.context_messages = updated
            return ContextEvent(event_type=self.processor_type(), messages_to_modify=modified), context_window

        # Passive GET: fold the window COPY (never write back to storage) so
        # images that entered via set_messages / restore / tool-only batches
        # still respect max_total_base64_bytes and keep_* caps on this path.
        # Without this, images never reaching on_add_messages would keep their
        # full base64 in the outgoing window and defeat the 413 fix.
        messages = list(context_window.context_messages)
        session_id = context.session_id() if hasattr(context, "session_id") else None
        folds = self._plan_folds(messages, _scan_live_images(messages))
        if folds:
            updated, modified = self._apply_folds(messages, folds)
            messages = updated
        else:
            modified = False
        rendered = self._render_summaries(messages, session_id)
        if rendered is not None:
            context_window.context_messages = rendered
            return None, context_window
        if modified:
            context_window.context_messages = messages
            return ContextEvent(event_type=self.processor_type(), messages_to_modify=modified), context_window
        return None, context_window

    # ------------------------------------------------------------------
    # Folding
    # ------------------------------------------------------------------
    def _plan_folds(self, messages: List[BaseMessage], images: List[_LiveImage]) -> Dict[Tuple[int, int], _LiveImage]:
        if not images:
            return {}
        cfg = self.config
        turn_start = _last_assistant_index(messages, without_tool_calls=True)
        unseen_start = _last_assistant_index(messages, without_tool_calls=False)
        folds: Dict[Tuple[int, int], _LiveImage] = {}

        newest_first: List[_LiveImage] = []
        seen_paths: Set[str] = set()
        for image in reversed(images):
            if image.path in seen_paths:
                folds[image.loc] = image
                continue
            seen_paths.add(image.path)
            newest_first.append(image)

        kept: List[_LiveImage] = []
        current_kept = 0
        history_kept = 0
        for image in newest_first:
            unseen = image.msg_idx > unseen_start
            if image.msg_idx > turn_start:
                if unseen or current_kept < cfg.keep_current_turn_max:
                    current_kept += 1
                    kept.append(image)
                    continue
            elif history_kept < cfg.keep_history_k:
                history_kept += 1
                kept.append(image)
                continue
            folds[image.loc] = image

        total_bytes = sum(image.nbytes for image in kept)
        for image in reversed(kept):
            if total_bytes <= cfg.max_total_base64_bytes:
                break
            if image.msg_idx > unseen_start:
                continue
            folds[image.loc] = image
            total_bytes -= image.nbytes
        return folds

    @staticmethod
    def _apply_folds(
        messages: List[BaseMessage],
        folds: Dict[Tuple[int, int], _LiveImage],
    ) -> Tuple[List[BaseMessage], List[int]]:
        """Return copies of *messages* with the given images replaced by placeholders.

        Pure helper shared by the stored-context path (``_fold``) and the passive
        GET window-copy path (``on_get_context_window``): it mutates no counter,
        writes nothing to storage, and logs nothing. The caller decides whether to
        persist the result.
        """
        if not folds:
            return messages, []

        by_message: Dict[int, List[_LiveImage]] = {}
        for image in folds.values():
            by_message.setdefault(image.msg_idx, []).append(image)

        updated = list(messages)
        for msg_idx, msg_images in by_message.items():
            message = updated[msg_idx]
            content = list(message.content)
            metadata = dict(_metadata(message))
            folded_entries = list(metadata.get(FOLDED_METADATA_KEY) or [])
            for image in msg_images:
                content[image.block_idx] = {
                    "type": "text",
                    "text": format_folded_placeholder(image.path),
                }
                entry: Dict[str, Any] = {
                    "block_index": image.block_idx,
                    "source_path": image.path,
                    "key": image.key,
                }
                if image.width and image.height:
                    entry["width"] = image.width
                    entry["height"] = image.height
                folded_entries.append(entry)
            metadata[FOLDED_METADATA_KEY] = folded_entries
            updated[msg_idx] = message.model_copy(update={"content": content, "metadata": metadata})
        return updated, sorted(by_message)

    def _fold(self, messages: List[BaseMessage]) -> Tuple[List[BaseMessage], List[int]]:
        images = _scan_live_images(messages)
        folds = self._plan_folds(messages, images)
        updated, modified = self._apply_folds(messages, folds)
        if not modified:
            return messages, []

        self._folded_total += len(folds)
        kept = [image for image in images if image.loc not in folds]
        logger.info(
            "[ImageWindowOffloader] folded=%d kept=%d kept_base64_bytes=%d "
            "folded_total=%d reread_total=%d reread_after_compression_total=%d",
            len(folds),
            len(kept),
            sum(image.nbytes for image in kept),
            self._folded_total,
            self._reread_total,
            self._reread_after_compression_total,
        )
        return updated, modified

    def _record_rereads(self, messages: List[BaseMessage], new_start: int) -> None:
        """Log images re-read after being folded or compressed (input for V5 decisions)."""
        new_images = [image for image in _scan_live_images(messages) if image.msg_idx >= new_start]
        if not new_images:
            return
        history = messages[:new_start]
        folded = _folded_paths(history)
        compressed = set(compressed_image_paths(history))
        for image in new_images:
            after_fold = image.path in folded
            after_compression = image.path in compressed
            if not (after_fold or after_compression):
                continue
            reread_id = f"{image.key}@{new_start}"
            if reread_id in self._reported_reread_keys:
                continue
            if len(self._reported_reread_keys) >= _MAX_REPORTED_REREAD_KEYS:
                self._reported_reread_keys.clear()
            self._reported_reread_keys.add(reread_id)
            self._reread_total += 1
            if after_compression:
                self._reread_after_compression_total += 1
            logger.info(
                "[ImageWindowOffloader] image_reread path=%s after_fold=%s after_compression=%s "
                "reread_total=%d folded_total=%d",
                image.path,
                after_fold,
                after_compression,
                self._reread_total,
                self._folded_total,
            )

    # ------------------------------------------------------------------
    # Summary rendering (window copy only)
    # ------------------------------------------------------------------
    @staticmethod
    def _render_summaries(messages: List[BaseMessage], session_id: Optional[str]) -> Optional[List[BaseMessage]]:
        if get_image_summary_provider() is None:
            return None
        changed = False
        rendered = list(messages)
        for msg_idx, message in enumerate(messages):
            entries = _entries_by_block(message, FOLDED_METADATA_KEY)
            content = getattr(message, "content", None)
            if not entries or not isinstance(content, list):
                continue
            new_content = list(content)
            message_changed = False
            for block_idx, entry in entries.items():
                if block_idx >= len(new_content) or not is_folded_placeholder_block(new_content[block_idx]):
                    continue
                summary = lookup_image_summary(entry.get("key"), session_id)
                if summary is None:
                    continue
                text = format_folded_placeholder(
                    str(entry.get("source_path")),
                    summary,
                    width=entry.get("width"),
                    height=entry.get("height"),
                )
                if text != new_content[block_idx].get("text"):
                    new_content[block_idx] = {"type": "text", "text": text}
                    message_changed = True
            if message_changed:
                rendered[msg_idx] = message.model_copy(update={"content": new_content})
                changed = True
        return rendered if changed else None
