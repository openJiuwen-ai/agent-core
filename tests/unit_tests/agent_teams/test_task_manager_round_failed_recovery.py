# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for the round-failed recovery path (BUG20260921370204 batch 2).

Covers ``TeamTaskManager.handle_member_round_failed``:

* claim release back to pool (existing ``IN_PROGRESS → PENDING`` reset edge);
* per-task failure budget → convergence to the existing CANCELLED terminal;
* session-level breaker (sliding window) → cancel all non-terminal tasks;
* breaker window expiry (scattered failures never trip the breaker).
"""

import pytest
import pytest_asyncio

from openjiuwen.agent_teams.messager import Messager
from openjiuwen.agent_teams.schema.status import MemberMode, TaskStatus
from openjiuwen.agent_teams.context import reset_session_id, set_session_id
from openjiuwen.agent_teams.schema.team import TeamSpec
from openjiuwen.agent_teams.tools.database import (
    DatabaseConfig,
    DatabaseType,
    TeamDatabase,
)
from openjiuwen.agent_teams.tools.task_manager import TeamTaskManager
from openjiuwen.core.single_agent import AgentCard


@pytest.fixture
def db_config():
    """Provide in-memory database config for testing."""
    return DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:")


@pytest_asyncio.fixture
async def db(db_config):
    """Provide initialized database instance."""
    token = set_session_id("session_id")
    database = TeamDatabase(db_config)
    try:
        await database.initialize()
        yield database
    finally:
        reset_session_id(token)
        await database.close()


@pytest_asyncio.fixture
async def message_bus():
    """Provide Messager mock instance for testing."""
    bus = AsyncMockLikeMessager()
    yield bus


class AsyncMockLikeMessager:
    """Minimal async messager double recording published messages."""

    def __init__(self) -> None:
        self.published: list = []

    async def publish(self, **kwargs) -> None:
        self.published.append(kwargs)


@pytest_asyncio.fixture
async def managers(db, message_bus):
    """Provide a team with two members and one task manager per member."""
    await db.team.create_team(
        team_name="test_team",
        display_name="Test Team",
        leader_member_name="leader1",
    )
    for name in ("member1", "member2"):
        await db.member.create_member(
            member_name=name,
            team_name="test_team",
            display_name=name,
            agent_card=AgentCard().model_dump_json(),
            status="BUSY",
            mode=MemberMode.BUILD_MODE.value,
        )
    return {
        name: TeamTaskManager(
            team_name="test_team", member_name=name, db=db, messager=message_bus
        )
        for name in ("member1", "member2")
    }


@pytest.mark.asyncio
@pytest.mark.level0
async def test_round_failed_releases_claim_to_pool(managers) -> None:
    """首次失败：claim 释放回池（IN_PROGRESS → PENDING 既有边，assignee 清空）。"""
    m1 = managers["member1"]
    task = await m1.add(title="T4", content="creative work")
    assert task is not None

    claim = await m1.claim(task.task_id)
    assert claim.ok
    held = await m1.db.task.get_task(task.task_id)
    assert held.status == TaskStatus.IN_PROGRESS.value
    assert held.assignee == "member1"

    outcome = await m1.handle_member_round_failed("member1", reason="model 500")

    assert outcome.breaker_open is False
    assert outcome.cancelled_task_ids == []
    assert outcome.released_task_id == task.task_id
    released = await m1.db.task.get_task(task.task_id)
    assert released.status == TaskStatus.PENDING.value
    assert released.assignee is None


@pytest.mark.asyncio
@pytest.mark.level0
async def test_round_failed_converges_to_cancelled_at_limit(managers) -> None:
    """同一任务失败 3 次（每次重新认领）：收敛到既有 CANCELLED 终态。"""
    m1 = managers["member1"]
    task = await m1.add(title="T4", content="creative work")
    from openjiuwen.agent_teams.tools import task_manager as tm_module

    limit = tm_module._ROUND_FAILED_RESET_LIMIT

    for attempt in range(1, limit + 1):
        claim = await m1.claim(task.task_id)
        assert claim.ok, f"re-claim #{attempt} must succeed"
        outcome = await m1.handle_member_round_failed("member1", reason=f"fail #{attempt}")
        if attempt < limit:
            assert outcome.released_task_id == task.task_id
            assert outcome.cancelled_task_ids == []
        else:
            assert outcome.released_task_id is None
            assert outcome.cancelled_task_ids == [task.task_id]

    final = await m1.db.task.get_task(task.task_id)
    assert final.status == TaskStatus.CANCELLED.value


@pytest.mark.asyncio
@pytest.mark.level0
async def test_breaker_opens_and_cancels_all_non_terminal(managers) -> None:
    """5 分钟窗口内累计 3 次任务失败：熔断打开，取消全部非终态任务（含健康在途任务）。"""
    m1, m2 = managers["member1"], managers["member2"]

    healthy = await m1.add(title="T1", content="healthy in-flight task")
    await m1.claim(healthy.task_id)

    failing = await m1.add(title="T4", content="task that keeps failing")
    await m2.claim(failing.task_id)

    # 失败 #1（member2 的任务回池）、#2（member2 重新认领后再失败回池）——未达熔断。
    outcome1 = await m2.handle_member_round_failed("member2", reason="fail #1")
    assert outcome1.breaker_open is False and outcome1.released_task_id == failing.task_id
    await m2.claim(failing.task_id)
    outcome2 = await m2.handle_member_round_failed("member2", reason="fail #2")
    assert outcome2.breaker_open is False

    # 失败 #3：熔断打开 → cancel_all_tasks 取消全部非终态任务。
    await m2.claim(failing.task_id)
    outcome3 = await m2.handle_member_round_failed("member2", reason="fail #3")
    assert outcome3.breaker_open is True
    assert set(outcome3.cancelled_task_ids) == {healthy.task_id, failing.task_id}

    assert (await m1.db.task.get_task(healthy.task_id)).status == TaskStatus.CANCELLED.value
    assert (await m1.db.task.get_task(failing.task_id)).status == TaskStatus.CANCELLED.value


@pytest.mark.asyncio
@pytest.mark.level0
async def test_breaker_window_expiry_keeps_breaker_closed(managers) -> None:
    """窗口外散落失败（间隔 > 300s）不触发熔断，仍走回池路径。"""
    m1 = managers["member1"]
    task = await m1.add(title="T2", content="scattered failures")
    await m1.claim(task.task_id)

    # 三次失败分别发生在 t=0 / t=200 / t=400：t=400 时窗口内只剩自己。
    outcome = await m1.handle_member_round_failed(
        "member1", reason="fail @t=0", _now=0.0
    )
    assert outcome.breaker_open is False and outcome.released_task_id == task.task_id

    await m1.claim(task.task_id)
    outcome = await m1.handle_member_round_failed(
        "member1", reason="fail @t=200", _now=200.0
    )
    assert outcome.breaker_open is False

    await m1.claim(task.task_id)
    outcome = await m1.handle_member_round_failed(
        "member1", reason="fail @t=400", _now=400.0
    )
    # 窗口未打开熔断（散落失败）；但任务级预算（3 次）恰在此耗尽 → 转 CANCELLED。
    assert outcome.breaker_open is False
    assert outcome.cancelled_task_ids == [task.task_id]
    # t=0 已滑出窗口（cutoff=100），t=200 仍在窗内。
    assert len(m1._round_failure_times) == 2


@pytest.mark.asyncio
@pytest.mark.level0
async def test_round_failed_without_claim_is_noop_but_counts(managers) -> None:
    """无在途任务时 round failed：无回池动作，但仍计入熔断窗口。"""
    m1 = managers["member1"]
    outcome = await m1.handle_member_round_failed("member1", reason="crash before claim")
    assert outcome.breaker_open is False
    assert outcome.released_task_id is None
    assert outcome.cancelled_task_ids == []
    assert len(m1._round_failure_times) == 1


@pytest.mark.level0
def test_team_spec_recovery_flag_defaults_true() -> None:
    """TeamSpec.enable_round_failed_recovery 默认开启（回滚开关语义）。"""
    spec = TeamSpec(team_name="t", display_name="T")
    assert spec.enable_round_failed_recovery is True
