# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""AgentTemplate snapshot loading at the NativeHarness build boundary."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from openjiuwen.agent_teams.harness.native_harness import NativeHarness
from openjiuwen.agent_teams.schema.deep_agent_spec import DeepAgentSpec
from openjiuwen.harness import deep_agent as deep_agent_module
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.schema.build_context import BuildContext
from openjiuwen.harness.schema.extension_spec import AgentTemplateSpec
from tests.unit_tests.agent_teams.harness.fixtures import make_spec


def _template_snapshot(agent_id: str = "member1") -> dict[str, Any]:
    return {
        "agent_card": {
            "id": agent_id,
            "name": agent_id,
            "description": "test template",
        },
        "prompt_sections": [
            {
                "name": "identity",
                "content": {"cn": "专家身份", "en": "expert identity"},
                "priority": 10,
            }
        ],
    }


def test_deep_agent_spec_round_trips_agent_template_snapshot() -> None:
    spec = DeepAgentSpec(agent_template_spec=_template_snapshot())

    restored = DeepAgentSpec.model_validate_json(spec.model_dump_json())

    assert restored.agent_template_spec == _template_snapshot()


@pytest.mark.asyncio
async def test_load_agent_template_spec_uses_in_memory_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    template = AgentTemplateSpec.model_validate(_template_snapshot())
    resolved_parts = object()
    captured: dict[str, object] = {}

    def _resolve(
        value: AgentTemplateSpec,
        context: BuildContext,
    ) -> object:
        captured["template"] = value
        captured["context"] = context
        return resolved_parts

    class _Host:
        deep_config = SimpleNamespace(model="parent-model")

        def _new_extension_context(
            self,
            context: BuildContext | None,
        ) -> BuildContext:
            return BuildContext() if context is None else context.derive()

        async def _apply_extension_parts(
            self,
            parts: object,
            *,
            source_uri: str | None,
        ) -> tuple[object, str | None]:
            return parts, source_uri

    monkeypatch.setattr(
        deep_agent_module,
        "resolve_agent_template_parts",
        _resolve,
    )

    result = await DeepAgent.load_agent_template_spec(_Host(), template)  # type: ignore[arg-type]

    assert result == (resolved_parts, None)
    assert captured["template"] is template
    context = captured["context"]
    assert isinstance(context, BuildContext)
    assert context.extras["_parent_model"] == "parent-model"


@pytest.mark.asyncio
async def test_prepare_initializes_then_loads_template_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = make_spec()
    spec.agent_template_spec = _template_snapshot()
    harness = NativeHarness(spec)
    calls: list[tuple[str, object | None]] = []

    async def _ensure_initialized() -> None:
        calls.append(("initialize", None))

    async def _load_agent_template_spec(
        template: AgentTemplateSpec,
        *,
        context: object | None = None,
    ) -> object:
        calls.append(("load", (template.agent_card.id, context)))
        return object()

    monkeypatch.setattr(harness, "ensure_initialized", _ensure_initialized)
    monkeypatch.setattr(
        harness,
        "load_agent_template_spec",
        _load_agent_template_spec,
    )

    await harness._prepare()
    await harness._prepare()

    assert calls == [
        ("initialize", None),
        ("load", ("member1", harness._build_context)),
    ]


@pytest.mark.asyncio
async def test_prepare_without_template_preserves_existing_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = NativeHarness(make_spec())
    calls: list[str] = []

    async def _ensure_initialized() -> None:
        calls.append("initialize")

    async def _unexpected_load(*args: object, **kwargs: object) -> object:
        raise AssertionError("template loader must not run")

    monkeypatch.setattr(harness, "ensure_initialized", _ensure_initialized)
    monkeypatch.setattr(harness, "load_agent_template_spec", _unexpected_load)

    await harness._prepare()

    assert calls == ["initialize"]
