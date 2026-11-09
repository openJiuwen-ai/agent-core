# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Host-owned Leader preparation at the restored, pre-supervisor boundary."""
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Awaitable, Callable, Any

LeaderPreparation = Callable[[Any, Any, Any], Awaitable[None]]
_preparation: ContextVar[LeaderPreparation | None] = ContextVar("leader_conversation_preparation", default=None)


@contextmanager
def bind_leader_preparation(callback: LeaderPreparation):
    """Bind a host preparation callback to newly started Leader cycles."""
    token = _preparation.set(callback)
    try:
        yield
    finally:
        _preparation.reset(token)


async def prepare_leader_conversation(harness, session, team_session=None):
    callback = _preparation.get()
    if callback is not None:
        await callback(harness, session, team_session)


async def persisted_team_conversation_is_idle(team_session: Any) -> bool:
    """Inspect cold team work through its saved runtime context, without starting it.

    Missing or invalid storage identity fails closed. A stopped Leader alone
    does not prove that its teammates, task board or mailbox have settled.
    """
    from openjiuwen.agent_teams.context import reset_session_id, set_session_id
    from openjiuwen.agent_teams.schema.status import MEMBER_QUIESCENT_STATUSES, MemberStatus
    from openjiuwen.agent_teams.schema.team import TeamRuntimeContext
    from openjiuwen.agent_teams.spawn.shared_resources import get_shared_db
    from openjiuwen.agent_teams.tools.database.graph import TASK_TERMINAL_STATUSES

    token = set_session_id(team_session.get_session_id())
    try:
        for team_name, bucket in (team_session.get_state("teams") or {}).items():
            if bucket.get("pending_resume") is not None or not bucket.get("context"):
                return False
            context = TeamRuntimeContext.model_validate(bucket["context"])
            if context.db_config is None:
                return False
            db = get_shared_db(context.db_config)
            await db.initialize()
            members = await db.member.get_team_members(team_name)
            if any(member.status not in MEMBER_QUIESCENT_STATUSES or member.status == MemberStatus.PAUSED
                   for member in members):
                return False
            tasks = await db.task.get_team_tasks(team_name)
            if any(task.status not in TASK_TERMINAL_STATUSES for task in tasks):
                return False
            if await db.message.has_unread_messages(team_name, include_broadcast=True):
                return False
        return True
    finally:
        reset_session_id(token)
