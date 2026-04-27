# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""rail-v1 upload batch ingestion for the online-RL gateway."""

from __future__ import annotations

import logging
from typing import Any

from ..message_utils import extract_last_user_instruction
from .sample_payloads import build_sample, coerce_logprobs

logger = logging.getLogger("online_rl.gateway")


class RailBatchIngestor:
    """Normalize rail-v1 uploads and stage samples for delayed judge scoring."""

    def __init__(
        self,
        *,
        pending_judge_store: Any,
        judge_dispatcher: Any,
    ) -> None:
        self._pending_judge_store = pending_judge_store
        self._judge_dispatcher = judge_dispatcher

    async def ingest_rail_batch(self, payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("protocol_version") != "rail-v1":
            raise ValueError(f"unsupported protocol_version: {payload.get('protocol_version')}")

        session_id = str(payload.get("session_id") or "")
        trajectory_id = str(payload.get("trajectory_id") or "")
        samples = payload.get("samples")
        if not session_id:
            raise ValueError("session_id is required")
        if not trajectory_id:
            raise ValueError("trajectory_id is required")
        if not isinstance(samples, list):
            raise ValueError("samples must be a list")

        judged = await self._judge_dispatcher.on_prev_feedback(session_id, payload.get("prev_feedback"))

        accepted = 0
        rejected = 0
        first_error: str | None = None
        for sample in samples:
            if not isinstance(sample, dict):
                rejected += 1
                if first_error is None:
                    first_error = "samples must contain only objects"
                continue
            try:
                normalized = self._normalize_rail_sample(payload, sample)
            except Exception as exc:
                rejected += 1
                if first_error is None:
                    first_error = str(exc)
                logger.warning("[Gateway] rail-v1 sample rejected trajectory=%s err=%s", trajectory_id, exc)
                continue
            await self._pending_judge_store.put(normalized)
            accepted += 1

        if accepted == 0 and rejected > 0:
            raise ValueError(first_error or "all rail-v1 samples were rejected")

        session_flushed = 0
        if bool(payload.get("session_done", False)):
            session_flushed = await self._judge_dispatcher.on_session_done(session_id)

        return {
            "protocol_version": "rail-v1",
            "session_id": session_id,
            "trajectory_id": trajectory_id,
            "accepted": accepted,
            "rejected": rejected,
            "judged": judged,
            "session_flushed": session_flushed,
        }

    @staticmethod
    def _normalize_rail_sample(payload: dict[str, Any], sample: dict[str, Any]) -> dict[str, Any]:
        session_id = str(sample.get("session_id") or payload.get("session_id") or "")
        trajectory_id = str(sample.get("trajectory_id") or payload.get("trajectory_id") or "")
        step_index = int(sample.get("step_index") or 0)
        trajectory_meta = payload.get("trajectory_meta") or {}
        messages = sample.get("messages")
        if not isinstance(messages, list) or not messages:
            raise ValueError("messages must be a non-empty list")
        response = sample.get("response") if isinstance(sample.get("response"), dict) else {}
        response_text = str(sample.get("response_text") or response.get("content") or "")
        tools = sample.get("tools")
        render_fingerprint_expected = sample.get("render_fingerprint_expected")
        if render_fingerprint_expected is not None and sample.get("render_fingerprint") != render_fingerprint_expected:
            raise ValueError("render_fingerprint mismatch")

        prompt_ids = sample.get("prompt_ids")
        if not isinstance(prompt_ids, list):
            raise ValueError("missing prompt_ids; rail-v1 samples must provide prompt token ids")
        prompt_ids = [int(x) for x in prompt_ids]
        prompt_text = str(sample.get("prompt_text") or "")

        response_ids = sample.get("response_tokens")
        if isinstance(response_ids, list):
            response_ids = [int(x) for x in response_ids]
        else:
            raise ValueError("missing response_tokens; rail-v1 samples must provide response token ids")

        logprobs = sample.get("logprobs")
        response_logprobs = coerce_logprobs(logprobs, len(response_ids))
        user_id = str(
            sample.get("user_id")
            or payload.get("user_id")
            or payload.get("tenant_id")
            or sample.get("tenant_id")
            or ""
        ).strip()
        if not user_id:
            raise ValueError("missing user_id/tenant_id; upload batch samples require a stable user id")
        turn_num = step_index + 1

        return build_sample(
            sample_id=f"{trajectory_id}:{step_index}",
            user_id=user_id,
            session_id=session_id,
            turn_num=turn_num,
            mode="rail-v1",
            io_mode="rail",
            model=sample.get("model_id") or payload.get("model_id"),
            messages=messages,
            tools=tools,
            assistant_message=response,
            usage=response.get("usage") or {},
            finish_reason=response.get("finish_reason"),
            prompt_text=prompt_text,
            prompt_ids=prompt_ids,
            response_text=response_text,
            response_ids=response_ids,
            response_logprobs=response_logprobs,
            tool_calls=response.get("tool_calls") or [],
            extra_fields={
                "trajectory_id": trajectory_id,
                "step_index": step_index,
                "rail_meta": {
                    "protocol_version": "rail-v1",
                    "sample_meta": sample.get("meta") or {},
                    "trajectory_meta": trajectory_meta,
                    "instruction_text": extract_last_user_instruction(messages),
                },
            },
        )
