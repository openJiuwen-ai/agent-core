# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Provider factory for the DeepSeek Harness external adapter."""

from __future__ import annotations

from openjiuwen.harness_providers.dsh.config import DshHarnessConfig
from openjiuwen.harness_providers.dsh.harness import DshHarness
from openjiuwen.harness_protocol import HarnessCard, JsonObject, ModelChoice


class DshHarnessProvider:
    """Validate provider configuration and create an unstarted DSH harness."""

    @property
    def card(self) -> HarnessCard:
        return DshHarness.card

    @staticmethod
    def create(config: JsonObject) -> DshHarness:
        return DshHarness(DshHarnessConfig.from_mapping(config))

    @staticmethod
    def model_config(choice: ModelChoice, *, fallback: ModelChoice | None = None) -> JsonObject:
        """Map a model choice onto ``model`` / ``base_url`` / ``api_key``.

        Raises:
            ValueError: For an effort or a fallback, which DSH has no setting for.
        """
        if choice.effort is not None:
            raise ValueError("DSH has no reasoning effort setting")
        if fallback is not None:
            raise ValueError("DSH has no authentication fallback")
        endpoint = choice.endpoint
        values = {
            "model": choice.model,
            "base_url": endpoint.api_base if endpoint is not None else None,
            "api_key": endpoint.api_key if endpoint is not None else None,
        }
        return {key: value for key, value in values.items() if value is not None}


__all__ = ["DshHarnessProvider"]
