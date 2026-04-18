# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""SKILL.md content parameter handle for self-evolution."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

from openjiuwen.core.common.logging import logger
from openjiuwen.core.operator.base import Operator, TunableSpec

if TYPE_CHECKING:
    from openjiuwen.agent_evolving.checkpointing import EvolutionStore
    from openjiuwen.agent_evolving.checkpointing.types import EvolutionRecord, EvolutionTarget


class SkillCallOperator(Operator):
    """SKILL.md content parameter handle for self-evolution.

    Manages skill experience records in memory until user approves:
    - set_parameter("experiences", record_or_list): sync, enqueue into _staged_records
    - flush_to_store(store): async, write staged records to EvolutionStore one by one
    - discard_staged(): sync, drop all staged records (user rejection path)

    Does NOT read or write files directly. All persistent IO via EvolutionStore.
    """

    def __init__(
        self,
        skill_name: str,
        on_parameter_updated: Optional[Callable[[str, Any], None]] = None,
    ) -> None:
        self._skill_name = skill_name
        self._on_parameter_updated = on_parameter_updated
        self._staged_records: List[Any] = []    # List[EvolutionRecord]; head-first write queue
        self._flushed_records: List[Any] = []   # records successfully written to store
        self._cached_state: Dict[str, Any] = {}

    @property
    def operator_id(self) -> str:
        return f"skill_call_{self._skill_name}"

    def get_tunables(self) -> Dict[str, TunableSpec]:
        return {
            "experiences": TunableSpec(
                name="experiences",
                kind="skill_experience",
                path="content",
                constraint={"type": "record"},
            )
        }

    def set_parameter(self, target: str, value: Any) -> None:
        """Sync: enqueue EvolutionRecord(s) into _staged_records.

        value may be a single EvolutionRecord or a List[EvolutionRecord].
        Records are appended to the tail of the staging queue in order.
        """
        if target != "experiences" or value is None:
            return
        items = value if isinstance(value, list) else [value]
        self._staged_records.extend(items)
        if self._on_parameter_updated is not None:
            self._on_parameter_updated(target, items)

    async def flush_to_store(self, store: "EvolutionStore") -> int:
        """Async: write staged records to EvolutionStore one by one.

        Each record is popped from _staged_records only after its write
        succeeds. On failure the remaining tail is preserved; the next
        call retries from the first unwritten record, so no record is
        written twice.

        Returns count flushed in this call.
        """
        flushed = 0
        while self._staged_records:
            record = self._staged_records[0]
            try:
                await store.append_record(self._skill_name, record)
                self._staged_records.pop(0)         # dequeue only on success
                self._flushed_records.append(record)
                flushed += 1
            except Exception as exc:
                logger.warning(
                    "[SkillCallOperator] flush failed at record %s: %s; "
                    "%d record(s) remain in staging buffer",
                    getattr(record, "id", repr(record)), exc, len(self._staged_records),
                )
                break   # retain remaining records for next retry
        return flushed

    def discard_staged(self) -> int:
        """Discard all in-memory staged records on user rejection.

        Only clears the in-memory buffer; nothing is written to or deleted
        from EvolutionStore (records were never persisted before approval).
        Returns count discarded.
        """
        count = len(self._staged_records)
        self._staged_records.clear()
        return count

    def take_snapshot(self) -> List[Any]:
        """Atomically snapshot the current staged records and clear the queue.

        Returns a stable copy of whatever was staged at the time of the call.
        Subsequent records will start a fresh queue independent of this snapshot,
        so concurrent approval requests for the same skill operate on disjoint
        batches.
        """
        snapshot = list(self._staged_records)
        self._staged_records.clear()
        return snapshot

    async def refresh_state(self, store: "EvolutionStore") -> None:
        """Load skill content + existing records from store into _cached_state."""
        from openjiuwen.agent_evolving.checkpointing.types import EvolutionTarget
        skill_content = await store.read_skill_content(self._skill_name)
        desc_records = await store.get_pending_records(self._skill_name, EvolutionTarget.DESCRIPTION)
        body_records = await store.get_pending_records(self._skill_name, EvolutionTarget.BODY)
        # Preserve messages if they were set externally (e.g., from conversation context)
        existing_messages = self._cached_state.get("messages", [])
        self._cached_state = {
            "skill_content": skill_content,
            "desc_records": desc_records,
            "body_records": body_records,
            "messages": existing_messages,
        }

    def get_state(self) -> Dict[str, Any]:
        return dict(self._cached_state)

    def load_state(self, state: Dict[str, Any]) -> None:
        self._cached_state = dict(state)
