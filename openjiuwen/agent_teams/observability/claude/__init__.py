# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Claude-specific observability adapters."""

from openjiuwen.agent_teams.observability.claude.bridge import (
    ClaudeSpanBridge,
    NoopClaudeSpanBridge,
)

__all__ = [
    "ClaudeSpanBridge",
    "NoopClaudeSpanBridge",
]
