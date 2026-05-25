# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Per-CLI launch knowledge for spawned third-party agent members.

Each third-party CLI differs in three ways the spawn path must know:

1. **launch command** — binary + permission-bypass flags + stream mode,
2. **input framing** — how a turn's text is written to the CLI stdin,
3. **turn completion** — how to tell from stdout that the turn is done.

:class:`CliAgentAdapter` captures these as data so tuning needs no code
change. Confidence varies by CLI and **none of these are validated against
the real binaries here** — comments on each built-in adapter state what is
assumed and what to verify in a real environment.

Only CLIs that read stdin continuously (a streaming-input / interactive
mode) support mid-turn injection; one-shot CLIs (prompt passed as an argv
flag) set ``supports_stdin_injection=False`` and would need a
re-invoke-per-turn runtime (not yet implemented).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, replace

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import raise_error

# ---- input framing strategies ----
INPUT_TEXT = "text"
# Claude Code stream-json: one NDJSON user message per turn on stdin.
INPUT_CLAUDE_STREAM_JSON = "claude_stream_json"
# Back-compat alias (kept for importers that referenced the old name).
INPUT_STREAM_JSON = INPUT_CLAUDE_STREAM_JSON
# Codex proto: one JSONL "submission" per turn on stdin.
INPUT_CODEX_PROTO = "codex_proto"

# ---- MCP-server injection strategies ----
# How to register a stdio MCP server with the CLI so the spawned agent gets
# the team collaboration tools. The team-join descriptor is NOT embedded in
# the MCP config: the MCP server is a child of the CLI process and inherits
# its environment (which carries OPENJIUWEN_TEAM_JOIN), so each member's
# server binds to that member's identity automatically.
MCP_INJECT_NONE = "none"
# Claude Code: pass an inline JSON config via ``--mcp-config <json>``.
MCP_INJECT_CLAUDE_FLAG = "claude_flag"
# Codex: set config keys via repeated ``-c mcp_servers.<name>.<key>=<value>``.
MCP_INJECT_CODEX_OVERRIDE = "codex_override"

# ---- turn-completion strategies ----
COMPLETION_NONE = "none"
# Claude Code stream-json: final event line is {"type": "result", ...}.
COMPLETION_RESULT_JSON = "result_json"
# Codex proto: an event line whose msg.type == "task_complete".
COMPLETION_CODEX_TASK_COMPLETE = "codex_task_complete"
# Substring marker: line contains the text after the prefix.
COMPLETION_MARKER_PREFIX = "marker:"


@dataclass(frozen=True, slots=True)
class CliAgentAdapter:
    """How to launch and talk to one third-party CLI agent.

    Attributes:
        name: Adapter key (``claude`` / ``codex`` / ...).
        command: Full launch argv (binary + flags).
        input_format: Input framing strategy (see ``INPUT_*``).
        completion: Turn-completion strategy (see ``COMPLETION_*``).
        supports_stdin_injection: Whether mid-turn stdin writes are observed.
            ``True`` CLIs run under the persistent-stdin streaming runtime;
            ``False`` (one-shot) CLIs run under the re-invoke-per-turn runtime
            (a fresh process per turn, prompt passed as argv, no mid-turn
            steer).
        prompt_flag: One-shot only. Pass the per-turn prompt as
            ``<prompt_flag> <prompt>`` (e.g. openclaw ``--message``); ``None``
            appends the prompt as the trailing positional argv.
        session_flag: One-shot only. Add ``<session_flag> <session_id>`` on
            every turn for per-member session isolation (e.g. openclaw
            ``--session-id``).
        continue_args: One-shot only. Args appended on turns *after* the first
            for cross-turn continuity (e.g. hermes ``("--continue",)``).
        mcp_inject: How to register a stdio MCP server with this CLI (see
            ``MCP_INJECT_*``). ``MCP_INJECT_NONE`` (default) means the spawn
            path cannot auto-inject team tools for this CLI.
    """

    name: str
    command: tuple[str, ...]
    input_format: str = INPUT_TEXT
    completion: str = COMPLETION_NONE
    supports_stdin_injection: bool = True
    prompt_flag: str | None = None
    session_flag: str | None = None
    continue_args: tuple[str, ...] = ()
    mcp_inject: str = MCP_INJECT_NONE

    def build_command(self, extra_args: tuple[str, ...] = ()) -> list[str]:
        """Return the launch argv, optionally with extra args appended."""
        return [*self.command, *extra_args]

    def build_turn_command(self, prompt: str, *, session_id: str, first_turn: bool) -> list[str]:
        """Build the per-turn launch argv for a one-shot (re-invoke) CLI.

        Args:
            prompt: The turn's message text.
            session_id: Per-member session id for ``session_flag`` CLIs.
            first_turn: Whether this is the member's first turn (controls
                ``continue_args``).
        """
        argv = list(self.command)
        if self.session_flag and session_id:
            argv += [self.session_flag, session_id]
        if not first_turn and self.continue_args:
            argv += list(self.continue_args)
        if self.prompt_flag:
            argv += [self.prompt_flag, prompt]
        else:
            argv.append(prompt)
        return argv

    def format_input(self, text: str) -> str:
        """Frame one turn's input text for writing to the CLI stdin."""
        if self.input_format == INPUT_CLAUDE_STREAM_JSON:
            return json.dumps({"type": "user", "message": {"role": "user", "content": text}})
        if self.input_format == INPUT_CODEX_PROTO:
            return json.dumps(
                {
                    "id": uuid.uuid4().hex,
                    "op": {"type": "user_input", "items": [{"type": "text", "text": text}]},
                }
            )
        return text

    def is_turn_complete(self, line: str) -> bool:
        """Return whether a stdout ``line`` signals the current turn is done."""
        if self.completion == COMPLETION_RESULT_JSON:
            return _json_field_equals(line, ("type",), "result")
        if self.completion == COMPLETION_CODEX_TASK_COMPLETE:
            return _json_field_equals(line, ("msg", "type"), "task_complete") or _json_field_equals(
                line, ("type",), "task_complete"
            )
        if self.completion.startswith(COMPLETION_MARKER_PREFIX):
            marker = self.completion[len(COMPLETION_MARKER_PREFIX) :]
            return bool(marker) and marker in line
        return False

    def mcp_launch_args(self, *, server_name: str, server_command: tuple[str, ...]) -> list[str]:
        """Build launch argv that registers a stdio MCP server with this CLI.

        Returns the extra arguments to append to the launch command so the
        CLI starts the named stdio MCP server (giving the agent the team
        collaboration tools). Returns an empty list for CLIs without an
        injection strategy (``MCP_INJECT_NONE``).

        Args:
            server_name: Logical MCP server name as the CLI should register it.
            server_command: Launch argv for the MCP server (binary + args).
        """
        if not server_command:
            return []
        binary = server_command[0]
        args = list(server_command[1:])
        if self.mcp_inject == MCP_INJECT_CLAUDE_FLAG:
            config = {"mcpServers": {server_name: {"command": binary, "args": args}}}
            return ["--mcp-config", json.dumps(config)]
        if self.mcp_inject == MCP_INJECT_CODEX_OVERRIDE:
            # Codex parses ``-c key=value`` values as TOML/JSON, so quote the
            # string and JSON-encode the args list. Dotted keys must use a
            # bare-key-safe server name (no characters needing quoting).
            key = server_name.replace("-", "_")
            argv = ["-c", f"mcp_servers.{key}.command={json.dumps(binary)}"]
            if args:
                argv += ["-c", f"mcp_servers.{key}.args={json.dumps(args)}"]
            return argv
        return []


def _json_field_equals(line: str, path: tuple[str, ...], expected: str) -> bool:
    """Return whether ``line`` is a JSON object with ``path`` == ``expected``."""
    stripped = line.strip()
    if not stripped.startswith("{"):
        return False
    try:
        node = json.loads(stripped)
    except json.JSONDecodeError:
        return False
    for key in path:
        if not isinstance(node, dict):
            return False
        node = node.get(key)
    return node == expected


# Built-in adapters. Launch flags follow ClawTeam's NativeCliAdapter
# conventions where known. NOT validated against the real binaries — verify
# command, input framing and completion detection per CLI version.
_BUILTIN: dict[str, CliAgentAdapter] = {
    # Claude Code — high confidence. `--print` is non-interactive;
    # `--input-format stream-json` reads NDJSON user messages from stdin
    # continuously (supports mid-turn injection); `--output-format
    # stream-json` (with `--verbose`) emits NDJSON events whose final per-turn
    # event is {"type": "result", ...}; `--dangerously-skip-permissions`
    # auto-approves tool use.
    "claude": CliAgentAdapter(
        name="claude",
        command=(
            "claude",
            "--print",
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
            "--verbose",
            "--dangerously-skip-permissions",
        ),
        input_format=INPUT_CLAUDE_STREAM_JSON,
        completion=COMPLETION_RESULT_JSON,
        mcp_inject=MCP_INJECT_CLAUDE_FLAG,
    ),
    # Codex CLI — moderate confidence. `codex proto` runs the JSONL protocol
    # stream over stdin/stdout: stdin takes "submission" objects
    # ({"id", "op": {"type": "user_input", "items": [...]}}) and stdout emits
    # "event" objects ({"id", "msg": {"type": ...}}); a turn ends on
    # msg.type == "task_complete". Config overrides make it non-interactive.
    # VERIFY: proto submission/event schema and the config keys per version.
    "codex": CliAgentAdapter(
        name="codex",
        command=(
            "codex",
            "proto",
            "-c",
            'approval_policy="never"',
            "-c",
            'sandbox_mode="danger-full-access"',
        ),
        input_format=INPUT_CODEX_PROTO,
        completion=COMPLETION_CODEX_TASK_COMPLETE,
        mcp_inject=MCP_INJECT_CODEX_OVERRIDE,
    ),
    # OpenClaw CLI — one-shot (re-invoke runtime). ClawTeam drives it as
    # `openclaw --local --session-id <id> --message "<msg>"`: prompt via the
    # --message flag, per-member continuity via a stable --session-id. The
    # re-invoke runtime launches this once per inbound message and reads
    # stdout to EOF. VERIFY flags against the real CLI.
    "openclaw": CliAgentAdapter(
        name="openclaw",
        command=("openclaw", "--local"),
        input_format=INPUT_TEXT,
        completion=COMPLETION_NONE,
        supports_stdin_injection=False,
        prompt_flag="--message",
        session_flag="--session-id",
    ),
    # Hermes Agent (NousResearch/hermes-agent) — one-shot, NOT stdin-streaming
    # (researched from the official CLI reference). `hermes -z "<prompt>"` is
    # the programmatic entry: reads ONE prompt (passed as argv; stdin is
    # supplementary context), prints only the final answer as plain text, then
    # exits — no continuous multi-prompt loop and no structured per-turn
    # delimiter (a turn ends at process exit / stdout EOF). `--yolo` bypasses
    # dangerous-command approval prompts. Cross-turn continuity uses
    # `--continue` / `--resume <session_id>`. The team MCP server is registered
    # out of band: `hermes mcp add <name> --command openjiuwen-team-mcp`.
    # The prompt is the trailing positional argv and the process exits per
    # turn, so it runs under the re-invoke runtime: one
    # `hermes -z --yolo [--continue] "<message>"` per inbound message, reading
    # stdout to EOF. `--continue` resumes the most-recent session for
    # cross-turn context — note this races across concurrent hermes members
    # (the version's named-session support would isolate them; VERIFY).
    "hermes": CliAgentAdapter(
        name="hermes",
        command=("hermes", "-z", "--yolo"),
        input_format=INPUT_TEXT,
        completion=COMPLETION_NONE,
        supports_stdin_injection=False,
        continue_args=("--continue",),
    ),
    # Line-based echo agent used by tests and simple integrations: one input
    # line, output terminated by an explicit end-of-turn marker.
    "generic": CliAgentAdapter(
        name="generic",
        command=(),
        input_format=INPUT_TEXT,
        completion=f"{COMPLETION_MARKER_PREFIX}<<END_OF_TURN>>",
    ),
}


def available_adapters() -> tuple[str, ...]:
    """Return the registered adapter names."""
    return tuple(_BUILTIN)


def build_adapter(name: str, *, command_override: tuple[str, ...] | None = None) -> CliAgentAdapter:
    """Resolve a built-in adapter by name.

    Args:
        name: Adapter key (see :func:`available_adapters`).
        command_override: Optional full launch argv replacing the default
            (e.g. an absolute binary path or extra flags).

    Raises:
        BaseError: ``AGENT_TEAM_CONFIG_INVALID`` for an unknown adapter.
    """
    adapter = _BUILTIN.get(name)
    if adapter is None:
        raise_error(
            StatusCode.AGENT_TEAM_CONFIG_INVALID,
            reason=f"unknown cli agent adapter '{name}'; known: {', '.join(available_adapters())}",
        )
        raise AssertionError  # pragma: no cover - raise_error always raises
    if command_override is not None:
        return replace(adapter, command=command_override)
    return adapter


__all__ = [
    "CliAgentAdapter",
    "available_adapters",
    "build_adapter",
    "INPUT_TEXT",
    "INPUT_CLAUDE_STREAM_JSON",
    "INPUT_STREAM_JSON",
    "INPUT_CODEX_PROTO",
    "COMPLETION_NONE",
    "COMPLETION_RESULT_JSON",
    "COMPLETION_CODEX_TASK_COMPLETE",
    "MCP_INJECT_NONE",
    "MCP_INJECT_CLAUDE_FLAG",
    "MCP_INJECT_CODEX_OVERRIDE",
]
