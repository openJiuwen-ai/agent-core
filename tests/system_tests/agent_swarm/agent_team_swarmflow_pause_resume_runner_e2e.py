# coding: utf-8
"""Swarmflow pause/resume E2E — pause/resume at EVERY feature point.

Mirrors ``agent_team_swarmflow_e2e.py`` (same ``party_planner`` script covering
every primitive — stateless / stateful agent, stateless / stateful human,
pipeline, parallel, nested workflow) but proves the external pause/resume control
path is correct **at every phase boundary**, not just once:

* a ``BackgroundTaskController`` is passed into
  ``Runner.run_agent_team_streaming(background_task_controller=...)``;
* the probe drains workflow progress and, the first time a primitive becomes
  active in a phase it has not paused at yet (an ``agent_started`` — emitted for
  a stateless agent, a session turn, AND a human turn's avatar, before the human
  even waits), signals an orchestrator to ``ctl.pause()`` — interrupting whatever
  is in flight in that phase (an LLM call, or a live ``human_session`` whose
  pending-reply future ``abort_all`` cancels) — then ``ctl.resume()`` relaunches
  and the journal replays the completed prefix;
* this repeats once per phase: 构思 → 征询嘉宾 → 拟菜单 → 筹备 → 审批 → 邀请函,
  so every primitive type is interrupted mid-flight and resumed, and the run
  still drives to a full, correct completion.

Humans are ALWAYS answered as soon as their prompt is seen (over the public
inbound path a UI uses) — never left waiting — so a missed pause can never hang
the run on a human timeout. Pausing at the two human phases (征询嘉宾 / 审批)
still runs ``abort_all`` over the live human sessions, exercising their
interruption; the deterministic proof of that lives in
``test_avatar_session_backend.py::test_abort_all_...``.

Run directly (needs a real model endpoint, see config_llm_local.yaml):
    python tests/system_tests/agent_swarm/agent_team_swarmflow_pause_resume_runner_e2e.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
import uuid
from pathlib import Path


_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent))

from openjiuwen.agent_teams import paths
from openjiuwen.agent_teams.interaction.payload import HumanAgentMessage
from openjiuwen.agent_teams.paths import configure_openjiuwen_home
from openjiuwen.agent_teams.runtime.background_task_controller import BackgroundTaskController
from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
from openjiuwen.core.common.logging.log_config import configure_log, configure_log_config
from openjiuwen.core.common.logging.loguru.constant import DEFAULT_INNER_LOG_CONFIG
from openjiuwen.core.runner.runner import Runner

from _e2e_utils import load_team_config
from llm_config import load_llm_config
from tests.test_logger import logger as test_logger

_LOG_CONFIG_PATH = _HERE / "logging.yaml"
_TEAM_CONFIG_PATH = _HERE / "config_swarmflow.yaml"
_SCRIPT_PATH = _HERE / "resources" / "party_planner.py"
_WORKDIR = _HERE / ".e2e_workdir_pause"
_SCRIPT_REL = "../resources/party_planner.py"

# The six phases party_planner runs, each with a distinct primitive type. The run
# pauses/resumes exactly once per phase, so by completion every feature point has
# been interrupted mid-flight and recovered. 征询嘉宾 / 审批 are the human phases.
_EXPECTED_PHASES = {"构思", "征询嘉宾", "拟菜单", "筹备", "审批", "邀请函"}
_HUMAN_PHASES = {"征询嘉宾", "审批"}
# Hold each pause briefly before resuming (let the cancel land + the leader idle).
_PAUSE_HOLD_S = 4.0

_START_DEADLINE_S = 180.0
_RUN_TIMEOUT_S = 900.0
_NARRATION_QUIESCE_S = 6.0
_NARRATION_MAX_S = 90.0
_TEARDOWN = os.getenv("SWARMFLOW_E2E_TEARDOWN", "1").strip().lower() not in ("0", "false", "no")

if _LOG_CONFIG_PATH.is_file():
    configure_log(str(_LOG_CONFIG_PATH))
else:
    configure_log_config(DEFAULT_INNER_LOG_CONFIG)

os.environ.setdefault("LLM_SSL_VERIFY", "false")
os.environ.setdefault("IS_SENSITIVE", "false")
configure_openjiuwen_home(str(_HERE / "openjiuwen_home"))


def _wire_model_env() -> None:
    """Populate API_BASE / *_API_KEY / MODEL_NAME from the local LLM config.

    Endpoint and model are resolved together from one ref, so they cannot
    disagree — this used to pin a model name of its own while taking the
    endpoint from the config, which fails outright once the two point at
    different vendors. Override with ``OPENJIUWEN_E2E_MODEL=<endpoint>/<model>``.
    """
    model = load_llm_config().resolve()
    test_logger.info("[swarmflow] model: %s (%s)", model.ref, model.api_base)
    os.environ.setdefault("API_BASE", model.api_base)
    os.environ.setdefault("LEADER_API_KEY", model.api_key)
    os.environ.setdefault("TEAMMATE_API_KEY", model.api_key)
    os.environ.setdefault("MODEL_NAME", model.model)


def _human_answer(prompt: str) -> str:
    """Deterministic raw reply for a human turn, picked from its question text."""
    if "忌口" in prompt or "过敏" in prompt:
        return "没有忌口,什么都能吃"
    if "口味" in prompt or "蛋糕" in prompt:
        return "我想要巧克力口味的蛋糕"
    if "批准" in prompt or "方案" in prompt:
        return "方案很好,我同意并批准"
    return "好的,没问题"


class _PauseEveryPhaseProbe:
    """Drains progress; pauses once per phase; always answers humans."""

    def __init__(self, team_name: str, session_id: str) -> None:
        self._team_name = team_name
        self._session_id = session_id
        self.done = asyncio.Event()
        self.pause_requested = asyncio.Event()
        self.started = False
        self.completed = False
        self.failed = False
        self.pausing = False  # True between a pause trigger and its resume
        self.current_phase: str | None = None
        self.paused_phases: set[str] = set()  # phases paused in (once each)
        self.pauses = 0
        self.resumes = 0
        self.restarts = 0  # workflow_started count
        self.human_replies = 0
        self.workflow_name: str | None = None
        self.phases_seen: set[str] = set()

    def _maybe_trigger_pause(self) -> None:
        """Trigger a pause if the current phase has not been paused at yet."""
        p = self.current_phase
        if p and p not in self.paused_phases and not self.pausing:
            self.paused_phases.add(p)
            self.pausing = True
            self.pause_requested.set()
            test_logger.info("[swarmflow] >>> pause requested in phase %s", p)

    async def drain(self, monitor: "object") -> None:
        """Consume ``monitor.workflow_events()`` until the run terminates."""
        async for event in monitor.workflow_events():
            payload = getattr(event, "payload", None) or {}
            kind = payload.get("kind")
            if kind == "workflow_started":
                self.started = True
                self.restarts += 1
                self.workflow_name = payload.get("workflow_name") or payload.get("name")
            elif kind == "phase":
                phase = payload.get("phase")
                if phase:
                    self.current_phase = phase
                    self.phases_seen.add(phase)
            elif kind == "agent_started":
                # A primitive (agent / session turn / human avatar) is now in
                # flight in this phase — pause here to interrupt it, once per phase.
                self._maybe_trigger_pause()
            elif kind == "human_prompt":
                # ALWAYS answer (never leave a human waiting → no timeout hang). A
                # concurrent pause may abort this turn; resume re-prompts the same
                # correlation_id and this answers it again.
                await self._answer_human(payload)
            elif kind == "human_replied":
                self.human_replies += 1
            elif kind == "workflow_completed":
                self.completed = True
                self.done.set()
                return
            elif kind == "workflow_failed":
                self.failed = True
                test_logger.error("[swarmflow] failed: %s", payload.get("text"))
                self.done.set()
                return

    async def _answer_human(self, payload: dict) -> None:
        corr = payload.get("correlation_id")
        prompt = payload.get("prompt") or ""
        if not corr:
            return
        answer = _human_answer(prompt)
        test_logger.info("[swarmflow] human '%s' -> %s", payload.get("label"), answer)
        result = await Runner.interact_agent_team(
            HumanAgentMessage(body=answer, sender="user", target=f"swarmflow:{corr}"),
            team_name=self._team_name,
            session_id=self._session_id,
        )
        if not result.ok:
            # A reply to an aborted (paused) turn is rejected — harmless; the
            # resumed turn re-prompts the same corr and we answer it then.
            test_logger.info("[swarmflow] human reply not delivered (likely paused): %s", result.reason)


async def _run_team(spec: TeamAgentSpec, query: str, session_id: str) -> _PauseEveryPhaseProbe:
    """Run the leader stream; pause/resume once per phase until completion."""
    probe = _PauseEveryPhaseProbe(spec.team_name, session_id)
    ctl = BackgroundTaskController()
    monitor = None
    drain_task: asyncio.Task[None] | None = None
    watchdog_task: asyncio.Task[None] | None = None
    last_chunk = [0.0]

    async def consume() -> None:
        nonlocal monitor, drain_task, watchdog_task
        async for chunk in Runner.run_agent_team_streaming(
            agent_team=spec,
            inputs={"query": query},
            session=session_id,
            background_task_controller=ctl,
        ):
            last_chunk[0] = time.monotonic()
            payload = getattr(chunk, "payload", None)
            ready = isinstance(payload, dict) and payload.get("event_type") == "team.runtime_ready"
            if ready and monitor is None:
                monitor = await Runner.get_agent_team_monitor(team_name=spec.team_name, session_id=session_id)
                if monitor is not None:
                    await monitor.start()
                    drain_task = asyncio.create_task(probe.drain(monitor))
                    watchdog_task = asyncio.create_task(_watchdog())

    async def _watchdog() -> None:
        """Fail fast if the leader never launches the workflow (e.g. bad path)."""
        await asyncio.sleep(_START_DEADLINE_S)
        if not probe.started and not probe.done.is_set():
            test_logger.error("[swarmflow] no workflow_started within %ss — leader failed to launch", _START_DEADLINE_S)
            probe.done.set()

    async def orchestrate() -> None:
        """One pause/resume cycle per phase, until the run completes."""
        while True:
            pr = asyncio.create_task(probe.pause_requested.wait())
            dn = asyncio.create_task(probe.done.wait())
            await asyncio.wait({pr, dn}, return_when=asyncio.FIRST_COMPLETED)
            if probe.done.is_set():
                pr.cancel()
                return
            probe.pause_requested.clear()
            ok = await ctl.pause()
            if ok:
                probe.pauses += 1
            test_logger.info("[swarmflow] paused (#%d) in phase %s; holding %ss",
                             probe.pauses, probe.current_phase, _PAUSE_HOLD_S)
            await asyncio.sleep(_PAUSE_HOLD_S)
            # Reset BEFORE relaunch so the resumed run's (new-phase) events can
            # trigger the next pause without racing this flag.
            probe.pausing = False
            ok = await ctl.resume()
            if ok:
                probe.resumes += 1
            test_logger.info("[swarmflow] resumed (#%d)", probe.resumes)

    consume_task = asyncio.create_task(consume())
    orch_task = asyncio.create_task(orchestrate())
    done_task = asyncio.create_task(probe.done.wait())
    try:
        await asyncio.wait({done_task, consume_task}, timeout=_RUN_TIMEOUT_S, return_when=asyncio.FIRST_COMPLETED)
        if consume_task.done() and not probe.done.is_set():
            exc = consume_task.exception()
            if exc is not None:
                test_logger.error("[swarmflow] leader stream crashed: %r", exc)
            else:
                test_logger.error("[swarmflow] leader stream ended before the workflow finished")
        elif not probe.done.is_set():
            test_logger.error("[swarmflow] timed out waiting for completion")
        elif probe.completed and not consume_task.done():
            last_chunk[0] = time.monotonic()
            deadline = time.monotonic() + _NARRATION_MAX_S
            while time.monotonic() < deadline and not consume_task.done():
                if time.monotonic() - last_chunk[0] >= _NARRATION_QUIESCE_S:
                    break
                await asyncio.sleep(0.5)
    finally:
        for task in (done_task, orch_task, watchdog_task, drain_task, consume_task):
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        if monitor is not None:
            await monitor.stop()
    return probe


def _verify(probe: _PauseEveryPhaseProbe) -> None:
    """Assert every phase was paused+resumed and the run still completed."""
    assert probe.started, "workflow never emitted workflow_started"
    assert probe.workflow_name == "party-planner", f"unexpected workflow name: {probe.workflow_name!r}"
    # Paused at EVERY feature point (one pause/resume per phase).
    missing = _EXPECTED_PHASES - probe.paused_phases
    assert not missing, f"phases never paused at: {sorted(missing)}"
    assert probe.pauses == len(probe.paused_phases), (
        f"expected one pause per paused phase: pauses={probe.pauses} phases={len(probe.paused_phases)}"
    )
    assert probe.resumes == probe.pauses, f"every pause must resume: pauses={probe.pauses} resumes={probe.resumes}"
    assert probe.restarts >= probe.pauses + 1, f"each resume must relaunch (restarts={probe.restarts})"
    # The two human phases were paused → abort_all ran over live human sessions.
    assert _HUMAN_PHASES <= probe.paused_phases, "human phases were not paused (human-session interruption untested)"
    assert probe.human_replies > 0, "humans were never answered"
    # Despite pausing at every point, the full surface completed cleanly.
    missing_phases = _EXPECTED_PHASES - probe.phases_seen
    assert not missing_phases, f"phases missing from the run: {sorted(missing_phases)}"
    assert probe.completed, "workflow did not complete"
    assert not probe.failed, "workflow reported a failure"
    test_logger.info(
        "[swarmflow] verified: pauses=%d resumes=%d phases=%d human_replies=%d",
        probe.pauses, probe.resumes, len(probe.paused_phases), probe.human_replies,
    )


async def main() -> int:
    _wire_model_env()
    _WORKDIR.mkdir(parents=True, exist_ok=True)
    os.chdir(_WORKDIR)
    cfg = load_team_config(_TEAM_CONFIG_PATH)
    cfg.pop("runtime", {})
    session_id = f"swarmflow_pr_e2e_{uuid.uuid4().hex[:8]}"
    spec = TeamAgentSpec.model_validate(cfg)

    query = (
        "请立即调用 swarmflow 工具来运行一个多 agent 工作流。"
        f"参数:script_path 必须填 \"{_SCRIPT_REL}\",args 填 \"小明的生日\"。"
        "只需调用这一个工具,然后等待工作流结果即可,不要自己拆解或执行其它步骤。"
    )

    await Runner.start()
    test_logger.info("=" * 60)
    test_logger.info("Swarmflow pause/resume-every-phase E2E — %s", _SCRIPT_PATH)
    test_logger.info("=" * 60)

    probe = await _run_team(spec, query, session_id)

    journal = paths.workflow_journal_path(spec.team_name, session_id, "party-planner")
    test_logger.info("[swarmflow] journal: %s", journal)

    if _TEARDOWN:
        await Runner.stop()

    try:
        _verify(probe)
    except AssertionError as e:
        test_logger.error("[swarmflow] VERIFICATION FAILED: %s", e)
        return 1
    test_logger.info("[swarmflow] PAUSE/RESUME-EVERY-PHASE E2E PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
