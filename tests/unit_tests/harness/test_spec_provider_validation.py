# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Provider boundary validation for serializable harness specs."""

from __future__ import annotations

from typing import Any, AsyncIterator

import pytest

from openjiuwen.core.foundation.tool import Tool, ToolCard
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.rail.base import AgentRail
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.schema.build_context import BuildContext
from openjiuwen.harness.schema.deep_agent_spec import (
    BuiltinToolSpec,
    DeepAgentSpec,
    RailSpec,
    SubAgentSpec,
    _RAIL_PROVIDER_REGISTRY,
    _TOOL_PROVIDER_REGISTRY,
)

pytestmark = pytest.mark.level0


class _ProviderTool(Tool):
    def __init__(self, card: ToolCard) -> None:
        super().__init__(card)

    async def invoke(self, inputs: dict[str, Any], **kwargs: object) -> dict[str, Any]:
        return inputs

    async def stream(
        self,
        inputs: dict[str, Any],
        **kwargs: object,
    ) -> AsyncIterator[dict[str, Any]]:
        if False:
            yield inputs


class _ProviderRail(AgentRail):
    pass


def _tool_card(
    name: str,
    *,
    tool_id: str | None = None,
    stateless: bool = False,
    description: str | None = None,
) -> ToolCard:
    return ToolCard(
        id=tool_id or name,
        name=name,
        description=description or name,
        input_params={"type": "object", "properties": {}},
        stateless=stateless,
    )


def _register_tool(tool: Tool) -> None:
    result = Runner.resource_mgr.add_tool(tool, refresh=True)
    assert result.is_ok(), result.msg()


@pytest.mark.parametrize("provided", [None, []])
def test_rail_provider_allows_none_and_empty_list(monkeypatch: pytest.MonkeyPatch, provided: Any) -> None:
    provider_name = "test.validation.empty_rail"
    monkeypatch.setitem(_RAIL_PROVIDER_REGISTRY, provider_name, lambda _params, _context: provided)

    assert RailSpec(type=provider_name).build(language="en") == provided


@pytest.mark.parametrize("provided", [None, []])
def test_tool_provider_allows_none_and_empty_list(monkeypatch: pytest.MonkeyPatch, provided: Any) -> None:
    provider_name = "test.validation.empty_tool"
    monkeypatch.setitem(_TOOL_PROVIDER_REGISTRY, provider_name, lambda _params, _context: provided)

    assert BuiltinToolSpec(type=provider_name).build(language="en") == provided


def test_rail_provider_rejects_non_rail_list_items(monkeypatch: pytest.MonkeyPatch) -> None:
    provider_name = "test.validation.bad_rail"
    monkeypatch.setitem(
        _RAIL_PROVIDER_REGISTRY,
        provider_name,
        lambda _params, _context: [_ProviderRail(), object()],
    )

    with pytest.raises(TypeError, match="expected AgentRail"):
        RailSpec(type=provider_name).build(language="en")


def test_tool_provider_rejects_non_tool_values(monkeypatch: pytest.MonkeyPatch) -> None:
    provider_name = "test.validation.bad_tool"
    monkeypatch.setitem(_TOOL_PROVIDER_REGISTRY, provider_name, lambda _params, _context: object())

    with pytest.raises(TypeError, match="expected Tool, ToolCard"):
        BuiltinToolSpec(type=provider_name).build(language="en")


def test_provider_lists_drop_none_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    rail_provider_name = "test.validation.optional_rail_list"
    tool_provider_name = "test.validation.optional_tool_list"
    rail = _ProviderRail()
    tool = _ProviderTool(_tool_card("optional_tool"))
    monkeypatch.setitem(
        _RAIL_PROVIDER_REGISTRY,
        rail_provider_name,
        lambda _params, _context: [rail, None],
    )
    monkeypatch.setitem(
        _TOOL_PROVIDER_REGISTRY,
        tool_provider_name,
        lambda _params, _context: [tool, None],
    )

    assert RailSpec(type=rail_provider_name).build(language="en") == [rail]
    assert BuiltinToolSpec(type=tool_provider_name).build(language="en") == [tool]


def test_tool_provider_rejects_tool_with_invalid_card(monkeypatch: pytest.MonkeyPatch) -> None:
    provider_name = "test.validation.invalid_tool_card"
    tool = _ProviderTool(_tool_card("invalid_card"))
    tool._card = object()  # type: ignore[assignment]  # pylint: disable=protected-access
    monkeypatch.setitem(_TOOL_PROVIDER_REGISTRY, provider_name, lambda _params, _context: tool)

    with pytest.raises(TypeError, match="invalid card"):
        BuiltinToolSpec(type=provider_name).build(language="en")


def test_tool_provider_returns_registered_canonical_stateless_card(monkeypatch: pytest.MonkeyPatch) -> None:
    provider_name = "test.validation.canonical_tool"
    tool = _ProviderTool(_tool_card("canonical_tool", stateless=True))
    supplied_card = tool.card.model_copy(deep=True)
    _register_tool(tool)
    monkeypatch.setitem(_TOOL_PROVIDER_REGISTRY, provider_name, lambda _params, _context: supplied_card)
    try:
        built = BuiltinToolSpec(type=provider_name).build(language="en")

        assert built is tool.card
    finally:
        Runner.resource_mgr.remove_tool(tool.card.id)


def test_tool_card_reference_rejects_spoofed_input_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    provider_name = "test.validation.mismatched_tool"
    tool = _ProviderTool(_tool_card("mismatched_tool", stateless=True, description="canonical"))
    mismatched_card = tool.card.model_copy(
        update={"input_params": {"type": "object", "required": ["spoofed"]}},
        deep=True,
    )
    _register_tool(tool)
    monkeypatch.setitem(_TOOL_PROVIDER_REGISTRY, provider_name, lambda _params, _context: mismatched_card)
    try:
        with pytest.raises(TypeError, match="does not match the registered Tool"):
            BuiltinToolSpec(type=provider_name).build(language="en")
    finally:
        Runner.resource_mgr.remove_tool(tool.card.id)


@pytest.mark.parametrize(
    ("context", "message"),
    [
        (BuildContext(), "requires an agent owner id"),
        (BuildContext(member_card_id="other-agent"), "must be registered as"),
        (BuildContext(member_card_id=123), "member_card_id must be a string"),  # type: ignore[arg-type]
    ],
)
def test_stateful_tool_card_reference_requires_matching_owner(
    monkeypatch: pytest.MonkeyPatch,
    context: BuildContext,
    message: str,
) -> None:
    provider_name = "test.validation.stateful_owner"
    tool = _ProviderTool(_tool_card("owned_tool", tool_id="owned_tool_agent-1"))
    _register_tool(tool)
    monkeypatch.setitem(
        _TOOL_PROVIDER_REGISTRY,
        provider_name,
        lambda _params, _context: tool.card.model_copy(deep=True),
    )
    try:
        with pytest.raises(TypeError, match=message):
            BuiltinToolSpec(type=provider_name).build(language="en", context=context)
    finally:
        Runner.resource_mgr.remove_tool(tool.card.id)


def test_tool_card_reference_requires_registered_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    provider_name = "test.validation.unregistered_tool"
    card = _tool_card("unregistered_tool", stateless=True)
    monkeypatch.setitem(_TOOL_PROVIDER_REGISTRY, provider_name, lambda _params, _context: card)

    with pytest.raises(TypeError, match="has no registered Tool instance"):
        BuiltinToolSpec(type=provider_name).build(language="en")


def test_tool_provider_rejects_awaitable_result(monkeypatch: pytest.MonkeyPatch) -> None:
    provider_name = "test.validation.awaitable_tool"

    async def _build_later() -> _ProviderTool:
        return _ProviderTool(_tool_card("late_tool"))

    awaitable = _build_later()
    monkeypatch.setitem(_TOOL_PROVIDER_REGISTRY, provider_name, lambda _params, _context: awaitable)
    with pytest.raises(TypeError, match="must return synchronously"):
        BuiltinToolSpec(type=provider_name).build(language="en")


def test_provider_exception_propagates_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    provider_name = "test.validation.provider_failure"

    def _provider(_params: dict[str, Any], _context: BuildContext) -> None:
        raise RuntimeError("provider failed")

    monkeypatch.setitem(_TOOL_PROVIDER_REGISTRY, provider_name, _provider)

    with pytest.raises(RuntimeError, match="provider failed"):
        BuiltinToolSpec(type=provider_name).build(language="en")


def test_deep_agent_spec_canonicalizes_direct_tool_card() -> None:
    tool = _ProviderTool(_tool_card("root_tool", tool_id="root_tool_root-agent"))
    supplied_card = tool.card.model_copy(deep=True)
    _register_tool(tool)
    try:
        resolved = DeepAgentSpec(tools=[supplied_card])._resolve_tools(
            "en",
            context=BuildContext(member_card_id="root-agent"),
        )

        assert resolved == [tool.card]
        assert resolved[0] is tool.card
    finally:
        Runner.resource_mgr.remove_tool(tool.card.id)


def test_deep_agent_spec_preserves_runtime_injected_tool_instance() -> None:
    tool = _ProviderTool(_tool_card("runtime_root_tool"))
    spec = DeepAgentSpec().model_copy(update={"tools": [tool]})

    resolved = spec._resolve_tools("en", context=BuildContext(member_card_id="root-agent"))

    assert resolved == [tool]
    assert resolved[0] is tool


def test_deep_agent_spec_rejects_runtime_injected_invalid_tool_value() -> None:
    spec = DeepAgentSpec().model_copy(update={"tools": [object()]})

    with pytest.raises(TypeError, match="DeepAgentSpec.tools must contain"):
        spec._resolve_tools("en", context=BuildContext(member_card_id="root-agent"))


def test_subagent_tool_provider_uses_child_agent_id(monkeypatch: pytest.MonkeyPatch) -> None:
    provider_name = "test.validation.subagent_owner"
    child_id = "child-agent"
    tool = _ProviderTool(_tool_card("child_tool", tool_id=f"child_tool_{child_id}"))
    observed_owner_ids: list[str | None] = []

    def _provider(_params: dict[str, Any], context: BuildContext) -> ToolCard:
        observed_owner_ids.append(context.member_card_id)
        return tool.card.model_copy(deep=True)

    _register_tool(tool)
    monkeypatch.setitem(_TOOL_PROVIDER_REGISTRY, provider_name, _provider)
    parent_context = BuildContext(
        member_card_id="parent-agent",
        extras={"marker": "keep", "_parent_model": "stale"},
    )
    try:
        built = SubAgentSpec(
            agent_card=AgentCard(id=child_id, name="child"),
            system_prompt="child",
            tools=[BuiltinToolSpec(type=provider_name)],
        ).build(
            parent_model=object(),  # type: ignore[arg-type]
            language="en",
            context=parent_context,
        )

        assert observed_owner_ids == [child_id]
        assert built.tools == [tool.card]
        assert built.tools[0] is tool.card
        assert parent_context.member_card_id == "parent-agent"
        assert parent_context.extras == {"marker": "keep", "_parent_model": "stale"}
    finally:
        Runner.resource_mgr.remove_tool(tool.card.id)


def test_subagent_spec_preserves_runtime_injected_tool_instance() -> None:
    tool = _ProviderTool(_tool_card("runtime_child_tool"))
    spec = SubAgentSpec(
        agent_card=AgentCard(id="child-agent", name="child"),
        system_prompt="child",
    ).model_copy(update={"tools": [tool]})

    built = spec.build(
        parent_model=object(),  # type: ignore[arg-type]
        language="en",
        context=BuildContext(member_card_id="parent-agent"),
    )

    assert built.tools == [tool]
    assert built.tools[0] is tool
