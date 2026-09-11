# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""DeepSeek Harness implementation of the external harness protocol."""

from openjiuwen.agent_teams.external.dsh.config import DshHarnessConfig
from openjiuwen.agent_teams.external.dsh.harness import ADAPTER_VERSION, DshHarness
from openjiuwen.agent_teams.external.dsh.provider import DshHarnessProvider

__all__ = [
    "ADAPTER_VERSION",
    "DshHarness",
    "DshHarnessConfig",
    "DshHarnessProvider",
]
