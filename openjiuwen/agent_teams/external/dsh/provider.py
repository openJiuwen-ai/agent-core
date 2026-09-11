# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Provider factory for the DeepSeek Harness external adapter."""

from __future__ import annotations

from openjiuwen.agent_teams.external.dsh.config import DshHarnessConfig
from openjiuwen.agent_teams.external.dsh.harness import DshHarness
from openjiuwen.agent_teams.external.protocol import ExternalHarnessCard, JsonObject


class DshHarnessProvider:
    """Validate provider configuration and create an unstarted DSH harness."""

    @property
    def card(self) -> ExternalHarnessCard:
        return DshHarness.card

    @staticmethod
    def create(config: JsonObject) -> DshHarness:
        return DshHarness(DshHarnessConfig.from_mapping(config))


__all__ = ["DshHarnessProvider"]
