# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Compatibility re-export; the DSH provider lives in ``openjiuwen.harness_providers.dsh``."""

from openjiuwen.harness_providers.dsh import (
    ADAPTER_VERSION,
    DshHarness,
    DshHarnessConfig,
    DshHarnessProvider,
)

__all__ = [
    "ADAPTER_VERSION",
    "DshHarness",
    "DshHarnessConfig",
    "DshHarnessProvider",
]
