from __future__ import annotations

from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.schema.config import DeepAgentConfig


def _enable_online_rl(monkeypatch) -> None:
    monkeypatch.setenv("USE_RL_ONLINE_RAIL", "1")
    monkeypatch.setenv("TRAJECTORY_GATEWAY_URL", "http://gateway.local")
    monkeypatch.setenv("RL_ONLINE_TENANT_ID", "user-1")


def test_deep_agent_queues_rl_online_rail_from_env(monkeypatch):
    from openjiuwen.agent_evolving.agent_rl.online.rail.online_rail import RLOnlineRail

    _enable_online_rl(monkeypatch)

    agent = DeepAgent(AgentCard(name="deep", description="test"))
    agent.configure(DeepAgentConfig(enable_task_loop=False))

    rails = [rail for rail in agent.configured_rails() if isinstance(rail, RLOnlineRail)]
    assert len(rails) == 1


def test_deep_agent_skips_duplicate_rl_online_rail(monkeypatch):
    from openjiuwen.agent_evolving.agent_rl.online.rail.online_rail import RLOnlineRail

    _enable_online_rl(monkeypatch)

    agent = DeepAgent(AgentCard(name="deep", description="test"))
    agent.configure(DeepAgentConfig(enable_task_loop=False))
    agent.configure(DeepAgentConfig(enable_task_loop=False))

    rails = [rail for rail in agent.configured_rails() if isinstance(rail, RLOnlineRail)]
    assert len(rails) == 1
