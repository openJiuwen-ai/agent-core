# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Agent Team runtime lifecycle E2E walkthrough.

Drives the five dispatch paths documented in
``openjiuwen/agent_teams/runtime/dispatch.py``:

* CREATE              — first run with a fresh ``(team_name, session_id)``
* RESUME_FROM_PAUSE   — re-run with the same name + session after the
                        previous round paused the pool entry
* NEW_TEAM_IN_SESSION — same spec, new session, no session bucket yet
* COLD_RECOVER        — ``team_name + old_session`` after ``stop_team``
                        cleared the pool entry (F_06: spec is reloaded
                        from the session checkpoint bucket)
* session-switch      — concurrent session change forces the pre-dispatch
                        ``stop_team`` tear-down + cold rebuild

Each scenario uses a unique team_name (uuid-suffixed) so reruns do not
leak persistent rows between invocations. Streams are cancelled right
after ``team.runtime_ready`` lands so the walkthrough stays cheap: the
lifecycle plumbing is what matters here, not LLM round content.

Run all scenarios:
    ./examples/agent_teams/run_lifecycle_e2e.sh

Run a single scenario:
    ./examples/agent_teams/run_lifecycle_e2e.sh -- cold_recover

CLI form:
    python examples/agent_teams/agent_team_lifecycle_e2e.py [scenario ...]
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import sys
import uuid
from dataclasses import (
    dataclass,
    field,
)
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent.parent))

from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
from openjiuwen.core.common.logging import LazyLogger
from openjiuwen.core.common.logging.log_config import (
    configure_log,
    configure_log_config,
)
from openjiuwen.core.common.logging.loguru.constant import DEFAULT_INNER_LOG_CONFIG
from openjiuwen.core.common.logging.manager import LogManager
from openjiuwen.core.runner.runner import Runner

from _e2e_utils import load_team_config

_LOG_CONFIG_PATH = _HERE / "logging.yaml"
_TEAM_CONFIG_PATH = _HERE / "config.yaml"

if _LOG_CONFIG_PATH.is_file():
    configure_log(str(_LOG_CONFIG_PATH))
else:
    configure_log_config(DEFAULT_INNER_LOG_CONFIG)

logger = LazyLogger(lambda: LogManager.get_logger("lifecycle_e2e"))

os.environ.setdefault("LLM_SSL_VERIFY", "false")
os.environ.setdefault("IS_SENSITIVE", "false")

_RUNTIME_READY_TIMEOUT = 60.0


@dataclass(slots=True)
class StreamHandle:
    """Tracks a ``run_agent_team_streaming`` task and its ready future."""

    team_name: str
    session_id: str
    task: asyncio.Task
    runtime_ready: asyncio.Future
    last_ack: dict[str, Any] | None = None
    chunks: int = 0
    error: BaseException | None = field(default=None)


async def _consume_stream(
    *,
    agent_team: str | TeamAgentSpec,
    inputs: dict[str, Any],
    session_id: str,
    runtime_ready: asyncio.Future,
    handle_ref: list[StreamHandle | None],
) -> None:
    """Pump the streaming iterator; resolve ``runtime_ready`` on first ack."""
    try:
        async for chunk in Runner.run_agent_team_streaming(
            agent_team=agent_team,
            inputs=inputs,
            session=session_id,
        ):
            payload = getattr(chunk, "payload", None)
            if (
                isinstance(payload, dict)
                and payload.get("event_type") == "team.runtime_ready"
                and not runtime_ready.done()
            ):
                runtime_ready.set_result(payload)
                continue
            handle = handle_ref[0]
            if handle is not None:
                handle.chunks += 1
    except asyncio.CancelledError:
        raise
    except BaseException as exc:
        if not runtime_ready.done():
            runtime_ready.set_exception(exc)
        handle = handle_ref[0]
        if handle is not None:
            handle.error = exc
        raise


async def _start_stream(
    *,
    agent_team: str | TeamAgentSpec,
    session_id: str,
    query: str,
) -> StreamHandle:
    """Kick off a streaming run and wait until ``team.runtime_ready`` lands."""
    team_name = (
        agent_team.team_name if isinstance(agent_team, TeamAgentSpec) else agent_team
    )
    runtime_ready: asyncio.Future = asyncio.get_running_loop().create_future()
    handle_ref: list[StreamHandle | None] = [None]
    task = asyncio.create_task(
        _consume_stream(
            agent_team=agent_team,
            inputs={"query": query},
            session_id=session_id,
            runtime_ready=runtime_ready,
            handle_ref=handle_ref,
        ),
        name=f"lifecycle::{team_name}::{session_id}",
    )
    handle = StreamHandle(
        team_name=team_name,
        session_id=session_id,
        task=task,
        runtime_ready=runtime_ready,
    )
    handle_ref[0] = handle
    try:
        ack = await asyncio.wait_for(runtime_ready, timeout=_RUNTIME_READY_TIMEOUT)
    except BaseException:
        await _cancel_stream(handle)
        raise
    handle.last_ack = ack
    return handle


async def _cancel_stream(handle: StreamHandle) -> None:
    """Cancel and await the stream task, swallowing cancellation."""
    if handle.task.done():
        return
    handle.task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await handle.task


def _expect_kind(handle: StreamHandle, expected: str) -> None:
    """Assert the activation kind reported by ``team.runtime_ready``."""
    ack = handle.last_ack or {}
    actual = ack.get("activation_kind")
    if actual != expected:
        raise AssertionError(
            f"unexpected activation_kind: expected={expected!r} actual={actual!r} "
            f"team={handle.team_name!r} session={handle.session_id!r} ack={ack!r}",
        )
    logger.info(
        "ack: team={} session={} activation_kind={}",
        handle.team_name,
        handle.session_id,
        actual,
    )


async def _snapshot_pool(label: str) -> None:
    """Log the current pool snapshot for visual inspection."""
    infos = await Runner.list_active_teams()
    if not infos:
        logger.info("[{}] pool: empty", label)
        return
    for info in infos:
        logger.info(
            "[{}] pool: team={} session={} state={} gate_closed={}",
            label,
            info.team_name,
            info.current_session_id,
            info.state.value,
            info.gate_closed,
        )


async def _cleanup_team(team_name: str, session_ids: list[str]) -> None:
    """Force-delete the team + its session checkpoints; ignore failures."""
    try:
        await Runner.delete_agent_team(
            team_name=team_name,
            session_ids=session_ids,
            force=True,
        )
    except BaseException as exc:
        logger.warning(
            "cleanup failed for team={} sessions={}: {}",
            team_name,
            session_ids,
            exc,
        )


def _build_spec(base: TeamAgentSpec, team_name: str) -> TeamAgentSpec:
    """Clone the base spec under a new ``team_name`` so scenarios stay isolated."""
    return base.model_copy(update={"team_name": team_name})


def _unique_team_name(base_name: str, tag: str) -> str:
    """Append a short uuid suffix so reruns do not collide on persisted rows."""
    return f"{base_name}_{tag}_{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


async def scenario_create(base: TeamAgentSpec) -> None:
    """spec + new session → CREATE."""
    team_name = _unique_team_name(base.team_name, "create")
    session_id = f"sess_{uuid.uuid4().hex[:8]}"
    spec = _build_spec(base, team_name)
    handle = await _start_stream(agent_team=spec, session_id=session_id, query="hello")
    _expect_kind(handle, "create")
    await _snapshot_pool("create")
    await _cancel_stream(handle)
    await _cleanup_team(team_name, [session_id])


async def scenario_resume(base: TeamAgentSpec) -> None:
    """Persistent team: first run CREATE → cancel → pool PAUSED → re-run RESUME_FROM_PAUSE.

    The persistent lifecycle finalize path (manager.finalize) keeps the
    pool entry alive in PAUSED state after the stream task is cancelled,
    which is exactly what RESUME_FROM_PAUSE expects on dispatch.
    """
    team_name = _unique_team_name(base.team_name, "resume")
    session_id = f"sess_{uuid.uuid4().hex[:8]}"
    spec = _build_spec(base, team_name)

    first = await _start_stream(agent_team=spec, session_id=session_id, query="hello")
    _expect_kind(first, "create")
    await _cancel_stream(first)
    await _snapshot_pool("resume::after-cancel")

    second = await _start_stream(
        agent_team=team_name,
        session_id=session_id,
        query="continue",
    )
    _expect_kind(second, "resume_from_pause")
    await _snapshot_pool("resume::after-resume")
    await _cancel_stream(second)
    await _cleanup_team(team_name, [session_id])


async def scenario_stop_new_session(base: TeamAgentSpec) -> None:
    """stop_team → spec + new session → NEW_TEAM_IN_SESSION.

    ``stop_agent_team`` clears the pool entry but preserves the static
    team row + the original session bucket. Dispatching on a brand-new
    session has ``team_in_db=True / team_in_session=False`` → cold path
    without a bucket → NEW_TEAM_IN_SESSION.
    """
    team_name = _unique_team_name(base.team_name, "stopnew")
    session_a = f"sess_a_{uuid.uuid4().hex[:8]}"
    session_b = f"sess_b_{uuid.uuid4().hex[:8]}"
    spec = _build_spec(base, team_name)

    first = await _start_stream(agent_team=spec, session_id=session_a, query="hello")
    _expect_kind(first, "create")
    await _cancel_stream(first)

    stopped = await Runner.stop_agent_team(team_name=team_name, session_id=session_a)
    logger.info("stop_agent_team ok={} team={}", stopped, team_name)
    await _snapshot_pool("stopnew::after-stop")

    second = await _start_stream(agent_team=spec, session_id=session_b, query="hello")
    _expect_kind(second, "new_team_in_session")
    await _snapshot_pool("stopnew::after-new-session")
    await _cancel_stream(second)
    await _cleanup_team(team_name, [session_a, session_b])


async def scenario_cold_recover(base: TeamAgentSpec) -> None:
    """stop_team → name + old session (no spec) → COLD_RECOVER.

    Exercises the F_06 contract: after the pool entry is gone, the
    ``team_name`` shorthand can still recover by reading the spec back
    from the session checkpoint bucket persisted on the previous round.
    """
    team_name = _unique_team_name(base.team_name, "coldrecover")
    session_id = f"sess_{uuid.uuid4().hex[:8]}"
    spec = _build_spec(base, team_name)

    first = await _start_stream(agent_team=spec, session_id=session_id, query="hello")
    _expect_kind(first, "create")
    await _cancel_stream(first)

    stopped = await Runner.stop_agent_team(team_name=team_name, session_id=session_id)
    logger.info("stop_agent_team ok={} team={}", stopped, team_name)
    await _snapshot_pool("coldrecover::after-stop")

    # No spec — only team_name + old session. Spec must be reloaded from
    # the session bucket via _resolve_spec_from_session_bucket.
    second = await _start_stream(
        agent_team=team_name,
        session_id=session_id,
        query="continue",
    )
    _expect_kind(second, "cold_recover")
    await _snapshot_pool("coldrecover::after-recover")
    await _cancel_stream(second)
    await _cleanup_team(team_name, [session_id])


async def scenario_session_switch(base: TeamAgentSpec) -> None:
    """Same team, two sessions back-to-back → cross-session tear-down + cold rebuild.

    The first stream is left in PAUSED state on session A (cancel triggers
    persistent finalize). When we start on session B with the same spec,
    ``manager.activate`` detects the cross-session pool entry and runs
    ``stop_team`` before dispatching — and B has no session bucket of its
    own, so the dispatch decides NEW_TEAM_IN_SESSION.
    """
    team_name = _unique_team_name(base.team_name, "switch")
    session_a = f"sess_a_{uuid.uuid4().hex[:8]}"
    session_b = f"sess_b_{uuid.uuid4().hex[:8]}"
    spec = _build_spec(base, team_name)

    first = await _start_stream(agent_team=spec, session_id=session_a, query="hello")
    _expect_kind(first, "create")
    await _cancel_stream(first)
    await _snapshot_pool("switch::after-cancel-A")

    second = await _start_stream(agent_team=spec, session_id=session_b, query="hello")
    _expect_kind(second, "new_team_in_session")
    await _snapshot_pool("switch::after-rebuild-B")
    await _cancel_stream(second)
    await _cleanup_team(team_name, [session_a, session_b])


SCENARIOS: dict[str, Any] = {
    "create": scenario_create,
    "resume": scenario_resume,
    "stop_new_session": scenario_stop_new_session,
    "cold_recover": scenario_cold_recover,
    "session_switch": scenario_session_switch,
}


async def _run(scenarios: list[str]) -> None:
    cfg = load_team_config(_TEAM_CONFIG_PATH)
    cfg.pop("runtime", {})
    base_spec = TeamAgentSpec.model_validate(cfg)

    await Runner.start()
    failures: list[tuple[str, BaseException]] = []
    try:
        for name in scenarios:
            scenario = SCENARIOS[name]
            logger.info("=" * 60)
            logger.info("[scenario] {}", name)
            logger.info("=" * 60)
            try:
                await scenario(base_spec)
            except BaseException as exc:
                logger.exception("[scenario:{}] FAILED: {}", name, exc)
                failures.append((name, exc))
    finally:
        await Runner.stop()

    if failures:
        for name, exc in failures:
            logger.error("[summary] {}: {!r}", name, exc)
        raise SystemExit(1)
    logger.info("[summary] all {} scenarios passed", len(scenarios))


def _parse_args(argv: list[str]) -> list[str]:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "scenarios",
        nargs="*",
        choices=[*SCENARIOS.keys(), "all"],
        default=["all"],
        help=(
            "scenario names to run (default: all). "
            f"available: {', '.join(SCENARIOS.keys())}"
        ),
    )
    args = parser.parse_args(argv)
    selected = args.scenarios or ["all"]
    if "all" in selected:
        return list(SCENARIOS.keys())
    return selected


if __name__ == "__main__":
    asyncio.run(_run(_parse_args(sys.argv[1:])))
