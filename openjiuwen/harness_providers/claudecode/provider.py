# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Provider factory for the Claude Code harness."""

from __future__ import annotations

from openjiuwen.harness_protocol import HarnessCard, JsonObject
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


__all__ = ["ClaudeCodeHarnessProvider"]
