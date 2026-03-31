#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
import importlib
from typing import Any

import pytest
import pytest_asyncio

from openjiuwen.core.runner import Runner
from openjiuwen.core.runner import runner as runner_module
from openjiuwen.core.runner.spawn import (
    ClassAgentSpawnConfig,
    MessageType,
    SpawnConfig,
)
from openjiuwen.core.runner.spawn import child_process as child_process_module
from tests.unit_tests.core.runner.mock_agents import (
    MockSimpleAgent,
    MockStreamingAgent,
    MockLongRunningAgent,
    MockShutdownIgnoringAgent,
)


def _class_agent_config(agent_cls: type, **init_kwargs: Any) -> ClassAgentSpawnConfig:
    return ClassAgentSpawnConfig(
        agent_module=agent_cls.__module__,
        agent_class=agent_cls.__name__,
        init_kwargs=init_kwargs,
    )


class _DummyHandle:
    def __init__(self) -> None:
        self.health_check_started = False

    async def start_health_check(self) -> None:
        self.health_check_started = True


@pytest.fixture(scope="class")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def runner():
    await Runner.start()
    yield Runner
    await Runner.stop()


@pytest.mark.asyncio
class TestBasicSpawnAndCommunication:
    """Test basic spawn and async communication with mock agent."""

    async def test_spawn_agent_passes_logging_config_to_child(self, runner, monkeypatch):
        snapshot = {"backend": "loguru", "defaults": {"level": "INFO"}}
        captured: dict[str, Any] = {}
        handle = _DummyHandle()

        async def fake_spawn_process(*, agent_config, inputs, config):
            captured["agent_config"] = agent_config
            captured["inputs"] = inputs
            captured["config"] = config
            return handle

        monkeypatch.setattr(runner_module._RunnerImpl, "_get_spawn_logging_config", staticmethod(lambda: snapshot))
        monkeypatch.setattr(runner_module, "spawn_process", fake_spawn_process)

        result = await runner.spawn_agent(
            agent_config=_class_agent_config(MockSimpleAgent, sleep_time=0.1),
            inputs={"query": "test"},
        )

        assert result is handle
        assert captured["agent_config"]["logging_config"] == snapshot
        assert captured["agent_config"]["session_id"] == "default_session"
        assert handle.health_check_started is False

    async def test_prepare_spawn_agent_config_applies_logging_snapshot(self, monkeypatch):
        snapshot = {"backend": "loguru", "defaults": {"level": "DEBUG"}}
        applied_configs = []
        log_config_module = importlib.import_module("openjiuwen.core.common.logging.log_config")

        monkeypatch.setattr(log_config_module, "configure_log_config", applied_configs.append)

        agent_config = _class_agent_config(MockSimpleAgent).model_dump(mode="json")
        agent_config["logging_config"] = snapshot

        prepared = child_process_module._prepare_spawn_agent_config(agent_config)

        assert prepared is not None
        assert prepared.logging_config == snapshot
        assert applied_configs == [snapshot]

    async def test_spawn_simple_agent(self, runner):
        handle = await runner.spawn_agent(
            agent_config=_class_agent_config(
                MockSimpleAgent,
                sleep_time=0.1,
                output={"result": "test_output"},
            ),
            inputs={"query": "test"},
        )

        assert handle is not None
        assert handle.is_alive
        assert handle.pid is not None

        message = await handle.receive_message()
        assert message is not None
        assert message.type == MessageType.DONE

        exit_code = await handle.wait_for_completion()
        assert exit_code is not None
        assert not handle.is_alive

    async def test_spawn_agent_with_custom_output(self, runner):
        handle = await runner.spawn_agent(
            agent_config=_class_agent_config(
                MockSimpleAgent,
                sleep_time=0.05,
                output={"data": "custom"},
            ),
            inputs={"query": "test"},
        )

        message = await handle.receive_message()
        assert message is not None
        assert message.type == MessageType.DONE

        await handle.wait_for_completion()


@pytest.mark.asyncio
class TestAsyncStreamingCommunication:
    """Test async streaming communication."""

    async def test_spawn_streaming_agent(self, runner):
        received_chunks = []
        handle = None

        async for h, message in runner.spawn_agent_streaming(
            agent_config=_class_agent_config(
                MockStreamingAgent,
                chunks=[{"text": "chunk_1"}, {"text": "chunk_2"}, {"text": "chunk_3"}],
                sleep_between_chunks=0.02,
            ),
            inputs={"query": "test"},
        ):
            if handle is None:
                handle = h
                continue
            if message is not None:
                received_chunks.append(message)

        assert handle is not None
        assert len(received_chunks) > 0

        await handle.wait_for_completion()

    async def test_streaming_multiple_messages(self, runner):
        count = 0
        handle = None

        async for h, message in runner.spawn_agent_streaming(
            agent_config=_class_agent_config(
                MockStreamingAgent,
                chunks=[{"i": i} for i in range(5)],
                sleep_between_chunks=0.01,
            ),
            inputs={"query": "test"},
        ):
            if handle is None:
                handle = h
                continue
            if message is not None:
                count += 1

        assert count > 0

        if handle:
            await handle.wait_for_completion()


@pytest.mark.asyncio
class TestAsyncHealthCheck:
    """Test async health check mechanism."""

    async def test_health_check_enabled(self, runner):
        spawn_config = SpawnConfig(
            health_check_interval=0.2,
            health_check_timeout=1.0,
        )

        handle = await runner.spawn_agent(
            agent_config=_class_agent_config(
                MockLongRunningAgent,
                duration=2.0,
            ),
            inputs={"query": "test"},
            spawn_config=spawn_config,
        )

        assert handle.is_alive
        assert handle.is_healthy

        await asyncio.sleep(0.5)

        assert handle.is_healthy

        await handle.shutdown(timeout=2.0)

        assert not handle.is_alive

    async def test_health_check_passes_during_execution(self, runner):
        spawn_config = SpawnConfig(
            health_check_interval=0.1,
            health_check_timeout=0.5,
        )

        handle = await runner.spawn_agent(
            agent_config=_class_agent_config(
                MockSimpleAgent,
                sleep_time=0.3,
            ),
            inputs={"query": "test"},
            spawn_config=spawn_config,
        )

        await asyncio.sleep(0.2)

        assert handle.is_healthy

        await handle.wait_for_completion()


@pytest.mark.asyncio
class TestParentInitiatedGracefulShutdown:
    """Test async parent-initiated graceful shutdown."""

    async def test_graceful_shutdown(self, runner):
        spawn_config = SpawnConfig(
            shutdown_timeout=2.0,
        )

        handle = await runner.spawn_agent(
            agent_config=_class_agent_config(
                MockLongRunningAgent,
                duration=10.0,
            ),
            inputs={"query": "test"},
            spawn_config=spawn_config,
        )

        assert handle.is_alive

        await asyncio.sleep(0.3)

        graceful = await handle.shutdown(timeout=2.0)

        assert not handle.is_alive
        assert handle.exit_code is not None

    async def test_shutdown_with_ack(self, runner):
        handle = await runner.spawn_agent(
            agent_config=_class_agent_config(
                MockLongRunningAgent,
                duration=10.0,
            ),
            inputs={"query": "test"},
        )

        await asyncio.sleep(0.2)

        result = await handle.shutdown(timeout=3.0)

        assert not handle.is_alive


@pytest.mark.asyncio
class TestChildInitiatedGracefulExit:
    """Test async child-initiated graceful exit."""

    async def test_child_normal_exit(self, runner):
        handle = await runner.spawn_agent(
            agent_config=_class_agent_config(
                MockSimpleAgent,
                sleep_time=0.1,
                output={"status": "completed"},
            ),
            inputs={"query": "test"},
        )

        exit_code = await handle.wait_for_completion()

        assert exit_code is not None
        assert not handle.is_alive

    async def test_child_exit_with_result(self, runner):
        handle = await runner.spawn_agent(
            agent_config=_class_agent_config(
                MockSimpleAgent,
                sleep_time=0.05,
                output={"output": "success"},
            ),
            inputs={"query": "test"},
        )

        message = await handle.receive_message()

        assert message is not None
        assert message.type == MessageType.DONE

        await handle.wait_for_completion()


@pytest.mark.asyncio
class TestForceKillOnTimeout:
    """Test force kill on timeout scenario."""

    async def test_force_kill_after_timeout(self, runner):
        spawn_config = SpawnConfig(
            shutdown_timeout=0.5,
        )

        handle = await runner.spawn_agent(
            agent_config=_class_agent_config(
                MockShutdownIgnoringAgent,
                duration=60.0,
            ),
            inputs={"query": "test"},
            spawn_config=spawn_config,
        )

        assert handle.is_alive

        graceful = await handle.shutdown(timeout=0.5)

        assert not graceful
        assert not handle.is_alive

    async def test_force_kill_immediate(self, runner):
        handle = await runner.spawn_agent(
            agent_config=_class_agent_config(
                MockShutdownIgnoringAgent,
                duration=60.0,
            ),
            inputs={"query": "test"},
        )

        assert handle.is_alive

        await handle.force_kill()

        assert not handle.is_alive

    async def test_shutdown_timeout_triggers_force_terminate(self, runner):
        spawn_config = SpawnConfig(
            shutdown_timeout=0.3,
        )

        handle = await runner.spawn_agent(
            agent_config=_class_agent_config(
                MockShutdownIgnoringAgent,
                duration=60.0,
            ),
            inputs={"query": "test"},
            spawn_config=spawn_config,
        )

        await asyncio.sleep(0.1)

        result = await handle.shutdown(timeout=0.3)

        assert result is False
        assert not handle.is_alive
        assert handle.exit_code is not None
