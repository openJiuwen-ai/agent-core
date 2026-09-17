# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Provider factory for the in-process DeepAgent harness."""

from __future__ import annotations

from typing import Any, Mapping

from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.schema.deep_agent_spec import DeepAgentSpec
from openjiuwen.harness.schema.extension_spec import AgentTemplateSpec
from openjiuwen.harness_protocol import HarnessCard, HarnessContext, JsonObject, json_value_to_builtin
from openjiuwen.harness_providers.native.harness import DeepAgentHarness

_KNOWN_KEYS = frozenset({"deep_agent", "agent_template", "session_id", "language", "event_buffer_capacity"})


class NativeHarnessProvider:
    """Create an unstarted DeepAgent harness from serializable configuration.

    ``config`` keys:

    * ``deep_agent``: ``DeepAgentSpec`` fields (model, card, rails, tools...).
    * ``agent_template``: optional ``AgentTemplateSpec`` payload hot-loaded
      onto the agent before its first turn.
    * ``session_id`` / ``language`` / ``event_buffer_capacity``: harness knobs.
    """

    @property
    def card(self) -> HarnessCard:
        return DeepAgentHarness.card

    @staticmethod
    def create(config: JsonObject) -> DeepAgentHarness:
        values = json_value_to_builtin(config)
        if not isinstance(values, dict):
            raise TypeError("native harness config must be an object")
        unknown = sorted(set(values) - _KNOWN_KEYS)
        if unknown:
            raise ValueError(f"unknown native harness configuration fields: {', '.join(unknown)}")
        spec_payload = values.get("deep_agent") or {}
        if not isinstance(spec_payload, Mapping):
            raise TypeError("native harness deep_agent config must be an object")
        spec = DeepAgentSpec.model_validate(dict(spec_payload))
        template_payload = values.get("agent_template")
        template = AgentTemplateSpec.model_validate(dict(template_payload)) if template_payload else None
        language = values.get("language")
        if template is not None and spec.card is None:
            spec = spec.model_copy(update={"card": template.agent_card})
        if template is not None and spec.model is None and template.model is not None:
            spec = spec.model_copy(update={"model": template.model})

        def _factory(context: HarnessContext) -> DeepAgent:
            _ = context
            return spec.build()

        return DeepAgentHarness(
            _factory,
            agent_template=template,
            session_id=_optional_str(values.get("session_id")),
            language=_optional_str(language) or spec.language,
            event_buffer_capacity=int(values.get("event_buffer_capacity") or 1024),
        )


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError("native harness string options must be non-empty strings")
    return value


__all__ = ["NativeHarnessProvider"]
