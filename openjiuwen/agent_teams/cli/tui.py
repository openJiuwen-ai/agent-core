# coding: utf-8
"""prompt_toolkit + rich main loop for the Team CLI.

:class:`TeamCli` owns the prompt session, the rich ``Console``, the
state object, and the inbox callback bound to that console. The public
entry point is :meth:`TeamCli.run`, which accepts an optional
``input_iter`` hook used by tests to feed scripted command sequences
without spinning up a real prompt.
"""

from __future__ import annotations

import contextlib
from typing import (
    AsyncIterator,
    Awaitable,
    Callable,
)

from prompt_toolkit import PromptSession
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console

from openjiuwen.agent_teams.cli.commands import (
    SlashCompleter,
    _ExitCli,
)
from openjiuwen.agent_teams.cli.inbox_sink import make_inbox_callback
from openjiuwen.agent_teams.cli.routing import route_text
from openjiuwen.agent_teams.cli.spec_loader import SpecRegistry
from openjiuwen.agent_teams.cli.state import TeamCliState
from openjiuwen.agent_teams.cli.stream_renderer import stop_stream
from openjiuwen.agent_teams.interaction import HumanAgentInboundEvent
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.runner.runner import Runner

InboxCallback = Callable[[HumanAgentInboundEvent], Awaitable[None]]


class TeamCli:
    """Interactive driver for ``Runner`` team lifecycle facades."""

    def __init__(
        self,
        spec_registry: SpecRegistry,
        *,
        console: Console | None = None,
    ) -> None:
        """Initialize the CLI with a populated spec registry.

        Args:
            spec_registry: Pre-populated registry; the CLI does not
                seed it itself, so callers must register specs (yaml
                or in-memory) before ``run()``.
            console: Optional rich Console; when omitted a fresh
                Console bound to the current TTY is created.
        """
        self._console = console or Console()
        self._state = TeamCliState(
            spec_registry=spec_registry,
            console=self._console,
        )
        self._inbox_callback = make_inbox_callback(self._console)
        self._prompt: PromptSession[str] = PromptSession(
            history=InMemoryHistory(),
            completer=SlashCompleter(),
            bottom_toolbar=self._render_toolbar,
        )

    @property
    def state(self) -> TeamCliState:
        """Mutable CLI state shared with command handlers."""
        return self._state

    @property
    def inbox_callback(self) -> InboxCallback:
        """Inbox callback bound to this CLI's console."""
        return self._inbox_callback

    def _render_toolbar(self) -> str:
        """Bottom-toolbar text shown beneath the prompt input."""
        active = (
            f"team={self._state.active_team_name} session={self._state.active_session_id}"
            if self._state.active_team_name
            else "no active team"
        )
        streams = len(self._state.stream_handles)
        watching = len(self._state.watch_bindings)
        return f"[{active}] streams={streams} watching={watching}  (/help for commands)"

    async def run(
        self,
        *,
        input_iter: AsyncIterator[str] | None = None,
    ) -> None:
        """Drive the main loop until ``/exit``, EOF, or input_iter exhausts.

        Args:
            input_iter: Optional async iterator of pre-canned input
                lines used by tests. When provided, the prompt UI is
                bypassed entirely.
        """
        if input_iter is None:
            await self._run_prompt_loop()
        else:
            await self._run_iter_loop(input_iter)

    async def _run_prompt_loop(self) -> None:
        with patch_stdout(raw=True):
            self._console.print(
                "[bold cyan]Team CLI[/bold cyan] — `/help` 查看命令，`/exit` 退出。",
            )
            while True:
                try:
                    line = await self._prompt.prompt_async("team> ")
                except (EOFError, KeyboardInterrupt):
                    break
                try:
                    await route_text(self, line)
                except _ExitCli:
                    break

    async def _run_iter_loop(self, input_iter: AsyncIterator[str]) -> None:
        async for line in input_iter:
            try:
                await route_text(self, line)
            except _ExitCli:
                break

    async def shutdown(self) -> None:
        """Tear down active streams + clear watch bindings on exit."""
        for binding in list(self._state.watch_bindings.values()):
            with contextlib.suppress(Exception):
                await Runner.register_human_agent_inbound(
                    team_name=binding.team_name,
                    session_id=binding.session_id,
                    member_name=binding.member_name,
                    callback=None,
                )
        self._state.watch_bindings.clear()
        for team_name, handle in list(self._state.stream_handles.items()):
            with contextlib.suppress(Exception):
                await Runner.stop_agent_team(
                    team_name=handle.team_name,
                    session_id=handle.session_id,
                )
            await stop_stream(handle)
            self._state.stream_handles.pop(team_name, None)
        team_logger.info("[cli] shutdown complete")


__all__ = ["TeamCli"]
