# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Evolution rail used to collect canonical trajectories for RL training."""

from __future__ import annotations

from typing import Any, Optional

from openjiuwen.agent_evolving.trajectory.model import Trajectory
from openjiuwen.agent_evolving.trajectory.processor import TrajectorySpanProcessor
from openjiuwen.agent_evolving.trajectory.schema import CASE_ID, TRAJECTORY_SOURCE
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, InvokeInputs
from openjiuwen.harness.rails import EvolutionRail


class RLRail(EvolutionRail):
    """Collect one RL trajectory through the shared span processor.

    The rail deliberately does not keep a builder or a trajectory snapshot.
    ``EvolutionRail`` owns the subscription and clean window; extension hooks
    receive detached canonical ``Trajectory`` projections from that window.
    """

    priority = 100

    def __init__(
        self,
        session_id: str = "",
        source: str = "rl_offline",
        case_id: Optional[str] = None,
        *,
        trajectory_span_processor: TrajectorySpanProcessor,
        **kwargs: Any,
    ) -> None:
        """Initialize an RL rail with an explicitly owned processor boundary."""

        # RL consumers need the complete invoke, rather than the default
        # bounded clean window used by general evolution rails.
        kwargs.setdefault("max_trajectory_spans", None)
        super().__init__(trajectory_span_processor=trajectory_span_processor, **kwargs)
        self._session_id = session_id
        self._source = source
        self._case_id = case_id

    def _resolve_trajectory_session_id(
        self,
        ctx: AgentCallbackContext,
        inputs: InvokeInputs,
    ) -> str:
        """Use the configured session when callback context has no session."""

        return self._session_id or super()._resolve_trajectory_session_id(ctx, inputs)

    def _scope_metadata(self, capture: Any) -> dict[str, Any]:
        """Add RL source/case metadata to each immutable clean projection."""

        metadata = super()._scope_metadata(capture)
        metadata[TRAJECTORY_SOURCE] = self._source
        if self._case_id is not None:
            metadata[CASE_ID] = self._case_id
        return metadata

    def get_trajectory(
        self,
        *,
        session_id: str,
        member_id: str | None = None,
        team_id: str | None = None,
    ) -> Trajectory | None:
        """Return a detached RL projection with source/case resource attrs."""

        trajectory = super().get_trajectory(session_id=session_id, member_id=member_id, team_id=team_id)
        if trajectory is None:
            return None
        attributes: dict[str, Any] = {TRAJECTORY_SOURCE: self._source}
        if self._case_id is not None:
            attributes[CASE_ID] = self._case_id
        return trajectory.with_resource_attributes(attributes)

    async def _on_after_invoke(
        self,
        ctx: AgentCallbackContext,
        trajectory: Trajectory | None,
    ) -> None:
        """Consume the clean projection without mutating or retaining it."""

        del ctx, trajectory


__all__ = ["RLRail"]
