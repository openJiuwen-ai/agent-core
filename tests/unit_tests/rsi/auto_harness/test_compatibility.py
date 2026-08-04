# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Compatibility tests for the relocated AutoHarness package."""

import openjiuwen.auto_harness as legacy_auto_harness
import openjiuwen.rsi.auto_harness as rsi_auto_harness


def test_legacy_package_reexports_rsi_public_api():
    assert legacy_auto_harness.AutoHarnessConfig is rsi_auto_harness.AutoHarnessConfig
    assert legacy_auto_harness.AutoHarnessOrchestrator is rsi_auto_harness.AutoHarnessOrchestrator
    assert legacy_auto_harness.create_auto_harness_orchestrator is rsi_auto_harness.create_auto_harness_orchestrator
