from __future__ import annotations

import pytest

from openjiuwen.agent_evolving.trajectory import LLMCallDetail, Trajectory, TrajectoryStep
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, InvokeInputs, ModelCallInputs


class _CollectingUploader:
    def __init__(self) -> None:
        self.batches = []

    async def enqueue(self, batch):
        self.batches.append(batch)


class _Config:
    llm_return_token_ids = False
    llm_logprobs = False
    llm_top_logprobs = 0
    custom_headers = None


class _Agent:
    class react_agent:
        config = _Config()


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
async def test_rl_online_rail_background_evolution_uploads_batch():
    from openjiuwen.agent_evolving.agent_rl.online.rail.online_rail import RLOnlineRail

    uploader = _CollectingUploader()
    rail = RLOnlineRail(
        session_id="s1",
        gateway_endpoint="http://gateway.local",
        tenant_id="user-1",
        uploader=uploader,
    )
    trajectory = Trajectory(
        execution_id="traj-1",
        session_id="s1",
        source="rl_online",
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
