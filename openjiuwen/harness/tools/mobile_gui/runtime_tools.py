# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from typing import List

from openjiuwen.core.foundation.tool import Tool

from openjiuwen.harness.tools.mobile_gui.config import MobileGuiRuntimeSettings
from openjiuwen.harness.tools.mobile_gui.coordinate_action_tools import build_coordinate_tools
from openjiuwen.harness.tools.mobile_gui.navigation_tools import build_navigation_tools


def build_mobile_gui_tool_instances(settings: MobileGuiRuntimeSettings) -> List[Tool]:
    """All tools for VLM grounding mode (coordinate + navigation)."""
    tools: List[Tool] = []
    tools.extend(build_coordinate_tools(settings))
    tools.extend(build_navigation_tools(settings))
    return tools
