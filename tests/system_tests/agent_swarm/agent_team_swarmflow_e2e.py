# coding: utf-8
"""Swarmflow E2E — drive a full swarmflow run from the team entry point.

This exercises the complete swarmflow surface end-to-end through the public
team facade (``Runner.run_agent_team_streaming``):

* the leader owns the ``swarmflow(script_path, args)`` tool (``enable_swarmflow``)
  and launches ``resources/party_planner.py`` in the background;
* that script covers every primitive — stateless ``agent``, stateful
  ``agent_session``, stateless ``human``, stateful ``human_session``,
  ``pipeline`` / ``parallel`` concurrency, and a nested ``workflow``;
* phase / human progress streams back as ``workflow_progress`` events, which we
  drain off a ``TeamMonitor``;
* each pending human turn is auto-answered through the same public inbound path a
  UI uses: ``interact_agent_team(HumanAgentMessage(target="swarmflow:<corr>"))``.

The script is self-verifying: it asserts the workflow started, every phase ran,
human turns were prompted and answered, and the run completed (not failed),
exiting non-zero on any failure.

Run directly (needs a real model endpoint, see config_llm_local.yaml):
    python tests/system_tests/agent_swarm/agent_team_swarmflow_e2e.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
import uuid
from pathlib import Path


# Ensure _e2e_utils / llm_config are importable regardless of working directory.
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent))

from openjiuwen.agent_teams import paths
from openjiuwen.agent_teams.interaction.payload import HumanAgentMessage
from openjiuwen.agent_teams.paths import configure_openjiuwen_home
from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
from openjiuwen.core.common.logging.log_config import (
    configure_log,
    configure_log_config,
)
from openjiuwen.core.common.logging.loguru.constant import DEFAULT_INNER_LOG_CONFIG
from openjiuwen.core.runner.runner import Runner

from _e2e_utils import load_team_config
from llm_config import load_llm_config
from tests.test_logger import logger as test_logger

# ---------------------------------------------------------------------------
# Paths / config
# ---------------------------------------------------------------------------
_LOG_CONFIG_PATH = _HERE / "logging.yaml"
_TEAM_CONFIG_PATH = _HERE / "config_swarmflow.yaml"
_SCRIPT_PATH = _HERE / "resources" / "party_planner.py"
# We run from a dedicated scratch dir (gitignored) so the team's runtime
# scaffolding (identity / memory / skills / logs written to cwd) never pollutes
# the test tree. The leader echoes this short relative path into its swarmflow
# tool call — a long absolute path is easily mangled by a small model, and the
# nested workflow resolves its sibling via __file__, so it is cwd-independent.
_WORKDIR = _HERE / ".e2e_workdir"
_SCRIPT_REL = "../resources/party_planner.py"

# Fail fast if the leader never launches the workflow within this window after
# the runtime is ready (instead of waiting out the whole-run ceiling).
_START_DEADLINE_S = 150.0
# Hard ceiling on the whole run (the party_planner flow is ~1-2 min on qwen flash).
_RUN_TIMEOUT_S = 600.0
# After the workflow completes, let the leader consume the async-tool result
# injection and narrate it before tearing down: wait until the leader stream has
# been quiet for this long (also a floor that lets the injection land when the
# leader narrates nothing), capped by the max.
_NARRATION_QUIESCE_S = 6.0
_NARRATION_MAX_S = 90.0
# Teardown (Runner.stop) is optional. Set SWARMFLOW_E2E_TEARDOWN=0 to leave the
# runtime up and keep every intermediate file (journal / scratch dir / team db)
# for inspection. Default on (clean shutdown). The journal is already flushed to
# disk by the time the workflow completes, so it survives either way.
_TEARDOWN = os.getenv("SWARMFLOW_E2E_TEARDOWN", "1").strip().lower() not in ("0", "false", "no")

if _LOG_CONFIG_PATH.is_file():
    configure_log(str(_LOG_CONFIG_PATH))
else:
    configure_log_config(DEFAULT_INNER_LOG_CONFIG)

os.environ.setdefault("LLM_SSL_VERIFY", "false")
os.environ.setdefault("IS_SENSITIVE", "false")

# Keep team DB / journals out of the user's real openjiuwen home.
configure_openjiuwen_home(str(_HERE / "openjiuwen_home"))


# ---------------------------------------------------------------------------
# Env wiring: source the model endpoint from config_llm_local.yaml
# ---------------------------------------------------------------------------
def _wire_model_env() -> None:
    """Populate API_BASE / *_API_KEY / MODEL_NAME from the local LLM config.

    ``config_swarmflow.yaml`` references these via ``${VAR}`` placeholders, so
    they must exist before the spec is validated.

    Endpoint and model are resolved together from one ref, so they cannot
    disagree — hard-coding a model name here drifts the moment the config
    points somewhere else, and the request then fails with a model the
    endpoint has never heard of. Defaults to the config's first model;
    override without editing YAML::

        OPENJIUWEN_E2E_MODEL=gateway/GLM-5.1 python ..._e2e.py
    """
    model = load_llm_config().resolve()
    test_logger.info("[swarmflow] model: %s (%s)", model.ref, model.api_base)
    os.environ.setdefault("API_BASE", model.api_base)
    os.environ.setdefault("LEADER_API_KEY", model.api_key)
    os.environ.setdefault("TEAMMATE_API_KEY", model.api_key)
    os.environ.setdefault("MODEL_NAME", model.model)


# ---------------------------------------------------------------------------
# Human auto-responder — answers every pending swarmflow human turn
# ---------------------------------------------------------------------------
def _human_answer(prompt: str) -> str:
    """Pick a deterministic raw reply for a human turn from its question text.

    The avatar (human persona) renders this raw reply into the turn's schema,
    so plain natural-language answers are enough.
    """
    if "忌口" in prompt or "过敏" in prompt:
        return "没有忌口,什么都能吃"
    if "口味" in prompt or "蛋糕" in prompt:
        return "我想要巧克力口味的蛋糕"
    if "批准" in prompt or "方案" in prompt:
        return "方案很好,我同意并批准"
    return "好的,没问题"


class _SwarmflowProbe:
    """Drains workflow progress events, auto-answers humans, tracks completion."""

    def __init__(self, team_name: str, session_id: str) -> None:
        self._team_name = team_name
        self._session_id = session_id
        self.done = asyncio.Event()
        self.started = False
        self.completed = False
        self.failed = False
        self.workflow_name: str | None = None
        self.phases_seen: set[str] = set()
        self.human_prompts = 0
        self.human_replies = 0
        # agent_completed counts keyed by the agent's label — lets a caller prove
        # e.g. how many concurrent sub-workflows actually ran.
        self.agent_completed: dict[str, int] = {}
        # Text of every ``log()`` the script emitted — how a script reports its
        # own view of the run (e.g. the token budget it saw) back to a verifier.
        self.logs: list[str] = []

    async def drain(self, monitor: "object") -> None:
        """Consume ``monitor.workflow_events()`` until the run terminates."""
        async for event in monitor.workflow_events():
            payload = getattr(event, "payload", None) or {}
            kind = payload.get("kind")
            if kind == "workflow_started":
                self.started = True
                self.workflow_name = payload.get("workflow_name") or payload.get("name")
                test_logger.info("[swarmflow] started: %s", self.workflow_name)
            elif kind == "phase":
                phase = payload.get("phase")
                if phase:
                    self.phases_seen.add(phase)
                    test_logger.info("[swarmflow] phase: %s", phase)
            elif kind == "agent_completed":
                label = payload.get("label")
                if label:
                    self.agent_completed[label] = self.agent_completed.get(label, 0) + 1
            elif kind == "log":
                text = payload.get("text")
                if text:
                    self.logs.append(text)
                    test_logger.info("[swarmflow] log: %s", text)
            elif kind == "human_prompt":
                await self._answer_human(payload)
            elif kind == "human_replied":
                self.human_replies += 1
            elif kind == "workflow_completed":
                self.completed = True
                test_logger.info("[swarmflow] completed")
                self.done.set()
                return
            elif kind == "workflow_failed":
                self.failed = True
                test_logger.error("[swarmflow] failed: %s", payload.get("text"))
                self.done.set()
                return

    async def _answer_human(self, payload: dict) -> None:
        """Reply to one pending human turn over the public interact facade."""
        corr = payload.get("correlation_id")
        prompt = payload.get("prompt") or ""
        if not corr:
            test_logger.warning("[swarmflow] human_prompt without correlation_id")
            return
        self.human_prompts += 1
        answer = _human_answer(prompt)
        test_logger.info("[swarmflow] human '%s' -> %s", payload.get("label"), answer)
        result = await Runner.interact_agent_team(
            HumanAgentMessage(body=answer, sender="user", target=f"swarmflow:{corr}"),
            team_name=self._team_name,
            session_id=self._session_id,
        )
        if not result.ok:
            test_logger.error("[swarmflow] human reply deliver failed: %s", result.reason)


# ---------------------------------------------------------------------------
# Stream consumer — drives the leader, wires the probe on runtime_ready
# ---------------------------------------------------------------------------
async def _run_team(spec: TeamAgentSpec, query: str, session_id: str) -> _SwarmflowProbe:
    """Run the leader stream until the swarmflow run terminates, then stop it."""
    probe = _SwarmflowProbe(spec.team_name, session_id)
    monitor = None
    drain_task: asyncio.Task[None] | None = None
    # Monotonic timestamp of the last leader stream chunk — drives the
    # post-completion quiescence wait. A one-element list so the nested
    # ``consume`` updates it without a nonlocal declaration.
    last_chunk = [0.0]

    async def consume() -> None:
        nonlocal monitor, drain_task
        async for chunk in Runner.run_agent_team_streaming(
            agent_team=spec,
            inputs={"query": query},
            session=session_id,
        ):
            last_chunk[0] = time.monotonic()
            payload = getattr(chunk, "payload", None)
            ready = (
                isinstance(payload, dict)
                and payload.get("event_type") == "team.runtime_ready"
            )
            if ready and monitor is None:
                monitor = await Runner.get_agent_team_monitor(
                    team_name=spec.team_name,
                    session_id=session_id,
                )
                if monitor is not None:
                    await monitor.start()
                    drain_task = asyncio.create_task(probe.drain(monitor))
                    nonlocal_watchdog()

    watchdog_task: asyncio.Task[None] | None = None

    def nonlocal_watchdog() -> None:
        """Fail fast if the leader never launches the workflow (e.g. bad path)."""
        nonlocal watchdog_task

        async def _watch() -> None:
            await asyncio.sleep(_START_DEADLINE_S)
            if not probe.started and not probe.done.is_set():
                test_logger.error(
                    "[swarmflow] no workflow_started within %ss — the leader likely "
                    "failed to launch swarmflow (bad script_path?)",
                    _START_DEADLINE_S,
                )
                probe.done.set()

        watchdog_task = asyncio.create_task(_watch())

    consume_task = asyncio.create_task(consume())
    done_task = asyncio.create_task(probe.done.wait())
    try:
        # Finish as soon as the workflow terminates OR the leader stream task
        # dies — never sit on the timeout because an exception was swallowed.
        await asyncio.wait(
            {done_task, consume_task},
            timeout=_RUN_TIMEOUT_S,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if consume_task.done() and not probe.done.is_set():
            exc = consume_task.exception()
            if exc is not None:
                test_logger.error("[swarmflow] leader stream crashed: %r", exc)
            else:
                test_logger.error("[swarmflow] leader stream ended before the workflow finished")
        elif not probe.done.is_set():
            test_logger.error("[swarmflow] timed out waiting for workflow completion")
        elif probe.completed and not consume_task.done():
            # The workflow finished; now let the leader receive the async-tool
            # result injection and narrate it before we stop the runtime. Treat
            # completion as fresh activity, then wait for the leader stream to go
            # quiet — this both rides out the narration round and gives the
            # injection time to land, so stop() never races a pending send.
            last_chunk[0] = time.monotonic()
            deadline = time.monotonic() + _NARRATION_MAX_S
            while time.monotonic() < deadline and not consume_task.done():
                if time.monotonic() - last_chunk[0] >= _NARRATION_QUIESCE_S:
                    break
                await asyncio.sleep(0.5)
            test_logger.info("[swarmflow] leader narration settled; tearing down")
    finally:
        done_task.cancel()
        if watchdog_task is not None:
            watchdog_task.cancel()
            try:
                await watchdog_task
            except asyncio.CancelledError:
                pass
        if monitor is not None:
            await monitor.stop()
        if drain_task is not None:
            drain_task.cancel()
            try:
                await drain_task
            except asyncio.CancelledError:
                pass
        if not consume_task.done():
            consume_task.cancel()
            try:
                await consume_task
            except asyncio.CancelledError:
                pass
    return probe


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------
_EXPECTED_PHASES = {"构思", "征询嘉宾", "拟菜单", "筹备", "审批", "邀请函"}


def _verify(probe: _SwarmflowProbe) -> None:
    """Assert the full swarmflow surface was exercised end-to-end."""
    assert probe.started, "workflow never emitted workflow_started"
    assert probe.workflow_name == "party-planner", (
        f"unexpected workflow name: {probe.workflow_name!r}"
    )
    missing = _EXPECTED_PHASES - probe.phases_seen
    assert not missing, f"phases missing from the run: {sorted(missing)}"
    assert probe.human_prompts > 0, "no human turns were prompted"
    assert probe.human_replies > 0, "no human turns were answered"
    assert probe.completed, "workflow did not complete (failed or timed out)"
    assert not probe.failed, "workflow reported a failure"
    test_logger.info(
        "[swarmflow] verified: phases=%d human_prompts=%d human_replies=%d",
        len(probe.phases_seen),
        probe.human_prompts,
        probe.human_replies,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def main() -> int:
    _wire_model_env()
    # Run from a dedicated gitignored scratch dir so the team's runtime
    # scaffolding (identity / memory / skills / logs written to cwd) stays out of
    # the test tree, and the leader only echoes a short relative script path.
    _WORKDIR.mkdir(parents=True, exist_ok=True)
    os.chdir(_WORKDIR)
    cfg = load_team_config(_TEAM_CONFIG_PATH)
    cfg.pop("runtime", {})
    # A unique session id every run: swarmflow resumes from a journal keyed by
    # (team, session_id, workflow name), so reusing one id would silently replay
    # the prior run's cached stages and could mask a real regression. A fresh id
    # forces a fully live end-to-end execution each time.
    session_id = f"swarmflow_e2e_{uuid.uuid4().hex[:8]}"

    spec = TeamAgentSpec.model_validate(cfg)

    query = (
        "请立即调用 swarmflow 工具来运行一个多 agent 工作流。"
        f"参数:script_path 必须填 \"{_SCRIPT_REL}\",args 填 \"小明的生日\"。"
        "只需调用这一个工具,然后等待工作流结果即可,不要自己拆解或执行其它步骤。"
    )

    await Runner.start()
    test_logger.info("=" * 60)
    test_logger.info("Swarmflow E2E — running %s", _SCRIPT_PATH)
    test_logger.info("=" * 60)

    probe = await _run_team(spec, query, session_id)

    # Report where the run's artifacts live (the journal is already flushed).
    journal = paths.workflow_journal_path(spec.team_name, session_id, "party-planner")
    test_logger.info("[swarmflow] journal:  %s", journal)
    test_logger.info("[swarmflow] workdir:  %s", _WORKDIR)
    test_logger.info("[swarmflow] home:     %s", _HERE / "openjiuwen_home")

    if _TEARDOWN:
        await Runner.stop()
    else:
        test_logger.info(
            "[swarmflow] teardown skipped (SWARMFLOW_E2E_TEARDOWN=0): runtime left up, "
            "intermediate files kept for inspection"
        )

    try:
        _verify(probe)
    except AssertionError as e:
        test_logger.error("[swarmflow] VERIFICATION FAILED: %s", e)
        return 1
    test_logger.info("[swarmflow] E2E PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
