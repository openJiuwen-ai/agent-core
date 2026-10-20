# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Auto-attach of OtelRail via the core default-rail registry (issue #2086).

Orchestrated agents (workflow inline agents, multi-agent members) are
constructed without anyone calling ``register_rail``; after
``init_otel_tracer`` they must still emit chain events carrying
``gen_ai.agent.*`` identity, and single-agent mode's explicit
registration must not be duplicated.
"""

import pytest

from openjiuwen.core.single_agent import AgentCard, BaseAgent
from openjiuwen.core.single_agent.rail.base import AgentCallbackEvent
from openjiuwen.core.single_agent.rail.default_rails import default_rail_factories
from openjiuwen.extensions.tracer_otel.config import OtelTracerConfig
from openjiuwen.extensions.tracer_otel.otel_rail import OtelRail
from openjiuwen.extensions.tracer_otel.setup import AUTO_ATTACH_RAIL_ENV, init_otel_tracer
from tests.unit_tests.extensions.tracer_otel.test_otel_rail import _StubSession, _StubTracer


class _OrchestratedAgent(BaseAgent):
    """Agent a workflow/multi-agent orchestrator would build inline.

    Nobody registers rails on it — that is exactly the gap auto-attach
    closes.
    """

    def __init__(self):
        card = AgentCard(
            id="inline-agent-1",
            name="InlineAgent",
            description="Built by an orchestrator",
        )
        super().__init__(card)

    def configure(self, config) -> "_OrchestratedAgent":
        return self

    async def invoke(self, inputs, session=None):
        return inputs

    async def stream(self, inputs, session=None, stream_modes=None):
        yield inputs


def _console_config() -> OtelTracerConfig:
    return OtelTracerConfig(exporter_type="console")


class TestFactoryRegistration:
    def test_init_registers_otel_rail_factory(self):
        init_otel_tracer(_console_config())

        assert OtelRail in default_rail_factories()

    def test_reinit_registers_factory_once(self):
        init_otel_tracer(_console_config())
        init_otel_tracer(_console_config())

        assert default_rail_factories().count(OtelRail) == 1

    @pytest.mark.parametrize("off", ["0", "false", "no", "off", "OFF", " false "])
    def test_env_off_disables_registration(self, monkeypatch, off):
        monkeypatch.setenv(AUTO_ATTACH_RAIL_ENV, off)

        init_otel_tracer(_console_config())

        assert default_rail_factories() == ()

    def test_env_unknown_value_stays_on(self, monkeypatch):
        """Default-on flag: only an explicit falsy value turns it off."""
        monkeypatch.setenv(AUTO_ATTACH_RAIL_ENV, "banana")

        init_otel_tracer(_console_config())

        assert OtelRail in default_rail_factories()

    def test_failed_init_registers_nothing(self):
        from openjiuwen.core.common.exception.errors import BaseError

        with pytest.raises(BaseError, match="exporter_type"):
            init_otel_tracer(OtelTracerConfig(exporter_type="invalid"))

        assert default_rail_factories() == ()


@pytest.mark.asyncio
class TestAutoAttachedRailFires:
    async def test_orchestrated_agent_emits_chain_event_with_identity(self):
        """The bug from issue #2086: an agent nobody registered rails on
        still raises on_chain_start with its card identity."""
        init_otel_tracer(_console_config())
        tracer = _StubTracer()
        session = _StubSession(tracer)
        agent = _OrchestratedAgent()

        await agent._execute_callbacks(AgentCallbackEvent.BEFORE_INVOKE, inputs={}, session=session)

        events = [call[1] for call in tracer.calls]
        assert "on_chain_start" in events
        info = tracer.calls[0][2]["instance_info"]
        assert info["agent_id"] == "inline-agent-1"
        assert info["agent_name"] == "InlineAgent"

    async def test_explicit_registration_and_auto_attach_coexist_once(self):
        """Single-agent mode (react_agent_runner) registers OtelRail by
        hand; auto-attach must not emit a second chain event."""
        init_otel_tracer(_console_config())
        tracer = _StubTracer()
        session = _StubSession(tracer)
        agent = _OrchestratedAgent()
        await agent.register_rail(OtelRail())

        await agent._execute_callbacks(AgentCallbackEvent.BEFORE_INVOKE, inputs={}, session=session)

        chain_starts = [call for call in tracer.calls if call[1] == "on_chain_start"]
        assert len(chain_starts) == 1
