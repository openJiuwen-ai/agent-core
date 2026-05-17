# coding: utf-8
"""Agent Team E2E with OpenTelemetry observability enabled.

Run directly:
    python examples/agent_teams/agent_team_observability_e2e.py

Prerequisite — start the OTel + Langfuse stack first:
    cd deploy/observability && docker-compose up -d
    # then sign up at http://localhost:3000

Same configuration files as agent_team_e2e.py:
    config.yaml   — team spec
    logging.yaml  — loguru sinks

Observability is configured via environment variables (with sane defaults):
    OTEL_ENDPOINT       — OTLP gRPC endpoint (default http://localhost:4317)
    OTEL_SERVICE_NAME   — service.name resource attribute (default openjiuwen-agent-teams)
    OTEL_EXPORTER       — otlp_grpc | otlp_http | console (default otlp_grpc)
    OTEL_SAMPLE_RATE    — float in [0,1] (default 1.0)
    OTEL_REDACT_PROMPTS, OTEL_REDACT_COMPLETIONS — "1" to enable hashing

After the run, open Langfuse / your trace backend to see:
    - agent.<id> spans with input / output
    - llm.call spans with prompt + completion + TTFT
    - llm.reasoning child spans (if the model returns reasoning_content)
    - tool.<name> spans nested under their LLM call
    - team.<team_name> root span with member / message events
    - task.<task_id> spans with status transitions
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Ensure _e2e_utils is importable regardless of working directory.
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from openjiuwen.agent_teams.observability import (
    ObservabilityConfig,
    ObservabilityRail,
    attach_to_team_agent,
    init_observability,
    shutdown_observability,
)
from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.common.logging.log_config import (
    configure_log,
    configure_log_config,
)
from openjiuwen.core.common.logging.loguru.constant import DEFAULT_INNER_LOG_CONFIG
from openjiuwen.core.runner.runner import Runner

from _e2e_utils import load_team_config, run_interactive

# ---------------------------------------------------------------------------
# Logging — must be configured before any logger is used
# ---------------------------------------------------------------------------
_LOG_CONFIG_PATH = _HERE / "logging.yaml"
_TEAM_CONFIG_PATH = _HERE / "config.yaml"

if _LOG_CONFIG_PATH.is_file():
    configure_log(str(_LOG_CONFIG_PATH))
else:
    configure_log_config(DEFAULT_INNER_LOG_CONFIG)

os.environ.setdefault("LLM_SSL_VERIFY", "false")
os.environ.setdefault("IS_SENSITIVE", "false")


# ---------------------------------------------------------------------------
# Observability config from env
# ---------------------------------------------------------------------------
def _build_observability_config() -> ObservabilityConfig:
    """Read OTEL_* environment variables; fall back to local-friendly defaults."""
    exporter = os.environ.get("OTEL_EXPORTER", "otlp_grpc")
    if exporter not in ("otlp_grpc", "otlp_http", "console"):
        team_logger.warning("OTEL_EXPORTER={} not recognized; falling back to otlp_grpc", exporter)
        exporter = "otlp_grpc"

    return ObservabilityConfig(
        enabled=os.environ.get("OTEL_ENABLED", "1") not in ("0", "false", "False"),
        service_name=os.environ.get("OTEL_SERVICE_NAME", "openjiuwen-agent-teams"),
        exporter=exporter,
        endpoint=os.environ.get("OTEL_ENDPOINT", "http://localhost:4317"),
        sample_rate=float(os.environ.get("OTEL_SAMPLE_RATE", "1.0")),
        redact_prompts=os.environ.get("OTEL_REDACT_PROMPTS", "0") in ("1", "true", "True"),
        redact_completions=os.environ.get("OTEL_REDACT_COMPLETIONS", "0") in ("1", "true", "True"),
    )


def _maybe_register_observability_rail(leader: object) -> None:
    """Best-effort: attach ObservabilityRail to the leader's DeepAgent.

    Rail covers the DeepAgent task-iteration boundary, which the
    AsyncCallbackFramework cannot reach. If the underlying agent does
    not expose ``register_rail`` we skip silently — Callback handlers
    still cover ~80% of the observable surface.
    """
    deep_agent = getattr(leader, "deep_agent", None)
    register_rail = getattr(deep_agent, "register_rail", None)
    if register_rail is None:
        team_logger.info("observability: leader has no register_rail; skipping rail attach")
        return
    try:
        # register_rail is async; schedule it on the running loop.
        asyncio.create_task(register_rail(ObservabilityRail()))
    except Exception as exc:
        team_logger.warning("observability: failed to register ObservabilityRail - {}", exc)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def main() -> None:
    obs_config = _build_observability_config()
    init_observability(obs_config)
    team_logger.info(
        "observability: enabled={} exporter={} endpoint={} sample_rate={}",
        obs_config.enabled,
        obs_config.exporter,
        obs_config.endpoint,
        obs_config.sample_rate,
    )

    cfg = load_team_config(_TEAM_CONFIG_PATH)
    runtime_cfg = cfg.pop("runtime", {})

    spec = TeamAgentSpec.model_validate(cfg)
    leader = spec.build()

    await Runner.start()

    # Attach the team monitor handler so member / message / task events
    # land on the team root span. Must come after build() and before any
    # interaction so we don't miss the team_created event.
    attach_to_team_agent(leader)
    _maybe_register_observability_rail(leader)

    print("=" * 60)
    print("Agent Team E2E (observability) — Interactive CLI")
    print(f"OTel endpoint: {obs_config.endpoint}")
    print(f"OTel service:  {obs_config.service_name}")
    print("Type your message and press Enter; 'exit' or 'quit' to stop.")
    print("=" * 60)

    try:
        await run_interactive(
            leader,
            runtime_cfg,
            default_session_id="agent_team_observability_session",
        )
    finally:
        await Runner.stop()
        shutdown_observability()
        print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
