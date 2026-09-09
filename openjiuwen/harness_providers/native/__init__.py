# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""In-process DeepAgent implementation of the harness protocol."""

from openjiuwen.harness_providers.native.harness import (
    ADAPTER_VERSION,
    INTERACTIVE_INPUT_METADATA_KIND,
    PROVIDER_NAME,
    AgentFactory,
    DeepAgentHarness,
)
from openjiuwen.harness_providers.native.provider import NativeHarnessProvider

__all__ = [
    "ADAPTER_VERSION",
    "AgentFactory",
    "DeepAgentHarness",
    "INTERACTIVE_INPUT_METADATA_KIND",
    "NativeHarnessProvider",
    "PROVIDER_NAME",
]
