# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Android emulator / device GUI agent (VLM coordinate grounding) for DeepAgent."""

from openjiuwen.harness.tools.mobile_gui.config import MobileGuiRuntimeSettings
from openjiuwen.harness.tools.mobile_gui.rails_factory import (
    build_mobile_gui_rails,
    infer_model_display_name,
    resolve_mobile_skill_root,
)
from openjiuwen.harness.tools.mobile_gui.runtime_tools import build_mobile_gui_tool_instances

__all__ = [
    "MobileGuiRuntimeSettings",
    "build_mobile_gui_rails",
    "build_mobile_gui_tool_instances",
    "infer_model_display_name",
    "resolve_mobile_skill_root",
]
