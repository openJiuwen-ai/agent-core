# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for external CLI adapter system-prompt and MCP-registration wiring."""

import pytest

from openjiuwen.agent_teams.external.cli_agent.adapters import available_adapters, build_adapter


@pytest.mark.level0
def test_gemini_adapter_registered_and_builds_headless_turn():
    assert "gemini" in available_adapters()
    adapter = build_adapter("gemini")
    argv = adapter.build_turn_command("do it", session_id="s", first_turn=True)
    assert argv[:4] == ["gemini", "-o", "stream-json", "-y"]
    assert argv[-2:] == ["-p", "do it"]
    # gemini -p is one-shot, so it runs under the re-invoke runtime.
    assert not adapter.supports_stdin_injection


@pytest.mark.level0
def test_claude_system_prompt_uses_append_flag():
    adapter = build_adapter("claude")
    assert adapter.injects_system_prompt_via_arg()
    assert adapter.system_prompt_args("PERSONA") == ["--append-system-prompt", "PERSONA"]
    # Empty system prompt yields no args.
    assert adapter.system_prompt_args("") == []


@pytest.mark.level0
@pytest.mark.parametrize("name", ["gemini", "openclaw", "hermes", "generic"])
def test_clis_without_system_prompt_flag_get_empty_args(name):
    adapter = build_adapter(name)
    assert not adapter.injects_system_prompt_via_arg()
    # They get the prompt prepended to the first message instead of a flag.
    assert adapter.system_prompt_args("PERSONA") == []


@pytest.mark.level0
def test_codex_system_prompt_uses_developer_instructions():
    # codex injects the system prompt via -c developer_instructions (no prepend).
    adapter = build_adapter("codex")
    assert adapter.injects_system_prompt_via_arg()
    args = adapter.system_prompt_args("be terse")
    assert args[0] == "-c"
    assert args[1] == 'developer_instructions="be terse"'
    assert adapter.system_prompt_args("") == []


@pytest.mark.level0
def test_codex_uses_json_output_and_turn_completed_sentinel():
    adapter = build_adapter("codex")
    assert "--json" in adapter.build_command()
    assert adapter.is_turn_complete('{"type": "turn.completed"}')
    assert not adapter.is_turn_complete('{"type": "item.completed"}')


@pytest.mark.level0
def test_gemini_cross_turn_starts_then_resumes():
    adapter = build_adapter("gemini")
    first = adapter.build_turn_command("hi", session_id="sid-1", first_turn=True)
    assert "--session-id" in first
    assert first[first.index("--session-id") + 1] == "sid-1"
    assert "--resume" not in first
    later = adapter.build_turn_command("again", session_id="sid-1", first_turn=False)
    assert "--resume" in later
    assert later[later.index("--resume") + 1] == "sid-1"
    assert "--session-id" not in later


@pytest.mark.level0
def test_gemini_registers_mcp_via_subcommand():
    adapter = build_adapter("gemini")
    cmd = adapter.mcp_register_command(
        server_name="openjiuwen-team",
        server_command=("openjiuwen-team-mcp",),
    )
    assert cmd == ["gemini", "mcp", "add", "openjiuwen-team", "openjiuwen-team-mcp"]


@pytest.mark.level0
def test_hermes_registers_mcp_via_subcommand():
    adapter = build_adapter("hermes")
    cmd = adapter.mcp_register_command(
        server_name="openjiuwen-team",
        server_command=("openjiuwen-team-mcp",),
    )
    assert cmd == ["hermes", "mcp", "add", "openjiuwen-team", "--command", "openjiuwen-team-mcp"]


@pytest.mark.level0
@pytest.mark.parametrize("name", ["claude", "codex"])
def test_launch_inject_clis_have_no_register_command(name):
    # claude / codex inject MCP at launch (mcp_launch_args), so there is no
    # out-of-band registration command.
    adapter = build_adapter(name)
    assert adapter.mcp_register_command(server_name="t", server_command=("openjiuwen-team-mcp",)) is None
    assert adapter.mcp_launch_args(server_name="t", server_command=("openjiuwen-team-mcp",))


@pytest.mark.level0
def test_openclaw_cannot_auto_inject_mcp():
    # openclaw has neither a launch flag nor a known register command; the
    # spawn path warns instead of silently leaving it without team tools.
    adapter = build_adapter("openclaw")
    assert adapter.mcp_launch_args(server_name="t", server_command=("openjiuwen-team-mcp",)) == []
    assert adapter.mcp_register_command(server_name="t", server_command=("openjiuwen-team-mcp",)) is None


@pytest.mark.level0
def test_claude_summarize_extracts_text_and_tool_skips_lifecycle():
    adapter = build_adapter("claude")
    assert adapter.structured_output
    line = '{"type":"assistant","message":{"content":[{"type":"text","text":"hi there"},{"type":"tool_use","name":"read_inbox"}]}}'
    assert adapter.summarize_output_line(line) == "hi there → read_inbox"
    # result is a turn-boundary lifecycle event: nothing to surface.
    assert adapter.summarize_output_line('{"type":"result","subtype":"success"}') is None
    # non-JSON line on a structured-output CLI is ignored.
    assert adapter.summarize_output_line("not json") is None


@pytest.mark.level0
def test_codex_summarize_extracts_item_text_skips_turn_completed():
    adapter = build_adapter("codex")
    assert adapter.summarize_output_line('{"type":"item.completed","item":{"text":"done the work"}}') == "done the work"
    assert adapter.summarize_output_line('{"type":"turn.completed"}') is None


@pytest.mark.level0
def test_plain_text_cli_surfaces_line_as_is():
    # Non-structured CLIs (openclaw / hermes) surface each stdout line verbatim.
    adapter = build_adapter("openclaw")
    assert not adapter.structured_output
    assert adapter.summarize_output_line("working on the task") == "working on the task"
    assert adapter.summarize_output_line("   ") is None
