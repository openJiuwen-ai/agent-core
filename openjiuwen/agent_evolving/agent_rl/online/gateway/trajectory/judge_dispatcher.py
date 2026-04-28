# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Delayed-judge dispatch for pending rail-v1 samples."""

from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from ..message_utils import extract_last_user_instruction

logger = logging.getLogger("online_rl.gateway")


class JudgeDispatcher:
    """Flush pending rail-v1 samples when feedback/session_done arrives."""

    def __init__(
        self,
        *,
        pending_store: Any,
        record_sample: Any,
        judge_scorer: Optional[Any] = None,
    ) -> None:
        self._pending_store = pending_store
        self._record_sample = record_sample
        self._judge_scorer = judge_scorer

    async def on_prev_feedback(self, session_id: str, prev_feedback: Optional[dict[str, Any]]) -> int:
        feedback = self._feedback_text(prev_feedback)
        if not feedback.strip():
            return 0
        sample = await self._pending_store.pop_earliest(session_id)
        if sample is None:
            return 0
        finalized = await self._finalize_sample(sample, feedback=feedback, tag="prev_feedback")
        await self._record_sample(finalized)
        return 1

    async def on_session_done(self, session_id: str) -> int:
        samples = await self._pending_store.pop_all(session_id)
        count = 0
        for idx, sample in enumerate(samples):
            finalized = await self._finalize_sample(
                sample,
                feedback="",
                tag="session_done" if idx == len(samples) - 1 else "session_flush",
            )
            await self._record_sample(finalized)
            count += 1
        return count

    async def _finalize_sample(self, sample: dict[str, Any], *, feedback: str, tag: str) -> dict[str, Any]:
        finalized = dict(sample)
        session_id = str(finalized.get("session_id") or "")
        turn_num = int(finalized.get("turn_num") or finalized.get("step_index") or 0)
        response_text = str(
            finalized.get("trajectory", {}).get("response_text")
            or finalized.get("response_text")
            or ""
        )
        messages = finalized.get("request", {}).get("messages")
        instruction_text = extract_last_user_instruction(messages) if isinstance(messages, list) else ""
        if not instruction_text:
            instruction_text = feedback

        if self._judge_scorer is not None:
            try:
                judge = await self._judge_scorer.score(
                    response_text=response_text,
                    instruction_text=instruction_text,
                    followup_user_feedback=feedback,
                    session_id=session_id,
                    turn_num=turn_num,
                )
            except Exception as exc:
                logger.warning("[JudgeDispatcher] judge failed session=%s turn=%d err=%s", session_id, turn_num, exc)
                judge = {"score": 0.0, "votes": ["fail"], "details": {}, "error": str(exc)}
        else:
            judge = {"score": 0.0, "votes": ["skip"], "details": {}, "error": tag}

        finalized["judge"] = judge
        finalized["judge_feedback"] = {
            "tag": tag,
            "followup_user_feedback": feedback,
        }
        finalized.setdefault("sample_id", str(uuid.uuid4()))
        return finalized

    @staticmethod
    def _feedback_text(prev_feedback: Optional[dict[str, Any]]) -> str:
        if not isinstance(prev_feedback, dict):
            return ""
        raw = prev_feedback.get("raw_user_text")
        if raw is None:
            raw = prev_feedback.get("text")
        if raw is None:
            raw = prev_feedback.get("feedback")
        return str(raw or "")
