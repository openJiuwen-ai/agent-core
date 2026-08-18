# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Public collection-session interface for external Agent integrations."""

from openjiuwen.agent_evolving.agent_rl.online.gateway.collector.http_manager import (
    HttpCollectionSessionManager,
)
from openjiuwen.agent_evolving.agent_rl.online.gateway.collector.types import (
    CollectionMode,
    CollectionSessionManager,
    CollectionSessionRecord,
    CollectionSessionSpec,
    CollectionSessionStatus,
    RewardMode,
)

__all__ = [
    "CollectionMode",
    "CollectionSessionManager",
    "CollectionSessionRecord",
    "CollectionSessionSpec",
    "CollectionSessionStatus",
    "HttpCollectionSessionManager",
    "RewardMode",
]
