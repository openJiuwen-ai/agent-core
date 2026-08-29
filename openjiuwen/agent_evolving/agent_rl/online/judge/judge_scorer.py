# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""LLM-as-Judge scorer adapter.

Calls the Judge service (which may be a dedicated judge_server with voting,
or a raw vLLM endpoint) to score a single turn.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
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
        request: Mapping[str, Any],
        response: Mapping[str, Any],
        followup_user_message: str,
    ) -> float:
        """Score one complete policy call and return reward in ``[0, 1]``.

        Args:
            request: Complete standard OpenAI request.
            response: Complete standard OpenAI response.
            followup_user_message: Next-turn user message, or empty on Task stop.

        Returns:
            Normalized reward in ``[0, 1]``.
        """
        result = await evaluate_judge_scores(
            client=self._http_client,
            config=self._config,
            response_text=self._prompt_json(response),
            instruction_text=self._prompt_json(request),
            followup_user_feedback=followup_user_message,
            logger=logger,
        )
        return float(result["score"])

    @staticmethod
    def _parse_scores(content: str) -> dict[str, Any]:
        return parse_judge_scores(content)

    @classmethod
    def _prompt_json(cls, value: Any) -> str:
        return json.dumps(cls._replace_images(value), ensure_ascii=False, sort_keys=True)

    @classmethod
    def _replace_images(cls, value: Any) -> Any:
        if isinstance(value, Mapping):
            item_type = str(value.get("type") or "")
            if item_type in {"image", "image_url", "input_image"} or "image_url" in value:
                return "[image]"
            return {str(key): cls._replace_images(item) for key, item in value.items()}
        if isinstance(value, list | tuple):
            return [cls._replace_images(item) for item in value]
        return value
