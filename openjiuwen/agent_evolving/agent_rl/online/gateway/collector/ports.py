# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Internal seams used by gateway trajectory collection."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from openjiuwen.agent_evolving.agent_rl.online.gateway.collector.types import CollectionSessionManager
from openjiuwen.agent_evolving.agent_rl.online.gateway.trajectory.task_reward import TaskReward


class GatewaySamplePipeline(Protocol):
    """Persist, score, and reward samples emitted by the collector."""

    async def on_gateway_followup(self, session_id: str, messages: list[dict[str, Any]]) -> int:
        pass

    async def stage_gateway_sample(self, sample: dict[str, Any]) -> None:
        pass

    async def flush_gateway_session(self, session_id: str) -> int:
        pass

    async def discard_gateway_session(self, session_id: str) -> int:
        pass

    async def submit_task_reward(self, session_id: str, reward: TaskReward) -> int:
        pass


class CollectorCapture(Protocol):
    """Prepared collection for one forwarded gateway request."""

    async def commit(self, response: Mapping[str, Any]) -> object | None:
        pass


class TrajectoryCollector(Protocol):
    """Capture active gateway sessions and ignore all other session IDs."""

    async def capture(
        self,
        session_id: str,
        request: Mapping[str, Any],
    ) -> CollectorCapture | None:
        pass


class GatewayCollector(TrajectoryCollector, CollectionSessionManager, Protocol):
    """Complete in-process collection interface used at app assembly."""
