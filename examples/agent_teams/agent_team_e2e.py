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
from pathlib import Path
from typing import Any

from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
from openjiuwen.core.common.logging.log_config import (
    configure_log,
    configure_log_config,
)
from openjiuwen.core.common.logging.loguru.constant import DEFAULT_INNER_LOG_CONFIG
from openjiuwen.core.runner.runner import Runner

from _e2e_utils import load_team_config, run_interactive

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
# Main
# ---------------------------------------------------------------------------
_team_cfg: dict[str, Any] = {}
_runtime_cfg: dict[str, Any] = {}


async def main() -> None:
    global _team_cfg, _runtime_cfg

    cfg = load_team_config(_TEAM_CONFIG_PATH)
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

    await run_interactive(leader, _runtime_cfg, default_session_id="agent_team_session")

    await Runner.stop()
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
