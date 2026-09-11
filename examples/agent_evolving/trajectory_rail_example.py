# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Persist one Agent invoke trajectory with TrajectoryRail.

This example intentionally contains no Skill evolution behavior. It registers
one shared ``TrajectorySpanProcessor``, mounts ``TrajectoryRail``, runs an
Agent, and reads the canonical trajectory back from ``FileTrajectoryStore``.

Run with:

    uv sync --extra observability
    uv run --extra observability python -m \
      examples.agent_evolving.trajectory_rail_example
"""

from __future__ import annotations

import argparse
import asyncio
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from openjiuwen.agent_evolving.trajectory import FileTrajectoryStore, TrajectorySpanProcessor
from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig
from openjiuwen.core.runner import Runner
from openjiuwen.core.session.agent import Session
from openjiuwen.extensions.observability.config import ObservabilityConfig
from openjiuwen.extensions.observability.demand import get_trajectory_span_processor
from openjiuwen.harness import create_deep_agent
from openjiuwen.harness.observability import (
    AgentObservabilityRail,
    acquire_observability,
    close_agent_run_span,
    open_agent_run_span,
    release_observability,
)
from openjiuwen.harness.rails import TrajectoryRail


DEFAULT_QUERY = "请用三句话说明为什么 Agent 执行轨迹适合用于调试和评估。"


def load_env_if_present() -> None:
    loaded: set[Path] = set()
    for candidate in (Path.cwd() / ".env", Path(__file__).resolve().parents[2] / ".env"):
        resolved = candidate.resolve()
        if resolved in loaded or not resolved.exists():
            continue
        load_dotenv(resolved, override=False)
        loaded.add(resolved)


def build_model_from_env() -> Model:
    load_env_if_present()
    api_key = os.getenv("API_KEY", "")
    api_base = os.getenv("API_BASE", "")
    model_name = os.getenv("MODEL_NAME", "")
    missing = [
        name for name, value in (("API_KEY", api_key), ("API_BASE", api_base), ("MODEL_NAME", model_name)) if not value
    ]
    if missing:
        raise SystemExit("Missing required environment variables: " + ", ".join(missing) + ".")

    return Model(
        model_client_config=ModelClientConfig(
            client_provider=os.getenv("MODEL_PROVIDER", "OpenAI"),
            api_key=api_key,
            api_base=api_base,
            timeout=int(os.getenv("MODEL_TIMEOUT", "120")),
            verify_ssl=False,
        ),
        model_config=ModelRequestConfig(model=model_name, temperature=0.2, top_p=0.9),
    )


async def run_agent_with_observability(
    agent: Any,
    inputs: dict[str, Any],
    *,
    session: Session,
) -> dict[str, Any]:
    """Run one Agent turn under the current single-Agent observability API."""
    session_id = session.get_session_id()
    # The explicit run root gives LLM/tool spans a stable parent and session
    # route, including spans completed by detached callbacks.
    root_span = open_agent_run_span(session_id=session_id, mode="trajectory.archive")
    try:
        result = await Runner.run_agent(agent, inputs, session=session)
    except BaseException as exc:
        close_agent_run_span(root_span, session_id=session_id, exception=exc)
        raise
    close_agent_run_span(
        root_span,
        session_id=session_id,
        output=result.get("output", result),
    )
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="TrajectoryRail file persistence example")
    parser.add_argument("--workspace", help="Workspace root. Defaults to a temporary directory.")
    parser.add_argument("--query", default=DEFAULT_QUERY)
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    workspace = (
        Path(args.workspace).expanduser().resolve()
        if args.workspace
        else Path(tempfile.mkdtemp(prefix="trajectory_rail_example_")).resolve()
    )
    workspace.mkdir(parents=True, exist_ok=True)
    model = build_model_from_env()
    # Acquire observability before asking for its process-wide trajectory
    # processor. The same instance must be shared by span producers and Rail.
    acquire_observability(ObservabilityConfig(exporter="console"))
    processor: TrajectorySpanProcessor = get_trajectory_span_processor()
    # FileTrajectoryStore appends canonical trajectories to
    # trajectory-archive/trajectories_default.jsonl.
    store = FileTrajectoryStore(workspace / "trajectory-archive")

    # AgentObservabilityRail produces the execution spans; TrajectoryRail
    # converts completed LLM/tool spans and persists them through the store.
    rail = TrajectoryRail(
        trajectory_span_processor=processor,
        trajectory_store=store,
    )
    agent = create_deep_agent(
        model=model,
        rails=[AgentObservabilityRail(), rail],
        enable_task_loop=False,
        max_iterations=4,
        trajectory_span_processor=processor,
        workspace=str(workspace),
        language="cn",
    )
    session_id = f"trajectory_rail_{uuid.uuid4().hex}"
    session = Session(session_id=session_id, card=agent.card)

    try:
        await Runner.start()
        result = await run_agent_with_observability(
            agent,
            {"query": args.query},
            session=session,
        )
        # Query by the same Session id used by the run root to prove the JSONL
        # archive can be read back through the public store API.
        trajectories = store.query(session_id=session_id)

        print("workspace:", workspace)
        print("output:", result.get("output", result))
        print("trajectory archive:", workspace / "trajectory-archive" / "trajectories_default.jsonl")
        print("archived trajectories:", len(trajectories))
        for trajectory in trajectories:
            print(
                "trajectory:",
                {
                    "trajectory_id": trajectory.trajectory_id,
                    "session_id": trajectory.session_id,
                },
            )
    finally:
        try:
            await Runner.stop()
        finally:
            # Stop Runner first so pending span callbacks are drained before
            # the process-wide observability demand is released.
            release_observability()


if __name__ == "__main__":
    asyncio.run(main())
