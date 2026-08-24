# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Trajectory collector for SFT online rails."""

from __future__ import annotations

from typing import Any, Optional

from openjiuwen.agent_evolving.agent_rl.online.core.interaction import TokenInTokenOutRecord
from openjiuwen.agent_evolving.trajectory.model import Trajectory
from openjiuwen.agent_evolving.trajectory.spans import iter_spans
from openjiuwen.agent_evolving.trajectory.team import span_category

from .converter import SFTRawTrajectoryBatch, SFTRawTrajectoryConverter


class SFTTrajectoryCollector:
    """Collect session-level raw trajectories used by SFT rollouters."""

    def __init__(self, *, converter: Optional[SFTRawTrajectoryConverter] = None) -> None:
        self.converter = converter or SFTRawTrajectoryConverter()

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
        meta = getattr(step, "meta", None)
        if not isinstance(meta, dict):
            step.meta = {}
            meta = step.meta
        meta.update({
            "turn_id": turn_id,
            "source": "sft_online",
            "tenant_id": tenant_id,
            "prompt_str": record.prompt_str,
            "llm_str": record.llm_str,
        })
        if record.prompt_ids is not None:
            meta["prompt_ids"] = record.prompt_ids
        if record.llm_ids is not None:
            meta["completion_token_ids"] = record.llm_ids
        if lora_meta:
            meta.update(lora_meta)

    def build_raw_batch(
        self,
        trajectory: Trajectory,
        *,
        tenant_id: str | None,
        user_id: str,
        session_done: bool,
        flush_reason: str,
        original_task: str,
        dataset_case: dict[str, Any],
        workspace_ref: dict[str, Any],
        context_compression: dict[str, Any] | None = None,
    ) -> SFTRawTrajectoryBatch:
        return self.converter.convert(
            trajectory,
            tenant_id=tenant_id,
            user_id=user_id,
            session_done=session_done,
            flush_reason=flush_reason,
            original_task=original_task,
            dataset_case=dataset_case,
            workspace_ref=workspace_ref,
            context_compression=context_compression,
        )

    @staticmethod
    def has_llm_steps(trajectory: Trajectory) -> bool:
        return any(span_category(span) == "llm" for span in iter_spans(trajectory))
