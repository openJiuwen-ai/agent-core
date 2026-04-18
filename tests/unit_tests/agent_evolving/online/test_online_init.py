# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Smoke tests for online module exports."""

from __future__ import annotations

import openjiuwen.agent_evolving.online as online


def test_online_module_exports():
    expected = {
        "EvolutionPatch",
        "EvolutionRecord",
        "EvolutionLog",
        "EvolutionSignal",
        "EvolutionCategory",
        "EvolutionContext",
        "EvolutionTarget",
        "SkillEvolver",
        "EvolutionStore",
    }
    assert expected.issubset(set(online.__all__))
