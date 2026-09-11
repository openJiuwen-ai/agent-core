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
        model = payload.model or "<unknown>"
        empty_field = "<unknown>"
        team_logger.info(
            "[external-runtime] member {} {} retrying model={} category={} summary={}",
            member_name,
            agent_kind,
            model,
            category,
            summary,
        )
        await self._round.deliver_input(
            t(
                "reliability.external_runtime_retrying",
                member_name=member_name,
                agent_kind=agent_kind,
                model=model,
                category=category,
                summary=summary,
                reason_message=payload.reason.message or empty_field,
                http_status=payload.reason.http_status
                if payload.reason.http_status is not None
                else empty_field,
                sdk_error_code=payload.reason.sdk_error_code or empty_field,
                attempt=payload.attempt if payload.attempt is not None else empty_field,
                max_attempts=payload.max_attempts if payload.max_attempts is not None else empty_field,
            )
        )


__all__ = ["ExternalRuntimeHandler"]
