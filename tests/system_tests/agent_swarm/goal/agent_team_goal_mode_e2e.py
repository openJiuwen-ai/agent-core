# coding: utf-8
"""Goal mode E2E — a real model driven through the public team entry point.

Every scenario builds a small team with ``leader.goal`` and ``member_goal`` on,
hands the leader one request through ``Runner.run_agent_team_streaming`` and
then checks what goal mode actually did. What this proves that the unit tests
cannot:

* a member whose round is cut short is **driven on** by its goal until the task
  is handed in;
* a hand-in **without evidence is refused** by a real assessor reading the real
  conversation, and accepted once the work is there;
* the goal follows the task through every way it can start or change hands —
  claim, scheduler start, plan approval, review send-back, reassignment;
* a member that runs out of attempts **escalates** to the leader;
* the leader **checks the board against the request** before the team is
  declared complete.

Self-verifying: each scenario prints its checks and the script exits non-zero
when any required check fails. The deliverables are re-verified here, not taken
on the agents' word.

The model is not deterministic. A scenario that depends on the model making a
particular move (see ``_Scenario.depends_on``) can miss it; read the scenario's
``trace.log`` before treating a miss as a regression.

Run directly (needs a real model endpoint, see ../../config_llm_local.example.yaml):
    python tests/system_tests/agent_swarm/goal/agent_team_goal_mode_e2e.py
    python tests/system_tests/agent_swarm/goal/agent_team_goal_mode_e2e.py --scenario lazy_autonomous --scenario blocked
    python tests/system_tests/agent_swarm/goal/agent_team_goal_mode_e2e.py --list
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))  # _e2e_utils
sys.path.insert(0, str(_HERE.parent.parent))  # llm_config

from openjiuwen.agent_teams.agent.goal_coordinator import TeamGoalCoordinator  # noqa: E402
from openjiuwen.agent_teams.paths import configure_openjiuwen_home  # noqa: E402
from openjiuwen.agent_teams.rails.team_goal_rail import TeamGoalRail  # noqa: E402
from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec  # noqa: E402
from openjiuwen.core.common.logging.log_config import configure_log_config  # noqa: E402
from openjiuwen.core.common.logging.loguru.constant import DEFAULT_INNER_LOG_CONFIG  # noqa: E402
from openjiuwen.core.runner.runner import Runner  # noqa: E402
from openjiuwen.harness.goal.manager import GoalManager  # noqa: E402

from _e2e_utils import configure_logging_into  # noqa: E402
from llm_config import load_llm_config  # noqa: E402
from tests.test_logger import logger as test_logger  # noqa: E402

_LOG_CONFIG_PATH = _HERE.parent / "logging.yaml"
# Scratch root (gitignored): one sub-directory per scenario holds its workspace,
# team DB, openjiuwen home, logs and trace.
_WORKDIR = _HERE / ".e2e_workdir"
_RUN_TIMEOUT_S = 900.0
_DEFAULT_JOBS = 4
# A member goal's objective names its task as ``#<task_id>``; the leader's does not.
_TASK_ID_RE = re.compile(r"#([\w.\-]+)")
# How the leader's objective opens, in either prompt language (``goal.leader_objective``).
_LEADER_OBJECTIVE_PREFIXES = ("用户的请求", "The user's request")

os.environ.setdefault("LLM_SSL_VERIFY", "false")
os.environ.setdefault("IS_SENSITIVE", "false")


# ---------------------------------------------------------------------------
# Probe: what goal mode did, read off the goal manager and the goal rail
# ---------------------------------------------------------------------------
class _GoalProbe:
    """Record every goal-record change and every refused hand-in of one run.

    Goal state lives in each member's own session, which the public team
    facade does not expose, so the probe wraps the few methods every change
    goes through. It observes only; each wrapper returns what the original did.
    """

    def __init__(self, trace_path: Path) -> None:
        self.events: list[dict[str, Any]] = []
        self._trace_path = trace_path
        self._started = time.monotonic()
        self._originals: dict[tuple[type, str], Any] = {}

    def record(self, kind: str, **fields: Any) -> None:
        """Append one event and mirror it to the scenario's trace file."""
        event = {"kind": kind, "t": round(time.monotonic() - self._started, 1), **fields}
        self.events.append(event)
        with self._trace_path.open("a", encoding="utf-8") as trace:
            trace.write(json.dumps(event, ensure_ascii=False) + "\n")
        test_logger.info("[goal-e2e] %s", json.dumps(event, ensure_ascii=False)[:300])

    def install(self) -> None:
        """Wrap the goal manager, the goal rail and the goal coordinator."""
        probe = self
        original_set = GoalManager.set
        original_begin = GoalManager.begin_attempt
        original_apply = GoalManager.apply_assessment
        original_clear = GoalManager.clear
        original_reject = TeamGoalRail._reject
        original_changed = TeamGoalCoordinator._on_goal_changed

        async def traced_set(manager: GoalManager, objective: str, **kwargs: Any) -> Any:
            record = await original_set(manager, objective, **kwargs)
            is_leader_goal = objective.startswith(_LEADER_OBJECTIVE_PREFIXES)
            match = None if is_leader_goal else _TASK_ID_RE.search(objective.split("\n", 1)[0])
            probe.record("set", goal_id=record.goal_id, task_id=match.group(1) if match else None)
            return record

        async def traced_begin(manager: GoalManager, *, goal_id: str, revision: int) -> Any:
            record = await original_begin(manager, goal_id=goal_id, revision=revision)
            if record is not None:
                probe.record("attempt", goal_id=goal_id, n=record.attempt_count)
            return record

        async def traced_apply(manager: GoalManager, *, goal_id: str, revision: int, assessment: Any) -> Any:
            record = await original_apply(manager, goal_id=goal_id, revision=revision, assessment=assessment)
            probe.record(
                "assess",
                goal_id=goal_id,
                verdict=assessment.status.value,
                status=record.status.value if record is not None else "stale",
                evidence=str(assessment.evidence)[:200],
            )
            return record

        async def traced_clear(manager: GoalManager) -> Any:
            record = await original_clear(manager)
            if record is not None:
                probe.record("clear", goal_id=record.goal_id)
            return record

        def traced_reject(ctx: Any, task_id: str, assessment: Any) -> None:
            probe.record("reject", task_id=task_id, remaining=str(assessment.remaining_work)[:200])
            original_reject(ctx, task_id, assessment)

        async def traced_changed(coordinator: TeamGoalCoordinator, goal: dict | None) -> None:
            if goal and goal.get("status") == "blocked":
                probe.record("blocked", member=coordinator._member_name, role=coordinator._role.value)
            await original_changed(coordinator, goal)

        GoalManager.set = traced_set
        GoalManager.begin_attempt = traced_begin
        GoalManager.apply_assessment = traced_apply
        GoalManager.clear = traced_clear
        TeamGoalRail._reject = staticmethod(traced_reject)
        TeamGoalCoordinator._on_goal_changed = traced_changed

    # -- views over the recorded events ------------------------------------

    def of(self, kind: str) -> list[dict[str, Any]]:
        """Return the events of one kind, in order."""
        return [event for event in self.events if event["kind"] == kind]

    def task_goals(self) -> dict[str, str]:
        """Map each member goal id to the task it stands for."""
        return {event["goal_id"]: event["task_id"] for event in self.of("set") if event["task_id"]}

    def leader_goal_ids(self) -> set[str]:
        """Return the ids of the goals opened for the user's request."""
        return {event["goal_id"] for event in self.of("set") if not event["task_id"]}

    def completed_goal_ids(self) -> set[str]:
        """Return the ids of the goals that ended completed."""
        return {event["goal_id"] for event in self.of("assess") if event["status"] == "completed"}

    def completed_tasks(self) -> set[str]:
        """Return the task ids whose goal was closed by an accepted hand-in."""
        goals = self.task_goals()
        return {goals[goal_id] for goal_id in self.completed_goal_ids() if goal_id in goals}


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------
@dataclass
class _Check:
    """One verified statement about a finished run."""

    name: str
    ok: bool
    detail: str = ""


@dataclass
class _Run:
    """Everything a scenario's checks may look at."""

    probe: _GoalProbe
    workspace: Path
    timed_out: bool


_LAZY_MEMBER = (
    "你是一个图省事的成员：拿到任务后不要做任何实际工作，第一步就直接提交任务完成。"
    "只有当提交被明确拒绝并告诉你还缺什么时，才去把缺的工作做完再提交。"
)


@dataclass
class _Scenario:
    """One request to a goal-mode team plus what must be true afterwards."""

    name: str
    summary: str
    query: Callable[[Path], str]
    checks: Callable[[_Run], list[_Check]]
    dispatch: str = "autonomous"
    teammate_mode: str = "build_mode"
    verification: bool = False
    member_prompt: str = ""
    member_iterations: int | None = None
    member_max_attempts: int | None = None
    # What the scenario needs the model to do of its own accord. Empty means the
    # checks hold whatever route the model takes.
    depends_on: str = ""
    spec_overrides: dict[str, Any] = field(default_factory=dict)


def _runs_clean(workspace: Path, script: str) -> _Check:
    """Run a delivered Python file and report whether it exits zero."""
    path = workspace / script
    if not path.is_file():
        return _Check(f"{script} delivered and runs", False, "file missing")
    result = subprocess.run(
        [sys.executable, script],
        cwd=workspace,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    detail = (result.stdout or result.stderr).strip()[-120:]
    return _Check(f"{script} delivered and runs", result.returncode == 0, detail)


def _file_is(workspace: Path, name: str, expected: str) -> _Check:
    """Report whether a delivered file holds exactly the expected text."""
    path = workspace / name
    actual = path.read_text(encoding="utf-8").strip() if path.is_file() else None
    return _Check(f"{name} == {expected!r}", actual == expected, f"got {actual!r}")


def _common(run: _Run) -> list[_Check]:
    """Checks every scenario shares: the leader goal gates team completion."""
    probe = run.probe
    leader_goals = probe.leader_goal_ids()
    completed = probe.completed_goal_ids()
    markers = [event["event"] for event in probe.of("marker")]
    leader_done = [
        event["t"]
        for event in probe.of("assess")
        if event["goal_id"] in leader_goals and event["status"] == "completed"
    ]
    team_done = [event["t"] for event in probe.of("marker") if event["event"] == "team.completed"]
    leader_attempts = [event for event in probe.of("attempt") if event["goal_id"] in leader_goals]
    return [
        _Check("run finished within the time limit", not run.timed_out),
        _Check("the user's request became the leader's goal", len(leader_goals) == 1, f"{len(leader_goals)} goal(s)"),
        _Check("the leader took a goal round to check the work", bool(leader_attempts), f"{len(leader_attempts)} round(s)"),
        _Check("the leader's goal was assessed as met", bool(leader_goals & completed)),
        _Check("team.completed was emitted", "team.completed" in markers),
        _Check(
            "team.completed came only after the leader's goal was met",
            bool(leader_done) and bool(team_done) and leader_done[0] <= team_done[0],
            f"goal met at {leader_done[:1]}, team completed at {team_done[:1]}",
        ),
    ]


def _task_goal_checks(run: _Run, minimum: int = 1) -> list[_Check]:
    """Checks that members' tasks got goals and were closed by a hand-in."""
    probe = run.probe
    opened = set(probe.task_goals().values())
    done = probe.completed_tasks()
    return [
        _Check(f"at least {minimum} task became a member's goal", len(opened) >= minimum, f"tasks: {sorted(opened)}"),
        _Check(f"at least {minimum} task goal closed on an accepted hand-in", len(done) >= minimum, f"tasks: {sorted(done)}"),
    ]


def _refusal_check(run: _Run) -> _Check:
    """Check that a hand-in was refused before one was accepted."""
    rejects = run.probe.of("reject")
    return _Check("a hand-in without evidence was refused", bool(rejects), f"{len(rejects)} refusal(s)")


def _checks_basic(run: _Run) -> list[_Check]:
    return [*_common(run), *_task_goal_checks(run), _runs_clean(run.workspace, "test_fib.py")]


def _checks_lazy(run: _Run) -> list[_Check]:
    return [*_common(run), *_task_goal_checks(run), _refusal_check(run), _runs_clean(run.workspace, "test_fib.py")]


def _checks_multi(run: _Run) -> list[_Check]:
    return [*_common(run), *_task_goal_checks(run, minimum=3), _runs_clean(run.workspace, "test_calc.py")]


def _checks_review(run: _Run) -> list[_Check]:
    opened = [event["task_id"] for event in run.probe.of("set") if event["task_id"]]
    reopened = sorted({task_id for task_id in opened if opened.count(task_id) >= 2})
    return [
        *_common(run),
        *_task_goal_checks(run),
        _Check("a task sent back by its reviewer got a fresh goal", bool(reopened), f"reopened: {reopened}"),
        _runs_clean(run.workspace, "test_sq.py"),
    ]


def _checks_plan(run: _Run) -> list[_Check]:
    return [*_common(run), *_task_goal_checks(run), _runs_clean(run.workspace, "hello.py")]


def _checks_iteration_limit(run: _Run) -> list[_Check]:
    task_goals = run.probe.task_goals()
    attempts = [event for event in run.probe.of("attempt") if event["goal_id"] in task_goals]
    return [
        *_common(run),
        *_task_goal_checks(run),
        _Check("the member's goal drove at least one continuation round", bool(attempts), f"{len(attempts)} round(s)"),
        _file_is(run.workspace, "sum.txt", "91"),
    ]


def _checks_reassign(run: _Run) -> list[_Check]:
    task_goals = run.probe.task_goals()
    cleared = [event for event in run.probe.of("clear") if event["goal_id"] in task_goals]
    return [
        *_common(run),
        *_task_goal_checks(run),
        _Check("the goal of the member that lost the task was dropped", bool(cleared), f"{len(cleared)} dropped"),
        _runs_clean(run.workspace, "greet.py"),
    ]


def _checks_blocked(run: _Run) -> list[_Check]:
    blocked = [event for event in run.probe.of("blocked") if event["role"] != "leader"]
    chain_ok = True
    detail = ""
    for step in range(1, 9):
        previous = run.workspace / f"c{step - 1}.txt"
        current = run.workspace / f"c{step}.txt"
        if not previous.is_file() or not current.is_file():
            chain_ok, detail = False, f"c{step - 1}.txt or c{step}.txt missing"
            break
        expected = hashlib.sha256(previous.read_bytes()).hexdigest()
        if current.read_text(encoding="utf-8").strip() != expected:
            chain_ok, detail = False, f"c{step}.txt is not sha256(c{step - 1}.txt)"
            break
    return [
        *_common(run),
        _Check("a task became a member's goal", bool(run.probe.task_goals())),
        _Check("the member's goal blocked at its attempt limit", bool(blocked), f"{len(blocked)} blocked event(s)"),
        _Check("the hash chain c0..c8 is correct", chain_ok, detail),
    ]


def _fib_query(workspace: Path) -> str:
    return (
        f"在 {workspace} 目录下交付一个小工具：fib.py 提供 fibonacci(n) 函数，test_fib.py 用 assert 覆盖 n=0,1,2,10，"
        "并且必须实际运行 python test_fib.py 确认通过。请组建只有 1 名成员的团队，建 1 个任务交给他完成。"
    )


_SCENARIOS: list[_Scenario] = [
    _Scenario(
        name="basic",
        summary="autonomous, one member: claim opens the goal, hand-in is assessed, leader checks before completion",
        query=_fib_query,
        checks=_checks_basic,
    ),
    _Scenario(
        name="lazy_autonomous",
        summary="autonomous, a member told to hand in without working: the hand-in is refused first",
        query=_fib_query,
        checks=_checks_lazy,
        member_prompt=_LAZY_MEMBER,
        depends_on="the member following its instruction to hand in before doing the work",
    ),
    _Scenario(
        name="lazy_scheduled",
        summary="scheduled dispatch, lazy member: a task started without a claim is still assessed at hand-in",
        query=_fib_query,
        checks=_checks_lazy,
        dispatch="scheduled",
        member_prompt=_LAZY_MEMBER,
        depends_on="the member following its instruction to hand in before doing the work",
    ),
    _Scenario(
        name="multi",
        summary="autonomous, three members with dependent tasks: each task gets and closes its own goal",
        query=lambda workspace: (
            f"在 {workspace} 目录下交付一个小库：calc.py 提供 add/sub/mul 三个函数；test_calc.py 用 assert 测这三个函数并实际运行通过；"
            "README.md 说明用法并贴出一次真实运行测试的输出。组建 3 名成员的团队，拆成 3 个任务（实现、测试、文档），"
            "测试依赖实现，文档依赖测试，每人一个。"
        ),
        checks=_checks_multi,
    ),
    _Scenario(
        name="review_send_back",
        summary="scheduled dispatch with a reviewer: a failed review gives the task a fresh goal",
        query=lambda workspace: (
            f"在 {workspace} 目录下交付 sq.py：提供 square(n) 函数，并附 test_sq.py 用 assert 实际运行通过。"
            "组建 2 名成员的团队：一名开发、一名验证者；建 1 个任务给开发，并把验证者设为该任务的 reviewer。"
            "请验证者第一次验证时必须打回，要求在 sq.py 里补上函数 docstring，开发补完后再次提交，第二次再通过。"
        ),
        checks=_checks_review,
        dispatch="scheduled",
        verification=True,
        depends_on="the reviewer failing the first review as asked",
    ),
    _Scenario(
        name="plan_mode",
        summary="plan mode: the task starts on plan approval and still gets its goal",
        query=lambda workspace: (
            f"在 {workspace} 目录下交付 hello.py：运行后打印 hello team，并且必须实际运行 python hello.py 确认输出。"
            "组建只有 1 名成员的团队，建 1 个任务但不要指派给任何人，让成员自己提交计划；成员提交计划后你直接批准。"
        ),
        checks=_checks_plan,
        teammate_mode="plan_mode",
    ),
    _Scenario(
        name="iteration_limit",
        summary="a member cut short every round: only goal continuation carries the task to the end",
        query=lambda workspace: (
            f"在 {workspace} 目录下交付：n1.txt 到 n6.txt 共 6 个文件，第 k 个文件内容是数字 k 的平方；再生成 sum.txt，"
            "内容是这 6 个平方数之和（91）；最后必须用命令实际查看 sum.txt 确认内容。每个文件必须单独用一次写文件工具创建，"
            "不允许用一条 shell 命令批量生成。组建只有 1 名成员的团队，建 1 个任务交给他。"
        ),
        checks=_checks_iteration_limit,
        member_iterations=4,
        spec_overrides={"member_goal": {"max_attempts": 8}},
    ),
    _Scenario(
        name="reassign",
        summary="the leader takes a started task away: the first owner's goal is dropped",
        query=lambda workspace: (
            f"在 {workspace} 目录下交付 greet.py：运行后打印 hi，并实际运行确认。组建 2 名成员的团队（dev-a、dev-b），"
            "建 1 个任务并指派给 dev-a。等 dev-a 认领开工（任务变成 in_progress）之后，你必须立刻用 update_task 把这个任务"
            "改派给 dev-b，由 dev-b 完成。"
        ),
        checks=_checks_reassign,
        depends_on="the leader reassigning while the first owner still holds the task in progress",
    ),
    _Scenario(
        name="blocked",
        summary="a strictly sequential task and one allowed attempt: the member's goal blocks and the leader takes over",
        query=lambda workspace: (
            f"在 {workspace} 目录下交付一条哈希链：c0.txt 内容为 seed；之后 c1.txt 到 c8.txt，每个文件的内容必须是用 shell 命令 "
            "shasum -a 256 实际计算出的前一个文件的 sha256 十六进制值（必须先读到上一步的真实输出才能写下一步，禁止心算或编造，"
            "禁止用一条命令或脚本一次生成多步）。组建只有 1 名成员的团队，建 1 个任务交给他。如果成员上报卡住，由你接手把剩余步骤做完。"
        ),
        checks=_checks_blocked,
        member_iterations=2,
        member_max_attempts=1,
    ),
]
_BY_NAME = {scenario.name: scenario for scenario in _SCENARIOS}


# ---------------------------------------------------------------------------
# Running one scenario
# ---------------------------------------------------------------------------
def _build_spec(scenario: _Scenario, root: Path, workspace: Path) -> TeamAgentSpec:
    """Build the team spec of one scenario on the configured model endpoint."""
    model = load_llm_config().resolve()
    test_logger.info("[goal-e2e] model: %s (%s)", model.ref, model.api_base)
    model_config = {
        "model_client_config": {
            "client_provider": model.provider,
            "api_base": model.api_base,
            "api_key": model.api_key,
            "timeout": 180,
            "verify_ssl": False,
        },
        "model_request_config": {"model": model.model, "temperature": 0.2},
    }
    agent: dict[str, Any] = {
        "model": model_config,
        "workspace": {"root_path": str(workspace)},
        "max_iterations": 40,
        "completion_timeout": 600.0,
        "language": "cn",
        "enable_task_planning": False,
        "rails": [{"type": "core.sys_operation"}],
    }
    member = dict(agent)
    if scenario.member_iterations is not None:
        member["max_iterations"] = scenario.member_iterations
    if scenario.member_prompt:
        member["system_prompt"] = scenario.member_prompt
    goal = {"strategy": "hybrid", "max_attempts": 5}
    member_goal = dict(goal)
    if scenario.member_max_attempts is not None:
        member_goal["max_attempts"] = scenario.member_max_attempts
    member_goal.update(scenario.spec_overrides.get("member_goal", {}))
    return TeamAgentSpec.model_validate(
        {
            "team_name": f"goal_e2e_{scenario.name}",
            "lifecycle": "persistent",
            "spawn_mode": "inprocess",
            "dispatch_mode": scenario.dispatch,
            "teammate_mode": scenario.teammate_mode,
            "enable_task_verification": scenario.verification,
            "evolution_enabled": False,
            "leader": {"member_name": "team_leader", "display_name": "Leader", "goal": goal},
            "member_goal": member_goal,
            "agents": {"leader": agent, "teammate": member},
            "transport": {"type": "inprocess"},
            "storage": {"type": "sqlite", "params": {"connection_string": str(root / "team.db")}},
        }
    )


async def _drive(scenario: _Scenario, root: Path) -> list[_Check]:
    """Run one scenario end to end and return its checks."""
    workspace = root / "project"
    workspace.mkdir(parents=True)
    probe = _GoalProbe(root / "trace.log")
    probe.install()
    spec = _build_spec(scenario, root, workspace)
    timed_out = False
    await Runner.start()
    try:
        async with asyncio.timeout(_RUN_TIMEOUT_S):
            async for chunk in Runner.run_agent_team_streaming(
                agent_team=spec,
                inputs={"query": scenario.query(workspace)},
                session=f"goal-e2e-{scenario.name}-{int(time.time())}",
            ):
                payload = getattr(chunk, "payload", None)
                event = payload.get("event_type") if isinstance(payload, dict) else None
                if isinstance(event, str) and event.startswith("team."):
                    probe.record("marker", event=event)
    except TimeoutError:
        timed_out = True
        test_logger.error("[goal-e2e] %s timed out after %.0fs", scenario.name, _RUN_TIMEOUT_S)
    finally:
        await Runner.stop()
    return scenario.checks(_Run(probe=probe, workspace=workspace, timed_out=timed_out))


def _run_one(name: str) -> int:
    """Run a single scenario in this process; return its exit code."""
    scenario = _BY_NAME[name]
    root = _WORKDIR / name
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    if _LOG_CONFIG_PATH.is_file():
        configure_logging_into(_LOG_CONFIG_PATH, root / "logs")
    else:
        configure_log_config(DEFAULT_INNER_LOG_CONFIG)
    # Keep the team DB and member workspaces out of the user's real home.
    configure_openjiuwen_home(str(root / "openjiuwen_home"))
    checks = asyncio.run(_drive(scenario, root))
    failed = [check for check in checks if not check.ok]
    verdict = {
        "scenario": name,
        "passed": not failed,
        "depends_on": scenario.depends_on,
        "checks": [{"name": check.name, "ok": check.ok, "detail": check.detail} for check in checks],
    }
    (root / "verdict.json").write_text(json.dumps(verdict, ensure_ascii=False, indent=2), encoding="utf-8")
    for check in checks:
        mark = "PASS" if check.ok else "FAIL"
        suffix = f"  ({check.detail})" if check.detail else ""
        sys.stdout.write(f"  [{mark}] {check.name}{suffix}\n")
    if failed and scenario.depends_on:
        sys.stdout.write(f"  note: this scenario depends on {scenario.depends_on}; see {root / 'trace.log'}\n")
    sys.stdout.write(f"{name}: {'PASSED' if not failed else 'FAILED'}\n")
    return 1 if failed else 0


# ---------------------------------------------------------------------------
# Running several scenarios, each in its own process
# ---------------------------------------------------------------------------
async def _run_many(names: list[str], jobs: int) -> int:
    """Run scenarios as child processes, ``jobs`` at a time; return the exit code.

    One process per scenario: the probe wraps process-wide classes, so two
    teams in one process would write into each other's record.
    """
    gate = asyncio.Semaphore(jobs)
    results: dict[str, tuple[int, str]] = {}

    async def _child(name: str) -> None:
        async with gate:
            sys.stdout.write(f"[start] {name}\n")
            sys.stdout.flush()
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                str(Path(__file__).resolve()),
                "--only",
                name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            output, _ = await process.communicate()
            text = output.decode("utf-8", errors="replace")
            (_WORKDIR / f"{name}.out").write_text(text, encoding="utf-8")
            results[name] = (process.returncode or 0, text)

    await asyncio.gather(*(_child(name) for name in names))
    sys.stdout.write("\n")
    for name in names:
        code, text = results[name]
        lines = [line for line in text.splitlines() if line.startswith(("  [PASS]", "  [FAIL]", "  note:"))]
        sys.stdout.write(f"=== {name}: {'PASSED' if code == 0 else 'FAILED'} — {_BY_NAME[name].summary}\n")
        sys.stdout.write("\n".join(lines) + "\n")
        if code != 0 and not lines:
            sys.stdout.write(f"  no verdict; see {_WORKDIR / (name + '.out')}\n")
    failed = [name for name in names if results[name][0] != 0]
    sys.stdout.write(f"\n{len(names) - len(failed)}/{len(names)} scenarios passed")
    sys.stdout.write(f"; failed: {', '.join(failed)}\n" if failed else "\n")
    return 1 if failed else 0


def main() -> int:
    """Parse arguments and run the selected scenarios."""
    parser = argparse.ArgumentParser(description="Goal mode E2E against a real model.")
    parser.add_argument("--scenario", action="append", choices=sorted(_BY_NAME), help="run only this scenario (repeatable)")
    parser.add_argument("--jobs", type=int, default=_DEFAULT_JOBS, help="scenarios to run at once")
    parser.add_argument("--list", action="store_true", help="list the scenarios and exit")
    parser.add_argument("--only", choices=sorted(_BY_NAME), help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.list:
        for scenario in _SCENARIOS:
            sys.stdout.write(f"{scenario.name:18} {scenario.summary}\n")
        return 0
    if args.only:
        return _run_one(args.only)
    names = args.scenario or [scenario.name for scenario in _SCENARIOS]
    _WORKDIR.mkdir(parents=True, exist_ok=True)
    return asyncio.run(_run_many(names, max(1, args.jobs)))


if __name__ == "__main__":
    sys.exit(main())
