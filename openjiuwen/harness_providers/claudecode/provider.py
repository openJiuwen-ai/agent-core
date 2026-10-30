# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Provider factory for the Claude Code harness."""

from __future__ import annotations

from openjiuwen.harness_protocol import HarnessCard, JsonObject, ModelChoice
from openjiuwen.harness_providers.claudecode.config import ClaudeCodeHarnessConfig
from openjiuwen.harness_providers.claudecode.harness import ClaudeCodeHarness


class ClaudeCodeHarnessProvider:
    """Validate provider configuration and create an unstarted Claude Code harness."""

    @property
    def card(self) -> HarnessCard:
        return ClaudeCodeHarness.card

    @staticmethod
    def create(config: JsonObject) -> ClaudeCodeHarness:
        return ClaudeCodeHarness(ClaudeCodeHarnessConfig.from_mapping(config))

    @staticmethod
    def model_config(choice: ModelChoice, *, fallback: ModelChoice | None = None) -> JsonObject:
        """Map a model choice onto ``model`` / ``fallback_model``.

        Claude Code speaks the Anthropic protocol only, so an endpoint's
        ``provider`` label is not forwarded.
        """
        fragment = {"model": _claude_model(choice)}
        if fallback is not None:
            fragment["fallback_model"] = _claude_model(fallback)
        return fragment


def _claude_model(choice: ModelChoice) -> JsonObject:
    endpoint = choice.endpoint
    values = {
        "model": choice.model,
        "effort": choice.effort,
        "api_base": endpoint.api_base if endpoint is not None else None,
        "api_key": endpoint.api_key if endpoint is not None else None,
    }
    return {key: value for key, value in values.items() if value is not None}


__all__ = ["ClaudeCodeHarnessProvider"]
