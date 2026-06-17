# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""TeamPermissionRail — team-mode permission guardrail for teammate tool calls.

Extends ``PermissionInterruptRail`` with two team-specific overrides:

1. ``_persist_allow_always()`` returns ``False`` — leader approvals are
   session-scoped and should not be persisted to disk (the team config
   is shared across members; persisting per-member session decisions
   would pollute the shared config and not survive across sessions).
2. ``parse_confirm_payload()`` automatically sets ``decided_by="leader"`` on
   every approval response, returning ``TeamPermissionConfirmResponse`` instead
   of the base ``PermissionConfirmResponse``.

The ``TeamApprovalOrchestrator`` implements ``RequestPermissionConfirmationHook``
and is injected into the rail's ``ToolPermissionHost`` as the hosted confirmation
path.  When ``PermissionEngine`` returns ASK, the orchestrator sends a message
to the leader and returns ``"interrupt"`` to trigger the interrupt flow.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from openjiuwen.agent_teams.rails.confirm_payload import (
    TeamConfirmPayload,
    TeamPermissionConfirmResponse,
)
from openjiuwen.agent_teams.tools.message_manager import TeamMessageManager
from openjiuwen.core.common.logging import team_logger
from openjiuwen.harness.security.host import (
    PermissionConfirmationRequest,
    PermissionConfirmationResult,
)
from openjiuwen.harness.security.models import (
    PermissionConfirmResponse,
)
from openjiuwen.harness.rails.interrupt.confirm_rail import ConfirmPayload
from openjiuwen.harness.rails.security.tool_security_rail import PermissionInterruptRail


class TeamApprovalOrchestrator:
    """Route ASK decisions to the team leader for approval.

    Implements ``RequestPermissionConfirmationHook`` so it can be injected
    into ``ToolPermissionHost.request_permission_confirmation``.  On receiving
    an ASK result, it sends a message to the leader describing the tool call
    and returns ``"interrupt"`` to let the rail suspend the teammate.

    The leader later calls ``approve_tool``, which writes a DB message
    (protocol=json) and publishes a ``ToolApprovalResultEvent``.  The
    teammate reads the approval data from DB on resume.
    """

    def __init__(
        self,
        message_manager: TeamMessageManager,
        leader_member_name: str,
    ) -> None:
        self._message_manager = message_manager
        self._leader_member_name = leader_member_name

    async def handle_approval_request(
        self,
        request: PermissionConfirmationRequest,
    ) -> PermissionConfirmationResult:
        """Send approval request to leader and return ``"interrupt"``."""
        tool_call = request.tool_call
        tool_call_id = tool_call.id if tool_call else ""
        tool_name = tool_call.name if tool_call else ""
        tool_args = PermissionInterruptRail.parse_tool_args(tool_call)
        matched_rule = request.result.matched_rule or "N/A"

        args_preview = PermissionInterruptRail.format_args_preview(tool_args)

        content = (
            f"Teammate tool approval request (permission: ASK).\n"
            f"Member: {self._message_manager.member_name}\n"
            f"Tool call ID: {tool_call_id}\n"
            f"Tool: {tool_name}\n"
            f"Matched rule: {matched_rule}\n"
            f"Arguments:\n{args_preview}\n"
            "Please review and call approve_tool.\n"
        )

        message_id = await self._message_manager.send_message(
            content=content,
            to_member_name=self._leader_member_name,
        )

        if not message_id:
            team_logger.error(
                "[TeamPermission] approval.request_failed tool=%s member=%s",
                tool_name,
                self._message_manager.member_name,
            )
            return None

        team_logger.info(
            "[TeamPermission] approval.request_sent tool=%s member=%s leader=%s",
            tool_name,
            self._message_manager.member_name,
            self._leader_member_name,
        )
        return "interrupt"


class TeamPermissionRail(PermissionInterruptRail):
    """Team-mode permission rail — same logic as ``PermissionInterruptRail``
    but with leader-mediated ASK resolution and session-scoped auto-confirm.

    Key overrides:
    - ``_persist_allow_always()`` → ``False``: leader approvals are
      session-scoped; skip disk persistence entirely.
    - ``parse_confirm_payload()`` → ``TeamPermissionConfirmResponse`` with
      ``decided_by="leader"`` automatically set.
    """

    def _persist_allow_always(
        self,
        normalized_name: str,
        tool_args: dict,
    ) -> bool:
        """Leader approvals are session-scoped; never persist to disk."""
        return False

    def should_emit_interrupt_output(self) -> bool:
        """Team permission approvals are routed through the leader."""
        return False

    @staticmethod
    def parse_confirm_payload(
        user_input: Any,
    ) -> Optional[TeamPermissionConfirmResponse]:
        """Parse confirmation payload and automatically set ``decided_by="leader"``.

        Mirrors ``PermissionInterruptRail.parse_confirm_payload`` but returns
        ``TeamPermissionConfirmResponse`` with ``decided_by="leader"`` instead
        of the base ``PermissionConfirmResponse``.
        """
        if isinstance(user_input, TeamPermissionConfirmResponse):
            # Already a team response — preserve existing decided_by if set.
            if user_input.decided_by is None:
                return TeamPermissionConfirmResponse(
                    approved=user_input.approved,
                    feedback=user_input.feedback,
                    auto_confirm=user_input.auto_confirm,
                    decided_by="leader",
                )
            return user_input

        if isinstance(user_input, PermissionConfirmResponse):
            return TeamPermissionConfirmResponse(
                approved=user_input.approved,
                feedback=user_input.feedback,
                auto_confirm=user_input.auto_confirm,
                decided_by="leader",
            )

        # Parse from raw ConfirmPayload or dict — same as base class but
        # wrap result in TeamPermissionConfirmResponse.
        if isinstance(user_input, TeamConfirmPayload):
            return TeamPermissionConfirmResponse(
                approved=user_input.approved,
                feedback=user_input.feedback,
                auto_confirm=user_input.auto_confirm,
                decided_by="leader",
            )

        if isinstance(user_input, ConfirmPayload):
            return TeamPermissionConfirmResponse(
                approved=user_input.approved,
                feedback=user_input.feedback,
                auto_confirm=user_input.auto_confirm,
                decided_by="leader",
            )

        if isinstance(user_input, dict):
            try:
                payload = TeamConfirmPayload.model_validate(user_input)
            except Exception:
                try:
                    payload = ConfirmPayload.model_validate(user_input)
                except Exception:
                    return None
            return TeamPermissionConfirmResponse(
                approved=payload.approved,
                feedback=payload.feedback,
                auto_confirm=payload.auto_confirm,
                decided_by="leader",
            )

        if isinstance(user_input, str):
            try:
                raw_payload = json.loads(user_input)
            except Exception:
                return None
            if not isinstance(raw_payload, dict):
                return None
            return TeamPermissionRail.parse_confirm_payload(raw_payload)

        return None


__all__ = ["TeamPermissionRail", "TeamApprovalOrchestrator"]
