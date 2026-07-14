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
loop, so messager / database resources bind to the loop that uses them. The
member's *role* (from the same descriptor) is resolved up front at build time
so the server only registers the tools that role is allowed to call.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from mcp.server.fastmcp import FastMCP

from openjiuwen.agent_teams.context import set_session_id
from openjiuwen.agent_teams.external.client import ExternalTeamClient
from openjiuwen.agent_teams.external.descriptor import TeamJoinDescriptor
from openjiuwen.agent_teams.external.format import render_task_board
from openjiuwen.agent_teams.i18n import set_language
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.agent_teams.timefmt import format_time_context
from openjiuwen.agent_teams.tools.database.engine import get_current_time
from openjiuwen.core.common.logging import team_logger

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

# Stable MCP tool names, kept in one place so the role→tools map below and the
# registrations cannot drift apart. These are the names FastMCP exposes; they
# are part of the public surface other code/tests depend on — do not rename.
TOOL_READ_INBOX = "read_inbox"
TOOL_SEND_MESSAGE = "send_message"
TOOL_LIST_TASKS = "list_tasks"
TOOL_CLAIMABLE_TASKS = "claimable_tasks"
TOOL_GET_TASK = "get_task"
TOOL_CLAIM_TASK = "claim_task"
TOOL_COMPLETE_TASK = "complete_task"
TOOL_UPDATE_TASK = "update_task"
TOOL_LIST_MEMBERS = "list_members"

# Read / messaging / inbox tools that carry no task-ownership semantics. Every
# role gets these — they are safe for an observer or a reviewer to call.
_READ_AND_MESSAGE_TOOLS: frozenset[str] = frozenset(
    {
        TOOL_READ_INBOX,
        TOOL_SEND_MESSAGE,
        TOOL_LIST_TASKS,
        TOOL_CLAIMABLE_TASKS,
        TOOL_GET_TASK,
        TOOL_LIST_MEMBERS,
    }
)

# Task-ownership tools: claiming a task and marking it done. A reviewer-style
# member must NOT own work, so these are gated by role.
_TASK_LIFECYCLE_TOOLS: frozenset[str] = frozenset(
    {
        TOOL_CLAIM_TASK,
        TOOL_COMPLETE_TASK,
        TOOL_UPDATE_TASK,
    }
)

# Every tool the server knows how to register. Used to compute the leader set
# (leader gets everything) and to validate the role map.
_ALL_TOOLS: frozenset[str] = _READ_AND_MESSAGE_TOOLS | _TASK_LIFECYCLE_TOOLS

# Role → the set of tool names that role may call.
#
# The mechanism is generic on purpose: adding a new role (e.g. a future
# ``reviewer`` that should see the board and speak but never claim/complete
# work) is a one-line entry here, with no change to the registration code.
# ``TeamRole`` today only declares LEADER / TEAMMATE / HUMAN_AGENT /
# BRIDGE_AGENT — there is no reviewer role yet — so the table covers the
# roles that actually reach this MCP server: leader and teammate. A
# bridge-agent avatar runs as a full local teammate, and a human-agent avatar
# relays on its user's behalf; both currently attach through the in-process
# path rather than this external MCP server, so they fall through to the
# default below.
#
# Unlisted roles fall back to ``_READ_AND_MESSAGE_TOOLS`` (read + messaging,
# no task ownership) — the conservative choice for any not-yet-modelled role.
_ROLE_TOOLS: dict[TeamRole, frozenset[str]] = {
    TeamRole.LEADER: _ALL_TOOLS,
    TeamRole.TEAMMATE: _READ_AND_MESSAGE_TOOLS | _TASK_LIFECYCLE_TOOLS,
}


def _allowed_tools_for_role(role: str) -> frozenset[str]:
    """Resolve the set of tool names a member of ``role`` may call.

    Args:
        role: The member role string from the team-join descriptor (e.g.
            ``"leader"`` / ``"teammate"``). Matched case-insensitively against
            :class:`TeamRole`.

    Returns:
        The frozen set of MCP tool names to expose. Unknown roles fall back to
        the read + messaging set (no task ownership), keeping an unmodelled
        role safe by default rather than over-privileged.
    """
    try:
        resolved = TeamRole(role.lower())
    except ValueError:
        team_logger.warning(
            "unknown member role %r for team MCP server; exposing read/messaging tools only",
            role,
        )
        return _READ_AND_MESSAGE_TOOLS
    return _ROLE_TOOLS.get(resolved, _READ_AND_MESSAGE_TOOLS)


async def connect_from_env() -> ExternalTeamClient:
    """Build and connect a client from the ``OPENJIUWEN_TEAM_JOIN`` env var."""
    client = ExternalTeamClient(TeamJoinDescriptor.from_env())
    await client.connect()
    return client


def _bind_session_context(client: ExternalTeamClient) -> None:
    """Re-assert the session-id / language contextvars from ``client``.

    FastMCP runs every tool call in its own ``asyncio.Task``. The session-id
    contextvar bound inside :meth:`ExternalTeamClient.connect` only lives in
    that connect call's task context, so it does NOT survive to later tool
    calls running in fresh tasks. Without re-binding, a later call would see
    an empty session id and target a non-existent per-session dynamic table.

    The descriptor (and therefore the session id / language) is owned by the
    client instance, so this re-binds deterministically from that single
    source of truth on every call. Centralising it here keeps the invariant
    explicit instead of re-asserting it ad hoc at each call site.
    """
    set_session_id(client.session_id)
    set_language(client.language)  # type: ignore[arg-type]


class _ClientHolder:
    """Lazily connects the client once, inside the server event loop.

    Connection happens on first use; every subsequent ``get`` re-binds the
    per-call session context (see :func:`_bind_session_context`).
    """

    def __init__(self, factory: ClientFactory):
        self._factory = factory
        self._client: ExternalTeamClient | None = None

    async def get(self) -> ExternalTeamClient:
        """Return the connected client, with session context bound for this call."""
        if self._client is None:
            self._client = await self._factory()
        _bind_session_context(self._client)
        return self._client


def build_server(
    client_factory: ClientFactory = connect_from_env,
    *,
    role: str | None = None,
) -> FastMCP:
    """Build the FastMCP server with role-scoped team collaboration tools.

    Only the tools the member's role is allowed to call are registered, so a
    read-only / reviewer-style member never sees ``claim_task`` /
    ``complete_task``. The role→tools mapping lives in :data:`_ROLE_TOOLS`.

    Args:
        client_factory: Async callable returning a connected
            :class:`ExternalTeamClient`. Defaults to reading the descriptor
            from the environment; tests inject a pre-built client.
        role: The member role driving tool scoping. When ``None`` (default,
            the production path) it is read from the ``OPENJIUWEN_TEAM_JOIN``
            descriptor in the environment. Tests pass it explicitly to avoid
            depending on process env.

    Returns:
        A configured :class:`FastMCP` instance (call ``.run()`` to serve).
    """
    resolved_role = role if role is not None else TeamJoinDescriptor.from_env().role
    allowed = _allowed_tools_for_role(resolved_role)
    team_logger.info(
        "team MCP server tools for role=%s: %s",
        resolved_role,
        sorted(allowed),
    )

    holder = _ClientHolder(client_factory)
    mcp = FastMCP(SERVER_NAME, instructions=INSTRUCTIONS)

    async def read_inbox() -> dict[str, Any]:
        """Read unread messages addressed to you plus the current task board.

        Reading marks the returned messages as read.
        """
        client = await holder.get()
        view = await client.fetch_inbox(mark_read=True)
        now_ms = get_current_time()
        return {
            "messages": [
                {
                    "message_id": m.message_id,
                    "from": m.from_member_name,
                    "content": m.content,
                    "broadcast": m.broadcast,
                    "time": format_time_context(m.timestamp, now_ms),
                }
                for m in view.messages
            ],
            "task_board": render_task_board(view.tasks, is_leader=client.is_leader, now_ms=now_ms),
        }

    async def send_message(to: str, content: str) -> dict[str, Any]:
        """Send a direct message to a member (use ``*`` to broadcast)."""
        client = await holder.get()
        message_id = await client.send_message(to, content)
        return {"ok": message_id is not None, "message_id": message_id}

    async def list_tasks(status: str | None = None) -> list[dict[str, Any]]:
        """List team tasks, optionally filtered by status."""
        client = await holder.get()
        now_ms = get_current_time()
        return [
            {
                "task_id": t.task_id,
                "title": t.title,
                "status": t.status,
                "assignee": t.assignee,
                "time": format_time_context(t.updated_at, now_ms),
            }
            for t in await client.list_tasks(status=status)
        ]

    async def claimable_tasks() -> list[dict[str, Any]]:
        """List pending tasks available to claim."""
        client = await holder.get()
        return [{"task_id": t.task_id, "title": t.title} for t in await client.claimable_tasks()]

    async def get_task(task_id: str) -> dict[str, Any] | None:
        """Get one task's full detail, or null if it does not exist."""
        client = await holder.get()
        detail = await client.get_task(task_id)
        return detail.model_dump() if detail is not None else None

    async def claim_task(task_id: str) -> dict[str, Any]:
        """Claim a pending task for yourself."""
        client = await holder.get()
        result = await client.claim_task(task_id)
        return {"ok": result.ok, "reason": result.reason}

    async def complete_task(task_id: str) -> dict[str, Any]:
        """Complete a task you have claimed."""
        client = await holder.get()
        result = await client.complete_task(task_id)
        return {"ok": result.ok, "reason": result.reason}

    async def update_task(
        task_id: str,
        title: str | None = None,
        content: str | None = None,
    ) -> dict[str, Any]:
        """Edit a task's title and/or content (pending / blocked tasks only)."""
        client = await holder.get()
        result = await client.update_task(task_id, title=title, content=content)
        return {"ok": result.ok, "reason": result.reason}

    async def list_members() -> list[dict[str, Any]]:
        """List the team's members."""
        client = await holder.get()
        return [
            {"member_name": m.member_name, "role": m.role, "status": m.status}
            for m in await client.list_members()
        ]

    # Single registry of every tool function keyed by its public name. The
    # role map (above) is the only thing that decides which of these get
    # exposed — registration has no per-role branches, so the map stays the
    # single source of truth.
    tool_registry: dict[str, Callable[..., Awaitable[Any]]] = {
        TOOL_READ_INBOX: read_inbox,
        TOOL_SEND_MESSAGE: send_message,
        TOOL_LIST_TASKS: list_tasks,
        TOOL_CLAIMABLE_TASKS: claimable_tasks,
        TOOL_GET_TASK: get_task,
        TOOL_CLAIM_TASK: claim_task,
        TOOL_COMPLETE_TASK: complete_task,
        TOOL_UPDATE_TASK: update_task,
        TOOL_LIST_MEMBERS: list_members,
    }

    for name, fn in tool_registry.items():
        if name in allowed:
            mcp.tool(name=name)(fn)

    return mcp


def main() -> None:
    """console_scripts entry point: serve over stdio."""
    build_server().run(transport="stdio")


if __name__ == "__main__":
    main()
