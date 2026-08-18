# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from openjiuwen.core.foundation.llm.schema.message import AssistantMessage
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ModelCallInputs
from openjiuwen.agent_evolving.agent_rl.online.rail.factory import build_rl_online_rail_from_env
from openjiuwen.agent_evolving.trajectory.processor import TrajectorySpanProcessor
from openjiuwen.extensions.observability.setup import shutdown_observability


class _FakeUploader:
    def __init__(self, endpoint: str, *, api_key: str = "", wal_dir: str = "", **kwargs) -> None:
        del kwargs
        self.gateway_endpoint = endpoint
        self.api_key = api_key
        self.wal_dir = wal_dir


def teardown_function() -> None:
    shutdown_observability()


def test_factory_injects_the_explicit_processor(monkeypatch) -> None:
    monkeypatch.setenv("USE_RL_ONLINE_RAIL", "true")
    monkeypatch.setattr(
        "openjiuwen.agent_evolving.agent_rl.online.core.uploader.TrajectoryUploader",
        _FakeUploader,
    )
    processor = TrajectorySpanProcessor()

    rail = build_rl_online_rail_from_env(trajectory_span_processor=processor)

    assert rail is not None
    assert rail.trajectory_span_processor is processor
    assert rail._uploader.gateway_endpoint == "http://127.0.0.1:18080"


def test_factory_creates_default_processor_for_legacy_no_arg_call(monkeypatch) -> None:
    monkeypatch.setenv("USE_RL_ONLINE_RAIL", "true")
    monkeypatch.setattr(
        "openjiuwen.agent_evolving.agent_rl.online.core.uploader.TrajectoryUploader",
        _FakeUploader,
    )

    rail = build_rl_online_rail_from_env()

    assert rail is not None
    assert isinstance(rail.trajectory_span_processor, TrajectorySpanProcessor)


def test_rl_online_rail_uploads_fallback_sample_when_span_is_missing() -> None:
    from openjiuwen.agent_evolving.agent_rl.online.backends.rl.rail import RLOnlineRail

    class _Session:
        def get_session_id(self) -> str:
            return "sess-1"

    class _Uploader:
        def __init__(self) -> None:
            self.payloads: list[dict] = []

        async def enqueue(self, batch) -> None:
            self.payloads.append(batch.to_dict())

    uploader = _Uploader()
    rail = RLOnlineRail(
        session_id="",
        gateway_endpoint="http://gateway.local",
        tenant_id="user-1",
        uploader=uploader,
        trajectory_span_processor=TrajectorySpanProcessor(),
    )
    ctx = AgentCallbackContext(
        agent=SimpleNamespace(config=SimpleNamespace(model_name="test-model")),
        session=_Session(),
        inputs=ModelCallInputs(
            messages=[{"role": "user", "content": "hello"}],
            response=AssistantMessage(
                content="world",
                prompt_token_ids=[1, 2],
                completion_token_ids=[3],
                logprobs=[-0.1],
            ),
        ),
    )

    asyncio.run(rail._on_after_model_call(ctx, None))

    assert len(uploader.payloads) == 1
    payload = uploader.payloads[0]
    assert payload["protocol_version"] == "rail-v1"
    assert payload["tenant_id"] == "user-1"
    assert payload["session_id"] == "sess-1"
    assert len(payload["samples"]) == 1
    sample = payload["samples"][0]
    assert sample["messages"][-1]["content"] == "hello"
    assert sample["response_text"] == "world"
    assert sample["prompt_ids"] == [1, 2]
    assert sample["response_tokens"] == [3]
