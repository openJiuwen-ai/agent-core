# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Multi-Agent Module

This module provides the new Card + Config pattern for agent groups.

New API (recommended):
    from openjiuwen.core.multi_agent import (
        GroupCard,
        GroupConfig,
        BaseGroup,
        AgentGroupSession
    )

Legacy API (deprecated, for backward compatibility only):
    from openjiuwen.core.multi_agent.legacy import (
        AgentGroupConfig,
        BaseGroup,
        ControllerGroup,
        AgentGroupSession,
        GroupCard
    )
"""

from openjiuwen.core.multi_agent.group import (
    AgentGroupSession,
    BaseGroup
)
from openjiuwen.core.multi_agent.config import GroupConfig
from openjiuwen.core.multi_agent.schema.group_card import (
    GroupCard,
    EventDrivenGroupCard
)

__all__ = [
    "GroupCard",
    "EventDrivenGroupCard",
    "GroupConfig",
    "AgentGroupSession",
    "BaseGroup",
]
