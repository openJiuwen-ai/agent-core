# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""PermissionInterruptRail must use public engine.llm / engine.model_name."""
from __future__ import annotations

from typing import Any

from openjiuwen.harness.rails.security.tool_security_rail import PermissionInterruptRail
from openjiuwen.harness.security.core import PermissionEngine
from openjiuwen.harness.security.models import PermissionLevel, PermissionResult


class _DuckTypedEngine:
    """Mirrors jiuwenclaw adapter: public llm/model_name, no private _llm."""

    def __init__(self) -> None:
        self.config: dict[str, Any] = {"enabled": True}
        self._bound_llm = object()
        self._bound_model = "duck-model"

    @property
    def enabled(self) -> bool:
        return True

    @property
    def llm(self) -> Any:
        return self._bound_llm

    @property
    def model_name(self) -> str | None:
        return self._bound_model

    def set_permission_checks_active(self, fn: Any) -> None:
        return None

    async def check_permission(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
    ) -> PermissionResult:
        return PermissionResult(permission=PermissionLevel.ALLOW)


def test_permission_engine_exposes_public_llm_and_model_name() -> None:
    llm = object()
    engine = PermissionEngine(llm=llm, model_name="m1")
    assert engine.llm is llm
    assert engine.model_name == "m1"
    engine.update_llm(None, "m2")
    assert engine.llm is None
    assert engine.model_name == "m2"


def test_permission_interrupt_rail_accepts_duck_typed_engine_without_private_llm() -> None:
    engine = _DuckTypedEngine()
    assert not hasattr(engine, "_llm")
    rail = PermissionInterruptRail(config={"enabled": True}, engine=engine)
    assert rail._engine is engine
    assert rail._engine.llm is engine.llm
    assert rail._engine.model_name == "duck-model"
