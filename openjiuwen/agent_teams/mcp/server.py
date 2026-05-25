# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""FastMCP stdio server exposing team collaboration to an external agent.

The server wraps :class:`ExternalTeamClient`: each MCP tool maps to one
collaboration operation. The coordination protocol travels with the server
as FastMCP server-level ``instructions`` (the MCP equivalent of the CLI's
SKILL.md), so an MCP-capable agent needs no separate skill file — tool
schemas are self-describing and the workflow lives in the instructions.

The team connection descriptor is read lazily from the ``OPENJIUWEN_TEAM_JOIN``
environment variable on the first tool call, inside the server's own event
loop, so messager / database resources bind to the loop that uses them.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from mcp.server.fastmcp import FastMCP

from openjiuwen.agent_teams.context import set_session_id
from openjiuwen.agent_teams.external.client import ExternalTeamClient
from openjiuwen.agent_teams.external.descriptor import TeamJoinDescriptor
from openjiuwen.agent_teams.external.format import render_task_board
from openjiuwen.agent_teams.i18n import set_language

SERVER_NAME = "openjiuwen-team-member"

INSTRUCTIONS = """\
You are a member of an OpenJiuWen agent team. Collaborate with the leader and
teammates over a shared task board and mailbox using these tools. The team
does not see your free text, your reasoning, or your local file edits — only
the tool calls you make here are visible to the team.

Workflow for an assigned task — do every step, in order:
1. read_inbox / get_task — read the full requirement and the task_id.
2. claim_task(task_id) — take ownership.
3. Do the actual work (e.g. write the requested file).
4. complete_task(task_id) — MANDATORY. The task stays OPEN until you call it;
   writing a file or replying in plain text does NOT complete the task.
5. send_message to the leader — MANDATORY. Report what you did.

A turn is finished only after BOTH complete_task AND send_message have been
called. Do not stop after merely claiming or after doing the work — that
leaves the task unfinished and the leader waiting. Refer to members by name.
An empty inbox is normal; reading it marks messages read.
"""

# Async callable that produces a connected ExternalTeamClient.
ClientFactory = Callable[[], Awaitable[ExternalTeamClient]]


async def connect_from_env() -> ExternalTeamClient:
    """Build and connect a client from the ``OPENJIUWEN_TEAM_JOIN`` env var."""
    client = ExternalTeamClient(TeamJoinDescriptor.from_env())
    await client.connect()
    return client


class _ClientHolder:
    """Lazily connects the client once, inside the server event loop."""

    def __init__(self, factory: ClientFactory):
        self._factory = factory
        self._client: ExternalTeamClient | None = None

    async def get(self) -> ExternalTeamClient:
        if self._client is None:
            self._client = await self._factory()
        # FastMCP handles each tool call in its own task, so the session-id
        # contextvar bound inside ``connect()`` does not survive to later
        # calls. Re-assert it (and language) on every call so the per-session
        # dynamic table names resolve consistently — otherwise a later call
        # sees an empty session id and targets a non-existent table.
        set_session_id(self._client.session_id)
        set_language(self._client.language)  # type: ignore[arg-type]
        return self._client


def build_server(client_factory: ClientFactory = connect_from_env) -> FastMCP:
    """Build the FastMCP server with team collaboration tools.

    Args:
        client_factory: Async callable returning a connected
            :class:`ExternalTeamClient`. Defaults to reading the descriptor
            from the environment; tests inject a pre-built client.

    Returns:
        A configured :class:`FastMCP` instance (call ``.run()`` to serve).
    """
    holder = _ClientHolder(client_factory)
    mcp = FastMCP(SERVER_NAME, instructions=INSTRUCTIONS)

    @mcp.tool()
    async def read_inbox() -> dict[str, Any]:
        """Read unread messages addressed to you plus the current task board.

        Reading marks the returned messages as read.
        """
        client = await holder.get()
        view = await client.fetch_inbox(mark_read=True)
        return {
            "messages": [
                {
                    "message_id": m.message_id,
                    "from": m.from_member_name,
                    "content": m.content,
                    "broadcast": m.broadcast,
                }
                for m in view.messages
            ],
            "task_board": render_task_board(view.tasks, is_leader=client.is_leader),
        }

    @mcp.tool()
    async def send_message(to: str, content: str) -> dict[str, Any]:
        """Send a direct message to a member (use ``*`` to broadcast)."""
        client = await holder.get()
        message_id = await client.send_message(to, content)
        return {"ok": message_id is not None, "message_id": message_id}

    @mcp.tool()
    async def list_tasks(status: str | None = None) -> list[dict[str, Any]]:
        """List team tasks, optionally filtered by status."""
        client = await holder.get()
        return [
            {"task_id": t.task_id, "title": t.title, "status": t.status, "assignee": t.assignee}
            for t in await client.list_tasks(status=status)
        ]

    @mcp.tool()
    async def claimable_tasks() -> list[dict[str, Any]]:
        """List pending tasks available to claim."""
        client = await holder.get()
        return [{"task_id": t.task_id, "title": t.title} for t in await client.claimable_tasks()]

    @mcp.tool()
    async def get_task(task_id: str) -> dict[str, Any] | None:
        """Get one task's full detail, or null if it does not exist."""
        client = await holder.get()
        detail = await client.get_task(task_id)
        return detail.model_dump() if detail is not None else None

    @mcp.tool()
    async def claim_task(task_id: str) -> dict[str, Any]:
        """Claim a pending task for yourself."""
        client = await holder.get()
        result = await client.claim_task(task_id)
        return {"ok": result.ok, "reason": result.reason}

    @mcp.tool()
    async def complete_task(task_id: str) -> dict[str, Any]:
        """Complete a task you have claimed."""
        client = await holder.get()
        result = await client.complete_task(task_id)
        return {"ok": result.ok, "reason": result.reason}

    @mcp.tool()
    async def update_task(
        task_id: str,
        title: str | None = None,
        content: str | None = None,
    ) -> dict[str, Any]:
        """Edit a task's title and/or content (pending / blocked tasks only)."""
        client = await holder.get()
        result = await client.update_task(task_id, title=title, content=content)
        return {"ok": result.ok, "reason": result.reason}

    @mcp.tool()
    async def list_members() -> list[dict[str, Any]]:
        """List the team's members."""
        client = await holder.get()
        return [{"member_name": m.member_name, "role": m.role, "status": m.status} for m in await client.list_members()]

    return mcp


def main() -> None:
    """console_scripts entry point: serve over stdio."""
    build_server().run(transport="stdio")


if __name__ == "__main__":
    main()
