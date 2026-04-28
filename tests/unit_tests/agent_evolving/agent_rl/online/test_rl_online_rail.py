from __future__ import annotations

import pytest

from openjiuwen.agent_evolving.trajectory import LLMCallDetail, Trajectory, TrajectoryStep
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext


class _CollectingUploader:
    def __init__(self) -> None:
        self.batches = []

    async def enqueue(self, batch):
        self.batches.append(batch)


@pytest.mark.asyncio
async def test_rl_online_rail_before_invoke_enables_token_capture():
    from openjiuwen.agent_evolving.agent_rl.online.rail.online_rail import RLOnlineRail

    class _Config:
        llm_return_token_ids = False
        llm_logprobs = False
        llm_top_logprobs = 0
        custom_headers = None

    class _Agent:
        class react_agent:
            config = _Config()

    rail = RLOnlineRail(
        session_id="s1",
        gateway_endpoint="http://gateway.local",
        tenant_id="user-1",
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
