# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for the identity a delegated sub-agent persists its state under."""

from __future__ import annotations

import re

from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.deep_agent import _sub_agent_card_for_session
from openjiuwen.harness.factory import _inject_general_purpose_subagent


def _card(name: str = "general-purpose") -> AgentCard:
    return AgentCard(name=name, description="desc")


def test_identity_survives_a_parent_rebuild() -> None:
    """A resumed delegation must find the state its first run wrote.

    The parent agent is rebuilt between the two runs, so a card minted per
    build strands the parked interruption state under an unreachable id.
    """
    first = _sub_agent_card_for_session(_card(), "parent_sub_general-purpose_ab12cd34")
    second = _sub_agent_card_for_session(_card(), "parent_sub_general-purpose_ab12cd34")

    assert first.id == second.id


def test_identity_separates_distinct_sub_sessions() -> None:
    """Concurrent delegations keep the isolation the random id gave them."""
    first = _sub_agent_card_for_session(_card(), "parent_sub_general-purpose_ab12cd34")
    second = _sub_agent_card_for_session(_card(), "parent_sub_general-purpose_ef56ab78")

    assert first.id != second.id


def test_identity_separates_distinct_sub_agent_types() -> None:
    """Two types sharing a sub-session id still get separate state."""
    first = _sub_agent_card_for_session(_card("general-purpose"), "parent_sub")
    second = _sub_agent_card_for_session(_card("code"), "parent_sub")

    assert first.id != second.id


def test_identity_keeps_the_hex_shape_of_the_id_it_replaces() -> None:
    """Consumers that treat the id as an opaque hex token are unaffected."""
    card = _sub_agent_card_for_session(_card(), "parent_sub_general-purpose_ab12cd34")

    assert re.fullmatch(r"[0-9a-f]{32}", card.id)


def test_identity_does_not_mutate_the_shared_spec_card() -> None:
    """The spec is reused for every delegation, so it must not be rewritten."""
    spec_card = _card()
    original_id = spec_card.id

    _sub_agent_card_for_session(spec_card, "parent_sub_general-purpose_ab12cd34")

    assert spec_card.id == original_id


def test_identity_preserves_the_rest_of_the_card() -> None:
    """Only the id is derived; name and description still describe the agent."""
    card = _sub_agent_card_for_session(_card(), "parent_sub_general-purpose_ab12cd34")

    assert card.name == "general-purpose"
    assert card.description == "desc"


def test_injected_general_purpose_spec_is_the_unstable_source() -> None:
    """Records why the identity has to be derived rather than taken as given.

    The injected spec mints a fresh card per parent build; without deriving
    from the sub-session id, the state namespace moves with it.
    """
    def _build() -> AgentCard:
        specs = _inject_general_purpose_subagent(
            subagents=None,
            add_general_purpose_agent=True,
            resolved_language="en",
            system_prompt="sp",
            tools=None,
            mcps=None,
            model=None,
            skills=None,
            rails=None,
            workspace=None,
        )
        return specs[0].agent_card

    first, second = _build(), _build()

    assert first.id != second.id
    assert (
        _sub_agent_card_for_session(first, "parent_sub_general-purpose_ab12cd34").id
        == _sub_agent_card_for_session(second, "parent_sub_general-purpose_ab12cd34").id
    )
