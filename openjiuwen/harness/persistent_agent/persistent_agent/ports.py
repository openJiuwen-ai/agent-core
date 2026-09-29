# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Typed host contracts. No backend, fallback, commit, or execution implementation.

Construction validates method shape only. Host responses must be validated and
matched at the execution boundary; annotations cannot attest reliability.
"""

from dataclasses import dataclass
from inspect import iscoroutinefunction
from typing import Protocol

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error

from .schema.context import (
    ContextHeadResult,
    HeadAdvance,
    MaterialQuery,
    MaterialReadResult,
    WindowMutationResult,
    WindowReadResult,
    WindowSwitch,
    WindowWrite,
)
from .schema.events import AgentEvent
from .schema.execution import ExecutionContext
from .schema.port_results import AuthorityResult, BudgetAdmission, BudgetReport, CommitResult, ControlResult
from .schema.protocol import ModelUsage


class ExecutionAuthority(Protocol):
    """Host port for checking the current execution's authority."""

    async def check(self, execution: ExecutionContext) -> AuthorityResult:
        """Return the host's authority result for this execution."""
        ...


class CommitPort(Protocol):
    """Host port for submitting and querying execution event proposals."""

    async def submit(self, execution: ExecutionContext, event: AgentEvent) -> CommitResult:
        """Submit one event proposal and return its commit status."""
        ...

    async def query(self, execution: ExecutionContext, event_id: str) -> CommitResult:
        """Query the host for the durable outcome of one event proposal."""
        ...


class ControlPort(Protocol):
    """Host port for polling controls addressed to one execution."""

    async def poll(self, execution: ExecutionContext) -> ControlResult:
        """Poll controls addressed to this invocation."""
        ...


class BudgetPort(Protocol):
    """Host port for model-call admission and usage reporting."""

    async def admit(self, execution: ExecutionContext, model_call_id: str) -> BudgetAdmission:
        """Request host admission for a model call."""
        ...

    async def report(self, execution: ExecutionContext, model_call_id: str, usage: ModelUsage) -> BudgetReport:
        """Report measured or explicitly unknown model usage to the host."""
        ...


class ContextStore(Protocol):
    """Host port for reading and conditionally updating private windows."""

    async def read_head(self, execution: ExecutionContext) -> ContextHeadResult:
        """Read the active-window pointer for this invocation."""
        ...

    async def read_window(self, execution: ExecutionContext, window_ref: str, window_revision: int) -> WindowReadResult:
        """Read one exact private-window revision."""
        ...

    async def save_window(self, execution: ExecutionContext, write: WindowWrite) -> WindowMutationResult:
        """Persist an immutable private-window version."""
        ...

    async def advance_head(self, execution: ExecutionContext, advance: HeadAdvance) -> WindowMutationResult:
        """Conditionally advance the active-window pointer."""
        ...

    async def create_and_activate_window(
        self, execution: ExecutionContext, switch: WindowSwitch
    ) -> WindowMutationResult:
        """Create and activate a new private window as one host operation."""
        ...

    async def query_operation(self, execution: ExecutionContext, operation_id: str) -> WindowMutationResult:
        """Query the host for the outcome of a window mutation."""
        ...


class HistoryPort(Protocol):
    """Host port for authorized search and reads of archived history."""

    async def search(self, execution: ExecutionContext, query: MaterialQuery) -> MaterialReadResult:
        """Search authorized archived history for matching source references."""
        ...

    async def read(self, execution: ExecutionContext, material_ref: str, version_ref: str) -> MaterialReadResult:
        """Read one exact archived-history version."""
        ...


class KnowledgePort(Protocol):
    """Host port for resolving and reading an authorized knowledge view."""

    async def resolve(self, execution: ExecutionContext, query: MaterialQuery) -> MaterialReadResult:
        """Resolve authorized knowledge sources for a bounded query."""
        ...

    async def read(self, execution: ExecutionContext, material_ref: str, version_ref: str) -> MaterialReadResult:
        """Read one exact authorized knowledge version."""
        ...


class GroupMaterialPort(Protocol):
    """Host port for authorized search and reads of shared group material."""

    async def search(self, execution: ExecutionContext, query: MaterialQuery) -> MaterialReadResult:
        """Search material shared in groups authorized for this invocation."""
        ...

    async def read(self, execution: ExecutionContext, material_ref: str, version_ref: str) -> MaterialReadResult:
        """Read one exact authorized group-material version."""
        ...


@dataclass(frozen=True, kw_only=True)
class PersistentPorts:
    """Collection of host contracts required or optionally enabled at build time."""

    authority: ExecutionAuthority | None = None
    commit: CommitPort | None = None
    control: ControlPort | None = None
    budget: BudgetPort | None = None
    context_store: ContextStore | None = None
    history: HistoryPort | None = None
    knowledge: KnowledgePort | None = None
    group_material: GroupMaterialPort | None = None

    def validate(self) -> None:
        """Reject missing/non-async port methods without contacting the host."""
        for name, methods, required in (
            ("authority", ("check",), True),
            ("commit", ("submit", "query"), True),
            ("control", ("poll",), True),
            ("budget", ("admit", "report"), True),
            (
                "context_store",
                (
                    "read_head",
                    "read_window",
                    "save_window",
                    "advance_head",
                    "create_and_activate_window",
                    "query_operation",
                ),
                False,
            ),
            ("history", ("search", "read"), False),
            ("knowledge", ("resolve", "read"), False),
            ("group_material", ("search", "read"), False),
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
