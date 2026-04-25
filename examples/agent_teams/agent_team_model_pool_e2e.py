# coding: utf-8
"""Agent Team model-pool E2E — verifies multi-type pool allocation.

Run directly:
    python examples/agent_teams/agent_team_model_pool_e2e.py

Configuration:
    config_model_pool.yaml — team spec with multi-type model pool
    logging.yaml           — loguru logging sinks / routes

Environment variables:
    POOL_OPENROUTER_BASE     — OpenRouter base URL (default: https://openrouter.ai/api/v1)
    POOL_GLM51_KEY_1/2       — z-ai/glm-5.1 (OpenRouter)
    POOL_MIMO_KEY_1/2        — xiaomi/mimo-v2-pro (OpenRouter)
    POOL_MINIMAX25_KEY_1/2   — minimax/minimax-m2.5 (OpenRouter)
    POOL_MINIMAX27_KEY_1/2   — minimax/minimax-m2.7 (OpenRouter)
    POOL_KIMI26_KEY_1/2      — moonshotai/kimi-k2.6 (OpenRouter)
    POOL_KIMI25_KEY_1/2      — moonshotai/kimi-k2.5 (OpenRouter)
    POOL_GLM5_BASE           — ZhipuAI OpenAI-compatible endpoint
    POOL_GLM5_KEY            — glm-5 (OpenAI direct)
    POOL_GLM47_KEY           — glm-4.7 (OpenAI direct)

API keys are intentionally mocked in config_model_pool.yaml.  Set the
environment variables above to real endpoints before running against a
live LLM service.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

# Ensure _e2e_utils is importable regardless of working directory
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
from openjiuwen.agent_teams.schema.team import ModelPoolEntry
from openjiuwen.core.common.logging.log_config import (
    configure_log,
    configure_log_config,
)
from openjiuwen.core.common.logging.loguru.constant import DEFAULT_INNER_LOG_CONFIG
from openjiuwen.core.runner.runner import Runner

from _e2e_utils import load_team_config, run_interactive

_LOG_CONFIG_PATH = _HERE / "logging.yaml"
_TEAM_CONFIG_PATH = _HERE / "config_model_pool.yaml"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
if _LOG_CONFIG_PATH.is_file():
    configure_log(str(_LOG_CONFIG_PATH))
else:
    configure_log_config(DEFAULT_INNER_LOG_CONFIG)

os.environ.setdefault("LLM_SSL_VERIFY", "false")
os.environ.setdefault("IS_SENSITIVE", "false")

# # Base URL — all models go through OpenRouter
# os.environ.setdefault("POOL_OPENROUTER_BASE", "https://openrouter.ai/api/v1")
#
# # API keys — must be set to real values before running against a live service
# os.environ.setdefault("POOL_GLM51_KEY_1", "sk-mock-glm51-key-1")
# os.environ.setdefault("POOL_GLM51_KEY_2", "sk-mock-glm51-key-2")
# os.environ.setdefault("POOL_MIMO_KEY_1", "sk-mock-mimo-key-1")
# os.environ.setdefault("POOL_MIMO_KEY_2", "sk-mock-mimo-key-2")
# os.environ.setdefault("POOL_MINIMAX25_KEY_1", "sk-mock-minimax25-key-1")
# os.environ.setdefault("POOL_MINIMAX25_KEY_2", "sk-mock-minimax25-key-2")
# os.environ.setdefault("POOL_MINIMAX27_KEY_1", "sk-mock-minimax27-key-1")
# os.environ.setdefault("POOL_MINIMAX27_KEY_2", "sk-mock-minimax27-key-2")
# os.environ.setdefault("POOL_KIMI26_KEY_1", "sk-mock-kimi26-key-1")
# os.environ.setdefault("POOL_KIMI26_KEY_2", "sk-mock-kimi26-key-2")
# os.environ.setdefault("POOL_KIMI25_KEY_1", "sk-mock-kimi25-key-1")
# os.environ.setdefault("POOL_KIMI25_KEY_2", "sk-mock-kimi25-key-2")
# os.environ.setdefault("POOL_GLM5_BASE", "https://open.bigmodel.cn/api/paas/v4")
# os.environ.setdefault("POOL_GLM5_KEY", "sk-mock-glm5-key")


# ---------------------------------------------------------------------------
# Pool allocation display
# ---------------------------------------------------------------------------
def _print_pool_summary(spec: TeamAgentSpec) -> None:
    """Print a table of pool entries so the user can verify allocation order."""
    pool = spec.model_pool
    if not pool:
        print("  (no model pool configured — members use per-agent model)")
        return

    print(f"  strategy : {spec.model_pool_strategy}")
    print(f"  entries  : {len(pool)}")
    print()
    print(f"  {'#':<3}  {'model_name':<20}  {'provider':<14}  {'api_base_url':<40}  description")
    print(f"  {'-'*3}  {'-'*20}  {'-'*14}  {'-'*40}  {'-'*20}")
    for idx, entry in enumerate(pool):
        _print_pool_entry(idx, entry)
    print()


def _print_pool_entry(idx: int, entry: ModelPoolEntry) -> None:
    api_key_hint = f"{entry.api_key[:8]}…" if len(entry.api_key) > 8 else entry.api_key
    print(
        f"  {idx:<3}  {entry.model_name:<20}  {entry.api_provider:<14}"
        f"  {entry.api_base_url:<40}  {entry.description or ''}"
    )
    print(f"       api_key: {api_key_hint}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def main() -> None:
    cfg = load_team_config(_TEAM_CONFIG_PATH)
    runtime_cfg: dict[str, Any] = cfg.pop("runtime", {})

    spec = TeamAgentSpec.model_validate(cfg)

    print("=" * 70)
    print("Agent Team Model Pool E2E")
    print("=" * 70)
    print()
    print(f"  team_name   : {spec.team_name}")
    print(f"  agent roles : {list(spec.agents.keys())}  (teammates spawned dynamically)")
    print()
    print("Model pool:")
    _print_pool_summary(spec)

    leader = spec.build()

    await Runner.start()

    print("=" * 70)
    print("Interactive CLI — type your message and press Enter.")
    print("Type 'exit' or 'quit' to stop.")
    print("=" * 70)

    await run_interactive(leader, runtime_cfg, default_session_id="model_pool_session")

    await Runner.stop()
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
