# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Versioned session settings shared by local and distributed team members."""

import json
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from openjiuwen.agent_teams.tools.database.engine import DbSessions
from openjiuwen.agent_teams.tools.models import TeamSessionSettings


class SessionSettingsDao:
    """Persist host-owned settings independently of the team creation tool."""

    def __init__(self, sessions: DbSessions) -> None:
        self._sessions = sessions

    async def get(self, session_id: str) -> dict[str, Any] | None:
        """Read committed settings; callers must not cache across tool calls."""
        async with self._sessions.read() as session:
            row = (await session.execute(select(TeamSessionSettings).where(
                TeamSessionSettings.session_id == session_id,
            ))).scalar_one_or_none()
            if row is None:
                return None
            return {"version": row.version, "settings": json.loads(row.settings_json)}

    async def initialize(self, session_id: str, settings: dict[str, Any]) -> None:
        """Create the initial record without overwriting a concurrent update."""
        async with self._sessions.write() as session:
            session.add(TeamSessionSettings(
                session_id=session_id, settings_json=json.dumps(settings), version=0,
            ))
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()

    async def replace(
        self, session_id: str, settings: dict[str, Any], *, expected_version: int,
    ) -> int:
        """Commit a compare-and-swap update or reject a stale client."""
        async with self._sessions.write() as session:
            result = await session.execute(update(TeamSessionSettings).where(
                TeamSessionSettings.session_id == session_id,
                TeamSessionSettings.version == expected_version,
            ).values(settings_json=json.dumps(settings), version=expected_version + 1))
            if result.rowcount != 1:
                raise ValueError("Execution settings changed; refresh and retry")
            await session.commit()
        return expected_version + 1
