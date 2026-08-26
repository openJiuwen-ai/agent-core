# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Trajectory collector for PPO online rails."""

from __future__ import annotations

from typing import Any, Optional

from openjiuwen.agent_evolving.agent_rl.online.core.interaction import TokenInTokenOutRecord
from openjiuwen.agent_evolving.trajectory import Trajectory

from .converter import OnlineTrajectoryConverter, RailV1Batch


class RLTrajectoryCollector:
    """Collect per-turn token/logprob fields required by online PPO training."""

    def __init__(self, *, converter: Optional[OnlineTrajectoryConverter] = None) -> None:
        self.converter = converter or OnlineTrajectoryConverter()

    @staticmethod
    def collect_llm_interaction(
        record: TokenInTokenOutRecord,
        *,
        step: Any,
        turn_id: int,
        tenant_id: str | None,
        lora_meta: Optional[dict[str, Any]] = None,
    ) -> None:
        if step is None:
            return
        if getattr(step, "prompt_token_ids", None) is None:
            step.prompt_token_ids = record.prompt_ids
        if getattr(step, "completion_token_ids", None) is None:
            step.completion_token_ids = record.llm_ids
        if getattr(step, "logprobs", None) is None:
            step.logprobs = record.logprobs

        meta = getattr(step, "meta", None)
        if not isinstance(meta, dict):
            step.meta = {}
            meta = step.meta
        meta.update({
            "turn_id": turn_id,
            "source": "rl_online",
            "tenant_id": tenant_id,
        })
        if lora_meta:
            meta.update(lora_meta)

    def build_batch(
        self,
        trajectory: Trajectory,
        *,
        tenant_id: str | None,
        session_done: bool,
    ) -> RailV1Batch:
        return self.converter.convert(
            trajectory,
            tenant_id=tenant_id,
            session_done=session_done,
        )
