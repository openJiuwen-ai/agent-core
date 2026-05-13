# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from openjiuwen.core.runner.resources_manager.agent_manager import AgentMgr
from openjiuwen.core.runner.runner_config import RunnerConfig, DistributedConfig, MessageQueueConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


class TestAgentManager:
    def test_add_agent_should_forward_interface_url_to_agent_adapter(self, monkeypatch):
        created = {}

        class FakeAgentAdapter:
            def __init__(self, agent_id, agent_card=None):
                created["agent_id"] = agent_id
                created["agent_card"] = agent_card

            def start(self):
                created["started"] = True

            def stop(self):
                created["stopped"] = True

        monkeypatch.setattr(
            "openjiuwen.core.runner.drunner.server_adapter.agent_adapter.AgentAdapter",
            FakeAgentAdapter,
        )
        monkeypatch.setattr(
            "openjiuwen.core.runner.resources_manager.agent_manager.get_runner_config",
            lambda: RunnerConfig(
                distributed_mode=True,
                enable_a2a=True,
                distributed_config=DistributedConfig(
                    message_queue_config=MessageQueueConfig(type="fake"),
                ),
            ),
        )

        mgr = AgentMgr()
        mgr.add_agent(
            "agent-x",
            lambda: object(),
            card=AgentCard(id="agent-x"),
            interface_url="http://127.0.0.1:9000/a2a/jsonrpc",
        )

        assert created["agent_id"] == "agent-x"
        assert created["agent_card"].id == "agent-x"
        assert created["agent_card"].interface_url == "http://127.0.0.1:9000/a2a/jsonrpc"
        assert created["started"] is True

    def test_add_agent_should_pass_none_interface_url_when_omitted(self, monkeypatch):
        created = {}

        class FakeAgentAdapter:
            def __init__(self, agent_id, agent_card=None):
                created["agent_id"] = agent_id
                created["agent_card"] = agent_card

            def start(self):
                created["started"] = True

            def stop(self):
                created["stopped"] = True

        monkeypatch.setattr(
            "openjiuwen.core.runner.drunner.server_adapter.agent_adapter.AgentAdapter",
            FakeAgentAdapter,
        )
        monkeypatch.setattr(
            "openjiuwen.core.runner.resources_manager.agent_manager.get_runner_config",
            lambda: RunnerConfig(
                distributed_mode=True,
                enable_a2a=True,
                distributed_config=DistributedConfig(
                    message_queue_config=MessageQueueConfig(type="fake"),
                ),
            ),
        )

        mgr = AgentMgr()
        mgr.add_agent("agent-y", lambda: object(), card=AgentCard(id="agent-y"))

        assert created["agent_id"] == "agent-y"
        assert created["agent_card"].id == "agent-y"
        assert created["agent_card"].interface_url is None
        assert created["started"] is True

    def test_add_agent_should_use_agent_card_interface_url_when_kwarg_omitted(self, monkeypatch):
        created = {}

        class FakeAgentAdapter:
            def __init__(self, agent_id, agent_card=None):
                created["agent_id"] = agent_id
                created["agent_card"] = agent_card

            def start(self):
                created["started"] = True

            def stop(self):
                created["stopped"] = True

        monkeypatch.setattr(
            "openjiuwen.core.runner.drunner.server_adapter.agent_adapter.AgentAdapter",
            FakeAgentAdapter,
        )
        monkeypatch.setattr(
            "openjiuwen.core.runner.resources_manager.agent_manager.get_runner_config",
            lambda: RunnerConfig(
                distributed_mode=True,
                enable_a2a=True,
                distributed_config=DistributedConfig(
                    message_queue_config=MessageQueueConfig(type="fake"),
                ),
            ),
        )

        card_url = "http://127.0.0.1:7001/a2a/jsonrpc/"
        mgr = AgentMgr()
        mgr.add_agent(
            "agent-z",
            lambda: object(),
            card=AgentCard(id="agent-z", interface_url=card_url),
        )

        assert created["agent_id"] == "agent-z"
        assert created["agent_card"].interface_url == card_url

    def test_add_agent_interface_url_kwarg_should_override_agent_card(self, monkeypatch):
        created = {}

        class FakeAgentAdapter:
            def __init__(self, agent_id, agent_card=None):
                created["agent_card"] = agent_card

            def start(self):
                pass

            def stop(self):
                pass

        monkeypatch.setattr(
            "openjiuwen.core.runner.drunner.server_adapter.agent_adapter.AgentAdapter",
            FakeAgentAdapter,
        )
        monkeypatch.setattr(
            "openjiuwen.core.runner.resources_manager.agent_manager.get_runner_config",
            lambda: RunnerConfig(
                distributed_mode=True,
                enable_a2a=True,
                distributed_config=DistributedConfig(
                    message_queue_config=MessageQueueConfig(type="fake"),
                ),
            ),
        )

        mgr = AgentMgr()
        mgr.add_agent(
            "agent-o",
            lambda: object(),
            card=AgentCard(id="agent-o", interface_url="http://127.0.0.1:7002/a2a/jsonrpc/"),
            interface_url="http://127.0.0.1:9999/a2a/jsonrpc/",
        )

        assert created["agent_card"].interface_url == "http://127.0.0.1:9999/a2a/jsonrpc/"
