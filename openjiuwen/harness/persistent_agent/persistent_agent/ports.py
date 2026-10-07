# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Typed host contracts. No backend, fallback, queue, or execution implementation.

Construction validates shape only. Host responses must be validated at the
execution boundary; annotations cannot attest durability or authorization.
"""

from dataclasses import dataclass
from inspect import iscoroutinefunction
from typing import Protocol

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.foundation.store.base_kv_store import BaseKVStore

from .schema.context import GroupMessageQuery, GroupMessagesResult, MaterialQuery, MaterialReadResult
from .schema.events import AgentEvent
from .schema.execution import ExecutionContext
from .schema.port_results import BudgetAdmission, BudgetReport
from .schema.protocol import ModelUsage


class CommitPort(Protocol):
    """Host port for ordered Execution logs that may have ordinary-event gaps."""

    async def record(self, execution: ExecutionContext, event: AgentEvent) -> None:
        """Hand one event to the host for its Execution.

        All calls are awaited. Ordinary events return once enqueued; failures
        are diagnosed without stopping the Execution. Ordinary log gaps or
        persistence retries do not block later critical calls. ``tool.intent``,
        ``execution.paused`` and ``execution.final`` return after this event is
        durable; failures stop the Execution. Final also requires every candidate
        output referenced by its ``output_refs`` to be durably stored, including
        its content and handoff information. Hosts deduplicate by ``event_id``;
        resubmitted candidates retain their original event and output IDs.
        """
        ...

    async def read_execution_log(self, execution: ExecutionContext, execution_id: str) -> tuple[AgentEvent, ...]:
        """Return saved events of the same Session and Request ordered by local_seq.

        Ordinary events may be missing; a nonempty log does not establish
        completeness. Missing tool results do not imply the operation never ran.
        """
        ...


class BudgetPort(Protocol):
    """Host port for model-call admission and usage reporting."""

    async def admit(self, execution: ExecutionContext, model_call_id: str) -> BudgetAdmission:
        """Request host admission for a model call."""
        ...

    async def report(self, execution: ExecutionContext, model_call_id: str, usage: ModelUsage) -> BudgetReport:
        """Report measured or explicitly unknown model usage to the host."""
        ...


class GroupChatPort(Protocol):
    """Host port for the group history of the Session's group; the host resolves the group."""

    async def recent_messages(
        self, execution: ExecutionContext, cursor: str | None = None, limit: int = 20
    ) -> GroupMessagesResult:
        """Return recent authorized group messages, after ``cursor`` when given."""
        ...

    async def search_messages(self, execution: ExecutionContext, query: GroupMessageQuery) -> GroupMessagesResult:
        """Search authorized group history of the Session's group."""
        ...


class KnowledgePort(Protocol):
    """Host port for resolving and reading an authorized knowledge view."""

    async def resolve(self, execution: ExecutionContext, query: MaterialQuery) -> MaterialReadResult:
        """Resolve authorized knowledge sources for a bounded query."""
        ...

    async def read(self, execution: ExecutionContext, material_ref: str, version_ref: str) -> MaterialReadResult:
        """Read one exact authorized knowledge version."""
        ...


@dataclass(frozen=True, kw_only=True)
class PersistentPorts:
    """Collection of host contracts required or optionally enabled at build time."""

    commit: CommitPort | None = None
    budget: BudgetPort | None = None
    # Session state records and window archives; must be persistent and unreachable from Agent tools.
    window_backend: BaseKVStore | None = None
    group_chat: GroupChatPort | None = None
    knowledge: KnowledgePort | None = None

    def validate(self) -> None:
        """Reject missing or malformed dependencies without contacting the host or storage."""
        for name, methods, required in (
            ("commit", ("record", "read_execution_log"), True),
            ("budget", ("admit", "report"), True),
            ("group_chat", ("recent_messages", "search_messages"), False),
            ("knowledge", ("resolve", "read"), False),
        ):
            port = getattr(self, name)
            if port is None:
                if required:
                    raise build_error(
                        StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR,
                        error_msg=f"missing async port {name}.{methods[0]}",
                    )
                continue
            for method in methods:
                if not iscoroutinefunction(getattr(port, method, None)):
                    raise build_error(
                        StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR,
                        error_msg=f"missing async port {name}.{method}",
                    )
        if not isinstance(self.window_backend, BaseKVStore):
            raise build_error(
                StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR, error_msg="window_backend must be a BaseKVStore"
            )
