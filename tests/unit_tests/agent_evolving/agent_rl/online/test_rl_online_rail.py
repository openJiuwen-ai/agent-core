from __future__ import annotations

from types import SimpleNamespace

import pytest

from openjiuwen.agent_evolving.agent_rl.online.rail.online_rail import RLOnlineRail
from openjiuwen.agent_evolving.trajectory.model import Trajectory
from openjiuwen.agent_evolving.trajectory.processor import TrajectorySpanProcessor
from openjiuwen.agent_evolving.trajectory.schema import (
    RL_COMPLETION_TOKEN_IDS,
    RL_LOGPROBS,
    RL_PROMPT_TOKEN_IDS,
    SESSION_ID,
    TRAJECTORY_ID,
    TRAJECTORY_SOURCE,
)
from openjiuwen.agent_evolving.trajectory.spans import (
    attributes_from_map, iter_spans, span_attributes,
    write_llm_exchange,
)
from openjiuwen.extensions.observability import semconv
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ModelCallInputs
from openjiuwen.harness.rails.evolution import PreparedEvolutionInput


class _CollectingUploader:
    def __init__(self) -> None:
        self.batches = []

    async def enqueue(self, batch) -> None:
        self.batches.append(batch)


class _FakeLoRARepo:
    def __init__(self, latest=None) -> None:
        self.latest = latest

    def get_latest(self, user_id: str):
        del user_id
        return self.latest


class _FakeGatewayResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self):
        return dict(self._payload)


class _FakeLoRAGatewayClient:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls: list[dict] = []

    async def post(self, url: str, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return _FakeGatewayResponse(self.payload)


class _Config:
    def __init__(self) -> None:
        self.model_name = "base-model"
        self.model_config_obj = SimpleNamespace(model_name="base-model")
        self.llm_return_token_ids = False
        self.llm_logprobs = False
        self.llm_top_logprobs = 0
        self.custom_headers = None


class _ReactAgent:
    def __init__(self) -> None:
        self.config = _Config()


class _Agent:
    def __init__(self) -> None:
        self.react_agent = _ReactAgent()


class _DirectReactAgent:
    def __init__(self) -> None:
        self._config = _Config()


def _span(span_id: int, *, prompt: str = "hi", response: str = "hello") -> dict:
    return {
        "traceId": "trace-1",
        "spanId": str(span_id),
        "name": "llm.call",
        "startTimeUnixNano": str(span_id),
        "endTimeUnixNano": str(span_id + 1),
        "attributes": attributes_from_map(
            {
                semconv.GEN_AI_REQUEST_MODEL: "m1",
                **write_llm_exchange(
                    [{"role": "user", "content": prompt}],
                    [{"role": "assistant", "content": response}],
                ),
            }
        ),
    }


def _trajectory(*spans: dict) -> Trajectory:
    return Trajectory.from_otlp(
        {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": attributes_from_map(
                            {
                                TRAJECTORY_ID: "traj-1",
                                SESSION_ID: "s1",
                                TRAJECTORY_SOURCE: "rl_online",
                            }
                        )
                    },
                    "scopeSpans": [{"scope": {"name": "test"}, "spans": list(spans)}],
                }
            ]
        }
    )


def _prepared(trajectory: Trajectory) -> PreparedEvolutionInput:
    return PreparedEvolutionInput(trajectory=trajectory, messages=())


@pytest.mark.asyncio
async def test_rl_online_rail_before_invoke_enables_token_capture() -> None:
    rail = RLOnlineRail(
        session_id="s1",
        gateway_endpoint="http://gateway.local",
        tenant_id="user-1",
        uploader=_CollectingUploader(),
        trajectory_span_processor=TrajectorySpanProcessor(),
    )
    ctx = AgentCallbackContext(agent=_Agent(), inputs=None)

    await rail._on_before_invoke(ctx)

    config = ctx.agent.react_agent.config
    assert config.llm_return_token_ids is True
    assert config.llm_logprobs is True
    assert config.llm_top_logprobs == 1
    assert config.custom_headers["x-user-id"] == "user-1"


@pytest.mark.asyncio
async def test_rl_online_rail_supports_direct_react_agent_config():
    from openjiuwen.agent_evolving.agent_rl.online.rail.online_rail import RLOnlineRail

    rail = RLOnlineRail(
        session_id="s1",
        gateway_endpoint="http://gateway.local",
        tenant_id="user-1",
        uploader=_CollectingUploader(),
        lora_default_policy="latest_by_user",
        lora_gateway_client=_FakeLoRAGatewayClient({
            "enabled": True,
            "model_id": "user-1",
            "lora_id": "user-1:v2",
            "version": "v2",
            "path": "/tmp/lora/v2",
        }),
        trajectory_span_processor=TrajectorySpanProcessor(),
    )
    agent = _DirectReactAgent()
    ctx = AgentCallbackContext(agent=agent, inputs=ModelCallInputs())

    await rail._on_before_invoke(ctx)
    await rail.before_model_call(ctx)

    assert agent._config.llm_return_token_ids is True
    assert agent._config.model_name == "user-1"
    assert agent._config.custom_headers["x-user-id"] == "user-1"

    await rail.after_model_call(ctx)

    assert agent._config.model_name == "base-model"
    assert agent._config.custom_headers == {"x-user-id": "user-1"}


@pytest.mark.asyncio
async def test_rl_online_rail_uses_latest_lora_model_for_one_call():
    from openjiuwen.agent_evolving.agent_rl.online.rail.online_rail import RLOnlineRail

    lora_client = _FakeLoRAGatewayClient({
        "enabled": True,
        "model_id": "user-1",
        "lora_id": "user-1:v2",
        "version": "v2",
        "path": "/tmp/lora/v2",
    })
    rail = RLOnlineRail(
        session_id="s1",
        gateway_endpoint="http://gateway.local",
        tenant_id="user-1",
        uploader=_CollectingUploader(),
        lora_default_policy="latest_by_user",
        gateway_api_key="gw-token",
        lora_gateway_client=lora_client,
        trajectory_span_processor=TrajectorySpanProcessor(),
    )
    agent = _Agent()
    ctx = AgentCallbackContext(agent=agent, inputs=ModelCallInputs())

    await rail.before_model_call(ctx)

    assert agent.react_agent.config.model_name == "user-1"
    assert agent.react_agent.config.model_config_obj.model_name == "user-1"
    assert ctx.extra["rl_online_lora_id"] == "user-1:v2"
    assert ctx.extra["rl_online_lora_version"] == "v2"
    assert lora_client.calls == [{
        "url": "http://gateway.local/v1/rl/lora/effective",
        "json": {"model_id": "user-1", "ensure_loaded": True},
        "headers": {"Authorization": "Bearer gw-token"},
    }]

    await rail.after_model_call(ctx)

    assert agent.react_agent.config.model_name == "base-model"


@pytest.mark.asyncio
async def test_rl_online_rail_skips_lora_when_gateway_has_no_effective_adapter():
    from openjiuwen.agent_evolving.agent_rl.online.rail.online_rail import RLOnlineRail

    rail = RLOnlineRail(
        session_id="s1",
        gateway_endpoint="http://gateway.local",
        tenant_id="user-1",
        uploader=_CollectingUploader(),
        lora_default_policy="latest_by_user",
        lora_gateway_client=_FakeLoRAGatewayClient({
            "enabled": False,
            "reason": "latest_lora_not_found",
        }),
        trajectory_span_processor=TrajectorySpanProcessor(),
    )
    agent = _Agent()
    ctx = AgentCallbackContext(agent=agent, inputs=ModelCallInputs())

    await rail.before_model_call(ctx)

    assert agent.react_agent.config.model_name == "base-model"
    assert agent.react_agent.config.model_config_obj.model_name == "base-model"


@pytest.mark.asyncio
async def test_rl_online_rail_lora_fallback_disabled_by_default():
    from openjiuwen.agent_evolving.agent_rl.online.rail.online_rail import RLOnlineRail

    rail = RLOnlineRail(
        session_id="s1",
        gateway_endpoint="http://gateway.local",
        tenant_id="user-1",
        uploader=_CollectingUploader(),
        lora_repo=_FakeLoRARepo(SimpleNamespace(version="v2", path="/tmp/lora/v2")),
        trajectory_span_processor=TrajectorySpanProcessor(),
    )
    agent = _Agent()
    ctx = AgentCallbackContext(agent=agent, inputs=ModelCallInputs())

    await rail.before_model_call(ctx)

    assert agent.react_agent.config.model_name == "base-model"


@pytest.mark.asyncio
async def test_rl_online_rail_background_evolution_uploads_batch():
    from openjiuwen.agent_evolving.agent_rl.online.rail.online_rail import RLOnlineRail

    uploader = _CollectingUploader()
    rail = RLOnlineRail(
        session_id="s1",
        gateway_endpoint="http://gateway.local",
        tenant_id="user-1",
        uploader=uploader,
        trajectory_span_processor=TrajectorySpanProcessor(),
    )

    await rail.run_evolution(_prepared(_trajectory(_span(1))))

    assert len(uploader.batches) == 1
    assert uploader.batches[0].tenant_id == "user-1"
    assert uploader.batches[0].samples[0].response_text == "hello"
    assert uploader.batches[0].trajectory_meta.extra[TRAJECTORY_SOURCE] == "rl_online"


@pytest.mark.asyncio
async def test_rl_online_rail_enriches_provider_nested_token_fields_immutably() -> None:
    rail = RLOnlineRail(
        session_id="s1",
        gateway_endpoint="http://gateway.local",
        tenant_id="user-1",
        uploader=_CollectingUploader(),
        trajectory_span_processor=TrajectorySpanProcessor(),
    )
    source = _trajectory(_span(1))
    response = {
        "choices": [
            {
                "prompt_token_ids": [1, 2, 3],
                "token_ids": [4, 5],
                "logprobs": [-0.4, -0.5],
            }
        ]
    }

    enriched = rail._enrich_latest_llm(source, response)

    attrs = span_attributes(next(iter(iter_spans(enriched))))
    assert attrs[RL_PROMPT_TOKEN_IDS] == [1, 2, 3]
    assert attrs[RL_COMPLETION_TOKEN_IDS] == [4, 5]
    assert attrs[RL_LOGPROBS] == [-0.4, -0.5]
    assert RL_PROMPT_TOKEN_IDS not in span_attributes(next(iter(iter_spans(source))))


@pytest.mark.asyncio
async def test_rl_online_rail_uploads_each_prepared_trajectory() -> None:
    uploader = _CollectingUploader()
    rail = RLOnlineRail(
        session_id="s1",
        gateway_endpoint="http://gateway.local",
        tenant_id="user-1",
        uploader=uploader,
        session_done_on_invoke_end=False,
        trajectory_span_processor=TrajectorySpanProcessor(),
    )

    await rail.run_evolution(_prepared(_trajectory(_span(1, prompt="q1", response="a1"))))
    await rail.run_evolution(_prepared(_trajectory(_span(2, prompt="q2", response="a2"))))

    assert len(uploader.batches) == 2
    assert [batch.samples[0].response_text for batch in uploader.batches] == ["a1", "a2"]


@pytest.mark.asyncio
async def test_rl_online_rail_keeps_full_prepared_trajectory() -> None:
    uploader = _CollectingUploader()
    rail = RLOnlineRail(
        session_id="s1",
        gateway_endpoint="http://gateway.local",
        tenant_id="user-1",
        uploader=uploader,
        trajectory_span_processor=TrajectorySpanProcessor(),
    )
    trajectory = _trajectory(*(_span(index, prompt=f"q{index}", response=f"a{index}") for index in range(201)))

    await rail.run_evolution(_prepared(trajectory))

    assert len(uploader.batches) == 1
    assert len(uploader.batches[0].samples) == 201
