"""Tests for the post-apply rail factory registry.

Covers ``openjiuwen.harness.factory.register_post_apply_rail_factory`` and the
extension point that ``apply_deep_agent_parts`` exposes for adapter-specific
rail injection (e.g. JiuwenSwarm's chat-team PermissionInterruptRail).
"""
from __future__ import annotations

from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from openjiuwen.core.single_agent.rail.base import AgentRail
from openjiuwen.harness.factory import (
    PostApplyRailFactory,
    _POST_APPLY_RAIL_FACTORIES,
    apply_deep_agent_parts,
    register_post_apply_rail_factory,
)


@pytest.fixture
def reset_registry():
    """Snapshot the post-apply rail factory registry and restore it after."""
    saved = list(_POST_APPLY_RAIL_FACTORIES)
    _POST_APPLY_RAIL_FACTORIES.clear()
    yield
    _POST_APPLY_RAIL_FACTORIES.clear()
    _POST_APPLY_RAIL_FACTORIES.extend(saved)


def _fake_parts(rails: list[Any] | None = None, config: Any | None = None) -> MagicMock:
    parts = MagicMock()
    parts.rails = rails or []
    parts.tool_cards = []
    parts.tool_instances = []
    parts.config = config
    return parts


def _fake_agent() -> MagicMock:
    agent = MagicMock()
    agent.configure = MagicMock()
    agent.ability_manager = MagicMock()
    return agent


def test_register_adds_factory_to_registry(reset_registry):
    factory: PostApplyRailFactory = lambda agent, parts: []
    register_post_apply_rail_factory(factory)
    assert factory in _POST_APPLY_RAIL_FACTORIES
    assert len(_POST_APPLY_RAIL_FACTORIES) == 1


def test_register_rejects_non_callable(reset_registry):
    with pytest.raises(TypeError):
        register_post_apply_rail_factory("not callable")  # type: ignore[arg-type]


def test_apply_invokes_factory_and_adds_returned_rails(reset_registry):
    rail_a = MagicMock(spec=AgentRail)
    rail_b = MagicMock(spec=AgentRail)

    factory_calls: list[Any] = []

    def factory(agent, parts):
        factory_calls.append((agent, parts))
        return [rail_a, rail_b]

    register_post_apply_rail_factory(cast(PostApplyRailFactory, factory))

    agent = _fake_agent()
    parts = _fake_parts()
    apply_deep_agent_parts(agent, parts)

    assert factory_calls == [(agent, parts)]
    agent.add_rail.assert_any_call(rail_a)
    agent.add_rail.assert_any_call(rail_b)


def test_factory_returning_none_or_empty_is_noop(reset_registry):
    called = []

    def factory(agent, parts):
        called.append(True)
        return None

    register_post_apply_rail_factory(factory)

    apply_deep_agent_parts(_fake_agent(), _fake_parts())

    assert called == [True]
    # add_rail is not called for parts.rails (empty) and not for factories (None)
    agent = _fake_agent()
    apply_deep_agent_parts(agent, _fake_parts())
    agent.add_rail.assert_not_called()


def test_factory_returning_none_rail_skips_silently(reset_registry):
    good_rail = MagicMock(spec=AgentRail)

    def factory(agent, parts):
        return [None, good_rail, None]

    register_post_apply_rail_factory(cast(PostApplyRailFactory, factory))

    agent = _fake_agent()
    apply_deep_agent_parts(agent, _fake_parts())

    agent.add_rail.assert_called_once_with(good_rail)


def test_factory_exception_is_caught_and_other_factories_still_run(reset_registry, caplog):
    def bad_factory(agent, parts):
        raise RuntimeError("boom")

    good_rail = MagicMock(spec=AgentRail)

    def good_factory(agent, parts):
        return [good_rail]

    register_post_apply_rail_factory(cast(PostApplyRailFactory, bad_factory))
    register_post_apply_rail_factory(cast(PostApplyRailFactory, good_factory))

    agent = _fake_agent()
    # Must not raise
    apply_deep_agent_parts(agent, _fake_parts())

    agent.add_rail.assert_called_once_with(good_rail)
    assert "post-apply rail factory" in caplog.text
    assert "boom" in caplog.text


def test_factory_sees_post_rails_state(reset_registry):
    """Factory is called AFTER parts.rails have been queued on the agent."""
    pre_rail = MagicMock(spec=AgentRail)

    pending_rails: list[Any] = []
    factory_seen_rails: list[Any] = []

    agent = _fake_agent()

    def track_add(rail):
        pending_rails.append(rail)
        agent._pending_rails = pending_rails

    agent.add_rail.side_effect = track_add

    def factory(agent, parts):
        factory_seen_rails.extend(getattr(agent, "_pending_rails", []) or [])
        return []

    register_post_apply_rail_factory(cast(PostApplyRailFactory, factory))

    parts = _fake_parts(rails=[pre_rail])
    apply_deep_agent_parts(agent, parts)

    assert pre_rail in pending_rails
    assert pre_rail in factory_seen_rails
    assert factory_seen_rails == pending_rails


def test_public_api_exports_register_function():
    from openjiuwen.harness import register_post_apply_rail_factory as exported
    from openjiuwen.harness.factory import register_post_apply_rail_factory as factory_fn

    assert exported is factory_fn


def test_harness_all_includes_register_function():
    import openjiuwen.harness as harness_pkg

    assert "register_post_apply_rail_factory" in harness_pkg.__all__