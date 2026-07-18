# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Framework-level tests for the harness-element manifest.

Exercise the descriptor / catalog / construction-input / reflection machinery in
isolation, using a lightweight fake build context and the real provider
registries. No agent-team or platform-specific element declarations are needed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pytest

from openjiuwen.harness.manifest import (
    ConstructionInput,
    ElementKind,
    HarnessElementDescriptor,
    InterfaceMethod,
    context_field,
    factory_ref,
    get_catalog,
    harness_element,
    list_elements,
    param_field,
    register_from_catalog,
    resolve_factory,
)
from openjiuwen.harness.manifest.introspect import class_rail_adapter
from openjiuwen.harness.schema.deep_agent_spec import (
    _RAIL_PROVIDER_REGISTRY,
    _SUBAGENT_PROVIDER_REGISTRY,
    _TOOL_PROVIDER_REGISTRY,
)


@dataclass
class _FakeCtx:
    """Minimal stand-in for a build context with duck-typed attributes."""

    language: str = "en"
    member_name: str | None = "alice"


def _resolve_member(context: Any) -> Any:
    """Resolver reading ``member_name`` off the context (reflectable target)."""
    return getattr(context, "member_name", None)


def _build_fake_tool(params: dict[str, Any], context: Any) -> dict[str, Any]:
    """Reflectable tool factory returning a marker dict."""
    return {"kind": "tool", "params": dict(params or {})}


def _build_fake_subagent(
    factory_kwargs: dict[str, Any],
    context: Any,
) -> dict[str, Any]:
    """Reflectable sub-agent factory returning a marker dict."""
    return {"kind": "subagent", "kwargs": dict(factory_kwargs or {})}


class _FakeRailWithLang:
    """Reflectable rail class whose constructor accepts ``language``."""

    def __init__(self, language: str = "cn", channel: str = "default") -> None:
        self.language = language
        self.channel = channel


class _FakeRailNoLang:
    """Reflectable rail class whose constructor ignores ``language``."""

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


class _SampleInput(ConstructionInput):
    """Construction input mixing every source kind."""

    tool_names: list[str] = param_field(
        default_factory=lambda: ["switch_mode"],
        description="Tool names from spec params.",
    )
    language: str = context_field(
        attr="language",
        default="cn",
        description="Language read directly off the context.",
    )
    member: str | None = context_field(
        resolver=_resolve_member,
        default=None,
        description="Member resolved via a reflectable callable.",
    )


@pytest.fixture
def isolated_catalog():
    """Swap the process catalog for an empty one, restoring it afterwards."""
    catalog = get_catalog()
    saved = dict(catalog)
    catalog.clear()
    yield catalog
    catalog.clear()
    catalog.update(saved)


def test_descriptor_json_round_trips() -> None:
    """A descriptor survives ``model_dump_json`` -> ``model_validate_json``."""
    descriptor = HarnessElementDescriptor(
        kind=ElementKind.RAIL,
        name="test.sample",
        description="A sample element.",
        factory_ref="some.module:builder",
        input_schema={"type": "object"},
        input_model_ref="some.module:SampleInput",
        interface_methods=[InterfaceMethod(name="run", is_async=True)],
    )
    restored = HarnessElementDescriptor.model_validate_json(descriptor.model_dump_json())
    assert restored == descriptor


def test_factory_ref_resolves_back_to_the_same_object() -> None:
    """``resolve_factory(factory_ref(x))`` returns the original callable / class."""
    assert resolve_factory(factory_ref(_build_fake_tool)) is _build_fake_tool
    assert resolve_factory(factory_ref(_FakeRailWithLang)) is _FakeRailWithLang


def test_resolve_extracts_each_source() -> None:
    """params / context-attr / resolver fields are each pulled from the right place."""
    resolved = _SampleInput.resolve(
        {"tool_names": ["a", "b"]},
        _FakeCtx(language="fr", member_name="bob"),
    )
    assert resolved.tool_names == ["a", "b"]
    assert resolved.language == "fr"
    assert resolved.member == "bob"


def test_resolve_falls_back_to_defaults_when_absent_or_none() -> None:
    """Missing params and ``None`` context values drop back to field defaults."""
    resolved = _SampleInput.resolve(None, _FakeCtx(language="de", member_name=None))
    assert resolved.tool_names == ["switch_mode"]
    assert resolved.language == "de"
    assert resolved.member is None


def test_class_rail_adapter_injects_language_only_when_accepted() -> None:
    """Language is injected for a rail that accepts it, skipped otherwise."""
    with_lang = class_rail_adapter(_FakeRailWithLang)({}, _FakeCtx(language="fr"))
    assert isinstance(with_lang, _FakeRailWithLang)
    assert with_lang.language == "fr"

    no_lang = class_rail_adapter(_FakeRailNoLang)({"x": 1}, _FakeCtx(language="fr"))
    assert isinstance(no_lang, _FakeRailNoLang)
    assert "language" not in no_lang.kwargs


def test_harness_element_rejects_duplicate_names(isolated_catalog) -> None:
    """Declaring two elements with the same name raises ``ValueError``."""
    harness_element(
        kind=ElementKind.TOOL,
        name="test.dup",
        description="first",
        builder=_build_fake_tool,
    )
    with pytest.raises(ValueError, match="Duplicate harness element name"):
        harness_element(
            kind=ElementKind.TOOL,
            name="test.dup",
            description="second",
            builder=_build_fake_subagent,
        )


def test_rail_descriptor_gets_default_interface_methods(isolated_catalog) -> None:
    """A rail without explicit methods inherits the canonical rail lifecycle hooks."""
    harness_element(
        kind=ElementKind.RAIL,
        name="test.rail.methods",
        description="rail",
        builder=_FakeRailWithLang,
    )
    descriptor = get_catalog()["test.rail.methods"]
    assert descriptor.interface_methods != []


def test_list_elements_is_json_serializable(isolated_catalog) -> None:
    """``list_elements()`` yields plain JSON-ready dicts."""
    harness_element(
        kind=ElementKind.TOOL,
        name="test.tool.json",
        description="tool",
        input_model=_SampleInput,
        builder=_build_fake_tool,
    )
    elements = list_elements()
    assert json.loads(json.dumps(elements)) == elements
    assert elements[0]["name"] == "test.tool.json"
    assert elements[0]["input_model_ref"] is not None


def test_register_from_catalog_routes_by_kind(isolated_catalog) -> None:
    """Each kind lands in its provider registry; class rails are adapted."""
    harness_element(
        kind=ElementKind.TOOL,
        name="test.tool",
        description="tool",
        builder=_build_fake_tool,
    )
    harness_element(
        kind=ElementKind.RAIL,
        name="test.rail",
        description="rail",
        builder=_FakeRailWithLang,
    )
    harness_element(
        kind=ElementKind.SUBAGENT,
        name="test.subagent",
        description="subagent",
        builder=_build_fake_subagent,
    )

    register_from_catalog()

    assert "test.tool" in _TOOL_PROVIDER_REGISTRY
    assert "test.subagent" in _SUBAGENT_PROVIDER_REGISTRY
    assert "test.rail" in _RAIL_PROVIDER_REGISTRY

    rail = _RAIL_PROVIDER_REGISTRY["test.rail"]({"channel": "x"}, _FakeCtx("fr"))
    assert isinstance(rail, _FakeRailWithLang)
    assert rail.language == "fr"
    assert rail.channel == "x"
