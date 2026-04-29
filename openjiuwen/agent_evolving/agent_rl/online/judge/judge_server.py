# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""LLM-as-Judge scoring service with voting and retry logic.

Inspired by agent-gateway/prm_server.py but uses a multi-dimensional
LLM-as-Judge prompt (0-10 per dimension) instead of discrete PRM votes
({-1, 0, +1}).  Retains the voting mechanism (majority over num_votes)
and the retry-on-max-tokens fallback logic.

Usage:
    python -m judge.judge_server \
        --llm-url http://127.0.0.1:18001 \
        --model-id Qwen3-32B \
        --num-votes 3
"""

from __future__ import annotations

import argparse
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Optional

import httpx
import uvicorn
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from openjiuwen.agent_evolving.agent_rl.online.judge.evaluator import (
    JudgeEvaluatorConfig,
    evaluate_judge_scores,
)

logger = logging.getLogger("online_rl.judge_server")


@dataclass
class JudgeConfig(JudgeEvaluatorConfig):
    timeout: float = 120.0
    expected_api_key: str = ""


class ScoreRequest(BaseModel):
    response_text: str = Field(default="")
    instruction_text: str = Field(default="")
    followup_user_feedback: str = Field(default="")
    session_id: str = Field(default="")
    turn_num: int = Field(default=0)


class ScoreResponse(BaseModel):
    score: float
    overall_raw: float
    votes: list[float]
    details: Any
    model: str
    session_id: str
    turn_num: int


def create_app(config: JudgeConfig) -> FastAPI:
    client = httpx.AsyncClient(timeout=config.timeout)

    @asynccontextmanager
    async def _lifespan(_: FastAPI):
        try:
            yield
        finally:
            await client.aclose()

    app = FastAPI(title="Judge Server", lifespan=_lifespan)

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {"ok": True, "model": config.model_id, "num_votes": config.num_votes}

    @app.post("/score", response_model=ScoreResponse)
    async def score(
        req: ScoreRequest,
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        if config.expected_api_key:
            if not authorization or not authorization.lower().startswith("bearer "):
                raise HTTPException(status_code=401, detail="missing bearer token")
            token = authorization.split(" ", 1)[1].strip()
            if token != config.expected_api_key:
                raise HTTPException(status_code=403, detail="invalid bearer token")

        return await evaluate_judge_scores(
            client=client,
            config=config,
            response_text=req.response_text,
            instruction_text=req.instruction_text,
            followup_user_feedback=req.followup_user_feedback,
            session_id=req.session_id,
            turn_num=req.turn_num,
            logger=logger,
        )

    return app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="LLM-as-Judge scoring server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--llm-url", required=True, help="Judge vLLM base URL")
    parser.add_argument("--model-id", required=True, help="Judge model name")
    parser.add_argument("--api-key", default="", help="vLLM bearer token")
    parser.add_argument("--judge-api-key", default="", help="API key for /score endpoint")
    parser.add_argument("--num-votes", type=int, default=1, help="Number of judge votes")
    parser.add_argument("--temperature", type=float, default=0.1)
    parser.add_argument("--max-completion-tokens", type=int, default=4096)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--log-level", default="info")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    config = JudgeConfig(
        llm_url=args.llm_url,
        model_id=args.model_id,
        api_key=args.api_key,
        num_votes=max(1, args.num_votes),
        temperature=args.temperature,
        max_completion_tokens=max(1, args.max_completion_tokens),
        timeout=max(1.0, args.timeout),
        expected_api_key=args.judge_api_key,
    )
    app = create_app(config)
    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)


if __name__ == "__main__":
    main()
