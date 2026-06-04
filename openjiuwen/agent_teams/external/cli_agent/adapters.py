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
from typing import Any

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
# Gemini CLI: register out of band via ``gemini mcp add <name> <bin> <args>``.
# There is no launch flag, so ``mcp_launch_args`` is empty and the spawn path
# runs ``mcp_register_command`` once before the member's first turn instead.
MCP_INJECT_GEMINI_ADD = "gemini_add"
# Hermes Agent: register out of band via ``hermes mcp add <name> --command <bin>``.
# Same out-of-band shape as gemini; ``mcp_launch_args`` is empty.
MCP_INJECT_HERMES_ADD = "hermes_add"

# ---- system-prompt injection strategies ----
# How to give the member a per-member system prompt (its persona / role).
SYSTEM_PROMPT_NONE = "none"
# Claude Code: pass the persona via ``--append-system-prompt <text>`` so it
# survives the whole long-lived process. CLIs without a system-prompt flag use
# ``SYSTEM_PROMPT_NONE``; the spawn path then prepends the persona to the
# member's first user message instead (proven, CLI-agnostic).
SYSTEM_PROMPT_CLAUDE_APPEND = "claude_append"
# Codex: inject the prompt as the developer-message layer via
# ``-c developer_instructions=<json>`` (additive — keeps codex's own base
# instructions). Verified accepted by ``codex exec --strict-config``.
SYSTEM_PROMPT_CODEX_DEVELOPER = "codex_developer"

# ---- turn-completion strategies ----
COMPLETION_NONE = "none"
# Claude Code stream-json: final event line is {"type": "result", ...}.
COMPLETION_RESULT_JSON = "result_json"
# Codex proto (legacy): an event line whose msg.type == "task_complete".
COMPLETION_CODEX_TASK_COMPLETE = "codex_task_complete"
# Codex exec --json: a JSONL event line whose type == "turn.completed".
COMPLETION_CODEX_JSON = "codex_json"
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
        structured_output: Whether stdout is a structured JSON event stream
            (claude / codex / gemini). When ``True`` :meth:`summarize_output_line`
            extracts assistant text / tool descriptors from each event for
            observability; when ``False`` each plain-text line is the narration.
        supports_stdin_injection: Whether mid-turn stdin writes are observed.
            ``True`` CLIs run under the persistent-stdin streaming runtime;
            ``False`` (one-shot) CLIs run under the re-invoke-per-turn runtime
            (a fresh process per turn, prompt passed as argv, no mid-turn
            steer).
        prompt_flag: One-shot only. Pass the per-turn prompt as
            ``<prompt_flag> <prompt>`` (e.g. openclaw ``--message``); ``None``
            appends the prompt as the trailing positional argv.
        session_flag: One-shot only. Add ``<session_flag> <session_id>`` to
            start/identify the member's session (e.g. openclaw / gemini
            ``--session-id``). Applied on every turn unless ``resume_flag`` is
            also set, in which case it is used only on the first turn.
        resume_flag: One-shot only. When set, turns *after* the first pass the
            same ``session_id`` via ``<resume_flag> <session_id>`` instead of
            ``session_flag`` to resume the started session (e.g. gemini starts
            with ``--session-id`` then resumes with ``--resume``).
        continue_args: One-shot only. Args appended on turns *after* the first
            for cross-turn continuity with no session id (e.g. hermes
            ``("--continue",)``).
        mcp_inject: How to register a stdio MCP server with this CLI (see
            ``MCP_INJECT_*``). ``MCP_INJECT_NONE`` (default) means the spawn
            path cannot auto-inject team tools for this CLI.
        system_prompt_inject: How to pass the member's system prompt (its
            team-rail sections) to this CLI (see ``SYSTEM_PROMPT_*``).
            ``SYSTEM_PROMPT_NONE`` (default) means the CLI has no
            system-prompt flag; the spawn path then prepends the prompt to the
            member's first user message instead.
        env_strip_prefixes: Env-var name prefixes to remove from the spawned
            CLI's environment. Used to drop a *parent* agent session's markers
            so the child runs as a fresh, independent instance instead of a
            nested one — e.g. launching a claude member from inside a Claude
            Code session would otherwise inherit ``CLAUDECODE`` /
            ``CLAUDE_CODE_*`` and make the child behave as a nested claude.
            Empty (default) keeps the full inherited environment.
    """

    name: str
    command: tuple[str, ...]
    input_format: str = INPUT_TEXT
    completion: str = COMPLETION_NONE
    structured_output: bool = False
    supports_stdin_injection: bool = True
    prompt_flag: str | None = None
    session_flag: str | None = None
    resume_flag: str | None = None
    continue_args: tuple[str, ...] = ()
    mcp_inject: str = MCP_INJECT_NONE
    system_prompt_inject: str = SYSTEM_PROMPT_NONE
    env_strip_prefixes: tuple[str, ...] = ()

    def build_command(self, extra_args: tuple[str, ...] = ()) -> list[str]:
        """Return the launch argv, optionally with extra args appended."""
        return [*self.command, *extra_args]

    def build_turn_command(
        self,
        prompt: str,
        *,
        session_id: str,
        first_turn: bool,
        extra_args: tuple[str, ...] = (),
    ) -> list[str]:
        """Build the per-turn launch argv for a one-shot (re-invoke) CLI.

        Args:
            prompt: The turn's message text.
            session_id: Per-member session id for ``session_flag`` CLIs.
            first_turn: Whether this is the member's first turn (controls
                ``continue_args``).
            extra_args: Extra launch args inserted before the prompt on every
                turn (e.g. MCP-server registration). Kept ahead of the prompt
                so CLIs that take the prompt as a trailing positional (codex
                exec) still parse the flags.
        """
        argv = list(self.command)
        if session_id:
            # First turn (or no resume_flag): start/identify the session with
            # session_flag. Later turns with a resume_flag resume that same id.
            if not first_turn and self.resume_flag:
                argv += [self.resume_flag, session_id]
            elif self.session_flag:
                argv += [self.session_flag, session_id]
        if not first_turn and self.continue_args:
            argv += list(self.continue_args)
        argv += list(extra_args)
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
        if self.completion == COMPLETION_CODEX_JSON:
            return _json_field_equals(line, ("type",), "turn.completed")
        if self.completion.startswith(COMPLETION_MARKER_PREFIX):
            marker = self.completion[len(COMPLETION_MARKER_PREFIX):]
            return bool(marker) and marker in line
        return False

    def summarize_output_line(self, line: str) -> str | None:
        """Extract a short human-readable summary of one stdout line.

        Used for observability (logging) and optional team-stream surfacing of
        what the external CLI is doing. For plain-text CLIs the line itself is
        the narration; for structured-output CLIs (claude / codex / gemini JSON
        streams) it pulls assistant text or a tool descriptor from the event and
        skips lifecycle / turn-boundary events. Returns ``None`` when there is
        nothing worth surfacing. Never raises.

        Best-effort across CLI JSON schemas: the assistant-text and tool shapes
        are confidently handled for claude; codex / gemini events fall through
        to defensive generic field extraction (VERIFY against real output).
        """
        text = line.strip()
        if not text:
            return None
        if not self.structured_output:
            return text
        if not text.startswith("{"):
            return None
        try:
            event = json.loads(text)
        except json.JSONDecodeError:
            return None
        if not isinstance(event, dict):
            return None
        return _summarize_event(event)

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

    def mcp_register_command(
        self,
        *,
        server_name: str,
        server_command: tuple[str, ...],
    ) -> list[str] | None:
        """Build a one-off command that registers a stdio MCP server with this CLI.

        Some CLIs have no launch flag for MCP and instead register servers via
        a subcommand that persists to their own config (``<cli> mcp add ...``).
        For those the spawn path runs this command once before the member's
        first turn. Returns ``None`` for CLIs that inject at launch (use
        :meth:`mcp_launch_args`) or have no known registration mechanism.

        Args:
            server_name: Logical MCP server name the CLI should register it under.
            server_command: Launch argv for the MCP server (binary + args).
        """
        if not server_command:
            return None
        binary = server_command[0]
        args = list(server_command[1:])
        cli = self.command[0] if self.command else self.name
        if self.mcp_inject == MCP_INJECT_GEMINI_ADD:
            # gemini mcp add <name> <commandOrUrl> [args...]
            return [cli, "mcp", "add", server_name, binary, *args]
        if self.mcp_inject == MCP_INJECT_HERMES_ADD:
            # hermes mcp add <name> --command <bin> -- <args...>; the exact
            # arg-forwarding form is version-specific (VERIFY against the real
            # CLI), so only the binary is forwarded by default.
            return [cli, "mcp", "add", server_name, "--command", binary]
        return None

    def system_prompt_args(self, text: str) -> list[str]:
        """Build launch argv that sets the member's system prompt for this CLI.

        Returns ``[]`` when the CLI has no system-prompt flag
        (``SYSTEM_PROMPT_NONE``); the caller then prepends ``text`` to the
        member's first user message instead.

        Args:
            text: The system prompt (the member's team-rail sections).
        """
        if not text:
            return []
        if self.system_prompt_inject == SYSTEM_PROMPT_CLAUDE_APPEND:
            return ["--append-system-prompt", text]
        if self.system_prompt_inject == SYSTEM_PROMPT_CODEX_DEVELOPER:
            # codex parses the -c value as TOML; a JSON-encoded string is a
            # valid TOML basic string and safely escapes newlines / quotes.
            return ["-c", f"developer_instructions={json.dumps(text)}"]
        return []

    def injects_system_prompt_via_arg(self) -> bool:
        """Return whether this CLI accepts a system prompt as a launch arg.

        ``False`` means the caller must prepend the system prompt to the
        member's first user message (no dedicated flag).
        """
        return self.system_prompt_inject != SYSTEM_PROMPT_NONE


# Max length of a surfaced output summary; longer narration is truncated.
_SUMMARY_LIMIT = 500
# Structured-output event types that carry no surfaceable narration (session /
# turn lifecycle markers across the claude / codex / gemini JSON schemas).
_LIFECYCLE_EVENT_TYPES = frozenset(
    {
        "result",
        "turn.started",
        "turn.completed",
        "thread.started",
        "session_init",
        "system",
        "task_started",
        "task_complete",
    }
)


def _summarize_event(event: dict[str, Any]) -> str | None:
    """Pull a short narration summary from one structured-output event.

    Tries, in order: claude assistant ``message.content`` blocks (text +
    tool_use names), codex ``item`` text / command fields, then generic
    top-level text fields (gemini and friends). Returns ``None`` for lifecycle
    events or events with no surfaceable text.
    """
    if event.get("type") in _LIFECYCLE_EVENT_TYPES:
        return None
    message = event.get("message")
    if isinstance(message, dict):
        summary = _summarize_content_blocks(message.get("content"))
        if summary:
            return summary
    item = event.get("item")
    if isinstance(item, dict):
        for key in ("text", "command", "aggregated_output"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:_SUMMARY_LIMIT]
    for key in ("text", "content", "delta"):
        value = event.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:_SUMMARY_LIMIT]
    return None


def _summarize_content_blocks(content: Any) -> str | None:
    """Summarize a claude assistant ``content`` block list (text + tool_use)."""
    if not isinstance(content, list):
        return None
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text" and isinstance(block.get("text"), str):
            parts.append(block["text"])
        elif block_type == "tool_use" and isinstance(block.get("name"), str):
            parts.append(f"→ {block['name']}")
    joined = " ".join(part.strip() for part in parts if part.strip())
    return joined[:_SUMMARY_LIMIT] if joined.strip() else None


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
        structured_output=True,
        mcp_inject=MCP_INJECT_CLAUDE_FLAG,
        system_prompt_inject=SYSTEM_PROMPT_CLAUDE_APPEND,
        # Drop a parent Claude Code session's markers so a claude member
        # spawned from inside another claude runs as a fresh top-level
        # instance, not a degraded nested one.
        env_strip_prefixes=("CLAUDECODE", "CLAUDE_CODE_"),
    ),
    # Codex CLI (>= 0.13x) — `codex exec --json` runs one prompt
    # non-interactively, streams JSONL events, and exits (one-shot, so it runs
    # under the re-invoke runtime). The prompt is the trailing positional argv;
    # the final per-turn event is {"type": "turn.completed"} and the process
    # exit / stdout EOF also ends the turn. `--dangerously-bypass-approvals-and-
    # sandbox` runs without approval prompts or sandbox; `--skip-git-repo-check`
    # is required because the team workspace is not a git repo (codex exec
    # otherwise refuses to run). MCP servers register via
    # `-c mcp_servers.<name>...` (codex_override) and the member system prompt
    # via `-c developer_instructions=<json>` (codex_developer), both applied on
    # every re-invocation.
    "codex": CliAgentAdapter(
        name="codex",
        command=(
            "codex",
            "exec",
            "--json",
            "--dangerously-bypass-approvals-and-sandbox",
            "--skip-git-repo-check",
        ),
        input_format=INPUT_TEXT,
        completion=COMPLETION_CODEX_JSON,
        structured_output=True,
        supports_stdin_injection=False,
        mcp_inject=MCP_INJECT_CODEX_OVERRIDE,
        system_prompt_inject=SYSTEM_PROMPT_CODEX_DEVELOPER,
    ),
    # Gemini CLI — `-p` runs one prompt non-interactively and exits (one-shot,
    # re-invoke runtime); the prompt is passed via `-p`, the process exit /
    # stdout EOF ends the turn. `-o stream-json` selects the structured headless
    # output and `-y` (yolo) auto-approves tool use. MCP registers out of band
    # via `gemini mcp add` (no launch flag), so the spawn path runs that once.
    # Cross-turn continuity (cf. clowder's `--resume <id>`): the first turn
    # starts the session with a client-chosen id via `--session-id`, later
    # turns resume it via `--resume`. VERIFY flags against the installed gemini
    # version (esp. that `--resume` accepts the `--session-id` id).
    "gemini": CliAgentAdapter(
        name="gemini",
        command=("gemini", "-o", "stream-json", "-y"),
        input_format=INPUT_TEXT,
        completion=COMPLETION_NONE,
        structured_output=True,
        supports_stdin_injection=False,
        prompt_flag="-p",
        session_flag="--session-id",
        resume_flag="--resume",
        mcp_inject=MCP_INJECT_GEMINI_ADD,
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
        mcp_inject=MCP_INJECT_HERMES_ADD,
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
    "COMPLETION_CODEX_JSON",
    "MCP_INJECT_NONE",
    "MCP_INJECT_CLAUDE_FLAG",
    "MCP_INJECT_CODEX_OVERRIDE",
    "MCP_INJECT_GEMINI_ADD",
    "MCP_INJECT_HERMES_ADD",
    "SYSTEM_PROMPT_NONE",
    "SYSTEM_PROMPT_CLAUDE_APPEND",
    "SYSTEM_PROMPT_CODEX_DEVELOPER",
]
