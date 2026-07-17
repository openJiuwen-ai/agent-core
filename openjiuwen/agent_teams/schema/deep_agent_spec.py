# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Re-export of the harness DeepAgent-level Spec types.

The DeepAgent-level specs (``DeepAgentSpec`` / ``RailSpec`` / ``BuiltinToolSpec``
/ ``SubAgentSpec`` and their leaf types) now live in
``openjiuwen.harness.schema.deep_agent_spec`` as the harness-level source of
truth. This module is preserved as a thin re-export so existing team import
paths keep working and refer to the same objects (``is``-identical). Team
topology specs (``TeamSpec`` / ``TeamAgentSpec`` / ``blueprint.py``) stay in
``agent_teams`` and are not affected by this re-export.
"""

from __future__ import annotations

from openjiuwen.harness.schema.deep_agent_spec import (
    AudioModelSpec,
    BuiltinToolSpec,
    DeepAgentSpec,
    ModelSpec,
    ProgressiveToolSpec,
    RailSpec,
    SubAgentSpec,
    SysOperationSpec,
    TeamModelConfig,
    VisionModelSpec,
    WorkspaceSpec,
    register_rail_provider,
    register_subagent_provider,
    register_tool_provider,
)

__all__ = [
    "AudioModelSpec",
    "BuiltinToolSpec",
    "DeepAgentSpec",
    "ModelSpec",
    "ProgressiveToolSpec",
    "RailSpec",
    "SubAgentSpec",
    "SysOperationSpec",
    "TeamModelConfig",
    "VisionModelSpec",
    "WorkspaceSpec",
    "register_rail_provider",
    "register_subagent_provider",
    "register_tool_provider",
]
