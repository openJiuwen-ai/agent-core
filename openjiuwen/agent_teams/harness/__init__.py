# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Agent-teams harness: TeamHarness and NativeHarness."""
from __future__ import annotations

from typing import Any

from openjiuwen.agent_teams.harness.team_harness import TeamHarness
from openjiuwen.agent_teams.harness.state import HarnessState
from openjiuwen.agent_teams.harness.protocol import HarnessProtocol
from openjiuwen.agent_teams.harness.native_harness import NativeHarness

__all__ = [
    "HarnessProtocol",
    "HarnessState",
    "NativeHarness",
    "NativeHarnessProtocolAdapter",
    "NativeV2HarnessProvider",
    "create_native_harness_protocol",
    "TeamHarness",
]


def __getattr__(name: str) -> Any:
    """Load the optional public-protocol adapter without import cycles."""
    if name in {"NativeHarnessProtocolAdapter", "NativeV2HarnessProvider", "create_native_harness_protocol"}:
        from openjiuwen.agent_teams.harness import protocol_adapter

        return getattr(protocol_adapter, name)
    raise AttributeError(name)
