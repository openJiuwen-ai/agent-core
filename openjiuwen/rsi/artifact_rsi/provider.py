# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Provider protocols for program and paper artifact optimization.

These protocols are the implementation boundary for downstream optimizer
owners.  They intentionally contain no storage, queue, or HTTP assumptions.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from openjiuwen.rsi.artifact_rsi.request import ArtifactEngineRequest
from openjiuwen.rsi.events import OnEvent
from openjiuwen.rsi.schema import (
    ArtifactRef,
    ArtifactType,
    ArtifactValidationResult,
    EngineReport,
    EngineResult,
    EngineState,
    TreeResponse,
)


@runtime_checkable
class ArtifactProvider(Protocol):
    """Common provider contract used by AgentServer routing.

    Implementations own task execution and persistence under the supplied
    ``run_dir``.  They must not create an event queue or call WebSocket/Web API
    push functions.  The optional callback is awaited after the corresponding
    provider snapshot has been persisted.
    """

    artifact_type: ArtifactType

    def validate_input(self, artifact_path: str | None) -> ArtifactValidationResult:
        """Validate provider-specific input without starting a task.

        Program providers must reject a missing path.  Paper providers may
        accept a missing path when the public request contains an optimization
        instruction.  Implementations should check readability and their own
        required file/layout rules here and return stable error ``code`` and
        human-readable ``message`` pairs.  No task snapshot should be created.
        """
        ...

    async def run(
        self,
        request: ArtifactEngineRequest,
        on_event: OnEvent | None = None,
    ) -> EngineResult:
        """Start a new task and return its current or terminal result.

        A fresh run creates the provider's root state/tree snapshot.  The
        provider persists each state, node, report, and artifact before
        emitting the corresponding event.  It should emit
        ``EventStatus("running")`` first, then node/progress events as
        snapshots become available, and a terminal status last.  The request's
        task ID and run directory must be used consistently for all snapshots.
        """
        ...

    async def pause(
        self,
        task_id: str,
        on_event: OnEvent | None = None,
    ) -> EngineResult:
        """Pause at a safe checkpoint, or report that the scenario is unsupported.

        A supported implementation persists its checkpoint before emitting
        checkpoint-related node/progress events and finally
        ``EventStatus("paused")``.  Paper providers return
        ``SCENARIO_NOT_SUPPORTED`` without changing task state.
        """
        ...

    async def resume(
        self,
        request: ArtifactEngineRequest,
        on_event: OnEvent | None = None,
    ) -> EngineResult:
        """Resume the same task from its checkpoint without creating a new root.

        The supplied request must retain the original ``task_id`` and
        ``run_dir``.  A supported implementation emits
        ``EventStatus("running")`` and continues the existing tree.  Paper
        providers return ``SCENARIO_NOT_SUPPORTED`` without changing task
        state.
        """
        ...

    def read_state(self, task_id: str) -> EngineState:
        """Read the current persisted state without changing task execution.

        This is the recovery/query path used after a service restart or lost
        event.  It must reflect the latest durable snapshot and must not emit
        events or start execution.
        """
        ...

    def read_report(self, task_id: str) -> EngineReport:
        """Read the current/final report and complete artifact index.

        The report's ``artifact_index`` should include every downloadable
        provider artifact known for the task, not only the currently adopted
        node.
        """
        ...

    def get_tree(self, task_id: str) -> TreeResponse:
        """Read the complete persisted tree, including rejected branches.

        Every node must use the same ``RsiTreeNode`` shape as ``EventNode``;
        return the root and all branches rather than only the best chain.
        """
        ...

    def locate_artifact(self, task_id: str, artifact_id: str | None = None) -> ArtifactRef:
        """Locate a task or node artifact for AgentServer download handling.

        ``artifact_id=None`` means the task's final artifact.  The returned
        provider path is not itself a frontend URL; AgentServer is responsible
        for ownership checks and URL projection.  The provider must scope the
        lookup to ``task_id`` so an artifact from another task cannot be
        returned.
        """
        ...

    async def terminate(
        self,
        task_id: str,
        on_event: OnEvent | None = None,
    ) -> EngineResult:
        """Stop a task permanently and return the ``terminated`` state.

        Persist any final checkpoint/report changes before emitting the final
        ``EventStatus("terminated")``.  A terminated task must not be
        resumable.
        """
        ...


__all__ = ["ArtifactProvider"]
