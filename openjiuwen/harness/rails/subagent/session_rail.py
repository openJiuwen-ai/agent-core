# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""SessionRail — deprecated shim for SubagentRail(async mode)."""

from __future__ import annotations

from openjiuwen.core.common.logging import logger

from openjiuwen.harness.rails.subagent.subagent_rail import SubagentRail


class SessionRail(SubagentRail):
    """Deprecated. Use SubagentRail(enable_async_subagent=True)."""

    def __init__(self) -> None:
        logger.warning(
            "SessionRail is deprecated; use SubagentRail(enable_async_subagent=True)."
        )
        super().__init__(enable_async_subagent=True)


__all__ = ["SessionRail"]
