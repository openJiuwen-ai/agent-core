# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Skill document operator for full markdown document optimization.

Unlike SkillExperienceOperator (stateless preview proxy),
this operator holds the full skill markdown document and
supports checkpoint/rollback via get_state/load_state.

The skill document may contain <!-- SLOW_UPDATE_START/END --> markers.
Step-level edits (apply_patch) skip edits targeting the protected region.
Slow_update at epoch boundaries force-injects new guidance into the markers.
The agent sees the full document (including markers + content) during rollout.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Dict, Optional

from openjiuwen.agent_evolving.protocols import SKILL_CONTENT_TARGET
from openjiuwen.core.operator.base import PreviewableOperator, TunableSpec

if TYPE_CHECKING:
    from openjiuwen.agent_evolving.types import ApplyResult, UpdateValue


class SkillDocumentOperator(PreviewableOperator):
    """Operator for skill document content (full markdown).

    Single tunable: ``skill_content`` — the entire skill document string.
    Supports checkpoint/rollback via get_state/load_state.
    load_state() triggers on_parameter_updated to keep consumers in sync.
    """

    def __init__(
        self,
        skill_name: str,
        initial_content: str = "",
        on_parameter_updated: Optional[Callable[[str, Any], None]] = None,
    ) -> None:
        self._skill_name = skill_name
        self._skill_content = initial_content
        self._on_parameter_updated = on_parameter_updated

    @property
    def operator_id(self) -> str:
        return f"skill_document_{self._skill_name}"

    def get_tunables(self) -> Dict[str, TunableSpec]:
        return {
            SKILL_CONTENT_TARGET: TunableSpec(
                name=SKILL_CONTENT_TARGET,
                kind="text",
                path="content",
                constraint={"type": "markdown"},
            ),
        }

    def set_parameter(self, target: str, value: Any) -> None:
        if target != SKILL_CONTENT_TARGET:
            return
        self._skill_content = str(value) if value is not None else ""
        if self._on_parameter_updated is not None:
            self._on_parameter_updated(target, self._skill_content)

    def preview_update(self, target: str, update: "UpdateValue") -> "ApplyResult":
        from openjiuwen.agent_evolving.types import ApplyResult

        if target != SKILL_CONTENT_TARGET:
            return ApplyResult(
                operator_id=self.operator_id,
                target=target,
                applied=False,
                mode=update.mode,
                effect=update.effect,
                value=update.payload,
                errors=[f"unsupported target: {target}"],
                metadata=dict(update.metadata),
            )

        return ApplyResult(
            operator_id=self.operator_id,
            target=target,
            applied=True,
            mode=update.mode,
            effect=update.effect,
            value=update.payload,
            change_type=update.change_type,
            metadata=dict(update.metadata),
        )

    def get_state(self) -> Dict[str, Any]:
        return {"skill_content": self._skill_content}

    def load_state(self, state: Dict[str, Any]) -> None:
        self._skill_content = state.get("skill_content", "")
        if self._on_parameter_updated is not None:
            self._on_parameter_updated(SKILL_CONTENT_TARGET, self._skill_content)


__all__ = ["SkillDocumentOperator"]
