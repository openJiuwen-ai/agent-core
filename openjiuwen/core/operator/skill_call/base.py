# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Skill experience preview operator for self-evolution."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Dict, Optional

from openjiuwen.agent_evolving.protocols import (
    APPEND_MODE,
    EXPERIENCES_TARGET,
    LOCAL_APPLY_COMPLETED,
    MERGE_MODE,
    PENDING_CHANGE_EFFECT,
)
from openjiuwen.core.operator.base import PreviewableOperator, TunableSpec

if TYPE_CHECKING:
    from openjiuwen.agent_evolving.types import ApplyResult, UpdateValue


class SkillExperienceOperator(PreviewableOperator):
    """Preview-only parameter handle for skill experience records.

    The operator owns only ``updates_generated -> local_apply_completed``.
    Pending approval is owned by ExperienceManager, and persistence is owned by
    EvolutionStore.
    """

    def __init__(
        self,
        skill_name: str,
        on_parameter_updated: Optional[Callable[[str, Any], None]] = None,
    ) -> None:
        self._skill_name = skill_name
        self._on_parameter_updated = on_parameter_updated

    @property
    def operator_id(self) -> str:
        return f"skill_experience_{self._skill_name}"

    def get_tunables(self) -> Dict[str, TunableSpec]:
        return {
            EXPERIENCES_TARGET: TunableSpec(
                name=EXPERIENCES_TARGET,
                kind="skill_experience",
                path="content",
                constraint={"type": "record"},
            )
        }

    def set_parameter(self, target: str, value: Any) -> None:
        """Notify consumers for direct compatibility calls without staging."""
        if target != EXPERIENCES_TARGET or value is None:
            return
        items = value if isinstance(value, list) else [value]
        if self._on_parameter_updated is not None:
            self._on_parameter_updated(target, items)

    def preview_update(self, target: str, update: "UpdateValue") -> "ApplyResult":
        """Return a local preview result for generated experience records."""
        from openjiuwen.agent_evolving.types import ApplyResult

        if target != EXPERIENCES_TARGET:
            return ApplyResult(
                operator_id=self.operator_id,
                target=target,
                applied=False,
                mode=update.mode,
                effect=update.effect,
                value=update.payload,
                change_type=update.change_type,
                errors=[f"unsupported target for SkillExperienceOperator: {target}"],
                metadata=dict(update.metadata),
            )

        if update.effect != PENDING_CHANGE_EFFECT or update.mode not in {APPEND_MODE, MERGE_MODE}:
            return ApplyResult(
                operator_id=self.operator_id,
                target=target,
                applied=False,
                mode=update.mode,
                effect=update.effect,
                value=update.payload,
                change_type=update.change_type,
                errors=[
                    "unsupported update mode/effect for SkillExperienceOperator: "
                    f"{update.mode}/{update.effect}"
                ],
                metadata=dict(update.metadata),
        )

        records = update.payload if isinstance(update.payload, list) else [update.payload]
        return ApplyResult(
            operator_id=self.operator_id,
            target=target,
            applied=bool(records),
            mode=update.mode,
            effect=update.effect,
            value=update.payload,
            records=records,
            change_type=update.change_type,
            lifecycle_stage=LOCAL_APPLY_COMPLETED,
            metadata={
                **dict(update.metadata),
                "skill_name": self._skill_name,
            },
        )

    def get_state(self) -> Dict[str, Any]:
        return {}

    def load_state(self, state: Dict[str, Any]) -> None:
        return None


SkillCallOperator = SkillExperienceOperator

__all__ = ["SkillExperienceOperator", "SkillCallOperator"]
