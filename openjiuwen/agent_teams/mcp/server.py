# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""stdio MCP server exposing team collaboration to an external agent.

Two scenarios, picked from the team-join descriptor's ``scope`` (resolved at
first connect) — see :class:`TeamJoinDescriptor`:

* ``member`` — a spawned third-party CLI acting as a first-class team member.
  The server exposes the **real** team tools (``view_task`` / ``claim_task`` /
  ``send_message``) built by ``create_team_tools(role="teammate")``, so the
  external member calls the exact same ``TeamTool`` instances — same input
  schema, same behaviour, same ``map_result()`` text — as a native in-process
  teammate. Plus the external-only ``read_inbox`` (natives get messages pushed
  by coordination; an external member must pull). Server-level instructions are
  **empty**: the team system prompt is injected directly into the CLI at spawn
  time, so the protocol is not repeated here.
* ``operator`` (default) — an external, non-member interface that operates and
  controls the team via tools (task board + messaging + roster + member
  control). Server-level instructions carry the control workflow.

Built on the low-level :class:`mcp.server.lowlevel.Server` (not FastMCP) so the
member tools can advertise their own raw ``card.input_params`` JSON schema and
return their ``map_result()`` text verbatim. The descriptor is read from the
``OPENJIUWEN_TEAM_JOIN`` environment variable; the session-id / language
contextvars are re-bound on every tool call (each call runs in its own task).
"""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

import anyio
import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from openjiuwen.agent_teams.external.client import ExternalTeamClient
from openjiuwen.agent_teams.external.descriptor import TeamJoinDescriptor
from openjiuwen.core.common.logging import team_logger

SERVER_NAME = "openjiuwen-team-member"

# Operator-scope server instructions — the control workflow for a non-member
# interface driving the team. (Member scope ships empty instructions: the team
# system prompt is injected into the CLI directly at spawn time.)
OPERATOR_INSTRUCTIONS = """\
You operate an OpenJiuWen agent team from outside it (you are not a team
member). Drive the team through these tools: read_inbox to see the mailbox and
task board, create_task to hand out work, update_task to edit / assign /
cancel, send_message to direct members (use "*" to broadcast), list_members to
see the roster, and the task-board tools. Refer to members by name.
"""

# Stable tool name (external-only pull op, both scopes).
READ_INBOX = "read_inbox"

# Async callable that produces a connected ExternalTeamClient.
ClientFactory = Callable[[], Awaitable[ExternalTeamClient]]


async def connect_from_env() -> ExternalTeamClient:
    """Build and connect a client from the ``OPENJIUWEN_TEAM_JOIN`` env var."""
    client = ExternalTeamClient(TeamJoinDescriptor.from_env())
    await client.connect()
    return client


def _read_inbox_tool() -> types.Tool:
    """The external-only inbox tool definition (no native counterpart)."""
    return types.Tool(
        name=READ_INBOX,
        description=(
            "Pull your unread messages (direct + broadcast) and the current "
            "task board, rendered as text. Reading marks messages read. Call "
            "it at the start of a turn and whenever you expect new work; an "
            "empty inbox is normal."
        ),
        inputSchema={"type": "object", "properties": {}, "required": []},
    )


class _ClientHolder:
    """Lazily connects the client once, inside the server event loop.

    Every ``get`` re-binds the per-call session/language context (each MCP tool
    call runs in its own task, so the bind done in ``connect`` does not survive).
    """

    def __init__(self, factory: ClientFactory):
        self._factory = factory
        self._client: ExternalTeamClient | None = None

    async def get(self) -> ExternalTeamClient:
        """Return the connected client, with session context bound for this call."""
        if self._client is None:
            self._client = await self._factory()
        self._client.bind_session_context()
        return self._client


# ---------------------------------------------------------------------------
# Operator scope: explicit tool defs + dispatch over ExternalTeamClient ops.
# ---------------------------------------------------------------------------

_STR = {"type": "string"}


def _operator_tool_defs() -> list[types.Tool]:
    """Tool definitions for the operator (external team-control) scope."""
    return [
        _read_inbox_tool(),
        types.Tool(
            name="send_message",
            description='Send a direct message to a member (use "*" to broadcast to the team).',
            inputSchema={
                "type": "object",
                "properties": {"to": _STR, "content": _STR},
                "required": ["to", "content"],
            },
        ),
        types.Tool(
            name="create_task",
            description="Create a team task members can claim. Optionally pin an id and dependency ids.",
            inputSchema={
                "type": "object",
                "properties": {
                    "title": _STR,
                    "content": _STR,
                    "task_id": _STR,
                    "dependencies": {"type": "array", "items": _STR},
                },
                "required": ["title", "content"],
            },
        ),
        types.Tool(
            name="update_task",
            description="Edit a task's title and/or content (pending / blocked tasks only).",
            inputSchema={
                "type": "object",
                "properties": {"task_id": _STR, "title": _STR, "content": _STR},
                "required": ["task_id"],
            },
        ),
        types.Tool(
            name="list_tasks",
            description="List team tasks, optionally filtered by status.",
            inputSchema={"type": "object", "properties": {"status": _STR}, "required": []},
        ),
        types.Tool(
            name="claimable_tasks",
            description="List pending tasks available to claim.",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        types.Tool(
            name="get_task",
            description="Get one task's full detail, or null if it does not exist.",
            inputSchema={"type": "object", "properties": {"task_id": _STR}, "required": ["task_id"]},
        ),
        types.Tool(
            name="claim_task",
            description="Claim a pending task.",
            inputSchema={"type": "object", "properties": {"task_id": _STR}, "required": ["task_id"]},
        ),
        types.Tool(
            name="complete_task",
            description="Complete a claimed task.",
            inputSchema={"type": "object", "properties": {"task_id": _STR}, "required": ["task_id"]},
        ),
        types.Tool(
            name="list_members",
            description="List the team's members.",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
    ]


async def _dispatch_operator(client: ExternalTeamClient, name: str, args: dict[str, Any]) -> str:
    """Run one operator tool over the client and return a text result."""
    if name == "send_message":
        message_id = await client.send_message(args["to"], args["content"])
        return json.dumps({"ok": message_id is not None, "message_id": message_id})
    if name == "create_task":
        result = await client.create_task(
            title=args["title"],
            content=args["content"],
            task_id=args.get("task_id"),
            dependencies=args.get("dependencies"),
        )
        return json.dumps(
            {"ok": result.ok, "reason": result.reason, "task_id": getattr(result, "task_id", None)}
        )
    if name == "update_task":
        result = await client.update_task(args["task_id"], title=args.get("title"), content=args.get("content"))
        return json.dumps({"ok": result.ok, "reason": result.reason})
    if name == "list_tasks":
        tasks = await client.list_tasks(status=args.get("status"))
        return json.dumps(
            [{"task_id": t.task_id, "title": t.title, "status": t.status, "assignee": t.assignee} for t in tasks]
        )
    if name == "claimable_tasks":
        tasks = await client.claimable_tasks()
        return json.dumps([{"task_id": t.task_id, "title": t.title} for t in tasks])
    if name == "get_task":
        detail = await client.get_task(args["task_id"])
        return json.dumps(detail.model_dump() if detail is not None else None)
    if name == "claim_task":
        result = await client.claim_task(args["task_id"])
        return json.dumps({"ok": result.ok, "reason": result.reason})
    if name == "complete_task":
        result = await client.complete_task(args["task_id"])
        return json.dumps({"ok": result.ok, "reason": result.reason})
    if name == "list_members":
        members = await client.list_members()
        return json.dumps([{"member_name": m.member_name, "role": m.role, "status": m.status} for m in members])
    return f"Unknown tool: {name}"


def build_server(
    client_factory: ClientFactory = connect_from_env,
    *,
    scope: str | None = None,
) -> Server:
    """Build the low-level stdio MCP server, scoped by the join descriptor.

    Args:
        client_factory: Async callable returning a connected
            :class:`ExternalTeamClient`. Defaults to reading the descriptor
            from the environment; tests inject a pre-built client.
        scope: ``"member"`` / ``"operator"``. Drives the server-level
            instructions only (the tool set is resolved per request from the
            connected client's scope). ``None`` (production) reads it from the
            ``OPENJIUWEN_TEAM_JOIN`` descriptor; tests pass it explicitly to
            avoid depending on process env.

    Returns:
        A configured low-level :class:`Server` (run it over stdio).
    """
    resolved_scope = scope if scope is not None else TeamJoinDescriptor.from_env().scope
    instructions = "" if resolved_scope == "member" else OPERATOR_INSTRUCTIONS
    team_logger.info("team MCP server scope=%s", resolved_scope)

    holder = _ClientHolder(client_factory)
    server: Server = Server(SERVER_NAME, instructions=instructions or None)

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        client = await holder.get()
        if client.scope == "member":
            tools = [
                types.Tool(
                    name=tool.card.name,
                    description=tool.card.description,
                    inputSchema=tool.card.input_params,
                )
                for tool in client.tools.values()
            ]
            tools.append(_read_inbox_tool())
            return tools
        return _operator_tool_defs()

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
        client = await holder.get()
        if name == READ_INBOX:
            return [types.TextContent(type="text", text=await client.read_inbox())]
        try:
            if client.scope == "member":
                tool = client.tools.get(name)
                if tool is None:
                    return [types.TextContent(type="text", text=f"Unknown tool: {name}")]
                text = str(await tool.invoke(arguments))
            else:
                text = await _dispatch_operator(client, name, arguments)
        except Exception as exc:  # noqa: BLE001 - never surface as an MCP protocol error
            team_logger.exception("team MCP tool %s failed", name)
            return [types.TextContent(type="text", text=f"Internal error: {exc}")]
        return [types.TextContent(type="text", text=text)]

    return server


async def _serve() -> None:
    """Run the server over stdio until the client disconnects."""
    server = build_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main() -> None:
    """console_scripts entry point: serve over stdio."""
    anyio.run(_serve)


if __name__ == "__main__":
    main()
