# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Messaging tool: send_message (point-to-point, multicast, and broadcast)."""

from typing import Any, Awaitable, Callable

from openjiuwen.agent_teams.tools.locales import Translator
from openjiuwen.agent_teams.tools.message_manager import TeamMessageManager
from openjiuwen.agent_teams.tools.team import TeamBackend
from openjiuwen.agent_teams.tools.tool_base import TeamTool
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.foundation.tool.base import ToolCard
from openjiuwen.harness.tools.base_tool import ToolOutput


# ========== Messaging ==========


class SendMessageTool(TeamTool):
    """Send a message to team members (point-to-point or broadcast)."""

    def __init__(
        self,
        message_manager: TeamMessageManager,
        t: Translator,
        team: TeamBackend | None = None,
        on_teammate_created: Callable[[str], Awaitable[None]] | None = None,
    ):
        super().__init__(
            ToolCard(
                id="team.send_message",
                name="send_message",
                description=t("send_message"),
            )
        )
        self.message_manager = message_manager
        self._team = team
        self._on_teammate_created = on_teammate_created
        self.card.input_params = {
            "type": "object",
            "properties": {
                "to": {
                    "anyOf": [
                        {"type": "string"},
                        {"type": "array", "items": {"type": "string"}, "minItems": 1},
                    ],
                    "description": t("send_message", "to"),
                },
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

        try:
            return await self._dispatch(to_raw, content, summary)
        except Exception as e:
            team_logger.error(f"send_message failed: {e}")
            return ToolOutput(success=False, error=f"Internal error: {e}")

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
        if self._team and to != "user":
            member = await self._team.get_member(to)
            if not member:
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
        if "user" in deduped:
            return ToolOutput(
                success=False,
                error="'user' cannot be combined in multicast; send to user separately",
            )

        # A multicast covering every other team member is just a more
        # expensive broadcast — reject it and force the caller onto the
        # broadcast path. list_members() already excludes the caller, so
        # an exact set match means the targets are the whole roster.
        if self._team:
            roster = {member.member_name for member in await self._team.list_members()}
            if roster and set(deduped) == roster:
                return ToolOutput(
                    success=False,
                    error=(
                        "Multicast targets cover every other team member; "
                        "use to='*' to broadcast instead — same delivery, lower cost."
                    ),
                )

        await self._auto_start_members()

        delivered: list[str] = []
        failed: list[dict[str, str]] = []
        for name in deduped:
            if self._team:
                member = await self._team.get_member(name)
                if not member:
                    failed.append({"to": name, "reason": f"Member '{name}' not found"})
                    continue
            msg_id = await self.message_manager.send_message(
                content=content,
                to_member_name=name,
            )
            if not msg_id:
                failed.append({"to": name, "reason": f"Failed to send message to '{name}'"})
                continue
            delivered.append(name)

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
