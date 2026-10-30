# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Provider cards declare which session-fork modes they can materialise natively."""
from __future__ import annotations

from openjiuwen.harness_protocol import HarnessCard


def test_fork_modes_default_empty():
    card = HarnessCard(name="x", implementation_version="1")
    assert card.fork_modes == frozenset()


def test_sdk_providers_declare_full_and_before():
    from openjiuwen.harness_providers.claudecode import ClaudeCodeHarness
    from openjiuwen.harness_providers.codex import CodexHarness

    assert ClaudeCodeHarness.card.fork_modes == frozenset({"full", "before"})
    assert CodexHarness.card.fork_modes == frozenset({"full", "before"})
