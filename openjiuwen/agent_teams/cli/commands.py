# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Slash-command registry and handlers for the Team CLI.

The CLI keeps a flat top-level ``SLASH_COMMANDS`` mapping for first
words (`/team`, `/session`, `/spec`, `/help`, ...) and three nested
sub-action dispatch tables (``_TEAM_ACTIONS`` / ``_SESSION_ACTIONS`` /
``_SPEC_ACTIONS``). Every handler is async and takes the live
:class:`TeamCli` plus the post-shlex argument list — all output goes
through ``cli.state.console`` so the prompt_toolkit ``patch_stdout``
context can keep the input area undisturbed.

Most handlers are thin wrappers over Runner facades; the only stateful
logic lives in ``_team_start`` / ``_team_switch`` / ``_session_switch``
where the CLI mirrors the active routing target.
"""

from __future__ import annotations

import asyncio
import shlex
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Callable,
)

from prompt_toolkit.completion import (
    Completer,
    Completion,
)
from prompt_toolkit.document import Document
from rich.table import Table

from openjiuwen.agent_teams.cli.state import (
    StreamHandle,
    WatchBinding,
)
from openjiuwen.agent_teams.cli.stream_renderer import (
    spawn_stream,
    stop_stream,
)
from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.runner.runner import Runner

if TYPE_CHECKING:
    from openjiuwen.agent_teams.cli.tui import TeamCli


_RUNTIME_READY_TIMEOUT = 30.0

CommandHandler = Callable[["TeamCli", list[str]], Awaitable[None]]


class _ExitCli(Exception):
    """Sentinel raised by ``/exit`` to break the main loop."""


# ---------------------------------------------------------------------------
# Argument helpers
# ---------------------------------------------------------------------------


def _split_args(rest: str) -> list[str]:
    """Split a command tail using shell-like quoting rules.

    Falls back to a naive whitespace split when shlex chokes (e.g. an
    unbalanced quote in a free-form ``query``) so the user gets a
    best-effort dispatch instead of an opaque parse error.
    """
    try:
        return shlex.split(rest, posix=True)
    except ValueError:
        return rest.split()


def _pop_flag(args: list[str], flag: str) -> bool:
    """Remove ``flag`` from ``args`` and return whether it was present."""
    if flag in args:
        args.remove(flag)
        return True
    return False


def _resolve_team_name(cli: "TeamCli", args: list[str]) -> str | None:
    """Return the team name from positional arg 0 or active selection."""
    if args:
        return args[0]
    return cli.state.active_team_name


def _resolve_session_id(cli: "TeamCli", args: list[str], index: int) -> str | None:
    """Return the session id from positional arg at ``index`` or active selection."""
    if len(args) > index:
        return args[index]
    return cli.state.active_session_id


# ---------------------------------------------------------------------------
# /spec actions
# ---------------------------------------------------------------------------


async def _spec_load(cli: "TeamCli", args: list[str]) -> None:
    """``/spec load <yaml_path>`` — register a YAML spec into the registry."""
    if not args:
        cli.state.console.print("[yellow]usage: /spec load <yaml_path>[/yellow]")
        return
    try:
        entry = cli.state.spec_registry.add_yaml(args[0])
    except BaseError as exc:
        cli.state.console.print(f"[red]load failed: {exc}[/red]")
        return
    cli.state.console.print(
        f"[green]loaded[/green] team=[bold]{entry.spec.team_name}[/bold] source={entry.source}",
    )
    cli.state.console.print(
        f"[dim]start with: /team start {entry.spec.team_name} <session_id> [query][/dim]",
    )


async def _spec_list(cli: "TeamCli", args: list[str]) -> None:
    """``/spec list`` — print every registered spec with its source."""
    entries = cli.state.spec_registry.entries()
    if not entries:
        cli.state.console.print("[dim]no specs registered (use `/spec load <yaml>`).[/dim]")
        return
    table = Table(title="registered specs", show_lines=False)
    table.add_column("team_name")
    table.add_column("source")
    table.add_column("members")
    for entry in entries:
        members = entry.spec.predefined_members or []
        table.add_row(
            entry.spec.team_name,
            entry.source,
            str(len(members)),
        )
    cli.state.console.print(table)


async def _spec_show(cli: "TeamCli", args: list[str]) -> None:
    """``/spec show <name>`` — dump a registered spec as JSON."""
    if not args:
        cli.state.console.print("[yellow]usage: /spec show <team_name>[/yellow]")
        return
    entry = cli.state.spec_registry.get(args[0])
    if entry is None:
        cli.state.console.print(f"[red]no spec registered for {args[0]}[/red]")
        return
    cli.state.console.print_json(data=entry.spec.model_dump(mode="json"))


_SPEC_ACTIONS: dict[str, CommandHandler] = {
    "load": _spec_load,
    "list": _spec_list,
    "show": _spec_show,
}


# ---------------------------------------------------------------------------
# /team actions
# ---------------------------------------------------------------------------


async def _team_list(cli: "TeamCli", args: list[str]) -> None:
    """``/team list`` — show pool entries plus registry diff."""
    infos = await Runner.list_active_teams()
    registered = cli.state.spec_registry.names()
    table = Table(title="active teams", show_lines=False)
    table.add_column("team_name")
    table.add_column("session_id")
    table.add_column("state")
    table.add_column("gate")
    table.add_column("registered")
    active_names = {info.team_name for info in infos}
    for info in infos:
        table.add_row(
            info.team_name,
            info.current_session_id,
            info.state.value,
            "closed" if info.gate_closed else "open",
            "yes" if info.team_name in registered else "no",
        )
    for name in registered:
        if name in active_names:
            continue
        table.add_row(name, "-", "[dim]inactive[/dim]", "-", "yes")
    if not infos and not registered:
        cli.state.console.print("[dim]no teams active or registered.[/dim]")
        return
    cli.state.console.print(table)


async def _team_status(cli: "TeamCli", args: list[str]) -> None:
    """``/team status [name]`` — print runtime state for one team."""
    target = _resolve_team_name(cli, args)
    if target is None:
        cli.state.console.print("[yellow]usage: /team status [name][/yellow]")
        return
    infos = await Runner.list_active_teams()
    match = next((info for info in infos if info.team_name == target), None)
    if match is None:
        cli.state.console.print(f"[red]team {target} is not active[/red]")
        return
    cli.state.console.print(
        f"[bold]{match.team_name}[/bold] session={match.current_session_id} "
        f"state={match.state.value} gate={'closed' if match.gate_closed else 'open'}",
    )


async def _team_monitor(cli: "TeamCli", args: list[str]) -> None:
    """``/team monitor [name]`` — render TeamMonitor info / members / tasks."""
    team_name = _resolve_team_name(cli, args)
    session_id = _resolve_session_id(cli, args, index=1) or cli.state.active_session_id
    if team_name is None or session_id is None:
        cli.state.console.print(
            "[yellow]usage: /team monitor [name [session_id]] (defaults to active)[/yellow]",
        )
        return
    monitor = await Runner.get_agent_team_monitor(team_name=team_name, session_id=session_id)
    if monitor is None:
        cli.state.console.print(
            f"[red]no active runtime for team={team_name} session={session_id}[/red]",
        )
        return
    info = await monitor.get_team_info()
    members = await monitor.get_members()
    tasks = await monitor.get_tasks()
    if info is not None:
        cli.state.console.print(
            f"[bold]{info.team_name}[/bold] created_at={info.created_at} updated_at={info.updated_at}",
        )
    if members:
        member_table = Table(title="members")
        member_table.add_column("name")
        member_table.add_column("role")
        member_table.add_column("status")
        for member in members:
            member_table.add_row(
                member.member_name,
                getattr(member, "role_type", "-") or "-",
                getattr(member, "status", "-") or "-",
            )
        cli.state.console.print(member_table)
    if tasks:
        task_table = Table(title="tasks")
        task_table.add_column("task_id")
        task_table.add_column("title")
        task_table.add_column("status")
        for task in tasks:
            task_table.add_row(
                getattr(task, "task_id", "-"),
                getattr(task, "title", "-") or "-",
                getattr(task, "status", "-") or "-",
            )
        cli.state.console.print(task_table)


async def _team_use(cli: "TeamCli", args: list[str]) -> None:
    """``/team use <name>`` — switch routing target without restarting any stream."""
    if not args:
        cli.state.console.print("[yellow]usage: /team use <team_name>[/yellow]")
        return
    name = args[0]
    handle = cli.state.stream_handles.get(name)
    if handle is None:
        cli.state.console.print(
            f"[red]team {name} has no active stream in this CLI; run `/team start` first[/red]",
        )
        return
    cli.state.set_active(handle.team_name, handle.session_id)
    cli.state.console.print(
        f"[green]active[/green] team=[bold]{handle.team_name}[/bold] session={handle.session_id}",
    )


async def _await_runtime_ready(handle: StreamHandle) -> dict[str, Any] | None:
    """Wait up to ``_RUNTIME_READY_TIMEOUT`` for the start ack; return None on timeout."""
    try:
        return await asyncio.wait_for(handle.runtime_ready, timeout=_RUNTIME_READY_TIMEOUT)
    except asyncio.TimeoutError:
        return None


async def _start_or_resume(
    cli: "TeamCli",
    *,
    team_name: str,
    session_id: str,
    query: str,
) -> bool:
    """Spawn a stream and wait for ``team.runtime_ready``. Returns True on success."""
    entry = cli.state.spec_registry.get(team_name)
    if entry is None:
        registered = cli.state.spec_registry.names()
        if registered:
            available = ", ".join(registered)
            cli.state.console.print(
                f"[red]no spec registered for [bold]{team_name}[/bold].[/red] "
                f"available: [bold]{available}[/bold]; "
                f"the team_name must match one of these (it comes from the yaml's "
                f"`team_name` field, not your CLI argument).",
            )
        else:
            cli.state.console.print(
                f"[red]no spec registered for {team_name}; load one first via `/spec load <yaml>`.[/red]",
            )
        return False
    if team_name in cli.state.stream_handles and not cli.state.stream_handles[team_name].task.done():
        cli.state.console.print(
            f"[yellow]team {team_name} already has a running stream; stop it before restart.[/yellow]",
        )
        return False
    cli.state.set_pending(team_name, session_id)
    handle = spawn_stream(
        spec=entry.spec,
        session_id=session_id,
        inputs={"query": query},
        console=cli.state.console,
    )
    cli.state.stream_handles[team_name] = handle
    ack = await _await_runtime_ready(handle)
    if ack is None:
        cli.state.console.print(
            f"[red]runtime_ready timeout for team={team_name} session={session_id}; check logs.[/red]",
        )
        await stop_stream(handle)
        cli.state.stream_handles.pop(team_name, None)
        cli.state.set_pending(None, None)
        return False
    cli.state.set_active(team_name, session_id)
    cli.state.remember_session(team_name, session_id)
    cli.state.console.print(
        f"[green]ready[/green] team=[bold]{team_name}[/bold] session={session_id}",
    )
    return True


async def _team_start(cli: "TeamCli", args: list[str]) -> None:
    """``/team start <name> <session_id> [query...]`` — first-time activation."""
    if len(args) < 2:
        cli.state.console.print(
            "[yellow]usage: /team start <team_name> <session_id> [query...][/yellow]",
        )
        return
    team_name, session_id = args[0], args[1]
    query = " ".join(args[2:]) if len(args) > 2 else "hello"
    await _start_or_resume(
        cli,
        team_name=team_name,
        session_id=session_id,
        query=query,
    )


async def _team_switch(cli: "TeamCli", args: list[str]) -> None:
    """``/team switch <name> [session_id] [query...]`` — cross-team rebuild."""
    if not args:
        cli.state.console.print(
            "[yellow]usage: /team switch <team_name> [session_id] [query...][/yellow]",
        )
        return
    new_team = args[0]
    new_session = args[1] if len(args) > 1 else cli.state.active_session_id
    query = " ".join(args[2:]) if len(args) > 2 else "hello"
    if new_session is None:
        cli.state.console.print(
            "[yellow]no active session; pass session_id explicitly[/yellow]",
        )
        return
    previous_team = cli.state.active_team_name
    previous_session = cli.state.active_session_id
    if previous_team is not None and previous_team != new_team:
        previous_handle = cli.state.stream_handles.get(previous_team)
        if previous_handle is not None and previous_session is not None:
            await Runner.stop_agent_team(
                team_name=previous_team,
                session_id=previous_session,
            )
            await stop_stream(previous_handle)
            cli.state.stream_handles.pop(previous_team, None)
    ok = await _start_or_resume(
        cli,
        team_name=new_team,
        session_id=new_session,
        query=query,
    )
    if not ok:
        cli.state.set_active(previous_team, previous_session)


async def _team_pause(cli: "TeamCli", args: list[str]) -> None:
    """``/team pause [name]`` — pause runtime; stream stays alive in idle."""
    team_name = _resolve_team_name(cli, args)
    session_id = _resolve_session_id(cli, args, index=1)
    if team_name is None or session_id is None:
        cli.state.console.print("[yellow]no active team to pause[/yellow]")
        return
    ok = await Runner.pause_agent_team(team_name=team_name, session_id=session_id)
    cli.state.console.print(
        f"[{'green' if ok else 'red'}]pause[/] team={team_name} session={session_id} ok={ok}",
    )


async def _team_resume(cli: "TeamCli", args: list[str]) -> None:
    """``/team resume [name] [query...]`` — re-activate via run_agent_team_streaming."""
    team_name = _resolve_team_name(cli, args)
    if team_name is None:
        cli.state.console.print("[yellow]no active team; pass <team_name>[/yellow]")
        return
    session_id = cli.state.active_session_id
    handle = cli.state.stream_handles.get(team_name)
    if handle is not None:
        session_id = handle.session_id
    if session_id is None:
        cli.state.console.print(
            "[yellow]no session id known; use `/team start` instead[/yellow]",
        )
        return
    query = " ".join(args[1:]) if len(args) > 1 else "resume"
    if handle is not None and not handle.task.done():
        cli.state.console.print(
            f"[yellow]team {team_name} stream already running; pause/stop first if you want a fresh start.[/yellow]",
        )
        return
    cli.state.stream_handles.pop(team_name, None)
    await _start_or_resume(
        cli,
        team_name=team_name,
        session_id=session_id,
        query=query,
    )


async def _team_stop(cli: "TeamCli", args: list[str]) -> None:
    """``/team stop [name]`` — tear down runtime + cancel the stream task."""
    team_name = _resolve_team_name(cli, args)
    if team_name is None:
        cli.state.console.print("[yellow]no active team to stop[/yellow]")
        return
    handle = cli.state.stream_handles.get(team_name)
    session_id = handle.session_id if handle is not None else cli.state.active_session_id
    if session_id is None:
        cli.state.console.print(
            f"[yellow]no session id for team {team_name}; cannot stop[/yellow]",
        )
        return
    ok = await Runner.stop_agent_team(team_name=team_name, session_id=session_id)
    if handle is not None:
        await stop_stream(handle)
        cli.state.stream_handles.pop(team_name, None)
    if cli.state.active_team_name == team_name:
        cli.state.set_active(None, None)
    cli.state.console.print(
        f"[{'green' if ok else 'yellow'}]stop[/] team={team_name} session={session_id} ok={ok}",
    )


async def _team_delete(cli: "TeamCli", args: list[str]) -> None:
    """``/team delete <name> [--force]`` — drop persisted state for a team."""
    force = _pop_flag(args, "--force")
    if not args:
        cli.state.console.print(
            "[yellow]usage: /team delete <team_name> [--force][/yellow]",
        )
        return
    team_name = args[0]
    session_ids = cli.state.known_sessions(team_name)
    if cli.state.active_session_id is not None and cli.state.active_team_name == team_name:
        if cli.state.active_session_id not in session_ids:
            session_ids.append(cli.state.active_session_id)
    try:
        ok = await Runner.delete_agent_team(
            team_name=team_name,
            session_ids=session_ids,
            force=force,
        )
    except BaseError as exc:
        cli.state.console.print(
            f"[red]delete failed: {exc}; pass --force or `/team stop` first[/red]",
        )
        return
    handle = cli.state.stream_handles.pop(team_name, None)
    if handle is not None:
        await stop_stream(handle)
    if cli.state.active_team_name == team_name:
        cli.state.set_active(None, None)
    cli.state.history_session_ids.pop(team_name, None)
    cli.state.console.print(
        f"[{'green' if ok else 'yellow'}]delete[/] team={team_name} sessions={session_ids} ok={ok}",
    )


async def _team_watch(cli: "TeamCli", args: list[str]) -> None:
    """``/team watch <member> [name]`` — register inbox callback for a human agent."""
    if not args:
        cli.state.console.print(
            "[yellow]usage: /team watch <member_name> [team_name][/yellow]",
        )
        return
    member_name = args[0]
    team_name = args[1] if len(args) > 1 else cli.state.active_team_name
    session_id = cli.state.active_session_id
    if team_name is None or session_id is None:
        cli.state.console.print("[yellow]no active team / session to watch[/yellow]")
        return
    callback = cli.inbox_callback
    try:
        ok = await Runner.register_human_agent_inbound(
            team_name=team_name,
            session_id=session_id,
            member_name=member_name,
            callback=callback,
        )
    except KeyError:
        cli.state.console.print(
            f"[red]{member_name} is not a registered human-agent member of {team_name}[/red]",
        )
        return
    if not ok:
        cli.state.console.print(
            f"[red]no active runtime for team={team_name} session={session_id}[/red]",
        )
        return
    binding = WatchBinding(
        team_name=team_name,
        session_id=session_id,
        member_name=member_name,
    )
    cli.state.watch_bindings[(team_name, session_id, member_name)] = binding
    cli.state.console.print(
        f"[green]watching[/green] {member_name} on team={team_name} session={session_id}",
    )


async def _team_unwatch(cli: "TeamCli", args: list[str]) -> None:
    """``/team unwatch <member> [name]`` — clear the inbox callback."""
    if not args:
        cli.state.console.print(
            "[yellow]usage: /team unwatch <member_name> [team_name][/yellow]",
        )
        return
    member_name = args[0]
    team_name = args[1] if len(args) > 1 else cli.state.active_team_name
    session_id = cli.state.active_session_id
    if team_name is None or session_id is None:
        cli.state.console.print("[yellow]no active team / session to unwatch[/yellow]")
        return
    try:
        ok = await Runner.register_human_agent_inbound(
            team_name=team_name,
            session_id=session_id,
            member_name=member_name,
            callback=None,
        )
    except KeyError:
        cli.state.console.print(
            f"[red]{member_name} is not a registered human-agent member of {team_name}[/red]",
        )
        return
    cli.state.watch_bindings.pop((team_name, session_id, member_name), None)
    cli.state.console.print(
        f"[{'green' if ok else 'yellow'}]unwatched[/] {member_name} on team={team_name} session={session_id}",
    )


_TEAM_ACTIONS: dict[str, CommandHandler] = {
    "list": _team_list,
    "status": _team_status,
    "monitor": _team_monitor,
    "use": _team_use,
    "start": _team_start,
    "switch": _team_switch,
    "pause": _team_pause,
    "resume": _team_resume,
    "stop": _team_stop,
    "delete": _team_delete,
    "watch": _team_watch,
    "unwatch": _team_unwatch,
}


# ---------------------------------------------------------------------------
# /session actions
# ---------------------------------------------------------------------------


async def _session_active(cli: "TeamCli", args: list[str]) -> None:
    """``/session active`` — print active routing target."""
    cli.state.console.print(
        f"team={cli.state.active_team_name or '-'} session={cli.state.active_session_id or '-'}",
    )


async def _session_list(cli: "TeamCli", args: list[str]) -> None:
    """``/session list`` — list known (team, session) pairs from CLI history."""
    if not cli.state.history_session_ids:
        cli.state.console.print("[dim]no sessions seen in this CLI yet.[/dim]")
        return
    table = Table(title="known sessions (this CLI)")
    table.add_column("team_name")
    table.add_column("session_ids")
    for team_name, sessions in cli.state.history_session_ids.items():
        table.add_row(team_name, ", ".join(sorted(sessions)))
    cli.state.console.print(table)


async def _session_switch(cli: "TeamCli", args: list[str]) -> None:
    """``/session switch <session_id> [query...]`` — restart active team on a new session."""
    if not args:
        cli.state.console.print(
            "[yellow]usage: /session switch <session_id> [query...][/yellow]",
        )
        return
    if cli.state.active_team_name is None:
        cli.state.console.print(
            "[yellow]no active team to switch session for[/yellow]",
        )
        return
    new_session = args[0]
    query = " ".join(args[1:]) if len(args) > 1 else "hello"
    team_name = cli.state.active_team_name
    previous_session = cli.state.active_session_id
    handle = cli.state.stream_handles.get(team_name)
    if handle is not None and previous_session is not None:
        await Runner.stop_agent_team(team_name=team_name, session_id=previous_session)
        await stop_stream(handle)
        cli.state.stream_handles.pop(team_name, None)
    ok = await _start_or_resume(
        cli,
        team_name=team_name,
        session_id=new_session,
        query=query,
    )
    if not ok:
        cli.state.set_active(team_name, previous_session)


async def _session_release(cli: "TeamCli", args: list[str]) -> None:
    """``/session release [session_id] [--force]`` — drop dynamic tables for a session."""
    force = _pop_flag(args, "--force")
    session_id = args[0] if args else cli.state.active_session_id
    if session_id is None:
        cli.state.console.print(
            "[yellow]usage: /session release [session_id] [--force][/yellow]",
        )
        return
    try:
        await Runner.release(session_id, force=force)
    except BaseError as exc:
        cli.state.console.print(
            f"[red]release failed: {exc}; pass --force or `/team stop` the active team first[/red]",
        )
        return
    cli.state.console.print(
        f"[green]released[/green] session={session_id} force={force}",
    )


_SESSION_ACTIONS: dict[str, CommandHandler] = {
    "active": _session_active,
    "list": _session_list,
    "switch": _session_switch,
    "release": _session_release,
}


# ---------------------------------------------------------------------------
# Top-level dispatch
# ---------------------------------------------------------------------------


def _print_subhelp(cli: "TeamCli", group: str, actions: dict[str, CommandHandler]) -> None:
    """Print the available subcommands under a group like ``/team``."""
    cli.state.console.print(
        f"usage: /{group} <action>; available actions: [bold]{', '.join(sorted(actions))}[/bold]",
    )


async def _cmd_team(cli: "TeamCli", args: list[str]) -> None:
    """``/team <action> ...`` dispatcher."""
    if not args:
        _print_subhelp(cli, "team", _TEAM_ACTIONS)
        return
    action, rest = args[0], args[1:]
    handler = _TEAM_ACTIONS.get(action)
    if handler is None:
        _print_subhelp(cli, "team", _TEAM_ACTIONS)
        return
    await handler(cli, rest)


async def _cmd_session(cli: "TeamCli", args: list[str]) -> None:
    """``/session <action> ...`` dispatcher."""
    if not args:
        _print_subhelp(cli, "session", _SESSION_ACTIONS)
        return
    action, rest = args[0], args[1:]
    handler = _SESSION_ACTIONS.get(action)
    if handler is None:
        _print_subhelp(cli, "session", _SESSION_ACTIONS)
        return
    await handler(cli, rest)


async def _cmd_spec(cli: "TeamCli", args: list[str]) -> None:
    """``/spec <action> ...`` dispatcher."""
    if not args:
        _print_subhelp(cli, "spec", _SPEC_ACTIONS)
        return
    action, rest = args[0], args[1:]
    handler = _SPEC_ACTIONS.get(action)
    if handler is None:
        _print_subhelp(cli, "spec", _SPEC_ACTIONS)
        return
    await handler(cli, rest)


async def _cmd_help(cli: "TeamCli", args: list[str]) -> None:
    """``/help`` — print the full command reference."""
    cli.state.console.print(
        """Team CLI commands
  /spec load <yaml>             load a YAML team spec
  /spec list                    list registered specs
  /spec show <name>             dump one spec as JSON
  /team list                    list active runtimes + registry diff
  /team status [name]           show runtime state for one team
  /team monitor [name [sid]]    rich monitor view (members + tasks)
  /team start <name> <sid> [q]  first-time activation, await runtime_ready
  /team switch <name> [sid] [q] cross-team rebuild
  /team use <name>              switch active routing target only
  /team pause [name]            pause active runtime
  /team resume [name] [q]       re-activate after pause
  /team stop [name]             tear down runtime + cancel stream
  /team delete <name> [--force] permanently delete a team
  /team watch <m> [name]        subscribe to a human-agent inbox
  /team unwatch <m> [name]      clear the subscription
  /session active               print current routing target
  /session list                 list (team, session) pairs from this CLI
  /session switch <sid> [q]     restart active team on a new session
  /session release [sid] [--force]  drop dynamic tables for a session
  /help                         this help
  /clear                        clear the screen
  /exit, /quit                  leave the CLI
  ! <shell-cmd>                 run a shell command

Plain text without a leading `/` or `! ` is forwarded to the active
team via Runner.interact_agent_team. Use `# `, `$<name> `, `@<member> `
prefixes to address GodView / HumanAgent / Operator channels (parsed
once by the runtime).
""",
        markup=False,
        highlight=False,
    )


async def _cmd_clear(cli: "TeamCli", args: list[str]) -> None:
    """``/clear`` — clear the rich console screen."""
    cli.state.console.clear()


async def _cmd_exit(cli: "TeamCli", args: list[str]) -> None:
    """``/exit`` — break the main loop."""
    raise _ExitCli


SLASH_COMMANDS: dict[str, CommandHandler] = {
    "/team": _cmd_team,
    "/session": _cmd_session,
    "/spec": _cmd_spec,
    "/help": _cmd_help,
    "/clear": _cmd_clear,
    "/exit": _cmd_exit,
    "/quit": _cmd_exit,
}


_TOP_LEVEL_DESCRIPTIONS: dict[str, str] = {
    "/team": "team lifecycle (list / start / stop / pause / ...)",
    "/session": "session lifecycle (switch / release / list)",
    "/spec": "team spec registry (load / list / show)",
    "/help": "show command reference",
    "/clear": "clear the screen",
    "/exit": "leave the CLI",
}


_SUB_ACTION_TABLES: dict[str, dict[str, CommandHandler]] = {
    "/team": _TEAM_ACTIONS,
    "/session": _SESSION_ACTIONS,
    "/spec": _SPEC_ACTIONS,
}


async def dispatch_slash(cli: "TeamCli", line: str) -> None:
    """Parse and run one slash-prefixed command line."""
    parts = _split_args(line[1:])
    if not parts:
        cli.state.console.print("[yellow]empty command[/yellow]")
        return
    head = "/" + parts[0]
    rest = parts[1:]
    handler = SLASH_COMMANDS.get(head)
    if handler is None:
        cli.state.console.print(f"[red]unknown command: {head}[/red]")
        return
    try:
        await handler(cli, rest)
    except _ExitCli:
        raise
    except BaseError as exc:
        cli.state.console.print(f"[red]{head} failed: {exc}[/red]")
    except Exception as exc:
        team_logger.exception(
            "[cli.commands] {} raised: {}",
            head,
            exc,
        )
        cli.state.console.print(f"[red]{head} crashed: {exc}[/red]")


# ---------------------------------------------------------------------------
# Tab completion
# ---------------------------------------------------------------------------


class SlashCompleter(Completer):
    """Two-level tab completion for ``/cmd <action>`` slash commands."""

    def get_completions(self, document: Document, complete_event: Any):
        text = document.text_before_cursor
        if not text.startswith("/"):
            return
        if " " not in text:
            for cmd in sorted(SLASH_COMMANDS):
                if cmd == "/quit":
                    continue
                if cmd.startswith(text):
                    yield Completion(
                        cmd,
                        start_position=-len(text),
                        display_meta=_TOP_LEVEL_DESCRIPTIONS.get(cmd, ""),
                    )
            return
        head, _, tail = text.partition(" ")
        actions = _SUB_ACTION_TABLES.get(head)
        if actions is None:
            return
        if " " in tail:
            return
        for action in sorted(actions):
            if action.startswith(tail):
                yield Completion(
                    action,
                    start_position=-len(tail),
                )


__all__ = [
    "SLASH_COMMANDS",
    "SlashCompleter",
    "_ExitCli",
    "dispatch_slash",
]
