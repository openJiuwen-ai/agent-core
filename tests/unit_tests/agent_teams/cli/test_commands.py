# coding: utf-8
"""Slash-command handlers — argument forwarding + Runner facade calls."""

from __future__ import annotations

from unittest.mock import (
    AsyncMock,
    patch,
)

import pytest
from prompt_toolkit.document import Document
from rich.console import Console

from openjiuwen.agent_teams.cli.commands import (
    SLASH_COMMANDS,
    SlashCompleter,
    _ExitCli,
    _spec_list,
    _spec_show,
    _team_delete,
    _team_list,
    _team_pause,
    _team_status,
    _team_stop,
    _team_unwatch,
    _team_use,
    _team_watch,
    dispatch_slash,
)
from openjiuwen.agent_teams.cli.spec_loader import SpecRegistry
from openjiuwen.agent_teams.cli.state import (
    TeamCliState,
    WatchBinding,
)
from openjiuwen.agent_teams.runtime.pool import (
    ActiveTeamInfo,
    RuntimeState,
)
from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec

pytestmark = pytest.mark.level0


def _make_spec(team_name: str = "alpha") -> TeamAgentSpec:
    return TeamAgentSpec.model_validate(
        {
            "agents": {"leader": {}},
            "team_name": team_name,
        },
    )


class _FakeCli:
    def __init__(self):
        registry = SpecRegistry()
        registry.add_inmemory(_make_spec("alpha"))
        self.state = TeamCliState(spec_registry=registry, console=Console(record=True))
        self.state.set_active("alpha", "s1")
        self.state.remember_session("alpha", "s1")
        self.inbox_callback = AsyncMock()


@pytest.mark.asyncio
async def test_dispatch_slash_unknown_command_does_not_raise():
    cli = _FakeCli()

    await dispatch_slash(cli, "/no-such-cmd")

    assert "unknown command" in cli.state.console.export_text()


@pytest.mark.asyncio
async def test_dispatch_slash_unknown_subaction_prints_subhelp():
    cli = _FakeCli()

    await dispatch_slash(cli, "/team xyz")

    out = cli.state.console.export_text()
    assert "available actions" in out
    assert "list" in out


@pytest.mark.asyncio
async def test_team_list_reports_active_and_registered_teams():
    cli = _FakeCli()
    info = ActiveTeamInfo(
        team_name="alpha",
        current_session_id="s1",
        state=RuntimeState.RUNNING,
        gate_closed=False,
    )

    with patch(
        "openjiuwen.core.runner.runner.Runner.list_active_teams",
        new=AsyncMock(return_value=[info]),
    ):
        await _team_list(cli, [])

    out = cli.state.console.export_text()
    assert "alpha" in out
    assert "s1" in out
    assert "running" in out


@pytest.mark.asyncio
async def test_team_status_with_no_args_uses_active_team():
    cli = _FakeCli()
    info = ActiveTeamInfo(
        team_name="alpha",
        current_session_id="s1",
        state=RuntimeState.PAUSED,
        gate_closed=True,
    )

    with patch(
        "openjiuwen.core.runner.runner.Runner.list_active_teams",
        new=AsyncMock(return_value=[info]),
    ):
        await _team_status(cli, [])

    out = cli.state.console.export_text()
    assert "alpha" in out
    assert "paused" in out
    assert "closed" in out


@pytest.mark.asyncio
async def test_team_pause_calls_runner_with_active_target():
    cli = _FakeCli()

    with patch(
        "openjiuwen.core.runner.runner.Runner.pause_agent_team",
        new=AsyncMock(return_value=True),
    ) as runner_call:
        await _team_pause(cli, [])

    runner_call.assert_awaited_once_with(team_name="alpha", session_id="s1")


@pytest.mark.asyncio
async def test_team_stop_clears_active_when_stopping_active_team():
    cli = _FakeCli()

    with patch(
        "openjiuwen.core.runner.runner.Runner.stop_agent_team",
        new=AsyncMock(return_value=True),
    ) as runner_call:
        await _team_stop(cli, [])

    runner_call.assert_awaited_once_with(team_name="alpha", session_id="s1")
    assert cli.state.active_team_name is None
    assert cli.state.active_session_id is None


@pytest.mark.asyncio
async def test_team_delete_collects_history_session_ids_and_calls_runner():
    cli = _FakeCli()
    cli.state.remember_session("alpha", "s2")

    with patch(
        "openjiuwen.core.runner.runner.Runner.delete_agent_team",
        new=AsyncMock(return_value=True),
    ) as runner_call:
        await _team_delete(cli, ["alpha", "--force"])

    runner_call.assert_awaited_once()
    kwargs = runner_call.await_args.kwargs
    assert kwargs["team_name"] == "alpha"
    assert kwargs["force"] is True
    assert sorted(kwargs["session_ids"]) == ["s1", "s2"]


@pytest.mark.asyncio
async def test_team_use_rejects_team_without_stream_handle():
    cli = _FakeCli()

    await _team_use(cli, ["beta"])

    assert "no active stream" in cli.state.console.export_text()


@pytest.mark.asyncio
async def test_team_watch_registers_callback_and_stores_binding():
    cli = _FakeCli()

    with patch(
        "openjiuwen.core.runner.runner.Runner.register_human_agent_inbound",
        new=AsyncMock(return_value=True),
    ) as runner_call:
        await _team_watch(cli, ["human_agent"])

    runner_call.assert_awaited_once()
    kwargs = runner_call.await_args.kwargs
    assert kwargs["team_name"] == "alpha"
    assert kwargs["session_id"] == "s1"
    assert kwargs["member_name"] == "human_agent"
    assert kwargs["callback"] is cli.inbox_callback
    assert ("alpha", "s1", "human_agent") in cli.state.watch_bindings


@pytest.mark.asyncio
async def test_team_unwatch_removes_binding():
    cli = _FakeCli()
    cli.state.watch_bindings[("alpha", "s1", "human_agent")] = WatchBinding(
        team_name="alpha",
        session_id="s1",
        member_name="human_agent",
    )

    with patch(
        "openjiuwen.core.runner.runner.Runner.register_human_agent_inbound",
        new=AsyncMock(return_value=True),
    ):
        await _team_unwatch(cli, ["human_agent"])

    assert ("alpha", "s1", "human_agent") not in cli.state.watch_bindings


@pytest.mark.asyncio
async def test_spec_list_renders_registered_specs():
    cli = _FakeCli()

    await _spec_list(cli, [])

    out = cli.state.console.export_text()
    assert "alpha" in out
    assert "in-memory" in out


@pytest.mark.asyncio
async def test_spec_show_dumps_spec_when_present():
    cli = _FakeCli()

    await _spec_show(cli, ["alpha"])

    out = cli.state.console.export_text()
    assert "alpha" in out


@pytest.mark.asyncio
async def test_spec_show_warns_when_missing():
    cli = _FakeCli()

    await _spec_show(cli, ["missing"])

    assert "no spec registered" in cli.state.console.export_text()


@pytest.mark.asyncio
async def test_dispatch_slash_exit_raises_sentinel():
    cli = _FakeCli()

    with pytest.raises(_ExitCli):
        await dispatch_slash(cli, "/exit")


def test_slash_completer_first_word_completes_top_level_commands():
    completer = SlashCompleter()
    document = Document("/team", cursor_position=5)

    completions = list(completer.get_completions(document, complete_event=None))
    labels = [c.text for c in completions]

    assert "/team" in labels
    assert "/spec" not in labels


def test_slash_completer_after_space_completes_subactions():
    completer = SlashCompleter()
    document = Document("/team list", cursor_position=10)

    completions = list(completer.get_completions(document, complete_event=None))
    labels = [c.text for c in completions]

    assert "list" in labels
    assert "start" not in labels


def test_slash_commands_table_covers_all_top_level_commands():
    expected = {"/team", "/session", "/spec", "/help", "/clear", "/exit", "/quit"}

    assert expected.issubset(set(SLASH_COMMANDS))
