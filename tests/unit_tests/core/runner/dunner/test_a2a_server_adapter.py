# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import pytest

from openjiuwen.core.runner.drunner import server_adapter as server_adapter_module
from openjiuwen.core.runner.drunner.server_adapter import create_server_adapter
from openjiuwen.core.runner.drunner.server_adapter.agent_adapter import AgentAdapter
from openjiuwen.core.runner.runner_config import RunnerConfig, set_runner_config
from openjiuwen.extensions.a2a.a2a_server_adapter import A2AServerAdapter
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


class TestA2AServerAdapter:
    @pytest.fixture(autouse=True)
    def reset_runner_config(self):
        set_runner_config(RunnerConfig())
        yield
        set_runner_config(RunnerConfig())

    def test_agent_adapter_should_skip_a2a_server_adapter_when_disabled(self, monkeypatch):
        created = {}

        class FakeMqServerAdapter:
            def __init__(self, adapter_id, topic, invoke_handler, stream_handler):
                created["mq"] = True

            def start(self):
                created["mq_started"] = True

            async def stop(self):
                created["mq_stopped"] = True

        def fake_create_server_adapter(protocol, **kwargs):
            created["a2a"] = {"protocol": protocol, **kwargs}
            return object()

        monkeypatch.setattr(
            "openjiuwen.core.runner.drunner.server_adapter.agent_adapter.MqServerAdapter",
            FakeMqServerAdapter,
        )
        monkeypatch.setattr(
            "openjiuwen.core.runner.drunner.server_adapter.agent_adapter.create_server_adapter",
            fake_create_server_adapter,
        )

        adapter = AgentAdapter(agent_id="agent-0")
        adapter.start()

        assert created["mq_started"] is True
        assert "a2a" not in created
        assert adapter.a2a_server is None

    def test_agent_adapter_should_create_a2a_server_adapter(self, monkeypatch):
        created = {}

        class FakeMqServerAdapter:
            def __init__(self, adapter_id, topic, invoke_handler, stream_handler):
                created["mq"] = {
                    "adapter_id": adapter_id,
                    "topic": topic,
                    "invoke_handler": invoke_handler,
                    "stream_handler": stream_handler,
                }

            def start(self):
                created["mq_started"] = True

            async def stop(self):
                created["mq_stopped"] = True

        class FakeA2AServerAdapter:
            def start(self):
                created["a2a_started"] = True

            async def stop(self):
                created["a2a_stopped"] = True

        def fake_create_server_adapter(protocol, **kwargs):
            created["a2a"] = {
                "protocol": protocol,
                **kwargs,
            }
            return FakeA2AServerAdapter()

        monkeypatch.setattr(
            "openjiuwen.core.runner.drunner.server_adapter.agent_adapter.MqServerAdapter",
            FakeMqServerAdapter,
        )
        monkeypatch.setattr(
            "openjiuwen.core.runner.drunner.server_adapter.agent_adapter.create_server_adapter",
            fake_create_server_adapter,
        )

        set_runner_config(RunnerConfig(enable_a2a=True))
        adapter = AgentAdapter(agent_id="agent-1", agent_card=AgentCard(id="agent-1"))
        adapter.start()

        assert created["mq"]["adapter_id"] == "agent-1"
        assert created["a2a"]["protocol"] == "A2A"
        assert created["a2a"]["adapter_id"] == "agent-1"
        assert created["a2a"]["agent_card"].id == "agent-1"
        assert "interface_url" not in created["a2a"]
        assert created["a2a_started"] is True

    def test_agent_adapter_should_require_agent_card_when_a2a_enabled(self):
        set_runner_config(RunnerConfig(enable_a2a=True))
        with pytest.raises(ValueError, match="agent_card is required when enable_a2a is True"):
            AgentAdapter(agent_id="agent-a2a")

    def test_agent_adapter_should_not_pass_interface_url_to_create_server_adapter(self, monkeypatch):
        created = {}

        class FakeMqServerAdapter:
            def __init__(self, adapter_id, topic, invoke_handler, stream_handler):
                created["mq"] = True

            def start(self):
                created["mq_started"] = True

            async def stop(self):
                created["mq_stopped"] = True

        class FakeA2AServerAdapter:
            def start(self):
                created["a2a_started"] = True

            async def stop(self):
                created["a2a_stopped"] = True

        def fake_create_server_adapter(protocol, **kwargs):
            created["a2a"] = {
                "protocol": protocol,
                **kwargs,
            }
            return FakeA2AServerAdapter()

        monkeypatch.setattr(
            "openjiuwen.core.runner.drunner.server_adapter.agent_adapter.MqServerAdapter",
            FakeMqServerAdapter,
        )
        monkeypatch.setattr(
            "openjiuwen.core.runner.drunner.server_adapter.agent_adapter.create_server_adapter",
            fake_create_server_adapter,
        )

        set_runner_config(RunnerConfig(enable_a2a=True))
        url = "http://127.0.0.1:8000/a2a/jsonrpc"
        adapter = AgentAdapter(
            agent_id="agent-1",
            agent_card=AgentCard(id="agent-1", interface_url=url),
        )
        adapter.start()

        assert "interface_url" not in created["a2a"]
        assert created["a2a"]["agent_card"].interface_url == url
        assert adapter.interface_url == url
        assert created["a2a"]["agent_card"].id == "agent-1"
        assert created["a2a"]["protocol"] == "A2A"
        assert created["a2a_started"] is True

    def test_agent_adapter_should_use_interface_url_from_agent_card(
        self, monkeypatch
    ):
        created = {}

        class FakeMqServerAdapter:
            def __init__(self, adapter_id, topic, invoke_handler, stream_handler):
                pass

            def start(self):
                pass

            async def stop(self):
                pass

        class FakeA2AServerAdapter:
            def start(self):
                pass

            async def stop(self):
                pass

        def fake_create_server_adapter(protocol, **kwargs):
            created["a2a"] = kwargs
            return FakeA2AServerAdapter()

        monkeypatch.setattr(
            "openjiuwen.core.runner.drunner.server_adapter.agent_adapter.MqServerAdapter",
            FakeMqServerAdapter,
        )
        monkeypatch.setattr(
            "openjiuwen.core.runner.drunner.server_adapter.agent_adapter.create_server_adapter",
            fake_create_server_adapter,
        )

        set_runner_config(RunnerConfig(enable_a2a=True))
        url = "http://127.0.0.1:8010/a2a/jsonrpc/"
        adapter = AgentAdapter(
            agent_id="agent-1",
            agent_card=AgentCard(id="agent-1", interface_url=url),
        )
        adapter.start()

        assert "interface_url" not in created["a2a"]
        assert created["a2a"]["agent_card"].interface_url == url

    def test_a2a_server_adapter_should_infer_jsonrpc_protocol_binding(self, monkeypatch):
        created = {}

        class FakeA2AServer:
            def __init__(self, adapter_id, **kwargs):
                created["server"] = {
                    "adapter_id": adapter_id,
                    **kwargs,
                }

        monkeypatch.setattr(
            "openjiuwen.extensions.a2a.a2a_server_adapter.A2AServer",
            FakeA2AServer,
        )

        A2AServerAdapter(
            adapter_id="agent-1",
            agent_card=AgentCard(id="agent-1"),
            interface_url="http://127.0.0.1:8000/a2a/jsonrpc",
        )

        assert created["server"]["protocol_binding"] == "JSONRPC"

    def test_a2a_server_adapter_should_reject_grpc_interface_url(self):
        with pytest.raises(ValueError, match="gRPC transport is not supported"):
            A2AServerAdapter(
                adapter_id="agent-1",
                agent_card=AgentCard(id="agent-1"),
                interface_url="http://127.0.0.1:8000/a2a/grpc",
            )

    def test_create_server_adapter_should_bootstrap_a2a_registration_without_preimport(self, monkeypatch):
        registry_snapshot = dict(server_adapter_module._CUSTOM_SERVER_ADAPTERS)
        server_adapter_module._CUSTOM_SERVER_ADAPTERS.clear()

        captured = {}

        class FakeA2AServerAdapter:
            def __init__(self, **kwargs):
                captured["init_kwargs"] = kwargs

        def fake_import_module(name):
            assert name == "openjiuwen.extensions.a2a"
            server_adapter_module.register_server_adapter(
                "A2A", lambda **kwargs: FakeA2AServerAdapter(**kwargs)
            )
            return object()

        monkeypatch.setattr(server_adapter_module.importlib, "import_module", fake_import_module)
        monkeypatch.setattr(
            server_adapter_module,
            "_resolve_entry_point",
            lambda protocol, kwargs: (_ for _ in ()).throw(AssertionError("entry point fallback should not be used")),
        )

        try:
            adapter = create_server_adapter(
                "A2A",
                adapter_id="agent-bootstrap",
                agent_card=AgentCard(id="agent-bootstrap"),
                interface_url="http://127.0.0.1:8000/a2a/jsonrpc",
            )
            assert "A2A" in server_adapter_module._CUSTOM_SERVER_ADAPTERS
        finally:
            server_adapter_module._CUSTOM_SERVER_ADAPTERS.clear()
            server_adapter_module._CUSTOM_SERVER_ADAPTERS.update(registry_snapshot)

        assert adapter is not None
        assert captured["init_kwargs"]["adapter_id"] == "agent-bootstrap"
        assert captured["init_kwargs"]["agent_card"].id == "agent-bootstrap"
        assert captured["init_kwargs"]["interface_url"] == "http://127.0.0.1:8000/a2a/jsonrpc"

    @pytest.mark.asyncio
    async def test_agent_adapter_stop_should_stop_both_adapters(self, monkeypatch):
        created = {}

        class FakeMqServerAdapter:
            def __init__(self, adapter_id, topic, invoke_handler, stream_handler):
                created["mq"] = True

            def start(self):
                created["mq_started"] = True

            async def stop(self):
                created["mq_stopped"] = True

        class FakeA2AServerAdapter:
            def start(self):
                created["a2a_started"] = True

            async def stop(self):
                created["a2a_stopped"] = True

        def fake_create_server_adapter(protocol, **kwargs):
            created["a2a"] = {"protocol": protocol, **kwargs}
            return FakeA2AServerAdapter()

        monkeypatch.setattr(
            "openjiuwen.core.runner.drunner.server_adapter.agent_adapter.MqServerAdapter",
            FakeMqServerAdapter,
        )
        monkeypatch.setattr(
            "openjiuwen.core.runner.drunner.server_adapter.agent_adapter.create_server_adapter",
            fake_create_server_adapter,
        )

        set_runner_config(RunnerConfig(enable_a2a=True))
        adapter = AgentAdapter(agent_id="agent-2", agent_card=AgentCard(id="agent-2"))
        adapter.start()
        await adapter.stop()

        assert created["mq_stopped"] is True
        assert created["a2a_stopped"] is True
