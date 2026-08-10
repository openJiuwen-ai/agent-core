# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""System test for the agent_rl online gateway without external services."""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from tests.unit_tests.agent_evolving.agent_rl.online.support import InMemoryRedis

httpx = pytest.importorskip("httpx")
pytest.importorskip("fastapi")

build_app_from_config = importlib.import_module(
    "openjiuwen.agent_evolving.agent_rl.online.gateway.app.bootstrap"
).build_app_from_config
GatewayConfig = importlib.import_module(
    "openjiuwen.agent_evolving.agent_rl.online.gateway.config"
).GatewayConfig
RLOnlineRail = importlib.import_module(
    "openjiuwen.agent_evolving.agent_rl.online.rail.online_rail"
).RLOnlineRail
TrajectoryUploader = importlib.import_module(
    "openjiuwen.agent_evolving.agent_rl.online.rail.uploader"
).TrajectoryUploader
trajectory_module = importlib.import_module("openjiuwen.agent_evolving.trajectory")
Trajectory = trajectory_module.Trajectory
TrajectorySpanProcessor = trajectory_module.TrajectorySpanProcessor
trajectory_spans = importlib.import_module("openjiuwen.agent_evolving.trajectory.spans")
trajectory_schema = importlib.import_module("openjiuwen.agent_evolving.trajectory.schema")
observability_semconv = importlib.import_module("openjiuwen.extensions.observability.semconv")
PreparedEvolutionInput = importlib.import_module(
    "openjiuwen.harness.rails.evolution.evolution_rail"
).PreparedEvolutionInput

_FakeRedis = InMemoryRedis


@pytest.mark.asyncio
async def test_online_gateway_proxy_and_rail_upload_e2e(tmp_path: Path):
    redis = _FakeRedis()
    upstream_requests: list[dict] = []

    async def _upstream_handler(request: httpx.Request) -> httpx.Response:
        upstream_requests.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-st",
                "object": "chat.completion",
                "created": 123,
                "model": "st-model",
                "prompt_token_ids": [101, 102],
                "choices": [{
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "pong"},
                    "token_ids": [201, 202],
                    "logprobs": {"content": [{"logprob": -0.1}, {"logprob": -0.2}]},
                }],
                "usage": {"prompt_tokens": 2, "completion_tokens": 2, "total_tokens": 4},
            },
        )

    config = GatewayConfig(
        port=18080,
        llm_url="http://llm.local",
        judge_url="",
        model_id="st-model",
        gateway_api_key="gw-token",
        record_dir=str(tmp_path / "records"),
        dump_token_ids=True,
        single_user_default=False,
    )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(_upstream_handler),
        base_url="http://llm.local",
    ) as upstream_client:
        app = build_app_from_config(config, http_client=upstream_client, redis_client=redis)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://gateway.local",
        ) as gateway_client:
            missing_user_response = await gateway_client.post(
                "/v1/chat/completions",
                headers={"Authorization": "Bearer gw-token"},
                json={"messages": [{"role": "user", "content": "ping"}]},
            )
            assert missing_user_response.status_code == 400
            assert "x-user-id" in missing_user_response.text
            assert upstream_requests == []

            chat_response = await gateway_client.post(
                "/v1/chat/completions",
                headers={
                    "Authorization": "Bearer gw-token",
                    "x-user-id": "st-user",
                },
                json={
                    "messages": [{"role": "user", "content": "ping"}],
                    "stream": True,
                },
            )

            assert chat_response.status_code == 200
            assert "data: [DONE]" in chat_response.text
            assert upstream_requests == [{
                "messages": [{"role": "user", "content": "ping"}],
                "stream": False,
                "model": "st-model",
            }]

            uploader = TrajectoryUploader(
                "http://gateway.local",
                api_key="gw-token",
                client=gateway_client,
                wal_dir=tmp_path / "wal",
                max_retries=0,
            )
            rail = RLOnlineRail(
                session_id="session-st",
                gateway_endpoint="http://gateway.local",
                tenant_id="st-user",
                uploader=uploader,
                trajectory_span_processor=TrajectorySpanProcessor(),
            )
            trajectory = Trajectory.from_otlp(
                {
                    "resourceSpans": [
                        {
                            "resource": {
                                "attributes": trajectory_spans.attributes_from_map(
                                    {
                                        trajectory_schema.TRAJECTORY_ID: "traj-st",
                                        trajectory_schema.SESSION_ID: "session-st",
                                        trajectory_schema.TRAJECTORY_SOURCE: "rl_online",
                                    }
                                )
                            },
                            "scopeSpans": [
                                {
                                    "scope": {"name": "system-test"},
                                    "spans": [
                                        {
                                            "traceId": "1" * 32,
                                            "spanId": "2" * 16,
                                            "name": "llm.call",
                                            "attributes": trajectory_spans.attributes_from_map(
                                                {
                                                    observability_semconv.GEN_AI_REQUEST_MODEL: "st-model",
                                                    f"{observability_semconv.GEN_AI_PROMPT}.0.role": "user",
                                                    f"{observability_semconv.GEN_AI_PROMPT}.0.content": "ping",
                                                    f"{observability_semconv.GEN_AI_COMPLETION}.0.role": "assistant",
                                                    f"{observability_semconv.GEN_AI_COMPLETION}.0.content": "pong",
                                                    trajectory_schema.RL_PROMPT_TOKEN_IDS: [101, 102],
                                                    trajectory_schema.RL_COMPLETION_TOKEN_IDS: [201, 202],
                                                    trajectory_schema.RL_LOGPROBS: [-0.1, -0.2],
                                                }
                                            ),
                                        }
                                    ],
                                }
                            ],
                        }
                    ]
                }
            )

            await rail.run_evolution(PreparedEvolutionInput(trajectory=trajectory, messages=()))
            await uploader.shutdown()

            stats_response = await gateway_client.get(
                "/v1/gateway/stats",
                headers={"Authorization": "Bearer gw-token"},
            )

    assert stats_response.status_code == 200
    assert stats_response.json()["trajectory_store_pending"] == 1

    sample_key = "rl:traj:traj-st:0"
    stored_sample = json.loads(await redis.hget(sample_key, "sample_json"))
    assert await redis.hget(sample_key, "user_id") == "st-user"
    assert stored_sample["user_id"] == "st-user"
    assert stored_sample["trajectory"]["prompt_ids"] == [101, 102]
    assert stored_sample["trajectory"]["response_ids"] == [201, 202]
    assert stored_sample["judge_feedback"]["tag"] == "session_done"
    assert (tmp_path / "records" / "samples.jsonl").exists()
