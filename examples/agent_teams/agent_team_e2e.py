# coding: utf-8
"""Agent Team E2E test — CLI interactive script.

Run directly:
    python examples/agent_teams/agent_team_e2e.py

Configuration:
    config.yaml   — team spec, model, transport, storage, runtime settings
    logging.yaml  — loguru logging sinks / routes

Environment variable overrides (take precedence over config.yaml):
    API_BASE, API_KEY, MODEL_NAME, MODEL_PROVIDER, MODEL_TIMEOUT
"""

from __future__ import annotations

import asyncio
import os
import re
from asyncio import CancelledError
from pathlib import Path
from typing import Any

import yaml

from openjiuwen.agent_teams.agent.team_agent import TeamAgent
from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
from openjiuwen.core.common.logging import logger
from openjiuwen.core.common.logging.log_config import (
    configure_log,
    configure_log_config,
)
from openjiuwen.core.common.logging.loguru.constant import DEFAULT_INNER_LOG_CONFIG
from openjiuwen.core.runner.runner import Runner

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent
_LOG_CONFIG_PATH = _HERE / "logging.yaml"
_TEAM_CONFIG_PATH = _HERE / "config.yaml"

# ---------------------------------------------------------------------------
# Logging — must be configured before any logger is used
# ---------------------------------------------------------------------------
if _LOG_CONFIG_PATH.is_file():
    configure_log(str(_LOG_CONFIG_PATH))
else:
    configure_log_config(DEFAULT_INNER_LOG_CONFIG)


os.environ.setdefault("LLM_SSL_VERIFY", "false")
os.environ.setdefault("IS_SENSITIVE", "false")

# ---------------------------------------------------------------------------
# Environment variable expansion
# ---------------------------------------------------------------------------
_ENV_VAR_RE = re.compile(r"\$\{(\w+)}")


def _expand_env_vars(value: Any) -> Any:
    """Recursively replace ``${VAR}`` placeholders with environment values."""
    if isinstance(value, str):
        return _ENV_VAR_RE.sub(lambda m: os.environ.get(m.group(1), m.group(0)), value)
    if isinstance(value, dict):
        return {k: _expand_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env_vars(v) for v in value]
    return value


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------
def _load_team_config(path: Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return _expand_env_vars(raw)



# ---------------------------------------------------------------------------
# Async stdin helper
# ---------------------------------------------------------------------------
async def ainput(prompt: str = "> ") -> str:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, input, prompt)


# ---------------------------------------------------------------------------
# Stream consumer — classify and aggregate chunks by type
# ---------------------------------------------------------------------------

# Chunk type constants
_CHUNK_LLM_OUTPUT = "llm_output"
_CHUNK_LLM_REASONING = "llm_reasoning"
_CHUNK_ANSWER = "answer"
_CHUNK_TOOL_CALL = "tool_call"
_CHUNK_TOOL_RESULT = "tool_result"
_CHUNK_MESSAGE = "message"
_CHUNK_INTERACTION = "__interaction__"

# ANSI colors for terminal output
_COLOR_RESET = "\033[0m"
_COLOR_DIM = "\033[2m"
_COLOR_GREEN = "\033[92m"
_COLOR_CYAN = "\033[96m"
_COLOR_YELLOW = "\033[93m"


def _write(text: str) -> None:
    """Write text directly to stdout fd for immediate flush."""
    os.write(1, text.encode())


def _flush_buffer(chunk_type: str, buf: list[str]) -> None:
    """Print accumulated buffer with type-specific formatting."""
    if not buf:
        return
    text = "".join(buf)
    if not text.strip():
        return

    if chunk_type == _CHUNK_LLM_REASONING:
        _write(f"{_COLOR_DIM}[Reasoning] {text}{_COLOR_RESET}\n")
    elif chunk_type == _CHUNK_LLM_OUTPUT:
        _write(f"{_COLOR_GREEN}[Output] {_COLOR_RESET}{text}\n")
    elif chunk_type == _CHUNK_ANSWER:
        _write(f"{_COLOR_YELLOW}[Answer] {_COLOR_RESET}{text}\n")
    else:
        _write(f"[{chunk_type}] {text}\n")


def _extract_content(payload: Any) -> str:
    """Extract text content from chunk payload."""
    if isinstance(payload, dict):
        return payload.get("content", "") or payload.get("output", "")
    if isinstance(payload, str):
        return payload
    return str(payload)


async def consume_stream(leader: TeamAgent, query: str) -> None:
    logger.info("Starting leader stream with query: %s", query)

    cur_type = ""
    buf: list[str] = []
    has_llm_output = False

    async for chunk in Runner.run_agent_team_streaming(
        agent_team=leader,
        inputs={"query": query},
        session=_runtime_cfg.get("session_id", "agent_team_session"),
    ):
        chunk_type = getattr(chunk, "type", "")
        payload = getattr(chunk, "payload", None)

        # Tool call / tool result — flush buffer, print immediately
        if chunk_type == _CHUNK_TOOL_CALL:
            _flush_buffer(cur_type, buf)
            cur_type, buf = "", []
            tool_name = payload.get("tool_name", "") if isinstance(payload, dict) else ""
            tool_args = payload.get("tool_args", "") if isinstance(payload, dict) else ""
            _write(f"{_COLOR_CYAN}● {tool_name}{_COLOR_RESET}")
            if tool_args:
                _write(f"{_COLOR_DIM}({tool_args}){_COLOR_RESET}")
            _write("\n")
            continue

        if chunk_type == _CHUNK_TOOL_RESULT:
            tool_result = payload.get("tool_result", "") if isinstance(payload, dict) else str(payload)
            preview = str(tool_result)[:200]
            _write(f"{_COLOR_DIM}  ⎿ {preview}{_COLOR_RESET}\n\n")
            continue

        if chunk_type == _CHUNK_MESSAGE:
            _flush_buffer(cur_type, buf)
            cur_type, buf = "", []
            _write(f"{_COLOR_DIM}  ⚙ {_extract_content(payload)}{_COLOR_RESET}\n")
            continue

        if chunk_type == _CHUNK_INTERACTION:
            _flush_buffer(cur_type, buf)
            cur_type, buf = "", []
            _write(f"{_COLOR_YELLOW}[Interaction] {payload}{_COLOR_RESET}\n")
            continue

        # Streaming text chunks: accumulate by type, flush on type switch
        if chunk_type == _CHUNK_ANSWER and has_llm_output:
            # answer duplicates llm_output, skip if we already have it
            continue

        if chunk_type != cur_type:
            _flush_buffer(cur_type, buf)
            cur_type = chunk_type
            buf = []

        if chunk_type == _CHUNK_LLM_OUTPUT:
            has_llm_output = True

        buf.append(_extract_content(payload))

    # Flush remaining buffer
    _flush_buffer(cur_type, buf)
    logger.info("Leader stream finished.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
_team_cfg: dict[str, Any] = {}
_runtime_cfg: dict[str, Any] = {}


async def main() -> None:
    global _team_cfg, _runtime_cfg

    cfg = _load_team_config(_TEAM_CONFIG_PATH)
    _runtime_cfg = cfg.pop("runtime", {})
    _team_cfg = cfg

    spec = TeamAgentSpec.model_validate(_team_cfg)
    leader = spec.build()

    await Runner.start()

    print("=" * 60)
    print("Agent Team E2E — Interactive CLI")
    print("Type your message and press Enter to interact with the leader.")
    print("Type 'exit' or 'quit' to stop.")
    print("=" * 60)

    initial_query = _runtime_cfg.get("initial_query", "hello")
    stream_task = asyncio.create_task(consume_stream(leader, initial_query))

    try:
        while True:
            try:
                user_input = await ainput("\n[You] > ")
            except (EOFError, CancelledError):
                break

            if user_input.strip().lower() in ("exit", "quit"):
                print("Exiting...")
                break
            if not user_input.strip():
                continue

            await leader.interact(user_input)
            print(f"[System] Input sent to leader: {user_input}")

    except KeyboardInterrupt:
        print("\nInterrupted by user.")

    if not stream_task.done():
        stream_task.cancel()
        try:
            await stream_task
        except asyncio.CancelledError:
            pass

    await Runner.stop()
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
