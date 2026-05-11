# coding: utf-8
"""route_text dispatch + DeliverResult rendering."""

from __future__ import annotations

from unittest.mock import (
    AsyncMock,
    MagicMock,
    patch,
)

import pytest
from rich.console import Console

from openjiuwen.agent_teams.cli.routing import (
    _translate_reason,
    render_deliver_result,
    route_text,
)
from openjiuwen.agent_teams.cli.spec_loader import SpecRegistry
from openjiuwen.agent_teams.cli.state import TeamCliState
from openjiuwen.agent_teams.interaction import DeliverResult

pytestmark = pytest.mark.level0


class _FakeCli:
    """Minimal stand-in for TeamCli that exposes only ``state``."""

    def __init__(self, *, active_team: str | None = None, active_session: str | None = None):
        self.state = TeamCliState(spec_registry=SpecRegistry(), console=Console(record=True))
        self.state.active_team_name = active_team
        self.state.active_session_id = active_session


@pytest.mark.asyncio
async def test_route_text_skips_blank_input():
    cli = _FakeCli()

    with patch("openjiuwen.core.runner.runner.Runner.interact_agent_team") as runner_call:
        await route_text(cli, "   ")

    runner_call.assert_not_called()


@pytest.mark.asyncio
async def test_route_text_dispatches_slash_to_dispatch_slash():
    cli = _FakeCli()

    with patch("openjiuwen.agent_teams.cli.commands.dispatch_slash", new=AsyncMock()) as ds:
        await route_text(cli, "/team list")

    ds.assert_awaited_once_with(cli, "/team list")


@pytest.mark.asyncio
async def test_route_text_runs_shell_command_via_subprocess():
    cli = _FakeCli()

    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(b"hello\n", b""))
    with patch("asyncio.create_subprocess_shell", new=AsyncMock(return_value=proc)) as ses:
        await route_text(cli, "! echo hello")

    ses.assert_awaited_once()


@pytest.mark.asyncio
async def test_route_text_forwards_plain_text_to_active_team():
    cli = _FakeCli(active_team="alpha", active_session="s1")
    success = DeliverResult.success("msg-1")

    with patch(
        "openjiuwen.core.runner.runner.Runner.interact_agent_team",
        new=AsyncMock(return_value=success),
    ) as runner_call:
        await route_text(cli, "hello team")

    runner_call.assert_awaited_once_with(
        "hello team",
        team_name="alpha",
        session_id="s1",
    )


@pytest.mark.asyncio
async def test_route_text_warns_when_no_active_team():
    cli = _FakeCli()

    with patch("openjiuwen.core.runner.runner.Runner.interact_agent_team") as runner_call:
        await route_text(cli, "plain text")

    runner_call.assert_not_called()


def test_translate_reason_known_token_returns_chinese_hint():
    assert "gate closed" in _translate_reason("gate_closed")
    assert "运行池" in _translate_reason("not_active")
    assert "选定" in _translate_reason("missing_target")


def test_translate_reason_pattern_tokens_extract_member():
    assert "human-agent: bob" in _translate_reason("unknown_human_agent:bob")
    assert "成员: alice" in _translate_reason("unknown_member:alice")
    assert "失败: alice" in _translate_reason("send_failed:alice")


def test_translate_reason_unknown_token_passes_through():
    assert _translate_reason("weird_token") == "weird_token"


def test_render_deliver_result_success_prints_message_id():
    cli = _FakeCli()

    render_deliver_result(cli, "raw", DeliverResult.success("xyz"))

    output = cli.state.console.export_text()
    assert "xyz" in output


def test_render_deliver_result_failure_prints_translated_reason():
    cli = _FakeCli()

    render_deliver_result(cli, "raw", DeliverResult.failure("gate_closed"))

    output = cli.state.console.export_text()
    assert "gate_closed" in output
    assert "gate closed" in output
