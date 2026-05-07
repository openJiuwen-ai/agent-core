# coding: utf-8
"""Top-level dispatcher that routes a raw input line to the right handler.

A user line falls into exactly one of three buckets:

1. ``/cmd ...`` — delegate to :func:`openjiuwen.agent_teams.cli.commands.dispatch_slash`.
2. ``! <shell>`` — execute the rest as a shell command (mirrors the
   ``harness/cli/ui/repl.py`` shell passthrough).
3. plain text — forward verbatim to ``Runner.interact_agent_team`` against
   the currently active ``(team_name, session_id)``. The runtime's
   ``parse_interact_str`` handles the ``# / $ / @member`` prefix grammar
   in one place; the CLI does not re-parse, otherwise prefixes such as
   ``# # body`` would be stripped twice.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING

from openjiuwen.agent_teams.interaction import DeliverResult
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.runner.runner import Runner

if TYPE_CHECKING:
    from openjiuwen.agent_teams.cli.tui import TeamCli


_SLASH_PREFIX = "/"
_SHELL_PREFIX = "! "

_REASON_HINTS: dict[str, str] = {
    "missing_target": "尚未选定 active team / session，先执行 `/team start` 或 `/team use`。",
    "not_active": "目标 team 不在运行池中。可能已被 stop / delete；试 `/team list` 或 `/team start`。",
    "gate_closed": "团队当前轮次已结束（gate closed），等待新一轮 wakeup 或先 `/team resume`。",
    "human_agent_not_enabled": "该 team 未启用 HITT，无法以 human-agent 身份发声。",
    "no_team_backend": "当前 team 未挂 team backend（裸 leader），不能走 operator/human-agent 通道。",
}


_REASON_PREFIX_HINTS: tuple[tuple[str, str], ...] = (
    ("unknown_human_agent:", "未知 human-agent: {}"),
    ("unknown_member:", "未知成员: {}"),
    ("send_failed:", "消息发送失败: {}"),
)


def _translate_reason(reason: str | None) -> str:
    """Return a Chinese hint for a known ``DeliverResult.reason`` token."""
    if not reason:
        return "未知错误"
    direct = _REASON_HINTS.get(reason)
    if direct is not None:
        return direct
    for prefix, template in _REASON_PREFIX_HINTS:
        if reason.startswith(prefix):
            return template.format(reason[len(prefix):])
    return reason


def render_deliver_result(cli: "TeamCli", raw: str, result: DeliverResult) -> None:
    """Render a ``DeliverResult`` onto the CLI console.

    Successful sends print a single dim ack line; failures translate the
    stable reason token to a Chinese hint to keep the output language
    consistent with the rest of the CLI.
    """
    if result.ok:
        msg_id = result.message_id or "-"
        cli.state.console.print(
            f"[dim][dispatch] msg_id={msg_id}[/dim]",
        )
        return
    hint = _translate_reason(result.reason)
    cli.state.console.print(
        f"[yellow][dispatch failed][/yellow] reason=[bold]{result.reason}[/bold]  {hint}",
    )


async def _handle_shell(cli: "TeamCli", cmd: str) -> None:
    """Run a shell command and print its stdout / stderr to the console."""
    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if stdout:
        cli.state.console.print(stdout.decode(errors="replace").rstrip())
    if stderr:
        cli.state.console.print(
            f"[red]{stderr.decode(errors='replace').rstrip()}[/red]",
        )


async def _interact_active(cli: "TeamCli", raw: str) -> None:
    """Forward ``raw`` to the currently active team via ``Runner.interact_agent_team``."""
    if cli.state.active_team_name is None or cli.state.active_session_id is None:
        cli.state.console.print(
            "[yellow]尚未选定 active team / session，先执行 `/team start` 或 `/team use`。[/yellow]",
        )
        return
    try:
        result = await Runner.interact_agent_team(
            raw,
            team_name=cli.state.active_team_name,
            session_id=cli.state.active_session_id,
        )
    except Exception as exc:
        team_logger.exception(
            "[cli.routing] interact failed team={} session={}: {}",
            cli.state.active_team_name,
            cli.state.active_session_id,
            exc,
        )
        cli.state.console.print(f"[red]interact 抛出异常: {exc}[/red]")
        return
    render_deliver_result(cli, raw, result)


async def route_text(cli: "TeamCli", raw: str) -> None:
    """Dispatch ``raw`` to slash / shell / interact based on its prefix."""
    text = raw.rstrip("\n")
    if not text.strip():
        return
    if text.startswith(_SHELL_PREFIX):
        await _handle_shell(cli, text[len(_SHELL_PREFIX):])
        return
    if text.startswith(_SLASH_PREFIX):
        from openjiuwen.agent_teams.cli.commands import dispatch_slash

        with contextlib.suppress(asyncio.CancelledError):
            await dispatch_slash(cli, text)
        return
    await _interact_active(cli, text)


__all__ = [
    "render_deliver_result",
    "route_text",
]
