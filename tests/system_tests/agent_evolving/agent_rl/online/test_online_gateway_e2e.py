# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""System test for the agent_rl online gateway without external services."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

httpx = pytest.importorskip("httpx")
pytest.importorskip("fastapi")

from openjiuwen.agent_evolving.agent_rl.online.gateway.app.bootstrap import build_app_from_config
from openjiuwen.agent_evolving.agent_rl.online.gateway.config import GatewayConfig
from openjiuwen.agent_evolving.agent_rl.online.rail.online_rail import RLOnlineRail
from openjiuwen.agent_evolving.agent_rl.online.rail.uploader import TrajectoryUploader
from openjiuwen.agent_evolving.trajectory import LLMCallDetail, Trajectory, TrajectoryStep


class _FakeRedisPipeline:
    def __init__(self, redis: _FakeRedis) -> None:
        self._redis = redis
        self._ops: list[tuple[str, tuple]] = []

    def delete(self, key: str):
        self._ops.append(("delete", (key,)))
        return self

    def hset(self, key: str, mapping: dict[str, str]):
        self._ops.append(("hset", (key, mapping)))
        return self

    def hmget(self, key: str, fields: list[str]):
        self._ops.append(("hmget", (key, fields)))
        return self

    def sadd(self, key: str, *members: str):
        self._ops.append(("sadd", (key, *members)))
        return self

    def zadd(self, key: str, mapping: dict[str, float]):
        self._ops.append(("zadd", (key, mapping)))
        return self

    def zcard(self, key: str):
        self._ops.append(("zcard", (key,)))
        return self

    def zrem(self, key: str, member: str):
        self._ops.append(("zrem", (key, member)))
        return self

    async def execute(self):
        out = []
        for name, args in self._ops:
            out.append(await getattr(self._redis, name)(*args))
        return out


class _FakeRedis:
    def __init__(self) -> None:
        self._kv: dict[str, str] = {}
        self._zsets: dict[str, dict[str, float]] = {}
        self._hashes: dict[str, dict[str, str]] = {}
        self._sets: dict[str, set[str]] = {}

    def register_script(self, script: str):
        del script

        async def _run(*, keys: list[str], args: list[object]):
            pending_key, training_key = keys
            limit = max(1, int(args[0]))
            now_score = float(args[1])
            new_status = str(args[2])
            traj_prefix = str(args[3])
            ids = await self.zrange(pending_key, 0, limit - 1)
            for sample_id in ids:
                await self.zrem(pending_key, sample_id)
                await self.zadd(training_key, {sample_id: now_score})
                await self.hset(f"{traj_prefix}{sample_id}", {"status": new_status})
            return ids

        return _run

    async def delete(self, key: str) -> None:
        self._kv.pop(key, None)
        self._hashes.pop(key, None)

    async def expire(self, key: str, ttl: int) -> None:
        del key, ttl

    async def get(self, key: str) -> str | None:
        return self._kv.get(key)

    async def hget(self, key: str, field: str) -> str | None:
        return self._hashes.get(key, {}).get(field)

    async def hmget(self, key: str, fields: list[str]) -> list[str | None]:
        row = self._hashes.get(key, {})
        return [row.get(field) for field in fields]

    async def hset(self, key: str, mapping: dict[str, str]) -> None:
        self._hashes.setdefault(key, {}).update(mapping)

    async def mget(self, keys: list[str]) -> list[str | None]:
        return [self._kv.get(key) for key in keys]

    def pipeline(self) -> _FakeRedisPipeline:
        return _FakeRedisPipeline(self)

    async def sadd(self, key: str, *members: str) -> None:
        self._sets.setdefault(key, set()).update(members)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        del ex
        self._kv[key] = value

    async def smembers(self, key: str) -> set[str]:
        return set(self._sets.get(key, set()))

    async def srem(self, key: str, *members: str) -> None:
        bucket = self._sets.setdefault(key, set())
        for member in members:
            bucket.discard(member)

    async def zadd(self, key: str, mapping: dict[str, float]) -> None:
        self._zsets.setdefault(key, {}).update(mapping)

    async def zcard(self, key: str) -> int:
        return len(self._zsets.get(key, {}))

    async def zrange(self, key: str, start: int, end: int) -> list[str]:
        members = [
            member
            for member, _ in sorted(
                self._zsets.get(key, {}).items(),
                key=lambda item: item[1],
            )
        ]
        if end == -1:
            end = len(members) - 1
        return members[start:end + 1]

    async def zrem(self, key: str, member: str) -> None:
        self._zsets.get(key, {}).pop(member, None)


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
                "logprobs": True,
                "top_logprobs": 1,
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
            )
            trajectory = Trajectory(
                execution_id="traj-st",
                session_id="session-st",
                source="rl_online",
                steps=[
                    TrajectoryStep(
                        kind="llm",
                        detail=LLMCallDetail(
                            model="st-model",
                            messages=[{"role": "user", "content": "ping"}],
                            response={"role": "assistant", "content": "pong", "finish_reason": "stop"},
                        ),
                        prompt_token_ids=[101, 102],
                        completion_token_ids=[201, 202],
                        logprobs=[-0.1, -0.2],
                    )
                ],
            )

            await rail.run_evolution(trajectory)
            await uploader.shutdown()

            stats_response = await gateway_client.get(
                "/v1/gateway/stats",
                headers={"Authorization": "Bearer gw-token"},
            )

    assert stats_response.status_code == 200
    assert stats_response.json()["trajectory_store_pending"] == 1

    row = redis._hashes["rl:traj:traj-st:0"]
    stored_sample = json.loads(row["sample_json"])
    assert row["user_id"] == "st-user"
    assert stored_sample["user_id"] == "st-user"
    assert stored_sample["trajectory"]["prompt_ids"] == [101, 102]
    assert stored_sample["trajectory"]["response_ids"] == [201, 202]
    assert stored_sample["judge_feedback"]["tag"] == "session_done"
    assert (tmp_path / "records" / "samples.jsonl").exists()
