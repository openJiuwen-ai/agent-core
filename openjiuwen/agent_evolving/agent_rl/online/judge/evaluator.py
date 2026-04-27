# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from openjiuwen.agent_evolving.agent_rl.online.judge.scoring import (
    build_judge_prompt,
    normalize_overall_score,
    parse_judge_scores,
)

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


@dataclass
class JudgeEvaluatorConfig:
    llm_url: str
    model_id: str
    api_key: str = ""
    num_votes: int = 1
    temperature: float = 0.1
    max_completion_tokens: int = 4096
    max_retries: int = 0
    retry_backoff_sec: float = 0.0


def build_judge_messages(
    *,
    response_text: str,
    instruction_text: str,
    followup_user_feedback: str,
) -> list[dict[str, str]]:
    prompt = build_judge_prompt(
        instruction_text=_sanitize_text(instruction_text) or "(无)",
        response_text=_sanitize_text(response_text) or "(无回复)",
        followup_user_feedback=_sanitize_text(followup_user_feedback) or "(无反馈)",
    )
    return [{"role": "user", "content": prompt}]


async def evaluate_judge_scores(
    *,
    client: httpx.AsyncClient,
    config: JudgeEvaluatorConfig,
    response_text: str,
    instruction_text: str,
    followup_user_feedback: str = "",
    session_id: str = "",
    turn_num: int = 0,
    logger: Optional[logging.Logger] = None,
) -> dict[str, Any]:
    logger = logger or logging.getLogger("online_rl.judge")
    messages = build_judge_messages(
        response_text=response_text,
        instruction_text=instruction_text,
        followup_user_feedback=followup_user_feedback,
    )
    vote_results = await asyncio.gather(*[
        _query_vote(
            client=client,
            config=config,
            messages=messages,
            vote_id=i,
            logger=logger,
        )
        for i in range(max(1, int(config.num_votes)))
    ])

    overall_values = [result.get("overall", 5.0) for result in vote_results]
    avg_overall = sum(overall_values) / len(overall_values) if overall_values else 5.0
    normalized_score = normalize_overall_score(avg_overall)

    logger.info(
        "[Judge] session=%s turn=%d votes=%s -> overall=%.2f score=%.3f",
        session_id,
        turn_num,
        overall_values,
        avg_overall,
        normalized_score,
    )
    return {
        "score": normalized_score,
        "overall_raw": avg_overall,
        "votes": overall_values,
        "details": vote_results[0] if len(vote_results) == 1 else vote_results,
        "model": config.model_id,
        "session_id": session_id,
        "turn_num": turn_num,
    }


async def _query_vote(
    *,
    client: httpx.AsyncClient,
    config: JudgeEvaluatorConfig,
    messages: list[dict[str, str]],
    vote_id: int,
    logger: logging.Logger,
) -> dict[str, Any]:
    payload = {
        "model": config.model_id,
        "messages": messages,
        "temperature": config.temperature,
        "max_tokens": config.max_completion_tokens,
        "stream": False,
    }

    try:
        data = await _post_chat_completion(
            client=client,
            config=config,
            payload=payload,
            logger=logger,
            vote_id=vote_id,
        )
        choice = data.get("choices", [{}])[0]
        content = _flatten_content(choice.get("message", {}).get("content", ""))
        scores = parse_judge_scores(content, raise_on_error=False)

        if scores is None and str(choice.get("finish_reason") or "") == "length":
            retry_payload = dict(payload)
            retry_payload["temperature"] = 0.0
            retry_payload["max_tokens"] = max(payload["max_tokens"], 1024)
            retry_data = await _post_chat_completion(
                client=client,
                config=config,
                payload=retry_payload,
                logger=logger,
                vote_id=vote_id,
            )
            retry_choice = retry_data.get("choices", [{}])[0]
            retry_content = _flatten_content(retry_choice.get("message", {}).get("content", ""))
            retry_scores = parse_judge_scores(retry_content, raise_on_error=False)
            if retry_scores is not None:
                return retry_scores
            content = retry_content

        if scores is None:
            logger.warning("[Judge] vote %d unparseable: %s", vote_id, content[:200])
            return {"overall": 5.0, "error": "unparseable", "content": content}
        return scores
    except Exception as exc:
        logger.warning("[Judge] vote %d failed: %s", vote_id, exc)
        return {"overall": 5.0, "error": str(exc)}


async def _post_chat_completion(
    *,
    client: httpx.AsyncClient,
    config: JudgeEvaluatorConfig,
    payload: dict[str, Any],
    logger: logging.Logger,
    vote_id: int,
) -> dict[str, Any]:
    url = f"{config.llm_url.rstrip('/')}/v1/chat/completions"
    headers = _build_headers(config.api_key)
    attempt = 0
    while True:
        try:
            response = await client.post(url, json=payload, headers=headers)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            if attempt >= config.max_retries:
                raise
            attempt += 1
            delay = config.retry_backoff_sec * attempt
            logger.warning("[Judge] vote %d network retry=%d/%d err=%s", vote_id, attempt, config.max_retries, exc)
            if delay > 0:
                await asyncio.sleep(delay)
            continue

        if response.status_code in RETRYABLE_STATUS_CODES and attempt < config.max_retries:
            attempt += 1
            delay = config.retry_backoff_sec * attempt
            logger.warning(
                "[Judge] vote %d status=%d retry=%d/%d",
                vote_id,
                response.status_code,
                attempt,
                config.max_retries,
            )
            if delay > 0:
                await asyncio.sleep(delay)
            continue

        response.raise_for_status()
        return response.json()


def _build_headers(api_key: str) -> dict[str, str]:
    if not api_key:
        return {}
    return {"Authorization": f"Bearer {api_key}"}


def _sanitize_text(text: str) -> str:
    text = re.sub(r"<tool_call>.*?</tool_call>", "[tool_call block]", text, flags=re.DOTALL)
    text = re.sub(r"<[a-zA-Z_][^>]{0,80}>", "[tag]", text)
    text = re.sub(r"</[a-zA-Z_][^>]{0,80}>", "[/tag]", text)
    return text


def _flatten_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            item.get("text", "")
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        ).strip()
    if content is None:
        return ""
    return str(content)
