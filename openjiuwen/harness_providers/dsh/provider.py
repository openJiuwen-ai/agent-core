# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Provider factory for the DeepSeek Harness external adapter."""

from __future__ import annotations

from openjiuwen.harness_providers.dsh.config import DshHarnessConfig
from openjiuwen.harness_providers.dsh.harness import DshHarness
from openjiuwen.harness_protocol import HarnessCard, JsonObject


class DshHarnessProvider:
    """Validate provider configuration and create an unstarted DSH harness."""

    @property
    def card(self) -> HarnessCard:
        return DshHarness.card

    @staticmethod
    def create(config: JsonObject) -> DshHarness:
        return DshHarness(DshHarnessConfig.from_mapping(config))


__all__ = ["DshHarnessProvider"]
