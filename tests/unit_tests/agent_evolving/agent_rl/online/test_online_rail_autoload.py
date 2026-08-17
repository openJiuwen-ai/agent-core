from __future__ import annotations

import asyncio

from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.schema.config import DeepAgentConfig


def _enable_online_training(monkeypatch, *, backend: str = "") -> None:
    monkeypatch.setenv("USE_RL_ONLINE_RAIL", "1")
    monkeypatch.setenv("TRAJECTORY_GATEWAY_URL", "http://gateway.local")
    monkeypatch.setenv("RL_ONLINE_TENANT_ID", "user-1")
    if backend:
        monkeypatch.setenv("TRAIN_BACKEND", backend)


def test_deep_agent_queues_rl_online_rail_from_env(monkeypatch):
    from openjiuwen.agent_evolving.agent_rl.online.backends.rl.rail import RLOnlineRail

    _enable_online_training(monkeypatch, backend="PPO")

    agent = DeepAgent(AgentCard(name="deep", description="test"))
    agent.configure(DeepAgentConfig(enable_task_loop=False))

    rails = [rail for rail in agent.configured_rails() if isinstance(rail, RLOnlineRail)]
    assert len(rails) == 1


def test_deep_agent_queues_sft_online_rail_from_env(monkeypatch):
    from openjiuwen.agent_evolving.agent_rl.online.backends.sft.rail import SFTOnlineRail

    _enable_online_training(monkeypatch, backend="SFT")

    agent = DeepAgent(AgentCard(name="deep", description="test"))
    agent.configure(DeepAgentConfig(enable_task_loop=False))

    rails = [rail for rail in agent.configured_rails() if isinstance(rail, SFTOnlineRail)]
    assert len(rails) == 1


def test_deep_agent_skips_duplicate_online_training_rail(monkeypatch):
    from openjiuwen.agent_evolving.agent_rl.online.backends.sft.rail import SFTOnlineRail

    _enable_online_training(monkeypatch, backend="SFT")

    agent = DeepAgent(AgentCard(name="deep", description="test"))
    agent.configure(DeepAgentConfig(enable_task_loop=False))
    agent.configure(DeepAgentConfig(enable_task_loop=False))

    rails = [rail for rail in agent.configured_rails() if isinstance(rail, SFTOnlineRail)]
    assert len(rails) == 1


def test_create_deep_agent_does_not_queue_factory_rails_twice(monkeypatch):
    from openjiuwen.agent_evolving.agent_rl.online.backends.sft.rail import SFTOnlineRail
    from openjiuwen.core.foundation.llm.model import Model
    from openjiuwen.core.foundation.llm.schema.config import (
        ModelClientConfig,
        ModelRequestConfig,
        ProviderType,
    )
    from openjiuwen.harness.factory import create_deep_agent

    _enable_online_training(monkeypatch, backend="SFT")

    agent = create_deep_agent(
        Model(
            ModelClientConfig(
                api_key="EMPTY",
                api_base="http://gateway.local/v1",
                client_provider=ProviderType.OpenAI,
            ),
            model_config=ModelRequestConfig(model="test-model"),
        ),
        enable_security_rail=False,
    )

    rails = [rail for rail in agent.configured_rails() if isinstance(rail, SFTOnlineRail)]
    assert len(rails) == 1


def test_legacy_factory_entry_selects_sft_online_rail(monkeypatch):
    from openjiuwen.agent_evolving.agent_rl.online.core.rail_factory import build_online_rail_from_env
    from openjiuwen.agent_evolving.agent_rl.online.backends.sft.rail import SFTOnlineRail
    from openjiuwen.agent_evolving.trajectory.processor import TrajectorySpanProcessor

    _enable_online_training(monkeypatch, backend="SFT")

    rail = build_online_rail_from_env(trajectory_span_processor=TrajectorySpanProcessor())

    assert isinstance(rail, SFTOnlineRail)


def test_deep_agent_registers_env_rail_when_env_is_set_after_configure(monkeypatch):
    from openjiuwen.agent_evolving.agent_rl.online.backends.sft.rail import SFTOnlineRail

    agent = DeepAgent(AgentCard(name="deep", description="test"))
    agent.configure(DeepAgentConfig(enable_task_loop=False))
    assert not any(isinstance(rail, SFTOnlineRail) for rail in agent.configured_rails())

    _enable_online_training(monkeypatch, backend="SFT")
    asyncio.run(agent.ensure_initialized())

    rails = [rail for rail in agent.configured_rails() if isinstance(rail, SFTOnlineRail)]
    assert len(rails) == 1
