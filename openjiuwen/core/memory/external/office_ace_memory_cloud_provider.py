# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""OfficeAce memory cloud provider — AgentArts-backed, no conversation upload.

云端部署形态：搜索/会话初始化复用 AgentArts SDK（与云端业务一致），但
``sync_turn`` 不在 provider 侧上传对话——云端由 relay-claw 的
CloudMemoryService 负责对话上报（避免双写）。
"""

from __future__ import annotations

from typing import Any

from openjiuwen.core.common.logging import memory_logger as logger
from openjiuwen.core.memory.external.agentarts_memory_provider import (
    AgentArtsMemoryProvider,
)


class OfficeAceMemoryCloudProvider(AgentArtsMemoryProvider):
    """Cloud-side OfficeAce memory provider.

    Search + memory-session init reuse the AgentArts SDK path (inherited from
    :class:`AgentArtsMemoryProvider`). ``sync_turn`` is a no-op: cloud-side
    conversation reporting is handled by relay-claw CloudMemoryService, so the
    provider must not duplicate it.
    """

    @property
    def name(self) -> str:
        return "officeace_cloud"

    async def sync_turn(self, user_msg: str, assistant_msg: str, **kwargs: Any) -> None:
        # 云端：对话上报由 relay-claw 侧 CloudMemoryService 负责，provider 不重复上报。
        logger.debug("[OfficeAceMemoryCloudProvider] sync_turn skipped (cloud reports via relay-claw)")
        return


__all__ = ["OfficeAceMemoryCloudProvider"]
