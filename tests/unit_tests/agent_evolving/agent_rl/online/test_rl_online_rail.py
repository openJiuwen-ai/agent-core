from __future__ import annotations

from types import SimpleNamespace

import pytest

from openjiuwen.agent_evolving.trajectory import LLMCallDetail, TrajectoryStep, trajectory_from_steps
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, InvokeInputs, ModelCallInputs


class _CollectingUploader:
    def __init__(self) -> None:
        self.batches = []

    async def enqueue(self, batch):
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


@pytest.mark.asyncio
async def test_rl_online_rail_before_invoke_enables_token_capture():
    from openjiuwen.agent_evolving.agent_rl.online.rail.online_rail import RLOnlineRail

    rail = RLOnlineRail(
        session_id="s1",
        gateway_endpoint="http://gateway.local",
        tenant_id="user-1",
        uploader=_CollectingUploader(),
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
    )
    trajectory = trajectory_from_steps(
        execution_id="traj-1",
        session_id="s1",
        steps=[
            TrajectoryStep(
                kind="llm",
                detail=LLMCallDetail(
                    model="m1",
                    messages=[{"role": "user", "content": "hi"}],
                    response={"role": "assistant", "content": "hello"},
                ),
            )
        ],
        source="rl_online",
    )

    await rail._safe_run_evolution({"trajectory": trajectory})

    assert len(uploader.batches) == 1
    assert uploader.batches[0].tenant_id == "user-1"
    assert uploader.batches[0].samples[0].response_text == "hello"


@pytest.mark.asyncio
async def test_rl_online_rail_keeps_one_invoke_per_uploaded_batch():
    from openjiuwen.agent_evolving.agent_rl.online.rail.online_rail import RLOnlineRail

    uploader = _CollectingUploader()
    rail = RLOnlineRail(
        session_id="s1",
        gateway_endpoint="http://gateway.local",
        tenant_id="user-1",
        uploader=uploader,
        async_evolution=False,
    )

    first_invoke = InvokeInputs(query="q1", conversation_id="same-session")
    await rail.before_invoke(AgentCallbackContext(agent=_Agent(), inputs=first_invoke))
    await rail.after_model_call(AgentCallbackContext(
        agent=_Agent(),
        inputs=ModelCallInputs(
            messages=[{"role": "user", "content": "q1"}],
            response={"role": "assistant", "content": "a1"},
        ),
    ))
    await rail.after_invoke(AgentCallbackContext(agent=_Agent(), inputs=first_invoke))

    second_invoke = InvokeInputs(query="q2", conversation_id="same-session")
    await rail.before_invoke(AgentCallbackContext(agent=_Agent(), inputs=second_invoke))
    await rail.after_model_call(AgentCallbackContext(
        agent=_Agent(),
        inputs=ModelCallInputs(
            messages=[{"role": "user", "content": "q2"}],
            response={"role": "assistant", "content": "a2"},
        ),
    ))
    await rail.after_invoke(AgentCallbackContext(agent=_Agent(), inputs=second_invoke))

    assert len(uploader.batches) == 2
    assert [len(batch.samples) for batch in uploader.batches] == [1, 1]
    assert uploader.batches[1].samples[0].response_text == "a2"


@pytest.mark.asyncio
async def test_rl_online_rail_uploads_extracted_token_fields():
    from openjiuwen.agent_evolving.agent_rl.online.rail.online_rail import RLOnlineRail

    uploader = _CollectingUploader()
    rail = RLOnlineRail(
        session_id="s1",
        gateway_endpoint="http://gateway.local",
        tenant_id="user-1",
        uploader=uploader,
        async_evolution=False,
    )

    invoke = InvokeInputs(query="q", conversation_id="same-session")
    await rail.before_invoke(AgentCallbackContext(agent=_Agent(), inputs=invoke))
    await rail.after_model_call(AgentCallbackContext(
        agent=_Agent(),
        inputs=ModelCallInputs(
            messages=[{"role": "user", "content": "q"}],
            response={
                "role": "assistant",
                "content": "a",
                "choices": [
                    {
                        "prompt_token_ids": [1, 2, 3],
                        "token_ids": [4, 5],
                        "logprobs": [-0.4, -0.5],
                    }
                ],
            },
        ),
    ))
    await rail.after_invoke(AgentCallbackContext(agent=_Agent(), inputs=invoke))

    assert len(uploader.batches) == 1
    sample = uploader.batches[0].samples[0]
    assert sample.prompt_ids == [1, 2, 3]
    assert sample.response_tokens == [4, 5]
    assert sample.logprobs == [-0.4, -0.5]
    assert sample.meta["turn_id"] == 0
    assert sample.meta["source"] == "rl_online"
    assert sample.meta["tenant_id"] == "user-1"


@pytest.mark.asyncio
async def test_rl_online_rail_uploads_full_single_invoke_batch():
    from openjiuwen.agent_evolving.agent_rl.online.rail.online_rail import RLOnlineRail

    uploader = _CollectingUploader()
    rail = RLOnlineRail(
        session_id="s1",
        gateway_endpoint="http://gateway.local",
        tenant_id="user-1",
        uploader=uploader,
        async_evolution=False,
    )

    invoke = InvokeInputs(query="q", conversation_id="same-session")
    await rail.before_invoke(AgentCallbackContext(agent=_Agent(), inputs=invoke))
    for index in range(201):
        await rail.after_model_call(AgentCallbackContext(
            agent=_Agent(),
            inputs=ModelCallInputs(
                messages=[{"role": "user", "content": f"q{index}"}],
                response={"role": "assistant", "content": f"a{index}"},
            ),
        ))
    await rail.after_invoke(AgentCallbackContext(agent=_Agent(), inputs=invoke))

    assert len(uploader.batches) == 1
    assert len(uploader.batches[0].samples) == 201
    assert uploader.batches[0].samples[0].response_text == "a0"
