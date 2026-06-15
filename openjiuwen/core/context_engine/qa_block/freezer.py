# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from openjiuwen.core.common.logging import context_engine_logger as logger
from openjiuwen.core.context_engine.base import ModelContext
from openjiuwen.core.context_engine.observability import write_context_trace
from openjiuwen.core.context_engine.qa_block.config import QABlockConfig
from openjiuwen.core.context_engine.qa_block.freezer_lifecycle import register_qa_block_freezer
from openjiuwen.core.context_engine.qa_block.history_buffer import HistoryQABuffer
from openjiuwen.core.context_engine.qa_block.messages import (
    estimate_messages_tokens,
    extract_final_answer,
    extract_qa_native_messages,
    extract_user_query,
    had_full_compact_in_messages,
    message_id_range,
    strip_active_buffer,
    truncate_excerpt,
)
from openjiuwen.core.context_engine.qa_block.registry import (
    load_registry,
    merge_registry_block,
    save_registry,
)
from openjiuwen.core.context_engine.qa_block.schema import L0Store, QABlockEntry, QABlockRegistry
from openjiuwen.core.context_engine.qa_block.store import QABlockStore
from openjiuwen.core.context_engine.qa_block.summarizer import QABlockSummarizer
from openjiuwen.core.foundation.llm import BaseMessage


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _session_id_from_persist(session: Any, registry: QABlockRegistry | None) -> str:
    if registry is not None and registry.session_id:
        return registry.session_id
    getter = getattr(session, "get_session_id", None)
    if callable(getter):
        return str(getter() or "")
    return ""


def allocate_qa_id(registry: QABlockRegistry) -> tuple[str, int]:
    qa_index = registry.next_qa_index
    qa_id = f"qa_{qa_index:03d}"
    registry.next_qa_index += 1
    return qa_id, qa_index


_allocate_qa_id = allocate_qa_id


def ensure_current_qa_id(registry: QABlockRegistry, native_messages: list[BaseMessage]) -> str | None:
    """Assign current_qa_id when assembly rail (PR-3) has not started the block yet."""
    if not native_messages:
        return None
    if registry.current_qa_id:
        return registry.current_qa_id
    qa_id, _ = _allocate_qa_id(registry)
    registry.current_qa_id = qa_id
    logger.info(
        "[QABlockFreezer] auto begin_qa qa_id=%s session_id=%s",
        qa_id,
        registry.session_id,
    )
    return qa_id


@dataclass
class FreezeCommitResult:
    entry: QABlockEntry
    native_messages: list[BaseMessage]


class QABlockFreezer:
    def __init__(
        self,
        config: QABlockConfig | None = None,
        *,
        summarizer: QABlockSummarizer | None = None,
    ):
        self._config = config or QABlockConfig()
        self._summarizer = summarizer or QABlockSummarizer(self._config)
        self._background_tasks: set[asyncio.Task] = set()
        self._freeze_tasks_by_session: dict[str, set[asyncio.Task]] = defaultdict(set)
        register_qa_block_freezer(self)

    async def cancel_session_tasks(self, session_id: str) -> None:
        if not session_id:
            return
        tasks = list(self._freeze_tasks_by_session.pop(session_id, set()))
        cancelled = 0
        for task in tasks:
            self._background_tasks.discard(task)
            if task.done():
                continue
            task.cancel()
            cancelled += 1
            try:
                await task
            except asyncio.CancelledError:
                pass
        if cancelled:
            logger.info(
                "[QABlockFreezer] cancelled session freeze tasks session_id=%s count=%s",
                session_id,
                cancelled,
            )

    async def cancel_all_session_tasks(self) -> None:
        for session_id in list(self._freeze_tasks_by_session):
            await self.cancel_session_tasks(session_id)

    def _track_freeze_task(self, session_id: str, task: asyncio.Task) -> None:
        if not session_id:
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)
            return
        self._background_tasks.add(task)
        self._freeze_tasks_by_session[session_id].add(task)

        def _on_done(done: asyncio.Task) -> None:
            self._background_tasks.discard(done)
            session_tasks = self._freeze_tasks_by_session.get(session_id)
            if session_tasks is None:
                return
            session_tasks.discard(done)
            if not session_tasks:
                self._freeze_tasks_by_session.pop(session_id, None)

        task.add_done_callback(_on_done)

    def bind_summarizer_model_defaults(self, model_config: Any, model_client_config: Any) -> None:
        self._summarizer.bind_model_defaults(model_config, model_client_config)

    async def freeze_commit(
        self,
        session: Any,
        context: ModelContext,
        history_buffer: HistoryQABuffer,
        *,
        registry: QABlockRegistry | None = None,
        status: Literal["completed", "interrupted"] = "completed",
        preloaded_qa_ids: list[str] | None = None,
    ) -> FreezeCommitResult | None:
        registry = registry or load_registry(session)
        native_messages = extract_qa_native_messages(context.get_messages(), registry)
        qa_id = ensure_current_qa_id(registry, native_messages)
        if qa_id is None:
            logger.info(
                "[QABlockFreezer] freeze_commit skipped: no native messages session_id=%s",
                registry.session_id,
            )
            return None

        qa_index = int(qa_id.rsplit("_", 1)[-1])

        existing = registry.blocks.get(qa_id)
        if existing is not None and existing.freeze_committed_at:
            logger.info(
                "[QABlockFreezer] freeze_commit skipped: already committed qa_id=%s",
                qa_id,
            )
            return None

        user_query = extract_user_query(native_messages)
        final_answer = extract_final_answer(native_messages)
        l1_text, l1_source = await self._summarizer.generate_l1(
            user_query,
            final_answer,
            allow_llm=False,
        )
        had_compact = had_full_compact_in_messages(native_messages)
        l0_mode: Literal["delta", "compact_summary_tail"] = "compact_summary_tail" if had_compact else "delta"

        token_counter = None
        counter = getattr(context, "token_counter", None)
        if callable(counter):
            token_counter = counter()
        approx_tokens = estimate_messages_tokens(native_messages, token_counter=token_counter)

        entry = QABlockEntry(
            qa_id=qa_id,
            qa_index=qa_index,
            status=status,
            is_history=True,
            message_id_range=message_id_range(native_messages),
            message_count=len(native_messages),
            approx_tokens=approx_tokens,
            user_query_excerpt=truncate_excerpt(user_query, self._config.excerpt_max_chars),
            final_answer_excerpt=truncate_excerpt(final_answer, self._config.excerpt_max_chars),
            l1_text=l1_text,
            l1_source=l1_source,
            l0_content_mode=l0_mode,
            had_full_compact_in_qa=had_compact,
            preloaded_qa_ids=list(preloaded_qa_ids or []),
            l0_persist_status="pending",
            freeze_committed_at=_utc_now_iso(),
        )

        history_buffer.push(qa_id, [message.model_copy(deep=True) for message in native_messages])
        registry.blocks[qa_id] = entry
        registry.current_qa_id = None
        save_registry(session, registry)
        strip_active_buffer(context, registry)

        logger.info(
            "[QABlockFreezer] freeze_commit qa_id=%s status=%s native=%s mode=%s",
            qa_id,
            status,
            len(native_messages),
            l0_mode,
        )
        write_context_trace(
            "qa_block.freeze_commit",
            {
                "session_id": registry.session_id,
                "qa_id": qa_id,
                "status": status,
                "native_count": len(native_messages),
                "l0_content_mode": l0_mode,
                "had_full_compact_in_qa": had_compact,
                "l1_source": l1_source,
                "l1_allow_llm": False,
            },
        )
        return FreezeCommitResult(entry=entry, native_messages=native_messages)

    async def freeze_persist(
        self,
        session: Any,
        entry: QABlockEntry,
        native_messages: list[BaseMessage],
        *,
        registry: QABlockRegistry | None = None,
        store: QABlockStore,
        context: ModelContext | None = None,
        summarizer_model: Any | None = None,
    ) -> QABlockEntry:
        session_id = _session_id_from_persist(session, registry)
        try:
            rel_path = await store.write_l0(
                entry.qa_id,
                native_messages,
                l0_content_mode=entry.l0_content_mode,
                metadata={
                    "session_id": session_id,
                    "frozen_at": _utc_now_iso(),
                },
            )
            user_query = extract_user_query(native_messages)
            final_answer = extract_final_answer(native_messages)
            prior_l1 = entry.l1_text
            l1_text, l1_source = await self._summarizer.generate_l1(
                user_query,
                final_answer,
                model=summarizer_model,
                allow_llm=True,
            )

            entry.l0_store = L0Store(path=rel_path, handle=entry.qa_id)
            entry.l1_text = l1_text
            entry.l1_source = l1_source
            entry.l0_persist_status = "done"
            entry.l0_persisted_at = _utc_now_iso()
            merged = merge_registry_block(session, entry)
            l1_upgraded = prior_l1 != l1_text
            logger.info(
                "[QABlockFreezer] freeze_persist done qa_id=%s path=%s l1_source=%s l1_upgraded=%s blocks=%s",
                entry.qa_id,
                rel_path,
                l1_source,
                l1_upgraded,
                len(merged.blocks),
            )
            write_context_trace(
                "qa_block.freeze_persist",
                {
                    "session_id": merged.session_id,
                    "qa_id": entry.qa_id,
                    "path": rel_path,
                    "l1_source": l1_source,
                    "l1_allow_llm": True,
                    "l1_upgraded": l1_upgraded,
                    "message_count": len(native_messages),
                },
            )
            return entry
        except Exception as exc:
            entry.l0_persist_status = "failed"
            merge_registry_block(session, entry)
            logger.exception(
                "[QABlockFreezer] freeze_persist failed qa_id=%s error=%s",
                entry.qa_id,
                exc,
            )
            raise

    async def freeze(
        self,
        session: Any,
        context: ModelContext,
        history_buffer: HistoryQABuffer,
        store: QABlockStore,
        *,
        status: Literal["completed", "interrupted"] = "completed",
        persist_mode: Literal["async", "sync"] = "async",
        preloaded_qa_ids: list[str] | None = None,
        summarizer_model: Any | None = None,
    ) -> QABlockEntry | None:
        commit = await self.freeze_commit(
            session,
            context,
            history_buffer,
            status=status,
            preloaded_qa_ids=preloaded_qa_ids,
        )
        if commit is None:
            return None

        if persist_mode == "sync":
            return await self.freeze_persist(
                session,
                commit.entry,
                commit.native_messages,
                store=store,
                context=context,
                summarizer_model=summarizer_model,
            )

        task = asyncio.create_task(
            self.freeze_persist(
                session,
                commit.entry,
                commit.native_messages,
                store=store,
                context=context,
                summarizer_model=summarizer_model,
            )
        )
        session_id = _session_id_from_persist(session, load_registry(session))
        self._track_freeze_task(session_id, task)
        logger.info(
            "[QABlockFreezer] freeze_persist scheduled async qa_id=%s",
            commit.entry.qa_id,
        )
        return commit.entry
