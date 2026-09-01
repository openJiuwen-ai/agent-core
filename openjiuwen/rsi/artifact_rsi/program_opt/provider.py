# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Program artifact optimization provider contract.

Implement this protocol in the ScienceDiscovery integration.  The provider
owns program optimization and durable task snapshots; AgentServer owns
routing, request assembly, event queuing, and Web transport.
"""

from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

from openjiuwen.rsi.artifact_rsi.provider import ArtifactProvider
from openjiuwen.rsi.artifact_rsi.request import ArtifactEngineRequest
from openjiuwen.rsi.events import OnEvent
from openjiuwen.rsi.schema import (
    ArtifactRef,
    ArtifactValidationResult,
    EngineReport,
    EngineResult,
    EngineState,
    TreeResponse,
)


@runtime_checkable
class ProgramArtifactProvider(ArtifactProvider, Protocol):
    """Interface implemented by a ``program`` artifact optimizer.

    The methods below are intentionally repeated from the common protocol so
    the complete implementation surface is visible in the program provider's
    own module.  The ``artifact_type`` literal is enforced by static type
    checkers; AgentServer must still route explicitly at runtime.
    """

    artifact_type: Literal["program"]

    def validate_input(self, artifact_path: str | None) -> ArtifactValidationResult:
        """Validate the program path and required program layout.

        Returns ``valid=True`` only when the program input is readable and
        satisfies the provider's minimum requirements.  This method must not
        start optimization or create task snapshots.
        """
        ...

    async def run(
        self,
        request: ArtifactEngineRequest,
        on_event: OnEvent | None = None,
    ) -> EngineResult:
        """Start a new program optimization task.

        Persist state, nodes, reports, and artifacts before invoking
        ``on_event``.  Emit running status first and a terminal status last.
        """
        ...

    async def pause(
        self,
        task_id: str,
        on_event: OnEvent | None = None,
    ) -> EngineResult:
        """Pause at a durable program checkpoint, or return ``NOT_IMPLEMENTED``."""
        ...

    async def resume(
        self,
        request: ArtifactEngineRequest,
        on_event: OnEvent | None = None,
    ) -> EngineResult:
        """Resume the original program task without creating a new root node."""
        ...

    def read_state(self, task_id: str) -> EngineState:
        """Read the latest durable program state without side effects."""
        ...

    def read_report(self, task_id: str) -> EngineReport:
        """Read the current/final report and all program artifact references."""
        ...

    def get_tree(self, task_id: str) -> TreeResponse:
        """Read the complete program tree, including rejected branches."""
        ...

    def locate_artifact(self, task_id: str, artifact_id: str | None = None) -> ArtifactRef:
        """Locate the task's final or requested program artifact.

        ``artifact_id=None`` selects the task's final artifact.  The returned
        path is provider-local; AgentServer performs ownership checks and URL
        projection.
        """
        ...

    async def terminate(
        self,
        task_id: str,
        on_event: OnEvent | None = None,
    ) -> EngineResult:
        """Terminate the program task permanently and return ``terminated``."""
        ...


__all__ = ["ProgramArtifactProvider"]
