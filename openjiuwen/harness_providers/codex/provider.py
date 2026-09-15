# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Provider factory for the Codex harness."""

from __future__ import annotations

from openjiuwen.harness_protocol import HarnessCard, JsonObject
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


__all__ = ["CodexHarnessProvider"]
