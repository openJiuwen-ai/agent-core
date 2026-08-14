# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Preview operator base for the context-evolve dimension.

Subclasses declare per-target ``update_policies``; the base validates the
full (target, mode, effect) combination against the declared pair set and
passes payloads through as preview records. Real state lives in the
algorithm's store; the rail commits after ``execute_updates``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Dict, FrozenSet, Mapping, Optional, Tuple

from openjiuwen.agent_evolving.protocols import LOCAL_APPLY_COMPLETED
from openjiuwen.core.operator.base import PreviewableOperator, TunableSpec

if TYPE_CHECKING:
    from openjiuwen.agent_evolving.types import ApplyResult, UpdateValue


@dataclass(frozen=True)
class UpdatePolicy:
    """Allowed (mode, effect) pairs for one target.

    A pair set (instead of separate mode/effect sets) rules out illegal
    cartesian combinations inside a single target.
    """

    allowed: FrozenSet[Tuple[str, str]]
    kind: Optional[str] = None
    path: Optional[str] = None
    constraint: Optional[Any] = None


class ContextEvolveOperator(PreviewableOperator):
    """Stateless preview handle; tunables derive from ``update_policies``."""

    update_policies: Mapping[str, UpdatePolicy] = {}
    operator_id_prefix: str = "context_evolve_"

    def __init__(
        self,
        scope_id: str,
        on_parameter_updated: Optional[Callable[[str, Any], None]] = None,
    ) -> None:
        """Create a preview operator for one evolution scope.

        Args:
            scope_id: Stable identity of the state scope being evolved.
            on_parameter_updated: Optional callback for direct compatibility
                calls to :meth:`set_parameter`.

        """
        self._scope_id = scope_id
        self._on_parameter_updated = on_parameter_updated

    @property
    def scope_id(self) -> str:
        """Return the state scope owned by this operator."""
        return self._scope_id

    @property
    def operator_id(self) -> str:
        """Return the stable operator identifier for this scope."""
        return f"{self.operator_id_prefix}{self._scope_id}"

    def get_tunables(self) -> Dict[str, TunableSpec]:
        """Build tunable specifications from the declared update policies."""
        return {
            target: TunableSpec(
                name=target,
                kind=policy.kind or target,
                path=policy.path or target,
                constraint=policy.constraint if policy.constraint is not None else {"type": "delta"},
            )
            for target, policy in self.update_policies.items()
        }

    def set_parameter(self, target: str, value: Any) -> None:
        """Notify consumers for direct compatibility calls without staging."""
        if target not in self.update_policies or value is None:
            return
        if self._on_parameter_updated is not None:
            self._on_parameter_updated(target, value)

    def preview_update(self, target: str, update: "UpdateValue") -> "ApplyResult":
        """Validate against the target's policy and pass the payload through."""
        from openjiuwen.agent_evolving.types import ApplyResult

        policy = self.update_policies.get(target)
        if policy is None:
            return self._rejected(target, update, f"unsupported target for {type(self).__name__}: {target}")
        if (update.mode, update.effect) not in policy.allowed:
            return self._rejected(
                target,
                update,
                f"unsupported mode/effect for target {target}: {update.mode}/{update.effect}",
            )

        records = [update.payload] if update.payload is not None else []
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
            metadata={**dict(update.metadata), "scope_id": self._scope_id},
        )

    def _rejected(self, target: str, update: "UpdateValue", reason: str) -> "ApplyResult":
        from openjiuwen.agent_evolving.types import ApplyResult

        return ApplyResult(
            operator_id=self.operator_id,
            target=target,
            applied=False,
            mode=update.mode,
            effect=update.effect,
            value=update.payload,
            change_type=update.change_type,
            errors=[reason],
            metadata=dict(update.metadata),
        )

    def get_state(self) -> Dict[str, Any]:
        """Return empty state because persistence belongs to the store."""
        return {}

    def load_state(self, state: Dict[str, Any]) -> None:
        """Ignore operator state because the store owns authoritative data."""
        return None


__all__ = ["ContextEvolveOperator", "UpdatePolicy"]
