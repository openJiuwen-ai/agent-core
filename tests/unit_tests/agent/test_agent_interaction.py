import pytest

from jiuwen.agent.config.base import AgentConfig
from jiuwen.core.agent.task.task_context import AgentRuntime
from jiuwen.core.runtime.config import Config
from jiuwen.core.runtime.interaction.base import AgentInterrupt

pytestmark = pytest.mark.asyncio


async def test_agent_checkpoint():
    session_id = "test"
    config = Config()
    config.set_agent_config(AgentConfig(id="test_agent_checkpoint"))
    agent_runtime = AgentRuntime(config=config)

    # round 1
    runtime = await agent_runtime.pre_run(session_id=session_id)
    assert runtime.get_state("a") is None
    runtime.update_state({"a": 1})
    try:
        await runtime.interact("feedback")
    except AgentInterrupt as e:
        assert e.message == "feedback"

    # round 2
    runtime2 = await agent_runtime.pre_run(session_id=session_id)
    assert runtime2.get_state("a") == 1
    runtime2.update_state({"a": 2})

    # round 3
    runtime3 = await agent_runtime.pre_run(session_id=session_id)
    assert runtime3.get_state("a") == 1
    runtime3.update_state({"a": 3})
    await runtime3.post_run()

    # round 4
    runtime4 = await agent_runtime.pre_run(session_id=session_id)
    assert runtime4.get_state("a") == 3
    await agent_runtime.release(session_id)

    # round 5
    runtime5 = await agent_runtime.pre_run(session_id=session_id)
    assert runtime5.get_state("a") is None