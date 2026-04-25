# coding: utf-8
"""Shared utilities for agent-team E2E scripts."""

from __future__ import annotations

import asyncio
import os
import re
from asyncio import CancelledError
from pathlib import Path
from typing import Any

import yaml

from openjiuwen.agent_teams.agent.team_agent import TeamAgent
from openjiuwen.core.common.logging import logger
from openjiuwen.core.runner.runner import Runner

# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------
_ENV_VAR_RE = re.compile(r"\$\{(\w+)}")


def expand_env_vars(value: Any) -> Any:
    """Recursively replace ``${VAR}`` placeholders with environment values."""
    if isinstance(value, str):
        return _ENV_VAR_RE.sub(lambda m: os.environ.get(m.group(1), m.group(0)), value)
    if isinstance(value, dict):
        return {k: expand_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [expand_env_vars(v) for v in value]
    return value


def load_team_config(path: Path) -> dict[str, Any]:
    """Load and env-expand a YAML team config, pop the ``runtime`` key."""
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return expand_env_vars(raw)


# ---------------------------------------------------------------------------
# Async stdin helper
# ---------------------------------------------------------------------------
async def ainput(prompt: str = "> ") -> str:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, input, prompt)


# ---------------------------------------------------------------------------
# Stream consumer
# ---------------------------------------------------------------------------
_CHUNK_LLM_OUTPUT = "llm_output"
_CHUNK_LLM_REASONING = "llm_reasoning"
_CHUNK_ANSWER = "answer"
_CHUNK_TOOL_CALL = "tool_call"
_CHUNK_TOOL_RESULT = "tool_result"
_CHUNK_MESSAGE = "message"
_CHUNK_INTERACTION = "__interaction__"

_COLOR_RESET = "\033[0m"
_COLOR_DIM = "\033[2m"
_COLOR_GREEN = "\033[92m"
_COLOR_CYAN = "\033[96m"
_COLOR_YELLOW = "\033[93m"


def _write(text: str) -> None:
    os.write(1, text.encode())


def _flush_buffer(chunk_type: str, buf: list[str]) -> None:
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
    if isinstance(payload, dict):
        return payload.get("content", "") or payload.get("output", "")
    if isinstance(payload, str):
        return payload
    return str(payload)


async def consume_stream(leader: TeamAgent, query: str, session_id: str) -> None:
    logger.info("Starting leader stream with query: %s", query)

    cur_type = ""
    buf: list[str] = []
    has_llm_output = False

    async for chunk in Runner.run_agent_team_streaming(
        agent_team=leader,
        inputs={"query": query},
        session=session_id,
    ):
        chunk_type = getattr(chunk, "type", "")
        payload = getattr(chunk, "payload", None)

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

        if chunk_type == _CHUNK_ANSWER and has_llm_output:
            continue

        if chunk_type != cur_type:
            _flush_buffer(cur_type, buf)
            cur_type = chunk_type
            buf = []

        if chunk_type == _CHUNK_LLM_OUTPUT:
            has_llm_output = True

        buf.append(_extract_content(payload))

    _flush_buffer(cur_type, buf)
    logger.info("Leader stream finished.")


# ---------------------------------------------------------------------------
# Interactive loop
# ---------------------------------------------------------------------------
async def run_interactive(
    leader: TeamAgent,
    runtime_cfg: dict[str, Any],
    default_session_id: str,
    default_initial_query: str = "hello",
) -> None:
    """Run the standard interactive CLI loop."""
    session_id = runtime_cfg.get("session_id", default_session_id)
    initial_query = runtime_cfg.get("initial_query", default_initial_query)

    stream_task = asyncio.create_task(consume_stream(leader, initial_query, session_id))

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
