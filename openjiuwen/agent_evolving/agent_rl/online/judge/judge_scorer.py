# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""LLM-as-Judge scorer adapter.

Calls the Judge service (which may be a dedicated judge_server with voting,
or a raw vLLM endpoint) to score a single turn.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from openjiuwen.agent_evolving.agent_rl.online.judge.evaluator import (
    JudgeEvaluatorConfig,
    evaluate_judge_scores,
)
from openjiuwen.agent_evolving.agent_rl.online.judge.scoring import parse_judge_scores

logger = logging.getLogger("online_rl.judge")


class JudgeScorer:
    """Call LLM-as-Judge to score a single (instruction, response, feedback) triple."""

    def __init__(
        self,
        *,
        judge_url: str,
        judge_model: str,
        api_key: str = "EMPTY",
        timeout: float = 60.0,
        num_votes: int = 1,
        max_retries: int = 2,
        retry_backoff_sec: float = 0.2,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        """Initialize judge scorer client.

        Args:
            judge_url: Base URL of judge-compatible chat endpoint.
            judge_model: Judge model id.
            api_key: Judge API key.
            timeout: Per-request timeout in seconds.
            num_votes: Number of judge votes per sample.
            max_retries: Max retries for transient judge failures.
            retry_backoff_sec: Linear retry backoff base in seconds.
            http_client: Optional shared HTTP client.
        """
        self.judge_url = judge_url.rstrip("/")
        self.judge_model = judge_model
        self.api_key = api_key
        self.num_votes = max(1, num_votes)
        self.max_retries = max(0, int(max_retries))
        self.retry_backoff_sec = max(0.0, float(retry_backoff_sec))
        self.timeout = timeout
        self._owned_client = http_client is None
        self._http_client = http_client or httpx.AsyncClient(timeout=timeout)
        self._config = JudgeEvaluatorConfig(
            llm_url=self.judge_url,
            model_id=self.judge_model,
            api_key=self.api_key,
            num_votes=self.num_votes,
            temperature=0.1,
            max_completion_tokens=4096,
            max_retries=self.max_retries,
            retry_backoff_sec=self.retry_backoff_sec,
        )

    async def close(self) -> None:
        """Close owned HTTP client if created internally."""
        if self._owned_client:
            await self._http_client.aclose()

    async def score(
        self,
        *,
        response_text: str,
        instruction_text: str,
        followup_user_feedback: str,
        session_id: str = "",
        turn_num: int = 0,
    ) -> dict[str, Any]:
        """Score a turn and return normalized reward details.

        Args:
            response_text: Assistant response content to score.
            instruction_text: User instruction text for this turn.
            followup_user_feedback: Next-turn user feedback for delayed scoring.
            session_id: Optional session id used for logging context.
            turn_num: Optional turn index used for logging context.

        Returns:
            Dict with normalized ``score`` and raw vote details.
        """
        result = await evaluate_judge_scores(
            client=self._http_client,
            config=self._config,
            response_text=response_text,
            instruction_text=instruction_text,
            followup_user_feedback=followup_user_feedback,
            session_id=session_id,
            turn_num=turn_num,
            logger=logger,
        )
        result.pop("model", None)
        result.pop("session_id", None)
        result.pop("turn_num", None)
        return result

    @staticmethod
    def _parse_scores(content: str) -> dict[str, Any]:
        return parse_judge_scores(content)
