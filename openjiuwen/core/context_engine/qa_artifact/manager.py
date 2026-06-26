# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from typing import Any

TaskKey = tuple[str, str]

from openjiuwen.core.common.logging import context_engine_logger as logger
from openjiuwen.core.context_engine.context.session_memory_manager import (
    find_message_index_by_context_message_id,
    get_context_message_id,
    group_completed_api_rounds,
)
from openjiuwen.core.context_engine.observability import write_context_trace
from openjiuwen.core.context_engine.qa_artifact.catalog import CatalogBuilder
from openjiuwen.core.context_engine.qa_artifact.checkpoint_flush import maybe_persist_qa_memory_state
from openjiuwen.core.context_engine.qa_artifact.overview import render_plain
from openjiuwen.core.context_engine.qa_artifact.schema import QAArtifactConfig, QAArtifacts
from openjiuwen.core.context_engine.qa_artifact.store import QAArtifactStore, resolve_sys_operation
from openjiuwen.core.context_engine.qa_artifact.assembly_state import (
    is_assembly_whole_compact_applied,
)
from openjiuwen.core.context_engine.qa_artifact.window import (
    apply_artifact_to_context,
    estimate_context_messages_tokens,
    is_qa_already_compact_in_window,
    make_processor_ctx,
    tail_after,
)
from openjiuwen.core.context_engine.qa_block.messages import message_qa_id
from openjiuwen.core.context_engine.qa_ref import QARef

_OVERVIEW_FREEZE_AWAIT_S = 3.0


class QAArtifactManager:
    def __init__(
        self,
        config: QAArtifactConfig,
        overview_generator: Any,
        catalog_builder: CatalogBuilder,
    ):
        self._config = config
        self._overview = overview_generator
        self._catalog = catalog_builder
        self._bg: dict[TaskKey, asyncio.Task] = {}
        self._overview_bg: dict[TaskKey, asyncio.Task] = {}
        self._session_task_keys: dict[str, set[TaskKey]] = defaultdict(set)

    def bind_model_defaults(self, model_config: Any, model_client_config: Any) -> None:
        if self._overview is not None:
            self._overview.bind_model_defaults(model_config, model_client_config)
        if self._catalog is not None:
            self._catalog.bind_model_defaults(model_config, model_client_config)

    @staticmethod
    def _session_id_from_ctx(ctx: Any) -> str:
        session = getattr(ctx, "session", None)
        if session is None:
            context = getattr(ctx, "context", None)
            session = getattr(context, "_session_ref", None) if context else None
        getter = getattr(session, "get_session_id", None)
        return str(getter() or "") if callable(getter) else ""

    @staticmethod
    def _task_key(ctx: Any, qa_id: str) -> TaskKey:
        return (QAArtifactManager._session_id_from_ctx(ctx), qa_id)

    def _track_session_task(self, session_id: str, key: TaskKey, task: asyncio.Task) -> None:
        if not session_id:
            return
        self._session_task_keys[session_id].add(key)

        def _on_done(_: asyncio.Task) -> None:
            keys = self._session_task_keys.get(session_id)
            if keys is None:
                return
            keys.discard(key)
            if not keys:
                self._session_task_keys.pop(session_id, None)

        task.add_done_callback(_on_done)

    def _register_bg_task(self, ctx: Any, qa_id: str, task: asyncio.Task) -> None:
        key = self._task_key(ctx, qa_id)
        self._bg[key] = task
        self._track_session_task(key[0], key, task)

    def _register_overview_task(self, ctx: Any, qa_id: str, task: asyncio.Task) -> None:
        key = self._task_key(ctx, qa_id)
        self._overview_bg[key] = task
        self._track_session_task(key[0], key, task)

    async def cancel_session_tasks(self, session_id: str) -> None:
        if not session_id:
            return
        keys = list(self._session_task_keys.pop(session_id, ()))
        cancelled = 0
        for key in keys:
            for pool in (self._bg, self._overview_bg):
                task = pool.pop(key, None)
                if task is None or task.done():
                    continue
                task.cancel()
                cancelled += 1
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        if cancelled:
            logger.info(
                "[QAArtifactManager] cancelled session tasks session_id=%s count=%s",
                session_id,
                cancelled,
            )

    async def cancel_all_session_tasks(self) -> None:
        for session_id in list(self._session_task_keys):
            await self.cancel_session_tasks(session_id)

    def _resolve_overview_key(self, qa_id: str, session_id: str | None) -> TaskKey | None:
        if session_id is not None:
            return (session_id, qa_id)
        matches = [key for key in self._overview_bg if key[1] == qa_id]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            logger.warning(
                "[QAArtifactManager] ambiguous await overview qa_id=%s sessions=%s",
                qa_id,
                [key[0] for key in matches],
            )
            return matches[0]
        return None

    @staticmethod
    def _produce_ctx(ctx: Any) -> Any:
        context = getattr(ctx, "context", None)
        if context is None:
            return ctx
        return make_processor_ctx(
            context,
            sys_operation=resolve_sys_operation(ctx),
        )

    @staticmethod
    def build_store(ctx: Any, workspace: Any) -> QAArtifactStore:
        workspace_root = getattr(workspace, "root_path", "") or ""
        return QAArtifactStore(ctx.session, workspace_root, resolve_sys_operation(ctx))

    @staticmethod
    def _has_products(store: QAArtifactStore, qa_id: str) -> bool:
        state = store.get_or_init(qa_id)
        return state.products_ready or state.state in ("STAGED", "COMPACTED")

    async def _has_products_for_fallback(self, store: QAArtifactStore, qa_id: str) -> bool:
        if self._has_products(store, qa_id):
            return True
        state = store.get_or_init(qa_id)
        try:
            entries = CatalogBuilder.load_entries(await store.read_text(state.catalog_path))
        except Exception as exc:
            logger.debug(
                "[QAArtifactManager] catalog read failed for fallback qa_id=%s path=%s: %s",
                qa_id,
                state.catalog_path,
                exc,
            )
            return False
        return len(entries) > 0

    @staticmethod
    def _claim(store: QAArtifactStore, qa_id: str, *, stale_refresh: bool = False) -> bool:
        state = store.get_or_init(qa_id)
        if state.is_extracting:
            return False
        if not stale_refresh and (state.state in ("STAGED", "COMPACTED") or state.products_ready):
            return False
        state.is_extracting = True
        store.save(qa_id, state)
        return True

    def _initial_fold_messages(self, qa: QARef) -> list:
        """Messages to fold on a first (non-rolling) artifact production.

        A history QA is finalized and folds entirely. An in-flight (active) QA
        must preserve a recent tail: otherwise ``covers_upto`` reaches the last
        message and ``apply_artifact_to_context`` treats the QA as
        already-compact and skips it (``zero progress`` -> fallback), so the QA
        layer never reduces a single growing active QA on its own. Reserving
        the same ``active_qa_rolling_keep_tail`` used by
        ``_refresh_active_qa_products`` keeps first compaction and subsequent
        rolling compaction on one boundary rule, and is applied per-QA so a
        window mixing history + active QAs stays correct (history folds whole,
        only the active QA keeps a tail).
        """
        messages = qa.get_messages()
        if qa.is_history:
            return messages
        keep_tail = self._config.active_qa_rolling_keep_tail
        if keep_tail <= 0:
            return messages
        if len(messages) <= keep_tail:
            return messages
        return list(messages[:-keep_tail])

    @staticmethod
    def _is_compact_marker(message: Any, qa_id: str) -> bool:
        meta = getattr(message, "metadata", None) or {}
        return bool(meta.get("qa_artifact_compacted")) and message_qa_id(message) == qa_id

    def _uncovered_messages(self, store: QAArtifactStore, qa_ref: QARef) -> list:
        messages = qa_ref.get_messages()
        state = store.get_or_init(qa_ref.qa_id)
        if state.covers_upto_message_id is None:
            return messages

        tail = tail_after(messages, state.covers_upto_message_id)
        if tail:
            return tail

        cutoff = find_message_index_by_context_message_id(messages, state.covers_upto_message_id)
        if cutoff >= 0 or qa_ref.is_history:
            return []

        for index in range(len(messages) - 1, -1, -1):
            if self._is_compact_marker(messages[index], qa_ref.qa_id):
                return list(messages[index + 1:])
        return messages

    def _uncovered_tokens(self, ctx: Any, store: QAArtifactStore, qa_ref: QARef) -> int:
        if not self._has_products(store, qa_ref.qa_id):
            return qa_ref.tokens
        uncovered = self._uncovered_messages(store, qa_ref)
        if not uncovered:
            return 0
        token_counter = None
        context = getattr(ctx, "context", None)
        if context is not None and callable(getattr(context, "token_counter", None)):
            token_counter = context.token_counter()
        return estimate_context_messages_tokens(uncovered, token_counter)

    def _is_compact_to_target_candidate(
        self,
        ctx: Any,
        store: QAArtifactStore,
        qa_ref: QARef,
    ) -> bool:
        if qa_ref.tokens < self._config.qa_min_worth_tokens:
            return False
        state = store.get_or_init(qa_ref.qa_id)
        if state.state in ("RAW", "STAGED") and not state.products_ready:
            return True
        if qa_ref.is_history:
            return False
        if not self._has_products(store, qa_ref.qa_id):
            return False
        uncov = self._uncovered_tokens(ctx, store, qa_ref)
        if uncov < self._config.qa_min_worth_tokens:
            return False
        return is_qa_already_compact_in_window(
            qa_ref.get_messages(),
            qa_ref.qa_id,
            covers_upto_message_id=state.covers_upto_message_id,
        )

    def _should_force_reapply_active_qa(
        self,
        ctx: Any,
        store: QAArtifactStore,
        qa_ref: QARef,
    ) -> bool:
        if qa_ref.is_history or not self._has_products(store, qa_ref.qa_id):
            return False
        state = store.get_or_init(qa_ref.qa_id)
        if self._uncovered_tokens(ctx, store, qa_ref) < self._config.qa_min_worth_tokens:
            return False
        return is_qa_already_compact_in_window(
            qa_ref.get_messages(),
            qa_ref.qa_id,
            covers_upto_message_id=state.covers_upto_message_id,
        )

    async def _refresh_active_qa_products(
        self,
        ctx: Any,
        workspace: Any,
        qa_ref: QARef,
    ) -> bool:
        """Rolling compact for in-flight QA: extend covers_upto when tail keeps growing."""
        if not self._should_force_reapply_active_qa(ctx, self.build_store(ctx, workspace), qa_ref):
            return False

        store = self.build_store(ctx, workspace)
        messages = qa_ref.get_messages()
        keep_tail = self._config.active_qa_rolling_keep_tail
        if keep_tail <= 0:
            fold_messages = list(messages)
        else:
            if len(messages) <= keep_tail:
                return False
            fold_messages = list(messages[:-keep_tail])
        if not fold_messages:
            return False

        if not self._claim(store, qa_ref.qa_id, stale_refresh=True):
            await self._await_products(store, qa_ref.qa_id)
            if not self._claim(store, qa_ref.qa_id, stale_refresh=True):
                logger.info(
                    "[QAArtifactManager] active qa rolling compact claim failed qa_id=%s",
                    qa_ref.qa_id,
                )
                return False

        try:
            await self._produce_impl(
                ctx,
                workspace,
                qa_ref,
                mark_ready=False,
                messages=fold_messages,
            )
            logger.info(
                "[QAArtifactManager] active qa rolling compact qa_id=%s "
                "fold_messages=%s uncovered_tokens=%s",
                qa_ref.qa_id,
                len(fold_messages),
                self._uncovered_tokens(ctx, store, qa_ref),
            )
            write_context_trace(
                "qa_artifact.active_qa_rolling_compact",
                {
                    "qa_id": qa_ref.qa_id,
                    "fold_messages": len(fold_messages),
                    "keep_tail": keep_tail,
                },
            )
            return True
        except Exception as exc:
            logger.warning(
                "[QAArtifactManager] active qa rolling compact failed qa_id=%s error=%s",
                qa_ref.qa_id,
                exc,
            )
            state = store.get_or_init(qa_ref.qa_id)
            state.is_extracting = False
            store.save(qa_ref.qa_id, state)
            return False

    async def maybe_compact_history_block(
        self,
        ctx: Any,
        *,
        workspace: Any,
        window_qas: list[QARef],
        window_tokens: int,
    ) -> bool:
        _ = window_tokens
        store = self.build_store(ctx, workspace)
        for qa_ref in self._priority(window_qas, store):
            key = self._task_key(ctx, qa_ref.qa_id)
            if not qa_ref.is_history or key in self._bg:
                continue
            uncov = self._uncovered_tokens(ctx, store, qa_ref)
            if uncov < self._config.history_block_compact_tokens:
                continue
            stale = self._has_products(store, qa_ref.qa_id)
            if self._claim(store, qa_ref.qa_id, stale_refresh=stale):
                task = asyncio.create_task(self._produce(ctx, workspace, qa_ref, mark_ready=True))
                self._register_bg_task(ctx, qa_ref.qa_id, task)
                logger.info(
                    "[QAArtifactManager] trigger1 qa_id=%s uncovered_tokens=%s stale=%s",
                    qa_ref.qa_id,
                    uncov,
                    stale,
                )
                return True
        return False

    @staticmethod
    def _freeze_artifacts_complete(
        store: QAArtifactStore,
        qa_id: str,
        native_messages: list,
    ) -> bool:
        if not native_messages:
            return True
        if not QAArtifactManager._has_products(store, qa_id):
            return False
        state = store.load(qa_id)
        if state is None or not state.products_ready:
            return False
        last_id = get_context_message_id(native_messages[-1])
        if not last_id:
            return True
        return state.covers_upto_message_id == last_id

    def _cancel_overview_task_for_freeze(self, session_id: str, qa_id: str) -> None:
        key = (session_id, qa_id)
        task = self._overview_bg.pop(key, None)
        if task is None or task.done():
            return
        task.cancel()
        logger.info(
            "[QAArtifactManager] cancelled overview refresh for freeze produce qa_id=%s",
            qa_id,
        )

    def _prepare_freeze_produce_claim(
        self,
        store: QAArtifactStore,
        *,
        session_id: str,
        qa_id: str,
    ) -> bool:
        self._cancel_overview_task_for_freeze(session_id, qa_id)
        key = (session_id, qa_id)
        if key in self._bg:
            logger.info(
                "[QAArtifactManager] freeze produce skipped: full produce in flight qa_id=%s",
                qa_id,
            )
            return False
        state = store.get_or_init(qa_id)
        if state.is_extracting:
            state.is_extracting = False
            store.save(qa_id, state)
        return self._claim(store, qa_id, stale_refresh=True)

    def schedule_freeze_artifact_produce(
        self,
        ctx: Any,
        *,
        workspace: Any,
        qa_id: str,
        native_messages: list,
    ) -> bool:
        """Async full artifact produce after freeze; host freeze rail calls via ``post_commit``.

        Skipped when below ``history_block_compact_tokens`` or artifacts already complete.
        """
        if not native_messages:
            return False
        proc_ctx = self._produce_ctx(ctx)
        session_id = self._session_id_from_ctx(proc_ctx)
        store = self.build_store(proc_ctx, workspace)
        if self._freeze_artifacts_complete(store, qa_id, native_messages):
            logger.info(
                "[QAArtifactManager] freeze produce skipped: artifacts complete qa_id=%s",
                qa_id,
            )
            return False

        token_counter = None
        context = getattr(proc_ctx, "context", None)
        if context is not None and callable(getattr(context, "token_counter", None)):
            token_counter = context.token_counter()
        tokens = estimate_context_messages_tokens(native_messages, token_counter)
        if tokens < self._config.history_block_compact_tokens:
            logger.info(
                "[QAArtifactManager] freeze produce skipped: below history_block_compact "
                "qa_id=%s tokens=%s threshold=%s",
                qa_id,
                tokens,
                self._config.history_block_compact_tokens,
            )
            return False

        if not self._prepare_freeze_produce_claim(
            store,
            session_id=session_id,
            qa_id=qa_id,
        ):
            logger.info(
                "[QAArtifactManager] freeze produce skipped: claim failed qa_id=%s",
                qa_id,
            )
            return False

        snapshot = [message.model_copy(deep=True) for message in native_messages]
        qa_ref = QARef(
            qa_id=qa_id,
            tokens=tokens,
            is_history=True,
            get_messages=lambda msgs=snapshot: list(msgs),
        )

        async def _run_freeze_produce() -> None:
            try:
                await self._produce_impl(
                    proc_ctx,
                    workspace,
                    qa_ref,
                    mark_ready=True,
                    messages=list(snapshot),
                )
                write_context_trace(
                    "qa_artifact.freeze_produce",
                    {
                        "qa_id": qa_id,
                        "session_id": session_id,
                        "message_count": len(snapshot),
                        "tokens": tokens,
                        "result": "done",
                    },
                )
                logger.info(
                    "[QAArtifactManager] freeze produce done qa_id=%s messages=%s",
                    qa_id,
                    len(snapshot),
                )
            except asyncio.CancelledError:
                state = store.get_or_init(qa_id)
                state.is_extracting = False
                store.save(qa_id, state)
                raise
            except Exception as exc:
                state = store.get_or_init(qa_id)
                state.is_extracting = False
                store.save(qa_id, state)
                logger.warning(
                    "[QAArtifactManager] freeze produce failed qa_id=%s error=%s",
                    qa_id,
                    exc,
                    exc_info=True,
                )
                write_context_trace(
                    "qa_artifact.freeze_produce",
                    {
                        "qa_id": qa_id,
                        "session_id": session_id,
                        "result": "failed",
                        "error": str(exc),
                    },
                )

        task = asyncio.create_task(_run_freeze_produce())
        self._register_bg_task(proc_ctx, qa_id, task)
        write_context_trace(
            "qa_artifact.freeze_produce",
            {
                "qa_id": qa_id,
                "session_id": session_id,
                "message_count": len(snapshot),
                "tokens": tokens,
                "result": "scheduled",
            },
        )
        logger.info(
            "[QAArtifactManager] freeze produce scheduled qa_id=%s messages=%s tokens=%s",
            qa_id,
            len(snapshot),
            tokens,
        )
        return True

    def _schedule_overview_refresh(
        self,
        ctx: Any,
        workspace: Any,
        qa_ref: QARef,
        messages: list,
    ) -> None:
        qa_id = qa_ref.qa_id
        key = self._task_key(ctx, qa_id)
        if key in self._bg:
            return
        existing = self._overview_bg.get(key)
        if existing is not None and not existing.done():
            return
        store = self.build_store(ctx, workspace)
        if store.get_or_init(qa_id).is_extracting:
            return
        task = asyncio.create_task(
            self._refresh_overview_only(ctx, workspace, qa_ref, list(messages))
        )
        self._register_overview_task(ctx, qa_id, task)
        logger.info(
            "[QAArtifactManager] fallback overview refresh scheduled qa_id=%s messages=%s",
            qa_id,
            len(messages),
        )
        write_context_trace(
            "qa_artifact.fallback_overview_refresh",
            {"qa_id": qa_id, "mode": "scheduled", "messages": len(messages)},
        )

    async def _refresh_overview_only(
        self,
        ctx: Any,
        workspace: Any,
        qa_ref: QARef,
        messages: list,
    ) -> None:
        ctx = self._produce_ctx(ctx)
        store = self.build_store(ctx, workspace)
        qa_id = qa_ref.qa_id
        state = store.get_or_init(qa_id)
        state.is_extracting = True
        store.save(qa_id, state)
        try:
            await self._overview.generate(ctx, store, state, messages, qa_id=qa_id)
            write_context_trace(
                "qa_artifact.fallback_overview_refresh",
                {"qa_id": qa_id, "mode": "done", "messages": len(messages)},
            )
        except asyncio.CancelledError:
            logger.info(
                "[QAArtifactManager] overview refresh cancelled qa_id=%s session_id=%s",
                qa_id,
                self._session_id_from_ctx(ctx),
            )
            raise
        except Exception as exc:
            logger.warning(
                "[QAArtifactManager] fallback overview refresh failed qa_id=%s error=%s",
                qa_id,
                exc,
            )
            write_context_trace(
                "qa_artifact.fallback_overview_refresh",
                {"qa_id": qa_id, "mode": "failed", "error": str(exc)},
            )
        finally:
            key = self._task_key(ctx, qa_id)
            state = store.get_or_init(qa_id)
            # Full produce (e.g. freeze) may have claimed after we were cancelled;
            # do not clear is_extracting while it is still in flight.
            if key not in self._bg:
                state.is_extracting = False
            store.save(qa_id, state)
            self._overview_bg.pop(key, None)

    async def await_pending_overview(
        self,
        qa_id: str,
        *,
        session_id: str | None = None,
        timeout_s: float = _OVERVIEW_FREEZE_AWAIT_S,
    ) -> bool:
        if timeout_s <= 0:
            return True
        key = self._resolve_overview_key(qa_id, session_id)
        if key is None:
            return True
        task = self._overview_bg.get(key)
        if task is None or task.done():
            return True
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout_s)
            return True
        except asyncio.TimeoutError:
            logger.info(
                "[QAArtifactManager] await overview timeout qa_id=%s timeout_s=%s",
                qa_id,
                timeout_s,
            )
            return False

    async def archive_fold_slice_before_fallback(
        self,
        ctx: Any,
        *,
        workspace: Any,
        qa_ref: QARef,
        fold_slice: list,
        all_messages: list | None = None,
    ) -> None:
        if not fold_slice:
            return

        ctx = self._produce_ctx(ctx)
        store = self.build_store(ctx, workspace)
        qa_id = qa_ref.qa_id
        state = store.get_or_init(qa_id)
        source_messages = list(all_messages) if all_messages is not None else qa_ref.get_messages()
        all_current = [message for message in source_messages if message_qa_id(message) == qa_id]

        if state.covers_upto_message_id and fold_slice and all_current:
            cutoff = find_message_index_by_context_message_id(
                all_current,
                state.covers_upto_message_id,
            )
            first_id = get_context_message_id(fold_slice[0])
            first_idx = find_message_index_by_context_message_id(all_current, first_id)
            if first_idx >= 0 and cutoff >= 0 and first_idx <= cutoff:
                logger.warning(
                    "[QAArtifactManager] stale fold_slice before fallback qa_id=%s",
                    qa_id,
                )
                if self._claim(store, qa_id, stale_refresh=True):
                    await self._produce_impl(
                        ctx,
                        workspace,
                        qa_ref,
                        mark_ready=False,
                        messages=qa_ref.get_messages(),
                    )
                write_context_trace(
                    "qa_artifact.fallback_archive",
                    {
                        "qa_id": qa_id,
                        "mode": "produce",
                        "stale_refresh": True,
                        "messages": len(qa_ref.get_messages()),
                    },
                )
                return

        if await self._has_products_for_fallback(store, qa_id):
            new_entries = await self._catalog.build_slice(ctx, fold_slice, qa_id=qa_id)
            await self._catalog.append_entries(store, state, qa_id, new_entries)
            state = store.get_or_init(qa_id)
            state.covers_upto_message_id = get_context_message_id(fold_slice[-1])
            store.save(qa_id, state)
            write_context_trace(
                "qa_artifact.fallback_archive",
                {
                    "qa_id": qa_id,
                    "mode": "append",
                    "entries": len(new_entries),
                    "fold_messages": len(fold_slice),
                    "overview_refresh": "scheduled",
                },
            )
            self._schedule_overview_refresh(
                ctx,
                workspace,
                qa_ref,
                list(qa_ref.get_messages()),
            )
            return

        messages = fold_slice if fold_slice else qa_ref.get_messages()
        await self._produce_impl(
            ctx,
            workspace,
            qa_ref,
            mark_ready=False,
            messages=messages,
        )
        write_context_trace(
            "qa_artifact.fallback_archive",
            {
                "qa_id": qa_id,
                "mode": "produce",
                "messages": len(messages),
            },
        )

    async def on_session_memory_trigger(
        self,
        ctx: Any,
        *,
        workspace: Any,
        window_qas: list[QARef],
    ) -> None:
        store = self.build_store(ctx, workspace)
        for qa_ref in window_qas:
            key = self._task_key(ctx, qa_ref.qa_id)
            if key in self._bg:
                continue
            if qa_ref.is_history:
                uncov = self._uncovered_tokens(ctx, store, qa_ref)
                if uncov < self._config.qa_min_worth_tokens:
                    continue
                stale = self._has_products(store, qa_ref.qa_id)
                if self._claim(store, qa_ref.qa_id, stale_refresh=stale):
                    task = asyncio.create_task(self._produce(ctx, workspace, qa_ref, mark_ready=True))
                    self._register_bg_task(ctx, qa_ref.qa_id, task)
            elif self._claim(store, qa_ref.qa_id):
                task = asyncio.create_task(self._produce(ctx, workspace, qa_ref, mark_ready=False))
                self._register_bg_task(ctx, qa_ref.qa_id, task)
        logger.info(
            "[QAArtifactManager] trigger2 scheduled count=%s",
            len(self._bg),
        )

    async def _produce(self, ctx: Any, workspace: Any, qa: QARef, *, mark_ready: bool) -> None:
        try:
            await self._produce_impl(
                ctx,
                workspace,
                qa,
                mark_ready=mark_ready,
                messages=self._initial_fold_messages(qa),
            )
        except asyncio.CancelledError:
            logger.info(
                "[QAArtifactManager] produce cancelled qa_id=%s session_id=%s",
                qa.qa_id,
                self._session_id_from_ctx(ctx),
            )
            raise
        except Exception as exc:
            logger.exception(
                "[QAArtifactManager] produce failed qa_id=%s error=%s",
                qa.qa_id,
                exc,
            )

    async def _produce_impl(
        self,
        ctx: Any,
        workspace: Any,
        qa: QARef,
        *,
        mark_ready: bool,
        messages: list | None = None,
    ) -> None:
        ctx = self._produce_ctx(ctx)
        store = self.build_store(ctx, workspace)
        state = store.get_or_init(qa.qa_id)
        msgs = list(messages) if messages is not None else qa.get_messages()
        covers_upto: str | None = None
        next_state: str | None = None
        products_ready = False
        try:
            await asyncio.gather(
                self._overview.generate(ctx, store, state, msgs, qa_id=qa.qa_id),
                self._catalog.build(ctx, store, state, msgs, qa_id=qa.qa_id),
            )
            covers_upto = get_context_message_id(msgs[-1]) if msgs else None
            if mark_ready:
                next_state = "RAW"
                products_ready = True
            else:
                next_state = "STAGED"
        finally:
            state = store.get_or_init(qa.qa_id)
            state.is_extracting = False
            if covers_upto is not None:
                state.covers_upto_message_id = covers_upto
            if next_state is not None and state.state != "COMPACTED":
                state.state = next_state
                state.products_ready = products_ready
            store.save(qa.qa_id, state)
            self._bg.pop(self._task_key(ctx, qa.qa_id), None)
            if next_state is not None:
                session = getattr(ctx, "session", None)
                await maybe_persist_qa_memory_state(session, qa_id=qa.qa_id)

    async def collect_ready_history(
        self,
        ctx: Any,
        *,
        workspace: Any,
        window_qas: list[QARef],
    ) -> list[tuple[str, QAArtifacts, str | None]]:
        store = self.build_store(ctx, workspace)
        ready: list[tuple[str, QAArtifacts, str | None]] = []
        for qa_ref in window_qas:
            if not qa_ref.is_history:
                continue
            state = store.get_or_init(qa_ref.qa_id)
            if not self._has_products(store, qa_ref.qa_id):
                continue
            ready.append(
                (
                    qa_ref.qa_id,
                    await self._read_artifacts(store, state),
                    state.covers_upto_message_id,
                )
            )
            if state.state != "COMPACTED":
                state.state = "COMPACTED"
                state.products_ready = False
                store.save(qa_ref.qa_id, state)
        return ready

    @staticmethod
    def has_history_artifacts(store: QAArtifactStore, window_qas: list[QARef]) -> bool:
        """True when store holds artifact products for any history QA in window_qas."""
        for qa_ref in window_qas:
            if not qa_ref.is_history:
                continue
            if QAArtifactManager._has_products(store, qa_ref.qa_id):
                return True
        return False

    @staticmethod
    def _history_messages_in_window(context: Any, window_qas: list[QARef]) -> bool:
        history_ids = {qa_ref.qa_id for qa_ref in window_qas if qa_ref.is_history}
        if not history_ids:
            return False
        for message in context.get_messages():
            qa_id = message_qa_id(message)
            if qa_id in history_ids:
                return True
        return False

    @staticmethod
    def history_settled(
        context: Any,
        store: QAArtifactStore,
        window_qas: list[QARef],
    ) -> bool:
        """History QA needs no further L0 apply work for this assembly round."""
        if is_assembly_whole_compact_applied(context):
            return True
        if not QAArtifactManager.has_history_artifacts(store, window_qas):
            return True
        if not QAArtifactManager._history_messages_in_window(context, window_qas):
            return True
        return QAArtifactManager._all_ready_history_already_compact(context, store, window_qas)

    @staticmethod
    def needs_history_artifact_work(
        context: Any,
        store: QAArtifactStore,
        window_qas: list[QARef],
    ) -> bool:
        if not QAArtifactManager.has_history_artifacts(store, window_qas):
            return False
        return not QAArtifactManager.history_settled(context, store, window_qas)

    @staticmethod
    def _all_ready_history_already_compact(
        context: Any,
        store: QAArtifactStore,
        window_qas: list[QARef],
    ) -> bool:
        messages = context.get_messages()
        saw_pending = False
        for qa_ref in window_qas:
            if not qa_ref.is_history:
                continue
            if not QAArtifactManager._has_products(store, qa_ref.qa_id):
                continue
            state = store.load(qa_ref.qa_id)
            if state is None:
                return False
            saw_pending = True
            if not is_qa_already_compact_in_window(
                messages,
                qa_ref.qa_id,
                covers_upto_message_id=state.covers_upto_message_id,
            ):
                return False
        return saw_pending

    async def apply_artifact_to_context(
        self,
        ctx: Any,
        *,
        workspace: Any,
        window_qas: list[QARef],
        context: Any,
    ) -> bool:
        """每轮应用产物：collect + replace（含已 COMPACTED 重贴）。"""
        store = self.build_store(ctx, workspace)
        if not self.needs_history_artifact_work(context, store, window_qas):
            return False
        ready = await self.collect_ready_history(ctx, workspace=workspace, window_qas=window_qas)
        token_counter = context.token_counter() if hasattr(context, "token_counter") else None
        for qa_id, artifacts, covers_upto in ready:
            apply_artifact_to_context(
                context,
                qa_id,
                artifacts,
                token_counter=token_counter,
                covers_upto_message_id=covers_upto,
            )
        if ready:
            write_context_trace(
                "qa_artifact.collect_ready_history",
                {"qa_ids": [qa_id for qa_id, _, _ in ready], "count": len(ready)},
            )
        return bool(ready)

    async def apply_ready_history_to_context(
        self,
        ctx: Any,
        *,
        workspace: Any,
        window_qas: list[QARef],
        context: Any,
    ) -> bool:
        return await self.apply_artifact_to_context(
            ctx,
            workspace=workspace,
            window_qas=window_qas,
            context=context,
        )

    async def compact_to_target(
        self,
        ctx: Any,
        *,
        workspace: Any,
        window_qas: list[QARef],
        total_tokens: int,
        context: Any,
        fallback: Any,
        trigger_total_tokens: int | None = None,
    ) -> bool:
        store = self.build_store(ctx, workspace)
        est = total_tokens
        token_counter = context.token_counter() if hasattr(context, "token_counter") else None
        trigger_line = trigger_total_tokens or self._config.full_compact_target_tokens

        for _ in range(self._config.full_compact_max_waves):
            if est <= self._config.full_compact_target_tokens:
                logger.info("[QAArtifactManager] compact_to_target reached target est=%s", est)
                return True

            candidates: list[QARef] = []
            for qa_ref in window_qas:
                if self._is_compact_to_target_candidate(ctx, store, qa_ref):
                    candidates.append(qa_ref)
            candidates.sort(key=lambda item: (0 if item.is_history else 1, -item.tokens))
            wave = candidates[: self._config.full_compact_max_qas]
            if not wave:
                logger.info("[QAArtifactManager] compact_to_target no candidates, fallback")
                return await fallback()

            reduced = 0
            for qa_ref in wave:
                refreshed = await self._refresh_active_qa_products(ctx, workspace, qa_ref)
                try:
                    artifact = await self.ensure_compacted(ctx, workspace=workspace, qa=qa_ref)
                except Exception as exc:
                    logger.warning(
                        "[QAArtifactManager] ensure_compacted failed qa_id=%s error=%s",
                        qa_ref.qa_id,
                        exc,
                    )
                    reduced += self._degrade_in_place(context, qa_ref, token_counter)
                    continue

                state = store.get_or_init(qa_ref.qa_id)
                force_reapply = refreshed or self._should_force_reapply_active_qa(ctx, store, qa_ref)
                qa_reduced = apply_artifact_to_context(
                    context,
                    qa_ref.qa_id,
                    artifact,
                    token_counter=token_counter,
                    covers_upto_message_id=state.covers_upto_message_id,
                    force_reapply=force_reapply,
                )
                reduced += qa_reduced
                if qa_reduced > 0:
                    state = store.get_or_init(qa_ref.qa_id)
                    state.state = "COMPACTED"
                    state.products_ready = False
                    store.save(qa_ref.qa_id, state)
            if reduced == 0:
                est = estimate_context_messages_tokens(context.get_messages(), token_counter)
                if est <= self._config.full_compact_target_tokens:
                    logger.info(
                        "[QAArtifactManager] compact_to_target zero progress but at target est=%s",
                        est,
                    )
                    return True
                if est <= trigger_line:
                    logger.info(
                        "[QAArtifactManager] compact_to_target zero progress but under trigger est=%s",
                        est,
                    )
                    return True
                logger.info("[QAArtifactManager] compact_to_target zero progress, fallback")
                return await fallback()
            est = estimate_context_messages_tokens(context.get_messages(), token_counter)
            if est <= self._config.full_compact_target_tokens:
                logger.info("[QAArtifactManager] compact_to_target reached target est=%s", est)
                write_context_trace(
                    "qa_artifact.compact_to_target",
                    {"result": "target_reached", "estimated_tokens": est, "waves_used": _ + 1},
                )
                return True

        if est > self._config.full_compact_target_tokens:
            logger.info("[QAArtifactManager] compact_to_target waves exhausted, fallback est=%s", est)
            return await fallback()
        return True

    @staticmethod
    def _degrade_in_place(context: Any, qa_ref: QARef, token_counter: Any | None) -> int:
        messages = qa_ref.get_messages()
        if not messages:
            return 0
        tail = messages[-2:] if len(messages) >= 2 else messages
        overview = render_plain(tail)
        artifacts = QAArtifacts(overview=overview, entries=[])
        return apply_artifact_to_context(
            context,
            qa_ref.qa_id,
            artifacts,
            token_counter=token_counter,
        )

    async def ensure_compacted(self, ctx: Any, *, workspace: Any, qa: QARef) -> QAArtifacts:
        store = self.build_store(ctx, workspace)
        state = store.get_or_init(qa.qa_id)
        if state.state in ("STAGED", "COMPACTED") or state.products_ready:
            return await self._read_artifacts(store, state)
        if not self._claim(store, qa.qa_id):
            await self._await_products(store, qa.qa_id)
            return await self._read_artifacts(store, store.get_or_init(qa.qa_id))
        messages = self._initial_fold_messages(qa)
        await self._produce_sync(ctx, workspace, qa, messages)
        return await self._read_artifacts(store, store.get_or_init(qa.qa_id))

    async def _produce_sync(self, ctx: Any, workspace: Any, qa: QARef, messages: list) -> None:
        await self._produce_sync_with_fallback(ctx, workspace, qa, messages)

    async def _produce_sync_with_fallback(
        self,
        ctx: Any,
        workspace: Any,
        qa: QARef,
        messages: list,
    ) -> None:
        working = list(messages)
        attempts = self._config.degrade_max_retries + 1
        for attempt in range(attempts):
            try:
                await self._produce_impl(
                    ctx,
                    workspace,
                    qa,
                    mark_ready=qa.is_history,
                    messages=working,
                )
                return
            except Exception as exc:
                logger.warning(
                    "[QAArtifactManager] produce_sync attempt=%s failed qa_id=%s error=%s",
                    attempt + 1,
                    qa.qa_id,
                    exc,
                )
                if attempt + 1 >= attempts:
                    break
                working = self._drop_earliest_unit(working)
                if not working:
                    break
        await self._commit_degraded_products(ctx, workspace, qa, messages)

    @staticmethod
    def _drop_earliest_unit(messages: list) -> list:
        rounds = group_completed_api_rounds(messages)
        if rounds:
            return list(messages[rounds[0][1]:])
        return list(messages[1:]) if len(messages) > 1 else []

    async def _commit_degraded_products(
        self,
        ctx: Any,
        workspace: Any,
        qa: QARef,
        messages: list,
    ) -> None:
        store = self.build_store(ctx, workspace)
        state = store.get_or_init(qa.qa_id)
        tail = messages[-2:] if len(messages) >= 2 else list(messages)
        overview = render_plain(tail)
        await store.write_atomic(state.overview_path, overview, state.pending_path)
        await store.write_atomic(
            state.catalog_path,
            json.dumps({"qa_id": qa.qa_id, "entries": []}, ensure_ascii=False),
            f"{state.pending_path}.catalog",
        )
        state.covers_upto_message_id = get_context_message_id(messages[-1]) if messages else None
        state.products_ready = True
        state.state = "COMPACTED" if qa.is_history else "STAGED"
        state.is_extracting = False
        store.save(qa.qa_id, state)
        self._bg.pop(self._task_key(ctx, qa.qa_id), None)
        logger.info(
            "[QAArtifactManager] degraded products committed qa_id=%s overview_chars=%s",
            qa.qa_id,
            len(overview),
        )

    async def _await_products(self, store: QAArtifactStore, qa_id: str) -> None:
        for _ in range(self._config.await_products_max_polls):
            state = store.get_or_init(qa_id)
            if state.products_ready or state.state in ("STAGED", "COMPACTED"):
                return
            if not state.is_extracting:
                return
            await asyncio.sleep(self._config.await_products_poll_interval_s)

    async def _read_artifacts(self, store: QAArtifactStore, state: Any) -> QAArtifacts:
        overview = await store.read_text(state.overview_path)
        entries = CatalogBuilder.load_entries(await store.read_text(state.catalog_path))
        return QAArtifacts(overview=overview, entries=entries)

    async def render_index(self, ctx: Any, *, workspace: Any, qa: QARef) -> str:
        store = self.build_store(ctx, workspace)
        state = store.get_or_init(qa.qa_id)
        if state.state == "RAW" and not state.products_ready:
            msgs = qa.get_messages()
            if not msgs:
                return f"QA {qa.qa_id} unavailable: no messages loaded for this block"
            return render_plain(msgs)
        overview = await store.read_text(state.overview_path)
        entries = CatalogBuilder.load_entries(await store.read_text(state.catalog_path))
        catalog = CatalogBuilder.render(qa.qa_id, entries)
        body = f"{overview}\n\n{catalog}".strip()
        tail = tail_after(qa.get_messages(), state.covers_upto_message_id)
        if tail:
            body = f"{body}\n\n[uncovered tail]\n{render_plain(tail)}".strip()
        return body

    @staticmethod
    def _priority(window_qas: list[QARef], store: QAArtifactStore) -> list[QARef]:
        _ = store
        return sorted(window_qas, key=lambda item: (0 if item.is_history else 1, -item.tokens))
