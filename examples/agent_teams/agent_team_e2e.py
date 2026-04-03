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
from openjiuwen.core.common.logging.log_config import configure_log, configure_log_config
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
# Stream consumer
# ---------------------------------------------------------------------------
async def consume_stream(leader: TeamAgent, query: str) -> None:
    logger.info("Starting leader stream with query: %s", query)
    async for chunk in Runner.run_agent_streaming(
        agent=leader,
        inputs={"query": query},
        session=_runtime_cfg.get("session_id", "agent_team_session"),
    ):
        print(chunk, flush=True)
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
