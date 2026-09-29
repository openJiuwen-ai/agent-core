# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Base class for scenario-scoped coordination handlers.

Each subclass declares its own ``EVENT_METHOD_MAP`` and exposes bound
callbacks via ``get_callbacks()``. Mirrors the rails convention from
``core/single_agent/rail/base.py:AgentRail``: a declarative
``event_key -> method_name`` table plus a ``get_callbacks()`` helper
that returns the bound-method dict for framework registration.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Awaitable, Callable, ClassVar

from openjiuwen.agent_teams.agent.blueprint import TeamAgentBlueprint
from openjiuwen.agent_teams.agent.coordination.event_bus import CoordinationEvent
from openjiuwen.agent_teams.agent.infra import TeamInfra

if TYPE_CHECKING:
    from openjiuwen.agent_teams.agent.coordination.dispatcher import (
        AgentRoundController,
        DispatcherHost,
        PollController,
        TeamLifecycleController,
    )

EventCallback = Callable[[CoordinationEvent], Awaitable[None]]


def _team_storage_gone(exc: BaseException) -> bool:
    """判定异常是否为「团队底层存储已被删除」（session.delete 删表后僵尸轮询的指纹）。

    双条件防误杀：必须是 sqlalchemy OperationalError 且消息含 "no such table"——
    锁库/连接抖动等其它 OperationalError 不算（那些是可恢复故障，停轮询反而有害）。
    """
    try:
        from sqlalchemy.exc import OperationalError
    except ImportError:  # pragma: no cover - sqlalchemy 是硬依赖
        return False
    return isinstance(exc, OperationalError) and "no such table" in str(exc)


class BaseCoordinationHandler:
    """Base class for scenario-scoped coordination event handlers.

    Subclasses:
        - declare ``EVENT_METHOD_MAP`` mapping ``event_key -> method_name``
        - implement the corresponding ``async`` methods
        - read static config / per-process infra directly via
          ``self._blueprint`` / ``self._infra``
        - drive the round through ``self._round``, trigger lifecycle
          effects through ``self._lifecycle``, and toggle the
          coordination poll timers through ``self._poll``

    Multiple handlers may register the same ``event_key`` — the
    framework fans out callbacks in registration order, so handlers
    stay decoupled and never call each other directly.
    """

    EVENT_METHOD_MAP: ClassVar[dict[str, str]] = {}

    def __init__(
        self,
        host: "DispatcherHost",
        blueprint: TeamAgentBlueprint,
        infra: TeamInfra,
        poll_ctrl: "PollController",
    ) -> None:
        # ``host`` satisfies both AgentRoundController and
        # TeamLifecycleController (it is the owning TeamAgent);
        # ``poll_ctrl`` is the coordination event bus. Aliasing under
        # narrower protocol-typed fields documents which surface each
        # call site actually depends on — handlers must not reach for
        # ``host`` directly.
        self._round: "AgentRoundController" = host
        self._lifecycle: "TeamLifecycleController" = host
        self._poll = poll_ctrl
        self._blueprint = blueprint
        self._infra = infra

    def get_callbacks(self) -> dict[str, EventCallback]:
        """Return ``event_key -> bound method`` for framework registration."""
        return {event_key: getattr(self, method_name) for event_key, method_name in self.EVENT_METHOD_MAP.items()}

    async def _retire_polls_if_storage_gone(self, exc: BaseException) -> bool:
        """团队存储已删除（session.delete 删表）时自我停轮，返回是否已处理。

        kernel teardown 中段失败会留下僵尸 EventBus（池条目已被移除、无人再
        停它），周期轮询会持续查询已删除的表刷屏报错——poll handler 查到
        "no such table" 即证明团队存储已不存在，主动 pause_polls 自我了断。
        僵尸内核无人 resume_polls，等效永久停轮；若属误判（理论上限），下一轮
        kernel.start/resume 会重建轮询，代价可控。
        """
        if not _team_storage_gone(exc):
            return False
        from openjiuwen.core.common.logging import team_logger

        team_logger.warning(
            "[{}] team storage gone (deleted?), retiring periodic polls: {}",
            self._blueprint.member_name or "?",
            exc,
        )
        try:
            await self._poll.pause_polls()
        except Exception as e:  # 停轮失败不掩盖原异常判定，下一轮还会再来
            team_logger.warning(
                "[{}] pause_polls failed while retiring polls: {}",
                self._blueprint.member_name or "?",
                e,
                exc_info=True,
            )
        return True
