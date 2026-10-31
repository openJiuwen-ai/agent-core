# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Typed host contracts. No backend, fallback, queue, or execution implementation.

Construction validates shape only. Host responses must be validated at the
execution boundary; annotations cannot attest durability or authorization.
"""

from dataclasses import dataclass
from inspect import iscoroutinefunction
from typing import Any, Protocol

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.foundation.store.base_kv_store import BaseKVStore

from .schema.context import (
    GroupMessageQuery,
    GroupMessageSearchResult,
    GroupMessagesResult,
    MaterialQuery,
    MaterialReadResult,
)
from .schema.events import AgentEvent
from .schema.execution import ExecutionContext
from .schema.port_results import BudgetAdmission, BudgetReport, ExecutionLink
from .schema.protocol import ModelUsage
from .workspace.access import WorkspaceAccessPort, WorkspaceWriteAccessPort


class CommitPort(Protocol):
    """Host port for ordered Execution logs that may have ordinary-event gaps."""

    async def record(self, execution: ExecutionContext, event: AgentEvent) -> None:
        """Hand one event to the host for its Execution.

        All calls are awaited. Ordinary events return once enqueued; failures
        are diagnosed without stopping the Execution. Ordinary log gaps or
        persistence retries do not block later critical calls. ``tool.intent``,
        ``execution.paused`` and ``execution.final`` return after this event is
        durable; failures stop the Execution. Paused and final also require every
        candidate output referenced by their ``output_refs`` to be durably stored,
        including its content and handoff information; a file candidate is read at
        its logical path and checked against its size and digest. Hosts deduplicate
        by ``event_id``; resubmitted candidates retain their original event and
        output IDs.
        """
        ...

    async def read_execution_log(self, execution: ExecutionContext, execution_id: str) -> tuple[AgentEvent, ...]:
        """Return saved events of the same Session and Request ordered by local_seq.

        Ordinary events may be missing; a nonempty log does not establish
        completeness. Missing tool results do not imply the operation never ran.
        """
        ...


class ExecutionLinkPort(Protocol):
    """Optional on the commit port; only continuation (PA-18) needs it, so construction does not require it."""

    async def read_execution_link(self, execution: ExecutionContext, execution_id: str) -> ExecutionLink:
        """Return the link the host saved before dispatching an Execution of the same Session and Request.

        Raises when the link cannot be read; a missing link is never reported as the first Execution.
        """


class BudgetPort(Protocol):
    """Host port for model-call admission and usage reporting."""

    async def admit(self, execution: ExecutionContext, model_call_id: str) -> BudgetAdmission:
        """Request host admission for a model call."""
        ...

    async def report(self, execution: ExecutionContext, model_call_id: str, usage: ModelUsage) -> BudgetReport:
        """Report measured or explicitly unknown model usage to the host."""
        ...


class GroupContentApprovalRequired(Exception):
    """Host-private candidates need owner approval before sharing to this destination.

    Never attach candidates or previews. ``approval_id`` is an opaque correlation
    identifier, not a permission token; the host must reauthorize on resume.
    """

    def __init__(self, message: str = "跨群内容分享需要所有者批准。", *, approval_id: str | None = None) -> None:
        super().__init__(message)
        self.approval_id = approval_id


class GroupMessageApprovalRequired(GroupContentApprovalRequired):
    """Compatibility signal for hosts that gate message-only searches."""

    def __init__(self, message: str = "跨群消息分享需要所有者批准。", *, approval_id: str | None = None) -> None:
        super().__init__(message, approval_id=approval_id)


class GroupChatPort(Protocol):
    """Current-group background and Agent-authorized cross-group search; the host resolves authorization."""

    async def recent_messages(
        self, execution: ExecutionContext, cursor: str | None = None, limit: int = 20
    ) -> GroupMessagesResult:
        """Return recent authorized group messages, after ``cursor`` when given.

        A reader backfilling a queued Request passes its trigger message as ``cursor`` to read everything
        after the trigger. ``scanned_through`` in the result is the position the host confirms it scanned.
        With a cursor, pages are in forward scan order; next_cursor continues that scan. Each call
        rechecks current authorization. Unknown positions or incomplete coverage are not empty results.
        """
        ...

    async def search_messages(self, execution: ExecutionContext, query: GroupMessageQuery) -> GroupMessageSearchResult:
        """Search retained history of groups currently authorized for execution.agent_id.

        Reauthorize every page; filters only narrow that scope, not grant access. Do not require the
        questioner to belong to the target group. Resolve exact names inside that scope, returning
        unavailable with safe disambiguation guidance for ambiguous names. Match literal substrings
        case-insensitively; order by receive time descending, then group_id and message_id. No time
        filter means all retained history, not proof of history before collection began. Bind cursors
        to the Agent, query and authorization scope; never trust a model-supplied cursor as permission.
        Reasons must be safe for the model; unavailable targets must not disclose their existence.
        Raise GroupContentApprovalRequired when messages or artifacts are held privately by the host
        pending owner approval. Return no candidate content or preview before sharing is authorized.
        A resumed call repeats the original query; recheck authorization and the host approval.
        """
        ...

    async def search_content(self, execution: ExecutionContext, query: GroupMessageQuery) -> GroupMessageSearchResult:
        """Search selected messages, final Session files and registered online-document links.

        ``content_types`` selects messages, files and online documents; omitted values search all types.
        ``query`` and ``keywords`` are case-insensitive literal OR terms. File results are restricted to
        final outputs and must be staged in the current Session only after approval. Every cross-group
        source is approved separately for this exact destination; never return candidates before approval.
        """
        ...


class KnowledgePort(Protocol):
    """Host port for resolving and reading an authorized knowledge view."""

    async def resolve(self, execution: ExecutionContext, query: MaterialQuery) -> MaterialReadResult:
        """Resolve authorized knowledge sources for a bounded query."""
        ...

    async def read(self, execution: ExecutionContext, material_ref: str, version_ref: str) -> MaterialReadResult:
        """Read one exact authorized knowledge version."""
        ...


class PermissionRailFactoryPort(Protocol):
    """Build one host-owned Smart Approval rail from a frozen employee policy."""

    def build(
        self,
        *,
        execution: ExecutionContext,
        permissions: dict[str, Any],
        model: Any,
        model_name: str | None,
        workspace_root: str | None,
        sys_operation: Any,
        tool_names: tuple[str, ...],
    ) -> Any:
        """Return a fresh rail for this invocation; must not mutate global policy or share review state."""
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
    workspace_access: WorkspaceAccessPort | None = None
    workspace_write_access: WorkspaceWriteAccessPort | None = None
    permission_rail_factory: PermissionRailFactoryPort | None = None

    def validate(self) -> None:
        """Reject missing or malformed dependencies without contacting the host or storage."""
        for name, methods, required in (
            ("commit", ("record", "read_execution_log"), True),
            ("budget", ("admit", "report"), True),
            ("group_chat", ("recent_messages", "search_messages"), False),
            ("knowledge", ("resolve", "read"), False),
            ("workspace_access", ("resolve_read_scope",), False),
            ("workspace_write_access", ("resolve_write_scope",), False),
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
        if self.permission_rail_factory is not None and not callable(
            getattr(self.permission_rail_factory, "build", None)
        ):
            raise build_error(
                StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR,
                error_msg="missing permission_rail_factory.build",
            )
        if not isinstance(self.window_backend, BaseKVStore):
            raise build_error(
                StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR, error_msg="window_backend must be a BaseKVStore"
            )
