# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Leader-side handler for third-party external runtime retrying progress.

Consumes the ``EXTERNAL_RUNTIME_RETRYING`` cross-process event (published by
Claude/Codex SDK runtimes when the SDK is still auto-retrying) and surfaces it
to the leader as a non-persistent progress nudge. Final failures are NOT
handled here — they are persisted to the leader mailbox as
``external_runtime_failed`` JSON messages and delivered by ``MessageHandler``.

This handler is the retrying counterpart to ``ReliabilityHandler``: both are
leader-only coordination handlers that route a signal into the leader's own
loop via ``deliver_input``. They stay decoupled — neither calls the other.
"""

from __future__ import annotations

from typing import ClassVar

from openjiuwen.agent_teams.agent.coordination.handlers.base import BaseCoordinationHandler
from openjiuwen.agent_teams.i18n import t
from openjiuwen.agent_teams.schema.events import (
    EventMessage,
    ExternalRuntimeRetryingEvent,
    TeamEvent,
)
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.core.common.logging import team_logger


class ExternalRuntimeHandler(BaseCoordinationHandler):
    """Surface Claude/Codex SDK auto-retry progress to the leader (leader only)."""

    EVENT_METHOD_MAP: ClassVar[dict[str, str]] = {
        TeamEvent.EXTERNAL_RUNTIME_RETRYING: "on_external_retry",
    }

    async def on_external_retry(self, event: EventMessage) -> None:
        """Deliver a retrying progress nudge to the leader's round input."""
        if self._blueprint.role != TeamRole.LEADER:
            return
        try:
            payload = event.get_payload()
        except ValueError:
            team_logger.warning("[external-runtime] retrying event payload unrecognized; skipping")
            return
        if not isinstance(payload, ExternalRuntimeRetryingEvent):
            team_logger.warning("[external-runtime] retrying event payload type mismatch; skipping")
            return
        member_name = payload.member_name or "unknown"
        category = payload.category
        summary = payload.summary
        agent_kind = payload.agent_kind
        team_logger.info(
            "[external-runtime] member {} {} retrying category={} summary={}",
            member_name,
            agent_kind,
            category,
            summary,
        )
        await self._round.deliver_input(
            t(
                "reliability.external_runtime_retrying",
                member_name=member_name,
                agent_kind=agent_kind,
                category=category,
                summary=summary,
            )
        )


__all__ = ["ExternalRuntimeHandler"]
