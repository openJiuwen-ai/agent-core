# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Connection descriptor for external agents joining a team.

An external agent process (a third-party CLI such as claudecode / codex,
or an independent service) needs three things to act as a first-class team
member: where the team database lives, how to reach the team messager, and
which member identity it carries. :class:`TeamJoinDescriptor` bundles those
into one JSON-serialisable payload that the team injects through an
environment variable at spawn time, or that an operator hands to an
independent service out of band.
"""

from __future__ import annotations

import os
from typing import Mapping

from pydantic import BaseModel, Field, ValidationError

from openjiuwen.agent_teams.messager.base import MessagerTransportConfig
from openjiuwen.agent_teams.tools.database.config import DatabaseConfig
from openjiuwen.agent_teams.tools.memory_database import MemoryDatabaseConfig
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import raise_error

# Single environment variable carrying the JSON-encoded descriptor.
TEAM_JOIN_ENV = "OPENJIUWEN_TEAM_JOIN"


class TeamJoinDescriptor(BaseModel):
    """Everything an external agent needs to attach to a running team.

    Attributes:
        session_id: Session identifier used to build event topics and to
            isolate per-session dynamic tables. Must match the team's live
            session.
        team_name: Target team identifier.
        member_name: The member identity this external agent serves. The
            team must already have a registered member row under this name.
        role: Member role driving op-surface filtering. ``"teammate"``
            (default) or ``"leader"``.
        language: Team runtime language (``"cn"`` or ``"en"``) used to render
            inbound message / task-board text consistently with in-process
            members.
        db_config: Team database connection. For cross-process use this must
            be a file-backed sqlite ``connection_string``; the in-memory
            backend (``MemoryDatabaseConfig``) is single-process only and is
            intended for tests.
        transport_config: Messager transport config. For cross-process use
            this must be the ``pyzmq`` backend with concrete socket
            addresses; ``inprocess`` is single-process only.
    """

    session_id: str
    team_name: str
    member_name: str
    role: str = "teammate"
    language: str = "cn"
    db_config: DatabaseConfig | MemoryDatabaseConfig = Field(default_factory=DatabaseConfig)
    transport_config: MessagerTransportConfig = Field(default_factory=MessagerTransportConfig)

    def to_json(self) -> str:
        """Serialise to a compact JSON string."""
        return self.model_dump_json()

    def to_env(self) -> dict[str, str]:
        """Return the env mapping a spawned external agent should receive."""
        return {TEAM_JOIN_ENV: self.to_json()}

    @classmethod
    def from_json(cls, raw: str) -> "TeamJoinDescriptor":
        """Parse a descriptor from its JSON string form.

        Raises:
            BaseError: ``AGENT_TEAM_CONFIG_INVALID`` when the payload is not
                a valid descriptor.
        """
        try:
            return cls.model_validate_json(raw)
        except ValidationError as e:
            raise_error(
                StatusCode.AGENT_TEAM_CONFIG_INVALID,
                reason=f"malformed team join descriptor: {e}",
                cause=e,
            )
            raise  # unreachable — raise_error always raises

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "TeamJoinDescriptor":
        """Load a descriptor from the ``OPENJIUWEN_TEAM_JOIN`` env var.

        Args:
            env: Optional environment mapping override (defaults to
                ``os.environ``); injectable for tests.

        Raises:
            BaseError: ``AGENT_TEAM_CONFIG_INVALID`` when the variable is
                unset or its value is not a valid descriptor.
        """
        source = env if env is not None else os.environ
        raw = source.get(TEAM_JOIN_ENV)
        if not raw:
            raise_error(
                StatusCode.AGENT_TEAM_CONFIG_INVALID,
                reason=f"environment variable {TEAM_JOIN_ENV} is not set; cannot join team",
            )
        return cls.from_json(raw)


__all__ = [
    "TEAM_JOIN_ENV",
    "TeamJoinDescriptor",
]
