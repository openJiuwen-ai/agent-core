# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Provider factory for the Codex harness."""

from __future__ import annotations

from openjiuwen.harness_protocol import HarnessCard, JsonObject, ModelChoice
from openjiuwen.harness_providers.codex.config import CodexHarnessConfig
from openjiuwen.harness_providers.codex.harness import CodexHarness


class CodexHarnessProvider:
    """Validate provider configuration and create an unstarted Codex harness."""

    @property
    def card(self) -> HarnessCard:
        return CodexHarness.card

    @staticmethod
    def create(config: JsonObject) -> CodexHarness:
        return CodexHarness(CodexHarnessConfig.from_mapping(config))

    @staticmethod
    def model_config(choice: ModelChoice, *, fallback: ModelChoice | None = None) -> JsonObject:
        """Map a model choice onto ``model`` / ``fallback_model``."""
        fragment = {"model": _codex_model(choice)}
        if fallback is not None:
            fragment["fallback_model"] = _codex_model(fallback)
        return fragment


def _codex_model(choice: ModelChoice) -> JsonObject:
    endpoint = choice.endpoint
    values = {
        "model": choice.model,
        "effort": choice.effort,
        "provider": endpoint.provider if endpoint is not None else None,
        "api_base": endpoint.api_base if endpoint is not None else None,
        "api_key": endpoint.api_key if endpoint is not None else None,
    }
    return {key: value for key, value in values.items() if value is not None}


__all__ = ["CodexHarnessProvider"]
