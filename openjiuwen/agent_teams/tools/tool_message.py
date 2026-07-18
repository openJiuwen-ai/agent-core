# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Messaging tool: send_message (point-to-point, multicast, and broadcast)."""

from abc import ABC, abstractmethod
from typing import Any, Awaitable, Callable

from openjiuwen.agent_teams.constants import USER_PSEUDO_MEMBER_NAME
from openjiuwen.agent_teams.tools.locales import Translator

# Role placeholder a scheduled-dispatch member addresses instead of the
# leader's concrete member_name. The tool resolves it to the real leader at
# delivery time, so the schema stays stable across leader renames and never
# leaks a specific identity.
LEADER_ROLE_RECIPIENT = "leader"

# Upper bound on ``content`` length, in characters. Past this size the body
# is an artifact, not a message, and belongs in a file under the shared team
# workspace with the message carrying only its path plus a summary. Counted
# in characters rather than tokens: no tokenizer dependency, no per-language
# branch, and the bound only has to be the right order of magnitude. Roughly
# one screenful of Chinese; ordinary instructions, replies and summaries land
# far below it.
MAX_CONTENT_CHARS = 2000
from openjiuwen.agent_teams.tools.message_manager import TeamMessageManager
from openjiuwen.agent_teams.tools.team import TeamBackend
from openjiuwen.agent_teams.tools.tool_base import TeamTool
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.foundation.tool.base import ToolCard
from openjiuwen.harness.tools.base_tool import ToolOutput


# ========== Messaging ==========


class _SendMessageBase(TeamTool, ABC):
    """Shared body of the ``send_message`` variants.

    Variants share ``ToolCard.id`` / ``name`` and the delivery primitives
    (``_send`` / ``_multicast`` / ``_broadcast``); they differ only in the
    ``to`` schema and in how ``_dispatch`` routes it. Both the schema and
    ``_dispatch`` enforce the contract: the schema is what the host LLM
    sees, while ``_dispatch`` is what an MCP client hits — ``mcp/server.py``
    invokes the tool directly and never validates against the schema.
    """

    def __init__(
        self,
        message_manager: TeamMessageManager,
        t: Translator,
        team: TeamBackend | None = None,
        on_teammate_created: Callable[[str], Awaitable[None]] | None = None,
        *,
        desc_key: str,
        to_schema: dict,
    ):
        super().__init__(
            ToolCard(
                id="team.send_message",
                name="send_message",
                description=t(desc_key),
            )
        )
        self.message_manager = message_manager
        self.t = t
        self._team = team
        self._on_teammate_created = on_teammate_created
        self.card.input_params = {
            "type": "object",
            "properties": {
                "to": to_schema,
                "content": {"type": "string", "description": t("send_message", "content")},
                "summary": {"type": "string", "description": t("send_message", "summary")},
            },
            "required": ["to", "content"],
        }

    async def invoke(self, inputs: dict[str, Any], **kwargs) -> ToolOutput:
        to_raw = inputs.get("to")
        content = inputs.get("content", "").strip()
        summary = inputs.get("summary", "").strip()

        if not content:
            return ToolOutput(success=False, error="'content' is required")
        oversize = self._reject_oversize_content(content)
        if oversize:
            return oversize

        try:
            return await self._dispatch(to_raw, content, summary)
        except Exception as e:
            team_logger.error(f"send_message failed: {e}")
            return ToolOutput(success=False, error=f"Internal error: {e}")

    @abstractmethod
    async def _dispatch(self, to_raw: Any, content: str, summary: str) -> ToolOutput:
        """Route the request to a delivery primitive based on ``to``."""
        ...

    def _reject_oversize_content(self, content: str) -> ToolOutput | None:
        """Bounce an artifact-sized body back so it moves to a file handoff.

        Sits in ``invoke``, ahead of ``_dispatch``, so one check covers every
        variant and every recipient — unicast, multicast, broadcast and the
        user alike — and also catches MCP clients, which reach ``invoke``
        without ever validating against the schema. The rule is about the
        shape of the content, so no recipient earns an exemption: the user
        reads a handed-off path through their own assistant agent.

        Args:
            content: The stripped message body.

        Returns:
            A failure ``ToolOutput`` telling the caller to write a file first,
            or ``None`` when the body is within bounds.
        """
        if len(content) <= MAX_CONTENT_CHARS:
            return None
        return ToolOutput(
            success=False,
            error=self.t(
                "send_message",
                "error_content_too_long",
                actual=len(content),
                limit=MAX_CONTENT_CHARS,
            ),
        )

    async def _broadcast(self, content: str, summary: str) -> ToolOutput:
        await self._auto_start_members()
        msg_id = await self.message_manager.broadcast_message(content=content)
        if not msg_id:
            return ToolOutput(success=False, error="Failed to broadcast message")
        return ToolOutput(
            success=True,
            data={
                "type": "broadcast",
                "from": self.message_manager.member_name,
                "summary": summary or None,
            },
        )

    async def _send(self, to: str, content: str, summary: str) -> ToolOutput:
        # "user" is the pseudo-member representing the human caller; skip
        # roster validation so teammates can reply through the same tool.
        if self._team and to != USER_PSEUDO_MEMBER_NAME:
            if not await self._team.member_exists(to):
                return ToolOutput(success=False, error=f"Member '{to}' not found")
        await self._auto_start_members()
        msg_id = await self.message_manager.send_message(content=content, to_member_name=to)
        if not msg_id:
            return ToolOutput(success=False, error=f"Failed to send message to '{to}'")
        return ToolOutput(
            success=True,
            data={
                "type": "message",
                "from": self.message_manager.member_name,
                "to": to,
                "summary": summary or None,
            },
        )

    async def _multicast(
        self,
        targets: list[str],
        content: str,
        summary: str,
    ) -> ToolOutput:
        """Send identical content to multiple members as independent point-to-point messages.

        Strict success: only returns success=True when every target receives the message.
        On any failure the data still carries delivered/failed lists so callers can
        avoid resending to members who already got the message.
        """
        # Strip + drop blanks while preserving order, then de-duplicate.
        stripped = [item.strip() if isinstance(item, str) else "" for item in targets]
        cleaned = [item for item in stripped if item]
        deduped = list(dict.fromkeys(cleaned))

        if not deduped:
            return ToolOutput(
                success=False,
                error="'to' list must contain at least one member name",
            )
        if "*" in deduped:
            return ToolOutput(
                success=False,
                error="Cannot mix broadcast '*' with member names; use to='*' for broadcast",
            )
        if USER_PSEUDO_MEMBER_NAME in deduped:
            return ToolOutput(
                success=False,
                error="'user' cannot be combined in multicast; send to user separately",
            )

        # A multicast covering every other team member is just a more
        # expensive broadcast — reject it and force the caller onto the
        # broadcast path. list_member_roster() already excludes the caller,
        # so an exact set match means the targets are the whole roster.
        if self._team:
            roster = {member.member_name for member in await self._team.list_member_roster()}
            if roster and set(deduped) == roster:
                return ToolOutput(
                    success=False,
                    error=(
                        "Multicast targets cover every other team member; "
                        "use to='*' to broadcast instead — same delivery, lower cost."
                    ),
                )

        await self._auto_start_members()

        # Split into existing members (valid) and not-found up front — the
        # existence check is a read. The valid set is then written in ONE
        # batched transaction (one fsync) instead of one write per recipient.
        failed: list[dict[str, str]] = []
        valid: list[str] = []
        for name in deduped:
            if self._team:
                if not await self._team.member_exists(name):
                    failed.append({"to": name, "reason": f"Member '{name}' not found"})
                    continue
            valid.append(name)

        delivered: list[str] = []
        if valid:
            ids = await self.message_manager.multicast_message(content=content, to_member_names=valid)
            if ids:
                delivered = valid
            else:
                # The batch is atomic — a failed write delivers to nobody, so
                # every valid target is reported failed for the caller to resend.
                failed.extend({"to": name, "reason": f"Failed to send message to '{name}'"} for name in valid)

        total = len(deduped)
        ok = not failed
        return ToolOutput(
            success=ok,
            error=(None if ok else f"Multicast partially failed: {len(failed)}/{total} target(s) failed"),
            data={
                "type": "multicast",
                "from": self.message_manager.member_name,
                "delivered": delivered,
                "failed": failed,
                "summary": summary or None,
            },
        )

    async def _auto_start_members(self) -> None:
        """Auto-start unstarted members if leader with startup callback."""
        if self._team and self._on_teammate_created and self._team.is_leader:
            started = await self._team.startup(on_created=self._on_teammate_created)
            if started:
                team_logger.info(f"Auto-started members: {started}")

    def map_result(self, output: ToolOutput) -> str:
        d = output.data
        if not output.success:
            base = output.error or "Failed to send message"
            if isinstance(d, dict) and d.get("type") == "multicast":
                return self._format_multicast_text(base, d)
            return base
        if d["type"] == "broadcast":
            return f"Broadcast sent from {d['from']}"
        if d["type"] == "multicast":
            return self._format_multicast_text(None, d)
        return f"Message sent from {d['from']} to {d['to']}"

    @staticmethod
    def _format_multicast_text(error: str | None, d: dict[str, Any]) -> str:
        """Render multicast outcome including delivered/failed lists when present."""
        delivered: list[str] = d.get("delivered", []) or []
        failed: list[dict[str, str]] = d.get("failed", []) or []
        sender = d.get("from", "")
        parts: list[str] = []
        if error:
            parts.append(error)
        else:
            head = f"Multicast sent from {sender}"
            if delivered:
                head += f" to: {', '.join(delivered)}"
            head += f" ({len(delivered)} delivered)"
            parts.append(head)
        if error and delivered:
            parts.append(f"delivered: {', '.join(delivered)}")
        if failed:
            failed_text = "; ".join(f"{item['to']} — {item['reason']}" for item in failed)
            parts.append(f"failed: {failed_text}")
        return "; ".join(parts)


class SendMessageTool(_SendMessageBase):
    """Full-reach ``send_message``: point-to-point, multicast, or broadcast."""

    def __init__(
        self,
        message_manager: TeamMessageManager,
        t: Translator,
        team: TeamBackend | None = None,
        on_teammate_created: Callable[[str], Awaitable[None]] | None = None,
    ):
        super().__init__(
            message_manager,
            t,
            team,
            on_teammate_created,
            desc_key="send_message",
            to_schema={
                "anyOf": [
                    {"type": "string"},
                    {"type": "array", "items": {"type": "string"}, "minItems": 1},
                ],
                "description": t("send_message", "to"),
            },
        )

    async def _dispatch(self, to_raw: Any, content: str, summary: str) -> ToolOutput:
        """Route the request based on the runtime type of ``to``."""
        if isinstance(to_raw, list):
            return await self._multicast(to_raw, content, summary)
        if isinstance(to_raw, str):
            to = to_raw.strip()
            if not to:
                return ToolOutput(success=False, error="'to' is required")
            if to == "*":
                return await self._broadcast(content, summary)
            return await self._send(to, content, summary)
        return ToolOutput(
            success=False,
            error="'to' must be a string or an array of strings",
        )


class ReportToLeaderTool(_SendMessageBase):
    """Scheduled-dispatch member ``send_message``: reaches the leader or the user only.

    Members never coordinate peer-to-peer under scheduled dispatch — the
    leader routes everything — so the schema offers exactly two recipients
    (the role word ``"leader"`` and ``"user"``) and drops multicast /
    broadcast entirely. Replying to the user stays reachable: it is the
    member's only channel back to the human caller.

    ``"leader"`` is a role placeholder, not a member_name. The tool resolves
    it to the concrete leader at delivery time, so the schema neither leaks
    the leader's identity nor requires it to be known at construction.
    """

    _ALLOWED = (LEADER_ROLE_RECIPIENT, USER_PSEUDO_MEMBER_NAME)

    def __init__(
        self,
        message_manager: TeamMessageManager,
        t: Translator,
        team: TeamBackend | None = None,
        on_teammate_created: Callable[[str], Awaitable[None]] | None = None,
    ):
        super().__init__(
            message_manager,
            t,
            team,
            on_teammate_created,
            desc_key="send_message_scheduled",
            to_schema={
                "type": "string",
                "enum": list(self._ALLOWED),
                "description": t("send_message_scheduled", "to"),
            },
        )

    async def _dispatch(self, to_raw: Any, content: str, summary: str) -> ToolOutput:
        """Resolve the role word and deliver; reject anything else.

        The enum already tells the host LLM what is reachable. This check is
        what stops an MCP client — which calls ``invoke`` without validating
        against the schema — from reaching a peer behind the leader's back.
        """
        if not isinstance(to_raw, str):
            return ToolOutput(success=False, error="'to' must be a string")
        to = to_raw.strip()
        if to == USER_PSEUDO_MEMBER_NAME:
            return await self._send(USER_PSEUDO_MEMBER_NAME, content, summary)
        if to == LEADER_ROLE_RECIPIENT:
            leader = (await self._team.resolve_leader_member_name()).strip() if self._team else ""
            if not leader:
                return ToolOutput(
                    success=False,
                    error="cannot resolve the team leader to deliver to; the team has no leader on record",
                )
            return await self._send(leader, content, summary)
        return ToolOutput(
            success=False,
            error=(
                f"'to' must be one of {list(self._ALLOWED)}; this team runs in scheduled "
                f"dispatch mode, where members report to the leader instead of contacting peers"
            ),
        )
