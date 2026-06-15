# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import json
import re
import uuid
from typing import Any

from pydantic import BaseModel

from openjiuwen.core.common.logging import context_engine_logger as logger
from openjiuwen.core.context_engine.context.context_utils import ContextUtils
from openjiuwen.core.context_engine.context.session_memory_manager import group_completed_api_rounds
from openjiuwen.core.context_engine.processor.base import ContextProcessor
from openjiuwen.core.context_engine.qa_artifact.schema import CatalogEntry, QAArtifactConfig
from openjiuwen.core.context_engine.qa_artifact.store import resolve_sys_operation
from openjiuwen.core.foundation.llm import BaseMessage, Model, ModelClientConfig, ModelRequestConfig, UserMessage


class _CatalogOffloadConfig(BaseModel):
    pass


class _CatalogOffloadProcessor(ContextProcessor):
    """Minimal processor shell to reuse filesystem offload_messages (§4.3)."""

    def __init__(self) -> None:
        super().__init__(_CatalogOffloadConfig())

    def load_state(self, state: dict[str, Any]) -> None:
        _ = state

    def save_state(self) -> dict[str, Any]:
        return {}


def _filesystem_handle_from_message(message: Any) -> str | None:
    handle = getattr(message, "offload_handle", None)
    if not handle:
        return None
    offload_type = getattr(message, "offload_type", None)
    if offload_type == "filesystem":
        return str(handle)
    if offload_type not in (None, ""):
        logger.warning(
            "[CatalogBuilder] skip non-filesystem offload handle=%s type=%s",
            handle,
            offload_type,
        )
    return None


class CatalogBuilder:
    def __init__(
        self,
        config: QAArtifactConfig,
        session_memory_config: Any | None = None,
    ):
        self._config = config
        self._offloader = _CatalogOffloadProcessor()
        self._model: Model | None = None
        if session_memory_config is not None:
            model = getattr(session_memory_config, "model", None)
            model_client = getattr(session_memory_config, "model_client", None)
            if model is not None and model_client is not None:
                self._model = Model(model_client, model)

    def bind_model_defaults(
        self,
        model_config: ModelRequestConfig | None,
        model_client_config: ModelClientConfig | None,
    ) -> None:
        if self._model is None and model_config is not None and model_client_config is not None:
            self._model = Model(model_client_config, model_config)

    async def build(
        self,
        ctx: Any,
        store: Any,
        state: Any,
        messages: list[BaseMessage],
        *,
        qa_id: str,
    ) -> None:
        entries = await self._build_entries(ctx, messages)
        body = json.dumps(
            {"qa_id": qa_id, "entries": [entry.model_dump(exclude_none=True) for entry in entries]},
            ensure_ascii=False,
        )
        pending_catalog = f"{state.pending_path}.catalog"
        await store.write_atomic(state.catalog_path, body, pending_catalog)
        logger.info(
            "[CatalogBuilder] built qa_id=%s entries=%s",
            qa_id,
            len(entries),
        )

    async def build_slice(
        self,
        ctx: Any,
        messages: list[BaseMessage],
        *,
        qa_id: str,
    ) -> list[CatalogEntry]:
        _ = qa_id
        return await self._build_entries(ctx, messages)

    async def append_entries(
        self,
        store: Any,
        state: Any,
        qa_id: str,
        new_entries: list[CatalogEntry],
    ) -> None:
        existing = self.load_entries(await store.read_text(state.catalog_path))
        merged = existing + new_entries
        body = json.dumps(
            {"qa_id": qa_id, "entries": [entry.model_dump(exclude_none=True) for entry in merged]},
            ensure_ascii=False,
        )
        pending_catalog = f"{state.pending_path}.catalog"
        await store.write_atomic(state.catalog_path, body, pending_catalog)
        logger.info(
            "[CatalogBuilder] appended qa_id=%s new_entries=%s total=%s",
            qa_id,
            len(new_entries),
            len(merged),
        )

    async def _build_entries(self, ctx: Any, messages: list[BaseMessage]) -> list[CatalogEntry]:
        token_counter = None
        if ctx.context is not None and callable(getattr(ctx.context, "token_counter", None)):
            token_counter = ctx.context.token_counter()

        entries: list[CatalogEntry] = []
        long_units: list[tuple[int, list[BaseMessage]]] = []
        for unit in self._split_units(messages):
            tokens = self._count_unit_tokens(unit, token_counter)
            plain = self._plain(unit)
            if tokens <= self._config.catalog_long_message_tokens:
                entries.append(CatalogEntry(preview=plain))
                continue
            handle = await self._offload_unit(ctx, unit)
            entry_index = len(entries)
            entries.append(CatalogEntry(preview="", handle=handle))
            long_units.append((entry_index, unit))

        if long_units:
            summaries = await self._summarize([unit for _, unit in long_units])
            for (entry_index, _), summary in zip(long_units, summaries):
                entries[entry_index].preview = summary
        return entries

    @staticmethod
    def _split_units(messages: list[BaseMessage]) -> list[list[BaseMessage]]:
        rounds = group_completed_api_rounds(messages)
        if rounds:
            return [messages[start:end] for start, end in rounds]
        return [[message] for message in messages]

    @staticmethod
    def _count_unit_tokens(unit: list[BaseMessage], token_counter: Any | None) -> int:
        if token_counter is not None:
            try:
                return token_counter.count_messages(unit)
            except Exception as exc:
                logger.debug("[QAArtifactCatalog] token counter failed for unit estimate: %s", exc)
        return sum(ContextUtils.estimate_message_tokens(message) for message in unit)

    @staticmethod
    def _offload_role(unit: list[BaseMessage]) -> str:
        for message in reversed(unit):
            role = getattr(message, "role", None)
            if role:
                return str(role)
        return "assistant"

    @staticmethod
    def _plain(unit: list[BaseMessage]) -> str:
        parts = []
        for message in unit:
            role = getattr(message, "role", "") or message.__class__.__name__
            content = (getattr(message, "content", "") or "").strip()
            if content:
                parts.append(f"[{role}] {content[:500]}")
        return " | ".join(parts) if parts else "(empty round)"

    async def _offload_unit(self, ctx: Any, unit: list[BaseMessage]) -> str | None:
        for message in unit:
            handle = _filesystem_handle_from_message(message)
            if handle:
                return handle

        context = getattr(ctx, "context", None)
        if context is None:
            logger.warning("[CatalogBuilder] offload skipped: no context on ctx")
            return None

        role = self._offload_role(unit)
        offload_kwargs: dict[str, Any] = {}
        if role == "tool":
            last = unit[-1]
            offload_kwargs["tool_call_id"] = getattr(last, "tool_call_id", None) or f"catalog-{uuid.uuid4().hex[:8]}"
            if getattr(last, "name", None):
                offload_kwargs["name"] = last.name

        sys_operation = resolve_sys_operation(ctx)
        offload_message = await self._offloader.offload_messages(
            role,
            "",
            unit,
            context=context,
            offload_type="filesystem",
            sys_operation=sys_operation,
            **offload_kwargs,
        )
        if offload_message is None:
            return None
        handle = _filesystem_handle_from_message(offload_message)
        if handle is None and getattr(offload_message, "offload_handle", None):
            logger.warning(
                "[CatalogBuilder] filesystem offload fell back to in_memory; omitting catalog handle",
            )
        return handle

    async def _summarize(self, units: list[list[BaseMessage]]) -> list[str]:
        if not units:
            return []
        if self._model is None:
            return [self._fallback_summary(self._plain(unit)) for unit in units]

        blocks = []
        for index, unit in enumerate(units, 1):
            blocks.append(f"{index}. {self._plain(unit)[:2000]}")
        max_tokens = self._config.catalog_summary_max_tokens
        prompt = (
            "Summarize each numbered block below in ONE concise line "
            f"(max {max_tokens} tokens each). "
            f"Return exactly {len(units)} lines in the same order, "
            f"format: 'N. summary'.\n\n" + "\n".join(blocks)
        )
        try:
            response = await self._model.invoke([UserMessage(content=prompt)], tools=None)
            content = (getattr(response, "content", None) or str(response) or "").strip()
            parsed = self._parse_summaries(content, len(units))
            if len(parsed) == len(units):
                return parsed
        except Exception as exc:
            logger.warning("[CatalogBuilder] summarize failed, fallback to truncation: %s", exc)
        return [self._fallback_summary(self._plain(unit)) for unit in units]

    def _fallback_summary(self, plain: str) -> str:
        preview = plain[: self._config.catalog_summary_max_tokens * 4].rstrip()
        if len(plain) > len(preview):
            preview = preview + "…"
        return preview

    @staticmethod
    def _parse_summaries(content: str, expected: int) -> list[str]:
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        summaries: list[str] = []
        for line in lines:
            match = re.match(r"^\d+[\).\s]+(.+)$", line)
            summaries.append((match.group(1) if match else line).strip())
        if len(summaries) >= expected:
            return summaries[:expected]
        return summaries

    @staticmethod
    def load_entries(text: str) -> list[CatalogEntry]:
        if not text:
            return []
        payload = json.loads(text)
        return [CatalogEntry.model_validate(item) for item in payload.get("entries", [])]

    @staticmethod
    def render(qa_id: str, entries: list[CatalogEntry]) -> str:
        lines = [f"[QA {qa_id} catalog]"]
        for index, entry in enumerate(entries, 1):
            marker = ""
            if entry.handle:
                marker = f"  [[OFFLOAD: handle={entry.handle}, type=filesystem]]"
            lines.append(f"{index}. {entry.preview}{marker}")
        return "\n".join(lines)
