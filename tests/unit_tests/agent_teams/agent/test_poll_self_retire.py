# coding: utf-8
"""Poll handler 自愈：团队存储被删除（no such table）时主动停轮。

回归守卫：删除会话删表后，若 kernel teardown 中段失败留下僵尸 EventBus，
poll handler 查到 "no such table" 必须 pause_polls 自我停轮，而不是靠
framework 吞异常无限刷屏。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import OperationalError

from openjiuwen.agent_teams.agent.coordination.event_bus import (
    InnerEventMessage,
    InnerEventType,
)
from openjiuwen.agent_teams.agent.coordination.handlers.stale_task import (
    StaleTaskHandler,
)
from openjiuwen.agent_teams.agent.coordination.handlers.team_completion import (
    TeamCompletionHandler,
)
from openjiuwen.agent_teams.schema.team import TeamRole


def _no_such_table() -> OperationalError:
    return OperationalError("SELECT ...", {}, Exception("no such table: team_task_x"))


def _poll_ctrl() -> SimpleNamespace:
    return SimpleNamespace(pause_polls=AsyncMock(), resume_polls=AsyncMock())


def _poll_event() -> InnerEventMessage:
    return InnerEventMessage(event_type=InnerEventType.POLL_TASK)


def _stale_handler(task_manager, poll) -> StaleTaskHandler:
    return StaleTaskHandler(
        host=SimpleNamespace(is_agent_running=lambda: False),
        blueprint=SimpleNamespace(
            member_name="m1",
            role=TeamRole.TEAMMATE,
            spec=SimpleNamespace(
                stale_claim_idle_timeout=300.0,
                stale_pending_idle_timeout=300.0,
            ),
        ),
        infra=SimpleNamespace(task_manager=task_manager),
        poll_ctrl=poll,
    )


@pytest.mark.asyncio
@pytest.mark.level1
async def test_stale_task_poll_retires_when_storage_gone():
    poll = _poll_ctrl()
    task_manager = SimpleNamespace(list_tasks=AsyncMock(side_effect=_no_such_table()))

    await _stale_handler(task_manager, poll).on_poll_task(_poll_event())

    poll.pause_polls.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_stale_task_poll_reraises_other_operational_errors():
    """锁库等其它 OperationalError 不命中自愈（可恢复故障，停轮反而有害）。"""
    poll = _poll_ctrl()
    task_manager = SimpleNamespace(
        list_tasks=AsyncMock(
            side_effect=OperationalError("SELECT ...", {}, Exception("database is locked"))
        )
    )

    with pytest.raises(OperationalError, match="locked"):
        await _stale_handler(task_manager, poll).on_poll_task(_poll_event())

    poll.pause_polls.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_team_completion_poll_retires_when_storage_gone():
    poll = _poll_ctrl()
    team_backend = SimpleNamespace(
        is_team_completed=AsyncMock(side_effect=_no_such_table())
    )
    handler = TeamCompletionHandler(
        host=SimpleNamespace(
            has_in_flight_round=lambda: False,
            is_agent_running=lambda: False,
            has_pending_interrupt=lambda: False,
        ),
        blueprint=SimpleNamespace(
            member_name="team-leader",
            role=TeamRole.LEADER,
            spec=SimpleNamespace(),
        ),
        infra=SimpleNamespace(team_backend=team_backend),
        poll_ctrl=poll,
    )

    await handler.on_poll_task(_poll_event())

    poll.pause_polls.assert_awaited_once()
