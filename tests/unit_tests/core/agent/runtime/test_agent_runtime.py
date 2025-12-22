import pytest

from openjiuwen.core.single_agent import AgentConfig, AgentSession
from openjiuwen.core.session import Config
from openjiuwen.core.session import AgentInterrupt

pytestmark = pytest.mark.asyncio


async def test_agent_checkpoint():
    session_id = "test"
    config = Config()
    config.set_agent_config(AgentConfig(id="test_agent_checkpoint"))
    agent_session = AgentSession(config)

    # round 1
    session = await agent_session.pre_run(session_id=session_id)
    assert session.get_state("a") is None
    session.update_state({"a": 1})
    try:
        await session.interact("feedback")
    except AgentInterrupt as e:
        assert e.message == "feedback"

    # round 2
    session2 = await agent_session.pre_run(session_id=session_id)
    assert session2.get_state("a") == 1
    session2.update_state({"a": 2})

    # round 3
    session3 = await agent_session.pre_run(session_id=session_id)
    assert session3.get_state("a") == 1
    session3.update_state({"a": 3})
    await session3.post_run()

    # round 4
    session4 = await agent_session.pre_run(session_id=session_id)
    assert session4.get_state("a") == 3
    await agent_session.release(session_id)

    # round 5
    session5 = await agent_session.pre_run(session_id=session_id)
    assert session5.get_state("a") is None