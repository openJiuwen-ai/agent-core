# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests that registration keeps ``ToolCard.exposure`` runtime metadata."""

from __future__ import annotations

from openjiuwen.core.foundation.tool import ToolCard, ToolExposure
from openjiuwen.core.single_agent.ability_manager import AbilityManager


def test_ability_manager_registration_preserves_card_exposure():
    manager = AbilityManager(owner_id="exposure-test-agent")
    card = ToolCard(
        id="deferred-tool",
        name="deferred_tool",
        exposure=ToolExposure.DEFERRED,
    )

    result = manager.add(card)

    assert result.added is True
    registered = manager.get("deferred_tool")
    assert registered is card
    assert registered.exposure is ToolExposure.DEFERRED


def test_progressive_registration_defaults_to_deferred_but_tool_search_is_direct():
    manager = AbilityManager(owner_id="progressive-agent")
    manager.set_tool_exposure_policy(True, direct_tool_names={"tool_search"})

    ordinary = ToolCard(id="ordinary", name="ordinary")
    search = ToolCard(id="search", name="tool_search")
    explicit_direct = ToolCard(
        id="direct",
        name="direct",
        exposure=ToolExposure.DIRECT,
    )

    assert manager.add(ordinary).added is True
    assert manager.add(search).added is True
    assert manager.add(explicit_direct).added is True

    assert manager.get("ordinary").exposure is ToolExposure.DEFERRED
    assert manager.get("tool_search").exposure is ToolExposure.DIRECT
    assert manager.get("direct").exposure is ToolExposure.DIRECT


def test_non_progressive_registration_defaults_to_direct():
    manager = AbilityManager(owner_id="normal-agent")
    manager.set_tool_exposure_policy(False, direct_tool_names={"tool_search"})
    card = ToolCard(id="ordinary", name="ordinary")

    assert manager.add(card).added is True
    assert manager.get("ordinary").exposure is ToolExposure.DIRECT


def test_dynamic_registration_changes_registry_revision():
    manager = AbilityManager(owner_id="revision-agent")
    manager.set_tool_exposure_policy(True)

    assert manager.add(ToolCard(id="deferred", name="deferred")).added is True
    registry_revision = manager.registry_revision
    assert manager.add(
        ToolCard(id="direct", name="direct", exposure=ToolExposure.DIRECT)
    ).added is True
    assert manager.registry_revision == registry_revision + 1


def test_shared_card_exposure_is_fixed_on_first_registration():
    card = ToolCard(id="shared", name="shared")
    progressive = AbilityManager(owner_id="progressive")
    progressive.set_tool_exposure_policy(True)

    assert progressive.add(card).added is True
    assert card.exposure is ToolExposure.DEFERRED

    # Exposure belongs to the ToolCard. Reusing the same card instance in a
    # second manager keeps that registration-time decision; callers that need a
    # different policy should provide a separate card or an explicit value.
    normal = AbilityManager(owner_id="normal")
    normal.set_tool_exposure_policy(False)
    assert normal.add(card).added is True
    assert card.exposure is ToolExposure.DEFERRED


def test_tool_card_exposure_marker_uses_public_accessors():
    card = ToolCard(id="marker", name="marker")

    assert card.get_exposure_declared() is None
    card.set_exposure_declared(True)
    assert card.get_exposure_declared() is True


def test_ability_manager_revision_changes_only_when_registry_changes():
    manager = AbilityManager(owner_id="revision-test-agent")
    first = ToolCard(id="first", name="first")
    duplicate_id = ToolCard(id="different", name="first")

    assert manager.registry_revision == 0
    assert manager.add(first).added is True
    assert manager.registry_revision == 1

    # A conflicting name is rejected and must not invalidate a cached index.
    assert manager.add(duplicate_id).added is False
    assert manager.registry_revision == 1

    assert manager.remove("first") is first
    assert manager.registry_revision == 2
    assert manager.remove("first") is None
    assert manager.registry_revision == 2
