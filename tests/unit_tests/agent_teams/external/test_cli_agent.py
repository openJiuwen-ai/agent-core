# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for the P2 external-CLI building blocks: adapters, injector, runtime."""

import asyncio
import os

import pytest

from openjiuwen.agent_teams.agent.member_runtime import MemberRuntime
from openjiuwen.agent_teams.external.cli_agent.adapters import (
    available_adapters,
    build_adapter,
)
from openjiuwen.agent_teams.external.cli_agent.injector import StdinPipeInjector
from openjiuwen.agent_teams.external.runtime import ExternalCliRuntime, ReinvokeCliRuntime
from openjiuwen.agent_teams.harness import TeamHarness
from openjiuwen.core.common.exception.errors import BaseError


# ---- adapters -------------------------------------------------------------


@pytest.mark.level0
def test_build_adapter_claude_stream_json():
    adapter = build_adapter("claude")
    cmd = adapter.build_command()
    assert cmd[0] == "claude"
    assert "--dangerously-skip-permissions" in cmd
    framed = adapter.format_input("hello")
    assert '"type": "user"' in framed
    assert '"content": "hello"' in framed


@pytest.mark.level0
def test_claude_completion_on_result_json():
    adapter = build_adapter("claude")
    assert adapter.is_turn_complete('{"type": "result", "subtype": "success"}')
    assert not adapter.is_turn_complete('{"type": "assistant"}')
    assert not adapter.is_turn_complete("plain text")


@pytest.mark.level0
def test_generic_adapter_marker_completion():
    adapter = build_adapter("generic")
    assert adapter.format_input("hi") == "hi"
    assert adapter.is_turn_complete("done <<END_OF_TURN>> now")
    assert not adapter.is_turn_complete("still working")


@pytest.mark.level0
def test_build_adapter_command_override():
    adapter = build_adapter("claude", command_override=("/usr/local/bin/claude", "-x"))
    assert adapter.build_command() == ["/usr/local/bin/claude", "-x"]


@pytest.mark.level1
def test_build_adapter_unknown_raises():
    with pytest.raises(BaseError):
        build_adapter("nope")


@pytest.mark.level1
def test_available_adapters_includes_known_clis():
    names = set(available_adapters())
    assert {"claude", "codex", "openclaw", "hermes", "generic"} <= names


@pytest.mark.level0
def test_claude_mcp_launch_args_use_mcp_config_flag():
    adapter = build_adapter("claude")
    args = adapter.mcp_launch_args(server_name="openjiuwen-team", server_command=("openjiuwen-team-mcp",))
    assert args[0] == "--mcp-config"
    import json

    config = json.loads(args[1])
    assert config["mcpServers"]["openjiuwen-team"]["command"] == "openjiuwen-team-mcp"
    assert config["mcpServers"]["openjiuwen-team"]["args"] == []


@pytest.mark.level0
def test_codex_mcp_launch_args_use_config_override():
    adapter = build_adapter("codex")
    args = adapter.mcp_launch_args(server_name="openjiuwen-team", server_command=("openjiuwen-team-mcp", "--flag"))
    assert args[0] == "-c"
    # Hyphen in the server name is normalised to an underscore for the TOML key.
    assert 'mcp_servers.openjiuwen_team.command="openjiuwen-team-mcp"' == args[1]
    assert 'mcp_servers.openjiuwen_team.args=["--flag"]' == args[3]


@pytest.mark.level1
def test_one_shot_adapters_have_no_mcp_injection():
    # openclaw / hermes register their MCP server out of band, not via launch args.
    for name in ("openclaw", "hermes", "generic"):
        adapter = build_adapter(name)
        assert adapter.mcp_launch_args(server_name="t", server_command=("openjiuwen-team-mcp",)) == []


# ---- runtime --------------------------------------------------------------


class _RecordingInjector:
    def __init__(self) -> None:
        self.writes: list[str] = []
        self.closed = False

    async def write(self, text: str) -> None:
        self.writes.append(text)

    async def aclose(self) -> None:
        self.closed = True


async def _lines(*items: str):
    for item in items:
        yield item


@pytest.mark.asyncio
@pytest.mark.level0
async def test_runtime_run_streaming_writes_input_and_consumes_until_complete():
    injector = _RecordingInjector()
    runtime = ExternalCliRuntime(
        member_name="dev-1",
        adapter=build_adapter("generic"),
        injector=injector,
        output_lines=_lines("thinking...", "more <<END_OF_TURN>>", "next-turn-line"),
    )

    chunks = [chunk async for chunk in runtime.run_streaming({"query": "do it"}, session_id="s")]

    # stdout narration is surfaced as output chunks up to the turn marker.
    assert [c.payload["content"] for c in chunks] == ["thinking...", "more <<END_OF_TURN>>"]
    assert injector.writes == ["do it"]  # input delivered once


@pytest.mark.asyncio
@pytest.mark.level0
async def test_runtime_steer_and_follow_up_inject():
    injector = _RecordingInjector()
    runtime = ExternalCliRuntime(
        member_name="dev-1",
        adapter=build_adapter("generic"),
        injector=injector,
        output_lines=_lines(),
    )
    await runtime.steer("urgent")
    await runtime.follow_up("later")
    assert injector.writes == ["urgent", "later"]


@pytest.mark.asyncio
@pytest.mark.level1
async def test_runtime_abort_stops_turn():
    injector = _RecordingInjector()

    async def _slow_lines():
        yield "line-1"
        runtime._abort_requested = True  # simulate abort arriving mid-turn
        yield "line-2"
        yield "should-not-matter <<END_OF_TURN>>"

    runtime = ExternalCliRuntime(
        member_name="dev-1",
        adapter=build_adapter("generic"),
        injector=injector,
        output_lines=_slow_lines(),
    )
    chunks = [chunk async for chunk in runtime.run_streaming({"query": "go"}, session_id="s")]
    # Only the pre-abort line is surfaced; abort stops before "line-2".
    assert [c.payload["content"] for c in chunks] == ["line-1"]


def _make_stderr_reader(payload: bytes) -> asyncio.StreamReader:
    """Build a StreamReader pre-loaded with ``payload`` then EOF."""
    reader = asyncio.StreamReader()
    reader.feed_data(payload)
    reader.feed_eof()
    return reader


class _FakeDeadProcess:
    """Stand-in for a crashed long-lived CLI subprocess.

    Exposes the attributes :class:`ExternalCliRuntime` reads: a non-zero
    ``returncode`` (already exited) and a ``stderr`` StreamReader carrying the
    failure reason. ``_terminate`` short-circuits because ``returncode`` is set.
    """

    def __init__(self, *, returncode: int, stderr: bytes) -> None:
        self.returncode = returncode
        self.stderr = _make_stderr_reader(stderr)

    def terminate(self) -> None:  # pragma: no cover - never called (already exited)
        raise AssertionError("terminate on an already-exited process")

    async def wait(self) -> int:
        return self.returncode


@pytest.mark.asyncio
@pytest.mark.level0
async def test_streaming_premature_eof_crash_raises_with_stderr():
    """A streaming subprocess that dies mid-turn must raise, not return clean.

    Without a turn-complete sentinel the stdout iterator simply ends at EOF;
    the runtime inspects the dead process's returncode and raises a structured
    error carrying the stderr reason, instead of silently reporting a
    successful empty turn.
    """
    proc = _FakeDeadProcess(returncode=1, stderr=b"Error: invalid api key\n")
    runtime = ExternalCliRuntime(
        member_name="dev-1",
        adapter=build_adapter("generic"),
        injector=_RecordingInjector(),
        output_lines=_lines("partial output", "more partial"),  # no <<END_OF_TURN>>
        process=proc,
    )

    with pytest.raises(BaseError) as excinfo:
        async for _ in runtime.run_streaming({"query": "go"}, session_id="s"):
            pass

    message = str(excinfo.value)
    assert "dev-1" in message
    assert "1" in message  # the non-zero exit code
    assert "invalid api key" in message  # the stderr reason is surfaced


@pytest.mark.asyncio
@pytest.mark.level1
async def test_streaming_eof_without_process_does_not_raise():
    """No process handle (or a clean exit) is a benign turn boundary, not a crash."""
    runtime = ExternalCliRuntime(
        member_name="dev-1",
        adapter=build_adapter("generic"),
        injector=_RecordingInjector(),
        output_lines=_lines("some output"),  # iterator ends, no sentinel, no process
    )
    # Must complete normally — no process to inspect means no crash to surface.
    async for _ in runtime.run_streaming({"query": "go"}, session_id="s"):
        pass


@pytest.mark.asyncio
@pytest.mark.level1
async def test_reinvoke_inactivity_timeout_terminates_silent_process(monkeypatch):
    """A subprocess that emits nothing is killed after the inactivity window.

    Drives a real CLI that sleeps silently far longer than the (tiny)
    inactivity timeout. The turn must end promptly via termination — proving
    the no-output ceiling fires — well before the process would exit on its own.
    """
    import sys
    import time as _time
    from unittest.mock import MagicMock

    from openjiuwen.agent_teams.external import runtime as runtime_mod
    from openjiuwen.agent_teams.external.cli_agent.adapters import (
        COMPLETION_NONE,
        INPUT_TEXT,
        CliAgentAdapter,
    )

    # Sleeps silently for 30s — far past the inactivity window below.
    script = "import time; time.sleep(30)"
    adapter = CliAgentAdapter(
        name="fake-hang",
        command=(sys.executable, "-c", script),
        input_format=INPUT_TEXT,
        completion=COMPLETION_NONE,
        supports_stdin_injection=False,
    )
    monkeypatch.setattr(runtime_mod, "team_logger", MagicMock())

    runtime = ReinvokeCliRuntime(
        member_name="hang-1",
        adapter=adapter,
        env=dict(os.environ),
        inactivity_timeout_s=0.3,
    )
    start = _time.monotonic()
    async for _ in runtime.run_streaming({"query": "do it"}, session_id="s"):
        pass
    elapsed = _time.monotonic() - start
    assert elapsed < 5.0, f"inactivity timeout did not fire promptly (took {elapsed:.1f}s)"


@pytest.mark.asyncio
@pytest.mark.level1
async def test_reinvoke_inactivity_timeout_does_not_kill_active_process(monkeypatch):
    """A subprocess that keeps emitting output is NOT killed by inactivity.

    Drives a CLI that prints a line every 0.1s for ~1.5s with a 0.5s
    inactivity window. Because each line resets the deadline, the process runs
    to completion and exits cleanly — the gap between lines never exceeds the
    window, so the watchdog never fires.
    """
    import sys
    from unittest.mock import MagicMock

    from openjiuwen.agent_teams.external import runtime as runtime_mod
    from openjiuwen.agent_teams.external.cli_agent.adapters import (
        COMPLETION_NONE,
        INPUT_TEXT,
        CliAgentAdapter,
    )

    # Emits 15 lines at 0.1s intervals (total ~1.5s), flushing each one.
    script = (
        "import sys, time\n"
        "for i in range(15):\n"
        "    print(i, flush=True)\n"
        "    time.sleep(0.1)\n"
    )
    adapter = CliAgentAdapter(
        name="fake-active",
        command=(sys.executable, "-c", script),
        input_format=INPUT_TEXT,
        completion=COMPLETION_NONE,
        supports_stdin_injection=False,
    )
    mock_logger = MagicMock()
    monkeypatch.setattr(runtime_mod, "team_logger", mock_logger)

    runtime = ReinvokeCliRuntime(
        member_name="active-1",
        adapter=adapter,
        env=dict(os.environ),
        inactivity_timeout_s=0.5,  # < total runtime, but > the 0.1s gap between lines
    )
    async for _ in runtime.run_streaming({"query": "work"}, session_id="s"):
        pass

    # Exited cleanly (code 0): no timeout warning, no failure warning.
    flat = " ".join(str(arg) for call in mock_logger.warning.call_args_list for arg in call.args)
    assert "timeout" not in flat, f"active process was wrongly timed out: {flat}"


@pytest.mark.asyncio
@pytest.mark.level1
async def test_reinvoke_abort_terminates_current_subprocess(monkeypatch):
    """abort() must promptly kill the in-flight re-invoke subprocess.

    Starts a long sleeping CLI turn in a background task, then aborts. The turn
    task must finish promptly (the subprocess is terminated) rather than waiting
    out the full sleep, and no further re-invocation is started.
    """
    import sys

    from openjiuwen.agent_teams.external.cli_agent.adapters import (
        COMPLETION_NONE,
        INPUT_TEXT,
        CliAgentAdapter,
    )

    script = "import time; time.sleep(30)"
    adapter = CliAgentAdapter(
        name="fake-long",
        command=(sys.executable, "-c", script),
        input_format=INPUT_TEXT,
        completion=COMPLETION_NONE,
        supports_stdin_injection=False,
    )
    runtime = ReinvokeCliRuntime(
        member_name="long-1",
        adapter=adapter,
        env=dict(os.environ),
        inactivity_timeout_s=60.0,  # large, so only abort can end the turn quickly
    )

    async def _run() -> None:
        async for _ in runtime.run_streaming({"query": "long task"}, session_id="s"):
            pass

    task = asyncio.create_task(_run())
    # Wait until the subprocess is actually live before aborting.
    while runtime._current is None:
        await asyncio.sleep(0.01)
    await runtime.abort()
    await asyncio.wait_for(task, timeout=5.0)  # raises if abort did not preempt
    assert runtime._aborted
    assert runtime._current is None


@pytest.mark.level0
def test_runtime_conforms_to_member_runtime_protocol():
    runtime = ExternalCliRuntime(
        member_name="dev-1",
        adapter=build_adapter("generic"),
        injector=_RecordingInjector(),
        output_lines=_lines(),
    )
    assert isinstance(runtime, MemberRuntime)


@pytest.mark.level1
def test_team_harness_exposes_member_runtime_surface():
    # TeamHarness is the default MemberRuntime; verify it carries every
    # member the Protocol declares (issubclass is unavailable for Protocols
    # with property members, so check attribute presence on the class).
    for member in (
        "run_streaming",
        "steer",
        "follow_up",
        "abort",
        "init_cwd_for_round",
        "has_pending_interrupt",
        "is_pending_interrupt_resume_valid",
        "find_rails",
        "register_rail",
        "unregister_rail",
        "register_member_tools",
        "inject_member_memory",
        "run_agent_customizer",
        "workspace",
        "sys_operation",
    ):
        assert hasattr(TeamHarness, member), member


# ---- injector -------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.level1
async def test_stdin_pipe_injector_writes_newline_framed():
    proc = await asyncio.create_subprocess_exec(
        "cat",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
    )
    injector = StdinPipeInjector(proc.stdin)
    await injector.write("hello")
    await injector.aclose()
    stdout, _ = await proc.communicate()
    assert stdout.decode().strip() == "hello"


# ---- one-shot (re-invoke) adapters + runtime ------------------------------


@pytest.mark.level0
def test_hermes_build_turn_command_positional_with_continue():
    adapter = build_adapter("hermes")
    assert not adapter.supports_stdin_injection
    first = adapter.build_turn_command("do it", session_id="s1", first_turn=True)
    assert first[0] == "hermes" and first[-1] == "do it"
    assert "--continue" not in first
    later = adapter.build_turn_command("again", session_id="s1", first_turn=False)
    assert "--continue" in later and later[-1] == "again"


@pytest.mark.level0
def test_openclaw_build_turn_command_message_and_session():
    adapter = build_adapter("openclaw")
    assert not adapter.supports_stdin_injection
    argv = adapter.build_turn_command("review", session_id="sess-9", first_turn=True)
    assert "--session-id" in argv
    assert argv[argv.index("--session-id") + 1] == "sess-9"
    assert "--message" in argv
    assert argv[argv.index("--message") + 1] == "review"


@pytest.mark.asyncio
@pytest.mark.level1
async def test_reinvoke_runtime_buffers_followups():
    runtime = ReinvokeCliRuntime(
        member_name="cli-1",
        adapter=build_adapter("hermes"),
        env={},
    )
    await runtime.steer("a")
    await runtime.follow_up("b")
    assert runtime._drain_pending() == "a\n\n---\n\nb"
    assert runtime._drain_pending() is None
    assert isinstance(runtime, ReinvokeCliRuntime)


@pytest.mark.asyncio
@pytest.mark.level0
async def test_reinvoke_runtime_surfaces_failed_turn(monkeypatch):
    """A CLI turn that fails (e.g. codex out of credits) must be perceivable.

    Drives a fake CLI that floods stderr (>64KB, enough to fill the OS pipe
    buffer) and exits non-zero. The run must (a) not deadlock — proving stderr
    is drained — and (b) log a warning carrying the exit code and the stderr
    reason, so an out-of-credits / auth / crash failure is visible instead of
    looking like the member silently did nothing.
    """
    import sys
    from unittest.mock import MagicMock

    from openjiuwen.agent_teams.external import runtime as runtime_mod
    from openjiuwen.agent_teams.external.cli_agent.adapters import (
        COMPLETION_NONE,
        INPUT_TEXT,
        CliAgentAdapter,
    )

    # 200KB of stderr (well past the ~64KB pipe buffer) ending in the reason.
    script = "import sys; sys.stderr.write('X' * 200000); sys.stderr.write('\\nError: insufficient credits'); sys.exit(1)"
    adapter = CliAgentAdapter(
        name="fake-broke",
        command=(sys.executable, "-c", script),
        input_format=INPUT_TEXT,
        completion=COMPLETION_NONE,
        supports_stdin_injection=False,
    )
    mock_logger = MagicMock()
    monkeypatch.setattr(runtime_mod, "team_logger", mock_logger)

    runtime = ReinvokeCliRuntime(member_name="codex-1", adapter=adapter, env=dict(os.environ))
    # Completes without hanging — if stderr were not drained this would block.
    async for _ in runtime.run_streaming({"query": "write the file"}, session_id="s"):
        pass

    assert mock_logger.warning.called, "a failed CLI turn must emit a warning"
    flat = " ".join(str(arg) for call in mock_logger.warning.call_args_list for arg in call.args)
    assert "codex-1" in flat
    assert "1" in flat  # the non-zero exit code
    assert "insufficient credits" in flat  # the stderr reason is surfaced
