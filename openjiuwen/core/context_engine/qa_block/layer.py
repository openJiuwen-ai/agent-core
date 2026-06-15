# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from openjiuwen.core.common.logging import context_engine_logger as logger
from openjiuwen.core.context_engine.observability import write_context_trace
from openjiuwen.core.context_engine.qa_block.config import QABlockConfig
from openjiuwen.core.context_engine.qa_block.messages import (
    load_qa_l0,
    message_qa_id,
    tag_messages_with_qa_id,
)
from openjiuwen.core.context_engine.qa_block.schema import (
    SOURCE_QA_ID_METADATA_KEY,
    QABlockRegistry,
)
from openjiuwen.core.context_engine.qa_ref import QARef
from openjiuwen.core.foundation.llm import BaseMessage, UserMessage

if TYPE_CHECKING:
    from openjiuwen.core.context_engine.base import ModelContext
    from openjiuwen.core.context_engine.qa_artifact.store import QAArtifactStore
    from openjiuwen.core.context_engine.qa_block.history_buffer import HistoryQABuffer
    from openjiuwen.core.context_engine.qa_block.store import QABlockStore

_CHARS_PER_TOKEN_ESTIMATE = 4


def _estimate_message_tokens(messages: list[BaseMessage], token_counter: Any | None) -> int:
    if token_counter is not None:
        return token_counter.count_messages(messages)
    total_chars = sum(len(getattr(message, "content", "") or "") for message in messages)
    return max(0, total_chars // _CHARS_PER_TOKEN_ESTIMATE)


def _group_messages_by_qa(
    messages: list[BaseMessage],
    registry: QABlockRegistry,
) -> dict[str, list[BaseMessage]]:
    grouped: dict[str, list[BaseMessage]] = {}
    current_qa_id = registry.current_qa_id

    for message in messages:
        qa_id = message_qa_id(message)
        if qa_id is None:
            if current_qa_id is None:
                continue
            qa_id = current_qa_id
        grouped.setdefault(qa_id, []).append(message)
    return grouped


class QABlockLayer:
    """QA slicing layer: hydrate history, expose window_qas / qa_ref (§5.7)."""

    def __init__(
        self,
        registry: QABlockRegistry,
        history_buffer: HistoryQABuffer,
        store: QABlockStore,
        *,
        token_counter: Any | None = None,
        config: QABlockConfig | None = None,
        artifact_store: QAArtifactStore | None = None,
    ):
        self._registry = registry
        self._history = history_buffer
        self._store = store
        self._token_counter = token_counter
        self._config = config or QABlockConfig()
        self._artifact_store = artifact_store
        self._message_cache: dict[str, list[BaseMessage]] = {}

    @property
    def registry(self) -> QABlockRegistry:
        return self._registry

    def _history_qa_ids(self) -> list[str]:
        history_blocks = [item for item in self._registry.blocks.values() if item.is_history]
        history_blocks.sort(key=lambda item: item.qa_index)
        return [entry.qa_id for entry in history_blocks]

    async def hydrate_history_into_window(
        self,
        context: ModelContext,
        *,
        selected_qa_ids: list[str] | None = None,
    ) -> int:
        """Re-inject selected frozen history QA messages before the current UserMessage."""
        if selected_qa_ids is None:
            history_ids = self._history_qa_ids()
        else:
            known = {entry.qa_id for entry in self._registry.blocks.values() if entry.is_history}
            history_ids = [qa_id for qa_id in selected_qa_ids if qa_id in known]
        if not history_ids:
            return 0

        from openjiuwen.core.context_engine.qa_artifact.hydrate_ops import (
            build_compact_hydrate_messages,
            resolve_hydrate_mode,
        )
        from openjiuwen.core.context_engine.qa_artifact.reconcile import (
            reconcile_artifact_state_from_disk,
        )

        hydrate_messages: list[BaseMessage] = []
        compact_qa_ids: list[str] = []
        raw_qa_ids: list[str] = []
        compact_reasons: dict[str, str] = {}
        raw_reasons: dict[str, str] = {}
        artifact_aware = self._artifact_store is not None and self._config.hydrate_artifact_aware

        for qa_id in history_ids:
            cached_l0: list[BaseMessage] | None = None
            reconciled = False
            if artifact_aware:
                cached_l0 = await load_qa_l0(qa_id, self._history, self._store)
                _, reconcile_reason = await reconcile_artifact_state_from_disk(
                    self._artifact_store,
                    qa_id,
                    l0_messages=cached_l0,
                    for_history=True,
                )
                reconciled = reconcile_reason not in ("is_extracting", "no_disk")

            mode, reason = await resolve_hydrate_mode(
                qa_id,
                artifact_store=self._artifact_store,
                config=self._config,
                l0_messages=cached_l0,
                reconciled=reconciled,
            )
            if mode == "compact" and self._artifact_store is not None:
                messages = await build_compact_hydrate_messages(
                    qa_id,
                    artifact_store=self._artifact_store,
                    l0_store=self._store,
                    history_buffer=self._history,
                    cached_l0=cached_l0,
                )
                compact_qa_ids.append(qa_id)
                compact_reasons[qa_id] = reason
            else:
                raw = cached_l0 if cached_l0 is not None else await load_qa_l0(qa_id, self._history, self._store)
                messages = tag_messages_with_qa_id(raw, qa_id)
                raw_qa_ids.append(qa_id)
                if reason:
                    raw_reasons[qa_id] = reason

            self._message_cache[qa_id] = messages
            hydrate_messages.extend(messages)

        if not hydrate_messages:
            return 0

        injected = await inject_messages_before_last_user_message(context, hydrate_messages)
        logger.info(
            "[QABlockLayer] hydrate session_id=%s qa_count=%s message_count=%s compact=%s raw=%s selected=%s",
            self._registry.session_id,
            len(history_ids),
            injected,
            compact_qa_ids,
            raw_qa_ids,
            selected_qa_ids is not None,
        )
        write_context_trace(
            "qa_block.hydrate",
            {
                "session_id": self._registry.session_id,
                "qa_ids": history_ids,
                "selected_mode": selected_qa_ids is not None,
                "message_count": injected,
                "compact_qa_ids": compact_qa_ids,
                "raw_qa_ids": raw_qa_ids,
                "compact_reasons": compact_reasons,
                "raw_reasons": raw_reasons,
            },
        )
        return injected

    def build_window_qas(self, context: ModelContext) -> list[QARef]:
        messages = context.get_messages()
        grouped = _group_messages_by_qa(messages, self._registry)
        refs: list[QARef] = []

        for qa_id in self._history_qa_ids():
            qa_messages = grouped.get(qa_id, [])
            if not qa_messages:
                continue

            def _getter(qid: str = qa_id, snapshot: list[BaseMessage] = qa_messages) -> list[BaseMessage]:
                live = _group_messages_by_qa(context.get_messages(), self._registry).get(qid, [])
                if live:
                    return live
                if snapshot:
                    return list(snapshot)
                cached = self._history.get(qid)
                return list(cached) if cached else []

            refs.append(
                QARef(
                    qa_id=qa_id,
                    tokens=_estimate_message_tokens(qa_messages, self._token_counter),
                    is_history=True,
                    get_messages=_getter,
                )
            )

        current_qa_id = self._registry.current_qa_id
        if current_qa_id:
            current_messages = grouped.get(current_qa_id, [])

            def _current_getter(
                qid: str = current_qa_id,
                snapshot: list[BaseMessage] = current_messages,
            ) -> list[BaseMessage]:
                live = _group_messages_by_qa(context.get_messages(), self._registry).get(qid, [])
                return live or list(snapshot)

            refs.append(
                QARef(
                    qa_id=current_qa_id,
                    tokens=_estimate_message_tokens(current_messages, self._token_counter),
                    is_history=False,
                    get_messages=_current_getter,
                )
            )

        logger.info(
            "[QABlockLayer] window_qas session_id=%s count=%s current=%s",
            self._registry.session_id,
            len(refs),
            current_qa_id,
        )
        return refs

    def qa_ref(self, qa_id: str, context: ModelContext | None = None) -> QARef:
        entry = self._registry.blocks.get(qa_id)
        is_history = entry.is_history if entry is not None else False

        if context is not None:
            grouped = _group_messages_by_qa(context.get_messages(), self._registry)
            window_messages = grouped.get(qa_id, [])
        else:
            window_messages = self._message_cache.get(qa_id, [])

        def _getter(qid: str = qa_id) -> list[BaseMessage]:
            if context is not None:
                grouped_live = _group_messages_by_qa(context.get_messages(), self._registry)
                if qid in grouped_live:
                    return grouped_live[qid]
            if qid in self._message_cache:
                return self._message_cache[qid]
            return self._history.get(qid) or []

        return QARef(
            qa_id=qa_id,
            tokens=_estimate_message_tokens(window_messages, self._token_counter),
            is_history=is_history,
            get_messages=_getter,
        )

    async def resolve_messages_for_qa(
        self,
        qa_id: str,
        context: ModelContext | None = None,
    ) -> list[BaseMessage]:
        """Resolve QA messages for load_qa_index: window → hydrate cache → L0 (hot cache → disk)."""
        if context is not None:
            live = _group_messages_by_qa(context.get_messages(), self._registry).get(qa_id)
            if live:
                return live
        cached_view = self._message_cache.get(qa_id)
        if cached_view is not None:
            return cached_view
        messages = await load_qa_l0(qa_id, self._history, self._store)
        if messages:
            self._message_cache[qa_id] = messages
        return messages

    async def qa_ref_for_index(
        self,
        qa_id: str,
        context: ModelContext | None = None,
    ) -> QARef:
        """Build QARef with L0-backed messages for load_qa_index (async read path)."""
        messages = await self.resolve_messages_for_qa(qa_id, context)
        entry = self._registry.blocks.get(qa_id)
        is_history = entry.is_history if entry is not None else False
        snapshot = list(messages)

        def _getter() -> list[BaseMessage]:
            return list(snapshot)

        return QARef(
            qa_id=qa_id,
            tokens=_estimate_message_tokens(snapshot, self._token_counter),
            is_history=is_history,
            get_messages=_getter,
        )

    @staticmethod
    def annotate_tool_result_source_qa(message: BaseMessage, qa_id: str) -> None:
        meta = dict(getattr(message, "metadata", None) or {})
        meta[SOURCE_QA_ID_METADATA_KEY] = qa_id
        message.metadata = meta


async def inject_messages_before_last_user_message(
    context: ModelContext,
    messages_to_inject: list[BaseMessage],
) -> int:
    """ForkInjection-style reorder: [prefix, history..., query UserMessage]."""
    if not messages_to_inject:
        return 0

    current = context.get_messages()
    last_user: UserMessage | None = None
    prefix = current
    if current and isinstance(current[-1], UserMessage):
        last_user = current[-1]
        prefix = current[:-1]

    rebuilt = [*prefix, *messages_to_inject]
    if last_user is not None:
        rebuilt.append(last_user)

    context.set_messages(rebuilt, with_history=True)
    return len(messages_to_inject)
