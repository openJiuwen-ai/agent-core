# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for deterministic subagent type normalization."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.schema.config import SubAgentConfig
from openjiuwen.harness.subagent_runtime.ids import build_subagent_id
from openjiuwen.harness.subagent_type import (
    canonical_subagent_key,
    normalize_subagent_type,
    resolve_subagent_type_name,
    subagent_spec_name,
)


def _parent(*names: str) -> DeepAgent:
    parent = DeepAgent(AgentCard(name="parent"))
    parent._deep_config = SimpleNamespace(
        subagents=[
            SubAgentConfig(
                agent_card=AgentCard(name=name),
                system_prompt="test",
            )
            for name in names
        ]
    )
    return parent


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (" general-purpose ", "general-purpose"),
        ("general\u2011purpose", "general-purpose"),
        ("general\u2013purpose", "general-purpose"),
        ("general\u2212purpose", "general-purpose"),
        ("general\uff0dpurpose", "general-purpose"),
        ("general\u00a0purpose", "general purpose"),
        ("general\u00adpurpose", "generalpurpose"),
        ("general_purpose", "general_purpose"),
        (None, ""),
    ],
)
def test_normalize_subagent_type(raw, expected):
    assert normalize_subagent_type(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["general-purpose", "general_purpose", "GENERAL-PURPOSE", "general purpose", "general\u2011purpose"],
)
def test_canonical_subagent_key_collapses_mechanical_variants(raw):
    assert canonical_subagent_key(raw) == "generalpurpose"


def test_find_subagent_spec_resolves_model_variants():
    parent = _parent("general-purpose")

    for variant in (
        "general-purpose",
        "general_purpose",
        "GENERAL-PURPOSE",
        "general\u2011purpose",
        " general-purpose ",
    ):
        spec = parent._find_subagent_spec(variant)
        assert spec is not None, variant
        assert spec.agent_card.name == "general-purpose"


def test_find_subagent_spec_rejects_typo_and_ambiguity():
    assert _parent("general-purpose")._find_subagent_spec("general-purpos") is None

    ambiguous = _parent("foo-bar", "foo_bar")
    assert ambiguous._find_subagent_spec("foo-bar").agent_card.name == "foo-bar"
    assert ambiguous._find_subagent_spec("foo_bar").agent_card.name == "foo_bar"
    assert ambiguous._find_subagent_spec("FOO-BAR") is None
    assert ambiguous._find_subagent_spec("foo bar") is None


def test_resolve_subagent_type_name_returns_registered_name():
    parent = _parent("general-purpose")

    assert resolve_subagent_type_name(parent, "general_purpose") == "general-purpose"
    assert (
        resolve_subagent_type_name(parent, "general_purpose", {"general-purpose"})
        == "general-purpose"
    )
    assert resolve_subagent_type_name(parent, "general-purpos") == "general-purpos"


def test_resolve_subagent_type_name_falls_back_to_allowed_set():
    parent = SimpleNamespace()

    assert (
        resolve_subagent_type_name(parent, "general_purpose", {"general-purpose"})
        == "general-purpose"
    )
    assert (
        resolve_subagent_type_name(parent, "general_purpose", {"other_agent"})
        == "general_purpose"
    )


def test_subagent_spec_name_ignores_dynamic_mock_attributes():
    spec = MagicMock()
    spec.card.name = MagicMock()

    assert subagent_spec_name(spec) == ""
    assert subagent_spec_name(SimpleNamespace(agent_card=SimpleNamespace(name=123))) == ""


def test_resolve_subagent_type_name_tolerates_mock_parent_agent():
    parent = MagicMock()

    assert resolve_subagent_type_name(parent, " general_purpose ") == "general_purpose"
    assert (
        resolve_subagent_type_name(parent, "general_purpose", {"general-purpose"})
        == "general-purpose"
    )


def test_build_subagent_id_normalizes_unicode_dash():
    assert (
        build_subagent_id("parent", "general\u2011purpose", sticky=True)
        == "parent_sub_general-purpose"
    )