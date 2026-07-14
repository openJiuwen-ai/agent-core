# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Non-interactive CLI for an external agent to participate in a team.

This is a thin, scriptable front end over :class:`ExternalTeamClient`. A
third-party CLI agent (claudecode / codex / ...) or a human operator runs
``team-member <subcommand>`` to send messages, work the task board, and read
an inbox. The connection descriptor is read from the ``OPENJIUWEN_TEAM_JOIN``
environment variable by default, or from ``--descriptor-file`` /
``--descriptor-json``.

Unlike the interactive ``agent_teams.cli`` TUI (prompt_toolkit + rich, for
humans), this CLI is single-shot and machine-friendly: one operation per
invocation, plain-text output, exit code 0 on success and 1 on failure.
``print`` is the intended output channel here (stdin/stdout-driving script).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from openjiuwen.agent_teams.external.client import ExternalTeamClient, InboxView
from openjiuwen.agent_teams.external.descriptor import TeamJoinDescriptor
from openjiuwen.agent_teams.external.format import render_messages, render_task_board
from openjiuwen.agent_teams.tools.database.engine import get_current_time

_PROG = "team-member"


def build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser for the external-member CLI."""
    parser = argparse.ArgumentParser(
        prog=_PROG,
        description="Participate in an agent team as an external member.",
    )
    src = parser.add_argument_group("connection")
    src.add_argument("--descriptor-file", help="Path to a JSON team join descriptor.")
    src.add_argument("--descriptor-json", help="Inline JSON team join descriptor.")

    sub = parser.add_subparsers(dest="command", required=True)

    p_inbox = sub.add_parser("inbox", help="Show unread messages and the task board.")
    p_inbox.add_argument("--watch", action="store_true", help="Block and stream the inbox as events arrive.")

    p_send = sub.add_parser("send", help="Send a direct message to a member.")
    p_send.add_argument("to", help="Recipient member name.")
    p_send.add_argument("content", help="Message body.")

    p_broadcast = sub.add_parser("broadcast", help="Broadcast a message to the whole team.")
    p_broadcast.add_argument("content", help="Message body.")

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


def _load_descriptor(args: argparse.Namespace) -> TeamJoinDescriptor:
    if args.descriptor_json:
        return TeamJoinDescriptor.from_json(args.descriptor_json)
    if args.descriptor_file:
        return TeamJoinDescriptor.from_json(Path(args.descriptor_file).read_text(encoding="utf-8"))
    return TeamJoinDescriptor.from_env()


def _print_inbox(view: InboxView, *, is_leader: bool) -> None:
    now_ms = get_current_time()
    if view.messages:
        print(render_messages(view.messages, now_ms=now_ms))
    board = render_task_board(view.tasks, is_leader=is_leader, now_ms=now_ms)
    if board:
        if view.messages:
            print()
        print(board)
    if view.is_empty():
        print("(inbox empty)")


async def _dispatch(client: ExternalTeamClient, args: argparse.Namespace) -> int:
    command = args.command

    if command == "inbox":
        if args.watch:
            _print_inbox(await client.fetch_inbox(), is_leader=client.is_leader)

            async def _on_update(view: InboxView) -> None:
                _print_inbox(view, is_leader=client.is_leader)

            await client.watch(_on_update)
            return 0
        _print_inbox(await client.fetch_inbox(), is_leader=client.is_leader)
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

    if command == "task":
        return await _dispatch_task(client, args)

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


async def _dispatch_task(client: ExternalTeamClient, args: argparse.Namespace) -> int:
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


async def _run(args: argparse.Namespace) -> int:
    descriptor = _load_descriptor(args)
    async with ExternalTeamClient(descriptor) as client:
        return await _dispatch(client, args)


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and execute one CLI operation. Returns an exit code."""
    args = build_parser().parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        return 130


def run() -> None:
    """console_scripts entry point."""
    sys.exit(main())


if __name__ == "__main__":
    run()
