# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Orchestration facade for subagent spawn, wait, and close."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Any

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.common.logging import logger
from openjiuwen.core.session.checkpointer import CheckpointerFactory
from openjiuwen.harness.kv_cache.kv_cache_hooks import is_sticky_subagent_type
from openjiuwen.harness.subagent_runtime.activity_events import ActivityEmitter
from openjiuwen.harness.subagent_runtime.config import (
    WAIT_TIMEOUT_MS_DEFAULT,
    WAIT_TIMEOUT_MS_MAX,
    WAIT_TIMEOUT_MS_MIN,
    SubagentRuntimeConfig,
)
from openjiuwen.harness.subagent_runtime.errors import build_subagent_runtime_error, raise_subagent_not_found
from openjiuwen.harness.subagent_runtime.ids import build_subagent_id, new_task_id
from openjiuwen.harness.subagent_runtime.models import (
    ResumeResult,
    SpawnResult,
    SubagentActivity,
    SubagentMessage,
    SubagentMetadata,
    SubagentMetadataBuildParams,
    SubagentRecord,
    SubagentSnapshot,
    SubagentStatus,
    SubagentStatusKind,
    SubagentTurn,
    UserInputOp,
    WaitResult,
    resolve_presentation,
)
from openjiuwen.harness.subagent_runtime.output_file import (
    resolve_parent_workspace_root,
    write_turn_output,
)
from openjiuwen.harness.subagent_runtime.persistence import (
    DEFAULT_SNAPSHOT_PAGE_SIZE,
    MAX_ACTIVITIES_PER_INSTANCE,
    max_persisted_records,
    merge_subagent_bucket,
    read_subagent_bucket,
    trim_persisted_bucket,
)
from openjiuwen.harness.subagent_runtime.registry import SpawnReservation, SubagentRegistry
from openjiuwen.harness.subagent_runtime.session_manager import SubagentSessionManager
from openjiuwen.harness.subagent_runtime.status import StatusReceiver
from openjiuwen.harness.subagent_runtime.status_events import (
    build_subagent_updated_payload,
    emit_subagent_updated,
    is_instance_closed,
    is_turn_finished,
    map_status_to_view,
    resolve_turn_outcome,
)
from openjiuwen.harness.subagent_runtime.transcript_events import TranscriptEmitter

_TASK_DESCRIPTION_MAX_LEN = 2000


def _cursor_start_index(turns: list[SubagentTurn], cursor: str | None) -> int:
    if not cursor:
        return 0
    if ":" not in cursor:
        return 0
    subagent_id, _, seq_text = cursor.partition(":")
    try:
        seq = int(seq_text)
    except ValueError:
        return 0
    for index, turn in enumerate(turns):
        if turn.subagent_id == subagent_id and turn.seq > seq:
            return index
    return len(turns)


def _cursor_start_index_merged(
    items: list[tuple[str, SubagentTurn | SubagentActivity]],
    cursor: str | None,
) -> int:
    if not cursor:
        return 0
    parts = cursor.split(":")
    if len(parts) < 2:
        return 0
    subagent_id = parts[0]
    try:
        seq = int(parts[1])
    except ValueError:
        return 0
    kind = parts[2] if len(parts) > 2 else None
    for index, (item_kind, item) in enumerate(items):
        if item.subagent_id != subagent_id:
            continue
        if item.seq > seq:
            return index
        if item.seq == seq and kind is not None and item_kind > kind:
            return index
    return len(items)


class SubagentControl:
    """Parent-session orchestration entry for subagent runtime."""

    def __init__(
        self,
        parent_agent: Any,
        parent_session_id: str,
        config: SubagentRuntimeConfig | None = None,
        parent_session: Any | None = None,
    ) -> None:
        self._parent_agent = parent_agent
        self._parent_session_id = parent_session_id
        self._parent_session = parent_session
        self._config = config or SubagentRuntimeConfig()
        self._registry = SubagentRegistry(self._config)
        self._semaphore = asyncio.Semaphore(self._config.max_concurrent_running)
        self._closed_records: dict[str, SubagentRecord] = {}
        self._turns: dict[str, list[SubagentTurn]] = {}
        self._turn_seq: dict[str, int] = {}
        self._activities: dict[str, deque[SubagentActivity]] = {}
        self._activity_seq: dict[str, int] = {}
        self._activity_ready: set[tuple[str, str]] = set()
        self._pending_activities: dict[tuple[str, str], list[SubagentActivity]] = {}
        self._hydrated = False
        self._activity_emitter: ActivityEmitter | None = None
        self._transcript_emitter: TranscriptEmitter | None = None
        if self._config.enable_activity_stream and self._parent_session is not None:
            self._activity_emitter = ActivityEmitter(self._parent_session, config=self._config)
            self._activity_emitter.start()
        if self._config.enable_transcript_stream and self._parent_session is not None:
            self._transcript_emitter = TranscriptEmitter(self._parent_session, config=self._config)
        self._manager = SubagentSessionManager(
            parent_agent,
            self._config,
            self._semaphore,
            status_change_handler=self._handle_instance_status_changed,
            activity_handler=self._handle_activity if self._config.enable_activity_stream else None,
            transcript_handler=(self._handle_transcript_message if self._config.enable_transcript_stream else None),
        )

    async def spawn(
        self,
        subagent_type: str,
        query: str,
        *,
        subagent_id: str | None = None,
        display_name: str | None = None,
        role: str | None = None,
        browser_capabilities: list[str] | None = None,
    ) -> SpawnResult:
        sticky = is_sticky_subagent_type(subagent_type)
        sid = subagent_id or build_subagent_id(
            self._parent_session_id,
            subagent_type,
            sticky=sticky,
        )
        existing = self._manager.find(sid)
        if existing is not None and not existing.is_closed():
            raise build_subagent_runtime_error(
                f"subagent already live: {sid}; use subagent_wait to collect its result",
            )

        task_id = new_task_id()
        resolved_name, resolved_role = self._resolve_spawn_presentation(
            subagent_type,
            display_name,
            role,
        )
        task_description = self._truncate_task_description(query)
        reservation = await self._acquire_slot()

        try:
            instance = await self._manager.create(
                subagent_type=subagent_type,
                subagent_id=sid,
                parent_session_id=self._parent_session_id,
                display_name=resolved_name,
                role=resolved_role,
                browser_capabilities=browser_capabilities,
            )
            reservation.commit(
                SubagentMetadataBuildParams(
                    subagent_id=sid,
                    subagent_type=subagent_type,
                    task_id=task_id,
                    display_name=resolved_name,
                    role=resolved_role,
                    task_description=task_description,
                ).to_metadata(parent_session_id=self._parent_session_id),
            )
        except Exception:
            reservation.rollback()
            raise

        try:
            await instance.enqueue(UserInputOp(query=query, task_id=task_id))
        except Exception:
            await self._manager.remove(sid, reason="spawn_failed")
            self._registry.release(sid)
            raise

        self._registry.touch(sid)
        self._prepare_turn_activity_gate(sid, task_id)
        await self._emit_running_for_turn(sid, task_id)
        return SpawnResult(
            subagent_id=sid,
            task_id=task_id,
            status=instance.agent_status(),
        )

    async def wait(
        self,
        subagent_ids: list[str],
        timeout_ms: int = WAIT_TIMEOUT_MS_DEFAULT,
    ) -> WaitResult:
        timeout_s = min(max(timeout_ms, WAIT_TIMEOUT_MS_MIN), WAIT_TIMEOUT_MS_MAX) / 1000
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s

        targets = list(dict.fromkeys(subagent_ids))
        statuses: dict[str, SubagentStatus] = {}
        waiters: dict[asyncio.Task[SubagentStatus], str] = {}

        for sid in targets:
            instance = self._manager.find(sid)
            if instance is None:
                statuses[sid] = SubagentStatus.not_found()
                continue
            self._registry.touch(sid)
            receiver = instance.subscribe_status()
            current = receiver.current()
            if current.is_final() and not instance.has_pending_work():
                statuses[sid] = current
                continue
            waiters[asyncio.create_task(receiver.wait_for_final())] = sid

        pending = set(waiters)
        try:
            while pending:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    break
                done, pending = await asyncio.wait(
                    pending,
                    timeout=remaining,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not done:
                    break
                for task in done:
                    sid = waiters[task]
                    statuses[sid] = self._resolve_final(sid, task)
        finally:
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

        for sid in targets:
            statuses.setdefault(sid, self.get_status(sid))

        timed_out = any(not status.is_final() for status in statuses.values())
        results: dict[str, str] = {}
        for sid in targets:
            if statuses[sid].kind is not SubagentStatusKind.COMPLETED:
                continue
            instance = self._manager.find(sid)
            if instance is None or not instance.last_output:
                continue
            results[sid] = instance.last_output
        output_files: dict[str, str] = {}
        for sid in targets:
            if statuses[sid].kind is not SubagentStatusKind.COMPLETED:
                continue
            turn = self._latest_answered_turn(sid)
            if turn is None or not turn.output_file:
                continue
            output_files[sid] = turn.output_file
        return WaitResult(
            statuses=statuses,
            results=results,
            output_files=output_files,
            timed_out=timed_out,
        )

    def get_status(self, subagent_id: str) -> SubagentStatus:
        instance = self._manager.find(subagent_id)
        if instance is None:
            return SubagentStatus.not_found()
        return instance.agent_status()

    def subscribe_status(self, subagent_id: str) -> StatusReceiver:
        return self._manager.get(subagent_id).subscribe_status()

    def list_live(self) -> list[SubagentMetadata]:
        return self._registry.list_live()

    def capacity(self) -> dict[str, int]:
        """Return current slot usage for the parent session."""
        return {
            "used": self._registry.count,
            "max": self._config.max_subagents,
        }

    def describe_one(self, subagent_id: str) -> dict[str, Any] | None:
        """Return one subagent's external status payload, or None if never registered."""
        metadata = self._registry.find_metadata(subagent_id)
        instance = self._manager.find(subagent_id)
        if metadata is None and instance is None:
            record = self._closed_records.get(subagent_id)
            if record is None:
                return None
            return self._closed_record_to_payload(record, self._parent_session_id)

        if metadata is None:
            status = instance.agent_status() if instance is not None else SubagentStatus.not_found()
            return build_subagent_updated_payload(
                subagent_id=subagent_id,
                subagent_type=instance.subagent_type if instance else "",
                display_name=instance.display_name if instance else subagent_id,
                role=instance.role if instance else "",
                parent_session_id=self._parent_session_id,
                task_description="",
                created_at_ms=0.0,
                updated_at_ms=0.0,
                closed_at_ms=None,
                status=status,
                revision=instance.revision() if instance is not None else 0,
            )

        status = instance.agent_status() if instance is not None else SubagentStatus.not_found()
        revision = instance.revision() if instance is not None else 0
        return self._metadata_to_payload(
            metadata,
            status=status,
            revision=revision,
            parent_session_id=self._parent_session_id,
        )

    def describe_live(self) -> list[dict[str, Any]]:
        """Return external status payloads for every live subagent."""
        rows: list[dict[str, Any]] = []
        for metadata in self._registry.list_live():
            instance = self._manager.find(metadata.subagent_id)
            if instance is None:
                continue
            rows.append(
                self._metadata_to_payload(
                    metadata,
                    status=instance.agent_status(),
                    revision=instance.revision(),
                    parent_session_id=self._parent_session_id,
                )
            )
        return rows

    async def emit_status_update(
        self,
        subagent_id: str,
        *,
        session: Any | None = None,
    ) -> None:
        """Push one subagent status update to the parent session stream."""
        target_session = session or self._parent_session
        if target_session is None:
            return
        projection = self.describe_one(subagent_id)
        if projection is None:
            return
        await emit_subagent_updated(target_session, projection=projection)

    async def send_input(
        self,
        subagent_id: str,
        query: str,
        *,
        interrupt: bool = False,
    ) -> str:
        """Enqueue follow-up input and return a new task_id without blocking."""
        instance = self._manager.find(subagent_id)
        if instance is None or instance.is_closed():
            raise build_subagent_runtime_error(
                f"subagent closed or not found: {subagent_id}; call subagent_resume first",
            )
        if interrupt:
            await instance.interrupt()
        task_id = new_task_id()
        await instance.enqueue(UserInputOp(query=query, task_id=task_id))
        await instance.status.set(SubagentStatus.pending_init())
        self._registry.touch(subagent_id)
        metadata = self._registry.find_metadata(subagent_id)
        if metadata is not None:
            metadata.current_task_id = task_id
            metadata.task_description = self._truncate_task_description(query)
            metadata.updated_at_ms = time.time() * 1000
        self._prepare_turn_activity_gate(subagent_id, task_id)
        await self._emit_running_for_turn(subagent_id, task_id)
        return task_id

    async def resume(self, subagent_id: str) -> ResumeResult:
        """Restore a closed or evicted subagent from checkpointer without enqueueing work."""
        existing = self._manager.find(subagent_id)
        if existing is not None and not existing.is_closed():
            status = existing.agent_status()
            return ResumeResult(
                status=status,
                restored=False,
                message="Instance is already live; use subagent_send_input directly.",
            )

        record = self._closed_records.get(subagent_id)
        if record is None:
            raise_subagent_not_found(subagent_id)

        checkpointer = CheckpointerFactory.get_checkpointer()
        if not await checkpointer.session_exists(subagent_id):
            raise_subagent_not_found(subagent_id)

        reservation = await self._acquire_slot()
        try:
            await self._manager.restore(
                subagent_id=subagent_id,
                subagent_type=record.subagent_type,
                parent_session_id=self._parent_session_id,
                display_name=record.display_name,
                role=record.role,
            )
            reservation.commit(
                self._build_metadata_from_record(record, task_id=None),
            )
        except Exception:
            reservation.rollback()
            raise

        self._closed_records.pop(subagent_id, None)
        restored = self._manager.find(subagent_id)
        status = restored.agent_status() if restored is not None else SubagentStatus.pending_init()
        return ResumeResult(status=status, restored=True)

    async def close(self, subagent_id: str, reason: str = "manual") -> SubagentStatus:
        instance = self._manager.get(subagent_id)
        previous = instance.agent_status()
        if previous.kind is SubagentStatusKind.RUNNING:
            raise build_subagent_runtime_error(
                f"cannot close running subagent: {subagent_id}",
            )
        await self._evict_from_memory(subagent_id, reason=reason)
        return previous

    async def cancel_all(self, reason: str = "parent_ended") -> list[str]:
        """Force-close every live subagent regardless of RUNNING state."""
        closed: list[str] = []
        for sid in list(self._manager.list_ids()):
            try:
                await self._evict_from_memory(sid, reason=reason)
            except Exception:
                logger.warning("[SubagentControl] cancel_all failed: sid=%s", sid)
                self._registry.release(sid)
            closed.append(sid)
        self.flush()
        return closed

    def hydrate(self) -> None:
        """Load persisted subagent records and turns from the parent session."""
        if self._hydrated or self._parent_session is None:
            return
        bucket = read_subagent_bucket(self._parent_session)
        records = bucket.get("records") or {}
        if isinstance(records, dict):
            for sid, raw in records.items():
                if not isinstance(raw, dict):
                    continue
                record = SubagentRecord.from_dict(raw)
                if record.closed_at_ms is None:
                    fallback_ms = record.updated_at_ms or record.created_at_ms
                    record = SubagentRecord(
                        subagent_id=record.subagent_id,
                        subagent_type=record.subagent_type,
                        display_name=record.display_name,
                        role=record.role,
                        task_description=record.task_description,
                        created_at_ms=record.created_at_ms,
                        updated_at_ms=record.updated_at_ms or fallback_ms,
                        closed_at_ms=fallback_ms,
                        closed_reason="parent_ended",
                    )
                self._closed_records[sid] = record

        turns = bucket.get("turns") or {}
        if isinstance(turns, dict):
            for sid, raw_turns in turns.items():
                if not isinstance(raw_turns, list):
                    continue
                items = [SubagentTurn.from_dict(item) for item in raw_turns if isinstance(item, dict)]
                if not items:
                    continue
                items.sort(key=lambda item: item.seq)
                self._turns[sid] = items
                self._turn_seq[sid] = max(item.seq for item in items)

        activities = bucket.get("activities") or {}
        if isinstance(activities, dict):
            for sid, raw_items in activities.items():
                if not isinstance(raw_items, list):
                    continue
                items = [SubagentActivity.from_dict(item) for item in raw_items if isinstance(item, dict)]
                if not items:
                    continue
                items.sort(key=lambda item: item.seq)
                self._activities[sid] = deque(items, maxlen=MAX_ACTIVITIES_PER_INSTANCE)
                self._activity_seq[sid] = max(item.seq for item in items)
        self._hydrated = True

    def flush(self) -> None:
        """Persist closed records and turns into the parent session state."""
        if self._parent_session is None:
            return
        try:
            bucket = read_subagent_bucket(self._parent_session)
            records: dict[str, Any] = dict(bucket.get("records") or {})
            turns: dict[str, Any] = dict(bucket.get("turns") or {})
            activities: dict[str, Any] = dict(bucket.get("activities") or {})

            for sid, record in self._closed_records.items():
                records[sid] = record.to_dict()

            for metadata in self._registry.list_live():
                records[metadata.subagent_id] = SubagentControl._metadata_to_record(metadata).to_dict()

            for sid, items in self._turns.items():
                existing = [SubagentTurn.from_dict(item) for item in (turns.get(sid) or []) if isinstance(item, dict)]
                merged = self._merge_turns(existing, items)
                turns[sid] = [item.to_dict() for item in merged]

            for sid, items in self._activities.items():
                existing = [
                    SubagentActivity.from_dict(item) for item in (activities.get(sid) or []) if isinstance(item, dict)
                ]
                merged = self._merge_activities(existing, list(items))
                activities[sid] = [item.to_dict() for item in merged]

            records, turns, activities = trim_persisted_bucket(
                records,
                turns,
                max_records=max_persisted_records(self._config.max_subagents),
                activities=activities,
            )
            revision = int(bucket.get("revision") or 0) + 1
            merge_subagent_bucket(
                self._parent_session,
                {
                    "records": records,
                    "turns": turns,
                    "activities": activities,
                    "revision": revision,
                },
            )
        except Exception as exc:
            logger.warning("[SubagentControl] flush failed: %s", exc)

    def snapshot(
        self,
        *,
        cursor: str | None = None,
        page_size: int = DEFAULT_SNAPSHOT_PAGE_SIZE,
    ) -> SubagentSnapshot:
        """Return a read-only parent-session view of subagents and turn history."""
        subagents: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in self.describe_live():
            sid = str(row.get("subagent_id") or "")
            if sid:
                seen.add(sid)
            subagents.append(row)
        for sid, record in self._closed_records.items():
            if sid in seen:
                continue
            subagents.append(self._closed_record_to_payload(record, self._parent_session_id))

        all_turns = self._all_turns_flat()
        all_activities = self._all_activities_flat()
        merged_items = self._merge_snapshot_items(all_turns, all_activities)
        start_index = _cursor_start_index_merged(merged_items, cursor)
        end_index = start_index + page_size
        page_items = merged_items[start_index:end_index]
        page_turns = [item[1] for item in page_items if item[0] == "turn"]
        page_activities = [item[1] for item in page_items if item[0] == "activity"]
        next_cursor = None
        if start_index + page_size < len(merged_items) and page_items:
            last_kind, last_item = page_items[-1]
            sid = last_item.subagent_id
            seq = last_item.seq
            next_cursor = f"{sid}:{seq}:{last_kind}"
        return SubagentSnapshot(
            subagents=subagents,
            turns=page_turns,
            activities=page_activities,
            cursor=next_cursor,
        )

    async def _evict_from_memory(self, subagent_id: str, *, reason: str) -> None:
        metadata = self._registry.find_metadata(subagent_id)
        if metadata is not None and metadata.closed_at_ms is None:
            metadata.closed_at_ms = time.time() * 1000
        await self._manager.remove(subagent_id, reason=reason)
        if metadata is not None:
            self._store_closed_record(metadata, close_reason=reason)
        self._registry.release(subagent_id)
        self.flush()

    async def _acquire_slot(self) -> SpawnReservation:
        try:
            return self._registry.reserve_slot()
        except BaseError:
            if not self._config.enable_lru_eviction:
                raise
            for sid in self._registry.lru_candidates():
                instance = self._manager.find(sid)
                if instance is not None and instance.is_evictable():
                    await self.close(sid, reason="evicted")
                    return self._registry.reserve_slot()
            raise

    def _build_metadata_from_record(
        self,
        record: SubagentRecord,
        *,
        task_id: str | None,
    ) -> SubagentMetadata:
        now_mono = time.monotonic()
        now_ms = time.time() * 1000
        return SubagentMetadata(
            subagent_id=record.subagent_id,
            subagent_type=record.subagent_type,
            display_name=record.display_name,
            role=record.role,
            parent_session_id=self._parent_session_id,
            created_at=now_mono,
            last_used_at=now_mono,
            current_task_id=task_id,
            task_description=record.task_description,
            created_at_ms=record.created_at_ms,
            updated_at_ms=now_ms,
        )

    def _store_closed_record(
        self,
        metadata: SubagentMetadata,
        *,
        close_reason: str,
    ) -> None:
        closed_at_ms = metadata.closed_at_ms or time.time() * 1000
        self._closed_records[metadata.subagent_id] = SubagentRecord(
            subagent_id=metadata.subagent_id,
            subagent_type=metadata.subagent_type,
            display_name=metadata.display_name,
            role=metadata.role,
            task_description=metadata.task_description,
            created_at_ms=metadata.created_at_ms,
            updated_at_ms=closed_at_ms,
            closed_at_ms=closed_at_ms,
            closed_reason=close_reason,
        )
        self._trim_closed_records()

    @staticmethod
    def _metadata_to_record(metadata: SubagentMetadata) -> SubagentRecord:
        return SubagentRecord(
            subagent_id=metadata.subagent_id,
            subagent_type=metadata.subagent_type,
            display_name=metadata.display_name,
            role=metadata.role,
            task_description=metadata.task_description,
            created_at_ms=metadata.created_at_ms,
            updated_at_ms=metadata.updated_at_ms,
            closed_at_ms=metadata.closed_at_ms,
            closed_reason=None,
        )

    @staticmethod
    def _merge_turns(existing: list[SubagentTurn], pending: list[SubagentTurn]) -> list[SubagentTurn]:
        merged = {turn.seq: turn for turn in existing}
        for turn in pending:
            merged[turn.seq] = turn
        return [merged[key] for key in sorted(merged)]

    @staticmethod
    def _merge_activities(
        existing: list[SubagentActivity],
        pending: list[SubagentActivity],
    ) -> list[SubagentActivity]:
        merged = {activity.seq: activity for activity in existing}
        for activity in pending:
            merged[activity.seq] = activity
        return [merged[key] for key in sorted(merged)]

    def _all_turns_flat(self) -> list[SubagentTurn]:
        turns: list[SubagentTurn] = []
        for sid in sorted(self._turns):
            turns.extend(self._turns[sid])
        turns.sort(key=lambda item: (item.subagent_id, item.seq))
        return turns

    def _all_activities_flat(self) -> list[SubagentActivity]:
        activities: list[SubagentActivity] = []
        for sid in sorted(self._activities):
            activities.extend(self._activities[sid])
        activities.sort(key=lambda item: (item.subagent_id, item.seq))
        return activities

    @staticmethod
    def _merge_snapshot_items(
        turns: list[SubagentTurn],
        activities: list[SubagentActivity],
    ) -> list[tuple[str, SubagentTurn | SubagentActivity]]:
        items: list[tuple[str, SubagentTurn | SubagentActivity, float]] = []
        for turn in turns:
            items.append(("turn", turn, turn.created_at_ms))
        for activity in activities:
            items.append(("activity", activity, activity.at_ms))
        items.sort(key=lambda item: (item[1].subagent_id, item[1].seq, item[0], item[2]))
        return [(kind, payload) for kind, payload, _at_ms in items]

    def _resolve_task_id(self, subagent_id: str) -> str:
        instance = self._manager.find(subagent_id)
        if instance is not None and instance.current_task_id:
            return instance.current_task_id
        metadata = self._registry.find_metadata(subagent_id)
        if metadata is not None and metadata.current_task_id:
            return metadata.current_task_id
        return ""

    def _prepare_turn_activity_gate(self, subagent_id: str, task_id: str) -> None:
        self._activity_ready = {key for key in self._activity_ready if key[0] != subagent_id}
        stale_keys = [key for key in self._pending_activities if key[0] == subagent_id]
        for key in stale_keys:
            del self._pending_activities[key]

    async def _emit_running_for_turn(
        self,
        subagent_id: str,
        task_id: str | None = None,
    ) -> None:
        resolved_task_id = task_id or self._resolve_task_id(subagent_id)
        if not resolved_task_id:
            return
        gate_key = (subagent_id, resolved_task_id)
        if gate_key in self._activity_ready:
            return
        await self.emit_status_update(subagent_id)
        projection = self.describe_one(subagent_id)
        if projection is None or projection.get("status") != "running":
            return
        self._mark_activity_ready(subagent_id, resolved_task_id)

    def _mark_activity_ready(self, subagent_id: str, task_id: str) -> None:
        gate_key = (subagent_id, task_id)
        self._activity_ready.add(gate_key)
        pending = self._pending_activities.pop(gate_key, [])
        for activity in pending:
            self._dispatch_activity(activity)

    def _handle_activity(self, activity: SubagentActivity) -> None:
        task_id = activity.task_id or self._resolve_task_id(activity.subagent_id)
        if task_id:
            gate_key = (activity.subagent_id, task_id)
            if gate_key not in self._activity_ready:
                self._pending_activities.setdefault(gate_key, []).append(activity)
                return
        self._dispatch_activity(activity)

    def _dispatch_activity(self, activity: SubagentActivity) -> None:
        if self._activity_emitter is not None:
            self._activity_emitter.offer(activity)
        if not activity.is_persistable():
            return
        sid = activity.subagent_id
        bucket = self._activities.setdefault(
            sid,
            deque(maxlen=MAX_ACTIVITIES_PER_INSTANCE),
        )
        bucket.append(activity)
        self._activity_seq[sid] = max(self._activity_seq.get(sid, 0), activity.seq)

    async def _handle_transcript_message(self, message: SubagentMessage) -> None:
        if self._transcript_emitter is not None:
            await self._transcript_emitter.emit(message)

    def _latest_answered_turn(self, subagent_id: str) -> SubagentTurn | None:
        turns = self._turns.get(subagent_id) or []
        for turn in reversed(turns):
            if turn.answer:
                return turn
        return None

    def _append_turn(
        self,
        subagent_id: str,
        metadata: SubagentMetadata,
        instance: Any | None,
        status: SubagentStatus,
    ) -> None:
        task_id = ""
        if instance is not None and instance.last_task_id:
            task_id = instance.last_task_id
        elif metadata.current_task_id:
            task_id = metadata.current_task_id

        existing = self._turns.get(subagent_id) or []
        if task_id and any(turn.task_id == task_id for turn in existing):
            return

        answer = None
        if status.kind is SubagentStatusKind.COMPLETED and instance is not None:
            answer = instance.last_output

        output_file: str | None = None
        if answer and task_id:
            try:
                workspace_root = resolve_parent_workspace_root(self._parent_agent)
                output_file = write_turn_output(
                    workspace_root,
                    subagent_id,
                    task_id,
                    answer,
                )
            except Exception as exc:
                logger.warning(
                    "Failed to write subagent turn output for %s task %s: %s",
                    subagent_id,
                    task_id,
                    exc,
                )

        seq = self._turn_seq.get(subagent_id, 0) + 1
        self._turn_seq[subagent_id] = seq
        turn = SubagentTurn(
            subagent_id=subagent_id,
            task_id=task_id,
            seq=seq,
            prompt=metadata.task_description,
            answer=answer,
            closed_reason=resolve_turn_outcome(status),
            created_at_ms=time.time() * 1000,
            output_file=output_file,
        )
        self._turns.setdefault(subagent_id, []).append(turn)

    def _trim_closed_records(self) -> None:
        limit = max(self._config.max_subagents * 2, 1)
        if len(self._closed_records) <= limit:
            return
        excess = len(self._closed_records) - limit
        oldest = sorted(
            self._closed_records.values(),
            key=lambda record: record.closed_at_ms or record.updated_at_ms,
        )[:excess]
        for record in oldest:
            self._closed_records.pop(record.subagent_id, None)

    @staticmethod
    def _closed_record_to_payload(
        record: SubagentRecord,
        parent_session_id: str,
    ) -> dict[str, Any]:
        close_reason = record.closed_reason or "parent_ended"
        return build_subagent_updated_payload(
            subagent_id=record.subagent_id,
            subagent_type=record.subagent_type,
            display_name=record.display_name,
            role=record.role,
            parent_session_id=parent_session_id,
            task_description=record.task_description,
            created_at_ms=record.created_at_ms,
            updated_at_ms=record.updated_at_ms,
            closed_at_ms=record.closed_at_ms,
            status=SubagentStatus.closed(close_reason),
            revision=0,
        )

    @staticmethod
    def _metadata_to_payload(
        metadata: SubagentMetadata,
        *,
        status: SubagentStatus,
        revision: int,
        parent_session_id: str,
    ) -> dict[str, Any]:
        return build_subagent_updated_payload(
            subagent_id=metadata.subagent_id,
            subagent_type=metadata.subagent_type,
            display_name=metadata.display_name,
            role=metadata.role,
            parent_session_id=parent_session_id,
            task_description=metadata.task_description,
            created_at_ms=metadata.created_at_ms,
            updated_at_ms=metadata.updated_at_ms,
            closed_at_ms=metadata.closed_at_ms,
            status=status,
            revision=revision,
        )

    def _resolve_spawn_presentation(
        self,
        subagent_type: str,
        display_name: str | None,
        role: str | None,
    ) -> tuple[str, str]:
        spec = self._lookup_subagent_config(subagent_type)
        agent_card = getattr(spec, "agent_card", None) if spec is not None else None
        config_display = getattr(spec, "display_name", None) if spec is not None else None
        config_role = getattr(spec, "role", None) if spec is not None else None
        return resolve_presentation(
            subagent_type=subagent_type,
            display_name=display_name or config_display,
            role=role or config_role,
            agent_card=agent_card,
        )

    def _lookup_subagent_config(self, subagent_type: str) -> Any | None:
        deep_config = getattr(self._parent_agent, "deep_config", None)
        subagents = getattr(deep_config, "subagents", None) or []
        for spec in subagents:
            card = getattr(spec, "agent_card", None)
            if card is None:
                continue
            if getattr(card, "id", None) == subagent_type or getattr(card, "name", None) == subagent_type:
                return spec
        return None

    @staticmethod
    def _truncate_task_description(query: str) -> str:
        text = str(query or "").strip()
        if len(text) <= _TASK_DESCRIPTION_MAX_LEN:
            return text
        return text[:_TASK_DESCRIPTION_MAX_LEN]

    @staticmethod
    def _touch_metadata_timestamps(
        metadata: SubagentMetadata,
        *,
        status: SubagentStatus,
    ) -> None:
        metadata.updated_at_ms = time.time() * 1000
        view = map_status_to_view(status)
        if view["status"] == "idle":
            metadata.closed_at_ms = None
        elif is_instance_closed(status) and metadata.closed_at_ms is None:
            metadata.closed_at_ms = metadata.updated_at_ms

    async def _handle_instance_status_changed(
        self,
        subagent_id: str,
        status: SubagentStatus,
    ) -> None:
        if status.kind in {
            SubagentStatusKind.PENDING_INIT,
            SubagentStatusKind.RUNNING,
        }:
            await self._emit_running_for_turn(subagent_id)
            return
        if not is_turn_finished(status):
            return
        metadata = self._registry.find_metadata(subagent_id)
        instance = self._manager.find(subagent_id)
        if metadata is not None:
            self._touch_metadata_timestamps(metadata, status=status)
            self._append_turn(subagent_id, metadata, instance, status)
        await self.emit_status_update(subagent_id)
        self.flush()

    def _resolve_final(
        self,
        sid: str,
        task: asyncio.Task[SubagentStatus],
    ) -> SubagentStatus:
        if task.cancelled() or task.exception() is not None:
            return self.get_status(sid)
        status = task.result()
        if status.is_final():
            return status
        return self.get_status(sid)
