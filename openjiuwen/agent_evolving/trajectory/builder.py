# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""TrajectoryBuilder: unified trajectory assembler.

Responsibilities:
- Step accumulation (steps ordered by insertion)
- Cost accumulation (input_tokens/output_tokens)
- Final Trajectory assembly

Explicitly NOT responsible for:
- Format conversion (done by caller)
- Span parsing (done by Extractor)
- Persistence (done by Store)
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from openjiuwen.agent_evolving.trajectory.types import (
    LLMCallDetail,
    Trajectory,
    TrajectoryStep,
)


def _generate_uuid() -> str:
    """Generate a unique execution ID."""
    return str(uuid.uuid4())


class TrajectoryBuilder:
    """Trajectory assembler for both online and offline paths.

    Usage:
        builder = TrajectoryBuilder(
            session_id="conv_123",
            source="online",
        )
        for step_data in steps:
            builder.record_step(TrajectoryStep(...))
        trajectory = builder.build()
    """

    def __init__(
        self,
        session_id: str,
        source: str,  # "online" | "offline"
        case_id: Optional[str] = None,
        member_id: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Initialize builder.

        Args:
            session_id: Session identifier (conversation_id for online,
                case_id for offline)
            source: Source type - "online" or "offline"
            case_id: Optional case ID (for offline scenarios)
            member_id: Optional team member identifier for trajectory aggregation.
            meta: Optional extension metadata.
        """
        self.session_id = session_id
        self.source = source
        self.case_id = case_id
        self.member_id = member_id
        self.meta: Dict[str, Any] = dict(meta or {})
        if member_id:
            self.meta.setdefault("member_id", member_id)
        self.steps: List[TrajectoryStep] = []
        self.cost: Dict[str, int] = {"input_tokens": 0, "output_tokens": 0}
        self._start_time_ms: Optional[int] = None

    def record_step(self, step: TrajectoryStep) -> None:
        """Record a step and accumulate cost.

        Args:
            step: Step to record
        """
        self.steps.append(step)

        # Accumulate token usage from LLM steps
        if step.kind == "llm" and step.detail:
            if isinstance(step.detail, LLMCallDetail) and step.detail.usage:
                self.cost["input_tokens"] += step.detail.usage.get("prompt_tokens", 0)
                self.cost["output_tokens"] += step.detail.usage.get("completion_tokens", 0)

        # Record start time
        if self._start_time_ms is None and step.start_time_ms:
            self._start_time_ms = step.start_time_ms

    def build(self) -> Trajectory:
        """Assemble Trajectory.

        Returns:
            Assembled Trajectory with all steps and metadata
        """
        meta: dict[str, Any] = {}
        if self.member_id:
            meta["member_id"] = self.member_id
        return Trajectory(
            execution_id=_generate_uuid(),
            session_id=self.session_id,
            source=self.source,
            case_id=self.case_id,
            steps=self.steps,
            cost=self.cost if self.cost["input_tokens"] > 0 else None,
            meta=dict(self.meta),
        )
