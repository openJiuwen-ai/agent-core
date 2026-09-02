# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for canonical subagent type names."""

from types import SimpleNamespace

from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.harness.subagent_types import (
    apply_subagent_type_enum,
    format_unknown_subagent_type,
    listed_subagent_types,
    listed_types_from_parent,
    resolve_subagent_type,
    resolved_type_from_parent,
    subagent_type_name,
    tool_owner_id_of,
)


def test_subagent_type_name_reads_agent_card_or_card() -> None:
    spec = SimpleNamespace(agent_card=SimpleNamespace(name="  research_agent  "))
    assert subagent_type_name(spec) == "research_agent"

    agent = SimpleNamespace(card=SimpleNamespace(name="browser_agent"))
    assert subagent_type_name(agent) == "browser_agent"

    mixed = SimpleNamespace(
        agent_card=SimpleNamespace(name=object()),
        card=SimpleNamespace(name="from_card"),
    )
    assert subagent_type_name(mixed) == "from_card"


def test_subagent_type_name_skips_blank() -> None:
    spec = SimpleNamespace(agent_card=SimpleNamespace(name="  "))
    assert subagent_type_name(spec) is None
    assert subagent_type_name(SimpleNamespace(card=None)) is None


def test_listed_subagent_types_dedupes_and_skips_nameless() -> None:
    specs = [
        SimpleNamespace(agent_card=SimpleNamespace(name="general-purpose")),
        SimpleNamespace(card=None),
        SimpleNamespace(agent_card=SimpleNamespace(name="general-purpose")),
        SimpleNamespace(card=SimpleNamespace(name="research_agent")),
    ]
    assert listed_subagent_types(specs) == ["general-purpose", "research_agent"]


def test_resolve_subagent_type_aliases_only_when_registered() -> None:
    available = ["general-purpose", "research_agent"]
    assert resolve_subagent_type("general-purpose", available) == "general-purpose"
    assert resolve_subagent_type("general_agent", available) == "general-purpose"
    assert resolve_subagent_type("general_purpose", available) == "general-purpose"
    assert resolve_subagent_type("research_agent", available) == "research_agent"
    assert resolve_subagent_type("missing", available) is None
    assert resolve_subagent_type("general_agent", ["research_agent"]) is None


def test_apply_subagent_type_enum_writes_and_clears() -> None:
    card = ToolCard(
        id="task_tool",
        name="task_tool",
        description="d",
        input_params={
            "type": "object",
            "properties": {
                "subagent_type": {"type": "string", "description": "type"},
            },
            "required": ["subagent_type"],
        },
    )
    apply_subagent_type_enum(card, ["general-purpose", "research_agent"])
    assert card.input_params["properties"]["subagent_type"]["enum"] == [
        "general-purpose",
        "research_agent",
    ]
    apply_subagent_type_enum(card, [])
    assert "enum" not in card.input_params["properties"]["subagent_type"]


def test_format_unknown_subagent_type_lists_live_names() -> None:
    text = format_unknown_subagent_type("general-purpose", ["research_agent"], "cn")
    assert "general-purpose" in text
    assert "research_agent" in text


def test_tool_owner_id_prefers_deep_config() -> None:
    agent = SimpleNamespace(
        deep_config=SimpleNamespace(tool_owner_id="jiuwenswarm_s_session"),
        card=SimpleNamespace(id="jiuwenswarm"),
    )
    assert tool_owner_id_of(agent) == "jiuwenswarm_s_session"
    assert tool_owner_id_of(SimpleNamespace(card=SimpleNamespace(id="fallback"))) == "fallback"


def test_resolved_type_from_parent_skips_mock_and_missing() -> None:
    from unittest.mock import MagicMock

    assert resolved_type_from_parent(SimpleNamespace(), "browser_agent") == "browser_agent"

    parent = SimpleNamespace(resolve_subagent_type=lambda name: "general-purpose")
    assert resolved_type_from_parent(parent, "general_agent") == "general-purpose"

    rejected = SimpleNamespace(resolve_subagent_type=lambda name: None)
    assert resolved_type_from_parent(rejected, "missing") is None

    mock_parent = MagicMock()
    mock_parent.resolve_subagent_type.return_value = MagicMock()
    assert resolved_type_from_parent(mock_parent, "browser_agent") == "browser_agent"


def test_listed_types_from_parent_ignores_non_strings() -> None:
    from unittest.mock import MagicMock

    assert listed_types_from_parent(SimpleNamespace()) == []
    parent = SimpleNamespace(available_subagent_types=lambda: ["general-purpose", ""])
    assert listed_types_from_parent(parent) == ["general-purpose"]
    assert listed_types_from_parent(MagicMock()) == []
