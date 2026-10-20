# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for the process-wide default rail factories."""

import pytest

from openjiuwen.core.single_agent import AgentCard, BaseAgent
from openjiuwen.core.single_agent.rail.base import AgentCallbackEvent, AgentRail
from openjiuwen.core.single_agent.rail.default_rails import (
    clear_default_rail_factories,
    default_rail_factories,
    register_default_rail_factory,
)


class _CallbackAgent(BaseAgent):
    """Minimal concrete agent exercising only callback plumbing."""

    def __init__(self, card_id: str = "default-rail-agent"):
        card = AgentCard(id=card_id)
        super().__init__(card)

    def configure(self, config) -> "_CallbackAgent":
        return self

    async def invoke(self, inputs, session=None):
        return inputs

    async def stream(self, inputs, session=None, stream_modes=None):
        yield inputs


class _RecordingRail(AgentRail):
    """Rail double counting init and hook invocations."""

    def __init__(self) -> None:
        self.init_calls = 0
        self.hook_calls = 0

    def init(self, agent) -> None:
        self.init_calls += 1

    async def before_invoke(self, ctx) -> None:
        self.hook_calls += 1


class _SilentRail(AgentRail):
    """Second rail type for ordering assertions; overrides no hooks."""


@pytest.fixture(autouse=True)
def _clean_factories():
    clear_default_rail_factories()
    yield
    clear_default_rail_factories()


class TestFactoryRegistry:
    def test_register_is_idempotent_by_identity(self):
        register_default_rail_factory(_RecordingRail)
        register_default_rail_factory(_RecordingRail)

        assert default_rail_factories() == (_RecordingRail,)

    def test_register_keeps_callables_and_order(self):
        def _factory():
            return _RecordingRail()

        register_default_rail_factory(_RecordingRail)
        register_default_rail_factory(_factory)

        assert default_rail_factories() == (_RecordingRail, _factory)

    def test_clear_empties_registry(self):
        register_default_rail_factory(_RecordingRail)
        clear_default_rail_factories()

        assert default_rail_factories() == ()

    def test_snapshot_is_immutable(self):
        register_default_rail_factory(_RecordingRail)

        snapshot = default_rail_factories()
        register_default_rail_factory(_SilentRail)

        assert _SilentRail not in snapshot


@pytest.mark.asyncio
class TestAutoAttach:
    async def test_first_callback_attaches_and_fires_on_same_event(self):
        """The rail must observe the very event that triggered the attach."""
        made = []

        def factory():
            rail = _RecordingRail()
            made.append(rail)
            return rail

        register_default_rail_factory(factory)
        agent = _CallbackAgent()
        assert agent._registered_rail_types == set()

        await agent._execute_callbacks(AgentCallbackEvent.BEFORE_INVOKE, inputs={})

        assert len(made) == 1
        assert made[0].init_calls == 1
        assert made[0].hook_calls == 1
        assert type(made[0]) in agent._registered_rail_types

    async def test_second_event_does_not_reattach(self):
        made = []

        def factory():
            rail = _RecordingRail()
            made.append(rail)
            return rail

        register_default_rail_factory(factory)
        agent = _CallbackAgent()

        await agent._execute_callbacks(AgentCallbackEvent.BEFORE_INVOKE, inputs={})
        await agent._execute_callbacks(AgentCallbackEvent.BEFORE_INVOKE, inputs={})

        assert len(made) == 1
        assert made[0].hook_calls == 2

    async def test_explicitly_registered_type_is_not_duplicated(self):
        """Single-agent mode registers OtelRail by hand; auto-attach must not
        stack a second rail of the same type on it."""
        made = []

        def factory():
            rail = _RecordingRail()
            made.append(rail)
            return rail

        register_default_rail_factory(factory)
        agent = _CallbackAgent()
        explicit = _RecordingRail()
        await agent.register_rail(explicit)

        await agent._execute_callbacks(AgentCallbackEvent.BEFORE_INVOKE, inputs={})

        assert made[0].hook_calls == 0
        assert explicit.hook_calls == 1
        assert explicit.init_calls == 1

    async def test_failing_factory_is_swallowed_and_later_factories_attach(self):
        def exploding_factory():
            raise ValueError("factory exploded")

        made = []

        def healthy_factory():
            rail = _RecordingRail()
            made.append(rail)
            return rail

        register_default_rail_factory(exploding_factory)
        register_default_rail_factory(healthy_factory)
        agent = _CallbackAgent()

        await agent._execute_callbacks(AgentCallbackEvent.BEFORE_INVOKE, inputs={})

        assert made[0].hook_calls == 1
        assert type(made[0]) in agent._registered_rail_types

    async def test_no_factories_callbacks_still_run(self):
        agent = _CallbackAgent()

        await agent._execute_callbacks(AgentCallbackEvent.BEFORE_INVOKE, inputs={})

        assert agent._registered_rail_types == set()

    async def test_register_and_unregister_maintain_type_set(self):
        agent = _CallbackAgent()
        rail = _RecordingRail()
        await agent.register_rail(rail)
        assert _RecordingRail in agent._registered_rail_types

        await agent.unregister_rail(rail)

        assert _RecordingRail not in agent._registered_rail_types
