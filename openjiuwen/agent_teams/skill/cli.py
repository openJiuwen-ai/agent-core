# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Non-interactive CLI for an external agent to join / control a team.

Two scenarios, picked from the join descriptor's ``scope`` (see
:class:`TeamJoinDescriptor`):

* ``member`` — a third-party CLI acting as a first-class team member. The CLI
  drives the **real** team tools (``view_task`` / ``claim_task`` /
  ``send_message``) built by ``create_team_tools(role="teammate")``, so output
  is the same ``map_result()`` text a native teammate sees. Plus ``inbox``
  (the external-only pull op).
* ``operator`` (default) — an external, non-member interface that controls the
  team (task board + messaging + roster + ``create_task``).

The connection descriptor is read from ``OPENJIUWEN_TEAM_JOIN`` by default, or
from ``--descriptor-file`` / ``--descriptor-json``. Single operation per
invocation, plain-text output, exit code 0 on success and non-zero on failure;
``print`` is the intended output channel here (stdin/stdout-driving script).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from openjiuwen.agent_teams.external.client import ExternalTeamClient
from openjiuwen.agent_teams.external.descriptor import TeamJoinDescriptor

_PROG = "team-member"


def _add_connection_args(parser: argparse.ArgumentParser) -> None:
    src = parser.add_argument_group("connection")
    src.add_argument("--descriptor-file", help="Path to a JSON team join descriptor.")
    src.add_argument("--descriptor-json", help="Inline JSON team join descriptor.")


def _build_member_parser() -> argparse.ArgumentParser:
    """Parser for the ``member`` scope — subcommands mirror the real team tools."""
    parser = argparse.ArgumentParser(prog=_PROG, description="Participate in an agent team as a member.")
    _add_connection_args(parser)
    sub = parser.add_subparsers(dest="command", required=True)

    p_inbox = sub.add_parser("inbox", help="Show unread messages and the task board.")
    p_inbox.add_argument("--watch", action="store_true", help="Block and stream the inbox as events arrive.")

    p_view = sub.add_parser("view_task", help="View tasks (list / get / claimable).")
    p_view.add_argument("--action", choices=["list", "get", "claimable"], default="list")
    p_view.add_argument("--task_id", help="Task id (required for action=get).")
    p_view.add_argument("--status", help="Status filter for action=list.")

    p_claim = sub.add_parser("claim_task", help="Claim a task, or complete one you hold.")
    p_claim.add_argument("task_id", help="Task identifier.")
    p_claim.add_argument("status", choices=["claimed", "completed"], help="Target status.")

    p_send = sub.add_parser("send_message", help='Send a message ("*" to broadcast).')
    p_send.add_argument("to", help='Recipient member name, or "*" to broadcast.')
    p_send.add_argument("content", help="Message body.")

    return parser


def _build_operator_parser() -> argparse.ArgumentParser:
    """Parser for the ``operator`` scope — external team-control surface."""
    parser = argparse.ArgumentParser(prog=_PROG, description="Operate an agent team from outside it.")
    _add_connection_args(parser)
    sub = parser.add_subparsers(dest="command", required=True)

    p_inbox = sub.add_parser("inbox", help="Show unread messages and the task board.")
    p_inbox.add_argument("--watch", action="store_true", help="Block and stream the inbox as events arrive.")

    p_send = sub.add_parser("send", help="Send a direct message to a member.")
    p_send.add_argument("to", help="Recipient member name.")
    p_send.add_argument("content", help="Message body.")

    p_broadcast = sub.add_parser("broadcast", help="Broadcast a message to the whole team.")
    p_broadcast.add_argument("content", help="Message body.")

    p_create = sub.add_parser("create_task", help="Create a team task.")
    p_create.add_argument("title", help="Task title.")
    p_create.add_argument("content", help="Task content / acceptance criteria.")
    p_create.add_argument("--task_id", help="Optional pinned task id.")

    p_task = sub.add_parser("task", help="Inspect the task board.")
    task_sub = p_task.add_subparsers(dest="task_action", required=True)
    p_task_list = task_sub.add_parser("list", help="List tasks.")
    p_task_list.add_argument("--status", help="Filter by task status.")
    task_sub.add_parser("claimable", help="List pending tasks available to claim.")
    p_task_get = task_sub.add_parser("get", help="Show one task in detail.")
    p_task_get.add_argument("task_id", help="Task identifier.")

    p_claim = sub.add_parser("claim", help="Claim a pending task.")
    p_claim.add_argument("task_id", help="Task identifier.")

    p_complete = sub.add_parser("complete", help="Complete a claimed task.")
    p_complete.add_argument("task_id", help="Task identifier.")

    p_update = sub.add_parser("update", help="Edit a task's title and/or content.")
    p_update.add_argument("task_id", help="Task identifier.")
    p_update.add_argument("--title", help="New title.")
    p_update.add_argument("--content", help="New content.")

    sub.add_parser("members", help="List team members.")

    return parser


def _load_descriptor(descriptor_file: str | None, descriptor_json: str | None) -> TeamJoinDescriptor:
    if descriptor_json:
        return TeamJoinDescriptor.from_json(descriptor_json)
    if descriptor_file:
        return TeamJoinDescriptor.from_json(Path(descriptor_file).read_text(encoding="utf-8"))
    return TeamJoinDescriptor.from_env()


# ---- member scope dispatch (drives the real team tools) -------------------


async def _dispatch_member(client: ExternalTeamClient, args: argparse.Namespace) -> int:
    command = args.command

    if command == "inbox":
        print(await client.read_inbox())
        if args.watch:

            async def _on_update(_view) -> None:
                print(await client.read_inbox())

            await client.watch(_on_update)
        return 0

    tool = client.tools.get(command)
    if tool is None:
        print(f"Unknown command: {command}", file=sys.stderr)
        return 2

    if command == "view_task":
        inputs = {"action": args.action}
        if args.task_id:
            inputs["task_id"] = args.task_id
        if args.status:
            inputs["status"] = args.status
    elif command == "claim_task":
        inputs = {"task_id": args.task_id, "status": args.status}
    elif command == "send_message":
        inputs = {"to": args.to, "content": args.content}
    else:
        inputs = {}

    result = await tool.invoke(inputs)
    print(str(result))
    return 0


# ---- operator scope dispatch (external team control) ----------------------


async def _dispatch_operator(client: ExternalTeamClient, args: argparse.Namespace) -> int:
    command = args.command

    if command == "inbox":
        print(await client.read_inbox())
        if args.watch:

            async def _on_update(_view) -> None:
                print(await client.read_inbox())

            await client.watch(_on_update)
        return 0

    if command == "send":
        message_id = await client.send_message(args.to, args.content)
        if message_id is None:
            print(f"Failed to send message to '{args.to}'", file=sys.stderr)
            return 1
        print(f"sent {message_id} -> {args.to}")
        return 0

    if command == "broadcast":
        message_id = await client.send_message("*", args.content)
        if message_id is None:
            print("Failed to broadcast message", file=sys.stderr)
            return 1
        print(f"broadcast {message_id}")
        return 0

    if command == "create_task":
        result = await client.create_task(title=args.title, content=args.content, task_id=args.task_id)
        if not result.ok:
            print(result.reason, file=sys.stderr)
            return 1
        print(f"created {result.task_id}")
        return 0

    if command == "task":
        return await _dispatch_operator_task(client, args)

    if command == "claim":
        result = await client.claim_task(args.task_id)
        return _report_op(result, ok=f"claimed {args.task_id}")

    if command == "complete":
        result = await client.complete_task(args.task_id)
        return _report_op(result, ok=f"completed {args.task_id}")

    if command == "update":
        result = await client.update_task(args.task_id, title=args.title, content=args.content)
        return _report_op(result, ok=f"updated {args.task_id}")

    if command == "members":
        for member in await client.list_members():
            print(f"{member.member_name} ({member.role}) [{member.status}]")
        return 0

    print(f"Unknown command: {command}", file=sys.stderr)
    return 2


async def _dispatch_operator_task(client: ExternalTeamClient, args: argparse.Namespace) -> int:
    action = args.task_action
    if action == "list":
        for task in await client.list_tasks(status=args.status):
            assignee = task.assignee or "-"
            print(f"[{task.task_id}] [{task.status}] {task.title} ({assignee})")
        return 0
    if action == "claimable":
        for task in await client.claimable_tasks():
            print(f"[{task.task_id}] {task.title}")
        return 0
    if action == "get":
        detail = await client.get_task(args.task_id)
        if detail is None:
            print(f"Task '{args.task_id}' not found", file=sys.stderr)
            return 1
        print(f"id:        {detail.task_id}")
        print(f"title:     {detail.title}")
        print(f"status:    {detail.status}")
        print(f"assignee:  {detail.assignee or '-'}")
        print(f"blocked_by: {', '.join(detail.blocked_by) or '-'}")
        print(f"blocks:     {', '.join(detail.blocks) or '-'}")
        print(f"content:\n{detail.content}")
        return 0
    print(f"Unknown task action: {action}", file=sys.stderr)
    return 2


def _report_op(result, *, ok: str) -> int:
    if result.ok:
        print(ok)
        return 0
    print(result.reason, file=sys.stderr)
    return 1


async def _run(descriptor: TeamJoinDescriptor, args: argparse.Namespace) -> int:
    async with ExternalTeamClient(descriptor) as client:
        client.bind_session_context()
        if client.scope == "member":
            return await _dispatch_member(client, args)
        return await _dispatch_operator(client, args)


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and execute one CLI operation. Returns an exit code.

    Two-stage parse: read the connection args first to load the descriptor
    (whose ``scope`` decides the subcommand set), then build the scope-specific
    parser and parse the rest.
    """
    pre = argparse.ArgumentParser(add_help=False)
    _add_connection_args(pre)
    pre_args, _ = pre.parse_known_args(argv)
    descriptor = _load_descriptor(pre_args.descriptor_file, pre_args.descriptor_json)

    parser = _build_member_parser() if descriptor.scope == "member" else _build_operator_parser()
    args = parser.parse_args(argv)
    try:
        return asyncio.run(_run(descriptor, args))
    except KeyboardInterrupt:
        return 130


def run() -> None:
    """console_scripts entry point."""
    sys.exit(main())


if __name__ == "__main__":
    run()
