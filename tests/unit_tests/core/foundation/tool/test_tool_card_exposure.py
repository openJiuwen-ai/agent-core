# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for runtime exposure metadata on ``ToolCard``."""

from __future__ import annotations

from openjiuwen.core.foundation.tool import ToolCard, ToolExposure


def test_tool_card_defaults_to_direct_exposure():
    card = ToolCard(id="direct-tool", name="direct_tool")

    assert card.exposure is ToolExposure.DIRECT


def test_tool_card_keeps_deferred_exposure_out_of_model_tool_info():
    card = ToolCard(
        id="deferred-tool",
        name="deferred_tool",
        description="Only callable after discovery",
        exposure=ToolExposure.DEFERRED,
    )

    assert card.exposure is ToolExposure.DEFERRED

    model_info = card.tool_info()
    assert model_info.name == "deferred_tool"
    assert model_info.description == "Only callable after discovery"
    assert not hasattr(model_info, "exposure")
