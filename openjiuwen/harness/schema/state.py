# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""DeepAgent runtime-state data types."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from openjiuwen.harness.schema.task import TaskPlan


_SESSION_STATE_KEY = "deepagent"
_SESSION_RUNTIME_ATTR = "_deepagent_runtime_state"


@dataclass
class PlanModeState:
    """Plan mode session-scoped state.

    Attributes:
        mode: Current agent mode — ``"normal"`` or ``"plan"``.
        pre_plan_mode: Mode that was active before.
        plan_slug: Short identifier for the active plan file
            (e.g. ``"gleaming-brewing-phoenix"``).  The absolute path is
            derived at runtime via ``resolve_plan_file_path()``.
    """

    mode: str = "normal"
    pre_plan_mode: str = "normal"
    plan_slug: str | None = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a JSON-friendly dict.

        Returns:
            Dict with mode, pre_plan_mode, and plan_slug fields.
        """
        return {
            "mode": self.mode,
            "pre_plan_mode": self.pre_plan_mode,
            "plan_slug": self.plan_slug,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any] | None) -> "PlanModeState":
        """Restore from a serialized dict.

        Args:
            data: Dict previously produced by ``to_dict()``.  ``None``
                is treated as an empty snapshot (returns defaults).

        Returns:
            Reconstructed PlanModeState.
        """
        if not data:
            return cls()
        return cls(
            mode=data.get("mode", "normal"),
            pre_plan_mode=data.get("pre_plan_mode"),
            plan_slug=data.get("plan_slug"),
        )


@dataclass
class DeepAgentState:
    """Per-invoke mutable state.

    The object lives on ``ctx.session`` while an
    invoke/stream request is running.  A serializable
    subset can be checkpointed to session state.
    """

    iteration: int = 0
    task_plan: Optional[TaskPlan] = None
    stop_condition_state: Optional[Dict[str, Any]] = None
    pending_follow_ups: List[str] = field(
        default_factory=list
    )
    plan_mode: PlanModeState = field(default_factory=PlanModeState)

    def to_session_dict(self) -> Dict[str, Any]:
        """Convert to a JSON-friendly dict."""
        return {
            "iteration": int(self.iteration),
            "task_plan": (
                self.task_plan.to_dict()
                if self.task_plan is not None
                else None
            ),
            "stop_condition_state": self.stop_condition_state,
            "pending_follow_ups": list(
                self.pending_follow_ups
            ),
            "plan_mode": self.plan_mode.to_dict(),
        }

    @classmethod
    def from_session_dict(
        cls,
        data: Optional[Dict[str, Any]],
    ) -> "DeepAgentState":
        """Build state from session snapshot."""
        if not data:
            return cls()
        raw_plan = data.get("task_plan")
        task_plan = (
            TaskPlan.from_dict(raw_plan)
            if isinstance(raw_plan, dict)
            else None
        )
        return cls(
            iteration=int(
                data.get("iteration", 0) or 0
            ),
            task_plan=task_plan,
            stop_condition_state=data.get(
                "stop_condition_state"
            ),
            pending_follow_ups=list(
                data.get("pending_follow_ups") or []
            ),
            plan_mode=PlanModeState.from_dict(
                data.get("plan_mode")
            ),
        )
