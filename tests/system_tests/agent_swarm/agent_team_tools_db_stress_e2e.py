# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""High-concurrency stress test for team tools against a real SQLite DB.

Simulates a 20-member team collaborating in a real scenario: every member
runs its own coroutine and hammers the shared, file-backed SQLite database
across the WHOLE collaboration surface — not just the team tools, but the
``TeamBackend`` / ``TeamTaskManager`` / ``TeamMessageManager`` / DAO methods
behind them:

  - Tools: create_task / send_message / view_task / list_members / claim_task
  - Task status: update_task / complete / reset
  - Messaging: send / multicast / broadcast / get_messages /
    get_broadcast_messages / has_unread_messages / mark_messages_read
  - Member state machine: update_member_status (READY<->BUSY) and
    update_member_execution_status (IDLE->...->IDLE loop) on each member's row

Traffic profile mirrors a real team: FEW tasks, MANY large messages. The task
pool is small and bounded (workers churn it, they don't grow it); the load is
dominated by a high volume of large communication messages (each worker sends
a burst of ~8 KB messages every iteration, then drains its inbox). This is
where the real DB pressure lives, so the workload leans there on purpose.

All members share ONE ``TeamDatabase`` — so the single process-wide write
lock (``DbSessions``) is the contention point this test puts under pressure.

Design:
  - File-backed SQLite (WAL) in a temp dir — measures real disk-write /
    fsync behaviour, not an in-memory shortcut.
  - Message payloads are ~8 KB (large comms); member/task rows ~2 KB.
  - Each of the 20 worker coroutines loops a fixed number of iterations.
    Results are ignored except when a follow-up id is needed (a claimable
    task, unread message ids) — this measures DB throughput, not business
    correctness.
  - Every call is timed with ``perf_counter``; any call slower than the
    threshold (default 1 s) emits a ``team_logger.warning`` and is counted.
  - A final report prints the per-operation latency distribution + slow count.

Run directly (no model / network needed):
    source .venv/bin/activate
    export PYTHONPATH=.:$PYTHONPATH
    python tests/system_tests/agent_swarm/agent_team_tools_db_stress_e2e.py

Tunables (env vars):
    STRESS_TEAM_SIZE          worker members / coroutines      (default 20)
    STRESS_ITERATIONS         loop rounds per member           (default 20)
    STRESS_MSGS_PER_ITER      point-to-point sends per round   (default 6)
    STRESS_MSG_PAYLOAD_SIZE   bytes per message (large comms)  (default 8192)
    STRESS_PAYLOAD_SIZE       bytes per member/task row        (default 2048)
    STRESS_TASK_POOL_SIZE     seeded, bounded task count       (default 20)
    STRESS_SLOW_THRESHOLD_S   slow-call warning threshold      (default 1.0)

Exit code: 0 = no call raised; 1 = at least one call raised an exception.
Slow calls only warn — they never fail the run.
"""

from __future__ import annotations

import asyncio
import os
import random
import shutil
import tempfile
import time
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeVar

from openjiuwen.agent_teams.context import reset_session_id, set_session_id
from openjiuwen.agent_teams.messager import InProcessMessager, MessagerTransportConfig
from openjiuwen.agent_teams.schema.status import ExecutionStatus, MemberStatus
from openjiuwen.agent_teams.tools.database import (
    DatabaseConfig,
    DatabaseType,
    TeamDatabase,
)
from openjiuwen.agent_teams.tools.locales import Translator, make_translator
from openjiuwen.agent_teams.tools.team import TeamBackend
from openjiuwen.agent_teams.tools.tool_member import ListMembersTool
from openjiuwen.agent_teams.tools.tool_message import SendMessageTool
from openjiuwen.agent_teams.tools.tool_task import (
    ClaimTaskTool,
    TaskCreateTool,
    ViewTaskToolV2,
)
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.foundation.tool.base import Tool
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.tools.base_tool import ToolOutput

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

TEAM_SIZE = int(os.getenv("STRESS_TEAM_SIZE", "20"))
ITERATIONS = int(os.getenv("STRESS_ITERATIONS", "20"))
# Member persona / task content payload — the lighter, ~2 KB rows.
PAYLOAD_SIZE = int(os.getenv("STRESS_PAYLOAD_SIZE", "2048"))
# Message payload — the dominant, "large communication" rows. Real teams
# churn far more message volume than task volume, so messages are both more
# numerous (MSGS_PER_ITER) and individually bigger (MSG_PAYLOAD_SIZE).
MSG_PAYLOAD_SIZE = int(os.getenv("STRESS_MSG_PAYLOAD_SIZE", "8192"))
# Point-to-point messages each worker sends per iteration (the burst size).
MSGS_PER_ITER = int(os.getenv("STRESS_MSGS_PER_ITER", "6"))
# Fixed, bounded task pool seeded up front. Tasks stay few — workers mostly
# claim / complete / reset / update this small pool instead of ballooning the
# board — so ``view_task`` reflects a realistic small board, not O(N) growth.
TASK_POOL_SIZE = int(os.getenv("STRESS_TASK_POOL_SIZE", "20"))
SLOW_THRESHOLD_S = float(os.getenv("STRESS_SLOW_THRESHOLD_S", "1.0"))

TEAM_NAME = "stress_team"
LEADER_NAME = "leader"

# Valid, repeating state-machine cycles a worker walks its own member row
# through — one transition per iteration. Each cycle returns to its start so
# it can be applied indefinitely (see MEMBER_TRANSITIONS / EXECUTION_TRANSITIONS
# in schema.status). Members are moved UNSTARTED -> READY at seed time, then
# toggle READY <-> BUSY here; execution status walks a full RUNNING loop.
MEMBER_STATUS_CYCLE = (MemberStatus.BUSY, MemberStatus.READY)
EXEC_STATUS_CYCLE = (
    ExecutionStatus.STARTING,
    ExecutionStatus.RUNNING,
    ExecutionStatus.COMPLETING,
    ExecutionStatus.COMPLETED,
    ExecutionStatus.IDLE,
)

_T = TypeVar("_T")

# Operation labels that only read (no write lock; served concurrently by the
# WAL reader pool). Everything else is a write — it serialises through the
# process-wide write lock, which is why the report splits the two: writes pay
# the shared-lock queuing tax, reads do not.
READ_OPS = frozenset({"has_unread", "read_inbox", "read_broadcast", "list_members", "view_task"})


def _make_payload(tag: str, size: int = PAYLOAD_SIZE) -> str:
    """Build a ``size``-char string with a readable prefix.

    A recognizable ``tag`` prefix keeps DB rows debuggable while the bulk
    filler brings each row up to the target size so every write moves a
    realistic amount of data through the write lock.
    """
    prefix = f"[{tag}] "
    filler = ("stress-payload-" * ((size // 15) + 1))[: max(0, size - len(prefix))]
    return prefix + filler


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


@dataclass
class Metrics:
    """Accumulates per-tool latencies and anomalies across all workers.

    Safe to mutate from many coroutines without a lock: the process runs on
    a single event loop, and every mutation here happens synchronously after
    the awaited tool call returns — there is no await between read and write.
    """

    latencies: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))
    slow: list[tuple[str, str, float]] = field(default_factory=list)
    errors: list[tuple[str, str, str]] = field(default_factory=list)

    def record(self, tool_name: str, member_name: str, elapsed: float) -> None:
        """Store one call's latency and flag it when over the slow threshold."""
        self.latencies[tool_name].append(elapsed)
        if elapsed > SLOW_THRESHOLD_S:
            self.slow.append((tool_name, member_name, elapsed))
            team_logger.warning(
                "SLOW team-tool call: tool=%s member=%s elapsed=%.3fs (threshold=%.1fs)",
                tool_name,
                member_name,
                elapsed,
                SLOW_THRESHOLD_S,
            )

    @property
    def total_calls(self) -> int:
        """Total number of timed tool calls across every tool."""
        return sum(len(v) for v in self.latencies.values())


async def _timed(
    metrics: Metrics,
    label: str,
    member_name: str,
    factory: Callable[[], Awaitable[_T]],
) -> _T | None:
    """Await ``factory()``, timing it and recording latency / errors.

    A single timing path for every measured call — team tools, manager
    methods, and DAO methods alike — so the report treats them uniformly.
    Results are returned for the few callers that need a follow-up id (a
    claimable task, unread message ids); most callers ignore them, because
    this measures DB throughput, not business correctness. A raised
    exception is captured (not re-raised) so one failing call never aborts a
    worker mid-loop; the latency is still recorded in ``finally``.
    """
    start = time.perf_counter()
    result: _T | None = None
    try:
        result = await factory()
    except Exception as e:  # noqa: BLE001 — stress harness records, never crashes
        metrics.errors.append((label, member_name, repr(e)))
        team_logger.error("call raised: op=%s member=%s err=%r", label, member_name, e)
    finally:
        metrics.record(label, member_name, time.perf_counter() - start)
    return result


# ---------------------------------------------------------------------------
# Environment assembly
# ---------------------------------------------------------------------------


def _make_backend(member_name: str, is_leader: bool, db: TeamDatabase) -> TeamBackend:
    """Build a TeamBackend for one member over the shared DB.

    Each member gets its own messager (distinct ``node_id``) sharing the
    process-global in-process bus, mirroring the real in-process team layout
    where every member publishes under its own identity.
    """
    messager = InProcessMessager(config=MessagerTransportConfig(node_id=member_name))
    return TeamBackend(
        team_name=TEAM_NAME,
        member_name=member_name,
        is_leader=is_leader,
        db=db,
        messager=messager,
    )


def _make_toolset(backend: TeamBackend, t: Translator) -> dict[str, Tool]:
    """Build the five collaboration tools bound to one member's backend."""
    return {
        "create_task": TaskCreateTool(backend, t),
        "send_message": SendMessageTool(backend.message_manager, t, team=backend),
        "view_task": ViewTaskToolV2(backend.task_manager, t),
        "claim_task": ClaimTaskTool(backend.task_manager, t),
        "list_members": ListMembersTool(backend, t),
    }


async def _seed_team(db: TeamDatabase, t: Translator, member_names: list[str]) -> None:
    """Create the team row and register a leader + all worker members.

    Every member is persisted with a ~2 KB persona ``desc`` so member rows
    are as heavy as the messages and tasks the workers will churn.
    """
    await db.team.create_team(
        team_name=TEAM_NAME,
        display_name="Stress Team",
        leader_member_name=LEADER_NAME,
    )
    leader_backend = _make_backend(LEADER_NAME, True, db)
    big_desc = _make_payload("persona")

    # Register the leader itself as a BUSY member so sends to "leader" resolve
    # and it shows up in list_members for the workers.
    await leader_backend.spawn_member(
        member_name=LEADER_NAME,
        display_name=LEADER_NAME,
        agent_card=AgentCard(id=f"{TEAM_NAME}_{LEADER_NAME}", name=LEADER_NAME, description=big_desc),
        desc=big_desc,
        status=MemberStatus.BUSY,
    )
    for name in member_names:
        await leader_backend.spawn_member(
            member_name=name,
            display_name=name,
            agent_card=AgentCard(id=f"{TEAM_NAME}_{name}", name=name, description=big_desc),
            desc=big_desc,
            status=MemberStatus.UNSTARTED,
        )
        # Move each worker into READY so the per-iteration READY <-> BUSY
        # status cycle starts from a valid state (UNSTARTED -> BUSY is not a
        # legal transition, READY -> BUSY is).
        await db.member.update_member_status(name, TEAM_NAME, MemberStatus.READY.value)

    # Seed a small, bounded task pool. Tasks stay few in a real team; workers
    # churn this pool (claim/complete/reset/update) rather than growing it, so
    # the board reads stay cheap and messaging stays the dominant DB load.
    leader_task_mgr = leader_backend.task_manager
    task_content = _make_payload("seed-task")
    for i in range(TASK_POOL_SIZE):
        await leader_task_mgr.add(title=f"seed task {i}", content=task_content)


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------


async def _worker(
    backend: TeamBackend,
    peers: list[str],
    toolset: dict[str, Tool],
    metrics: Metrics,
) -> None:
    """One member's workload: message-heavy, task-light, whole-surface.

    Modelled on a real team's traffic mix — a small, bounded set of tasks but
    a high volume of large communication messages. Each iteration therefore
    leans on the messaging path (many big sends + inbox drains) while task
    work just churns the pre-seeded pool. Per iteration:

    Messaging (dominant load):
      - send_message x MSGS_PER_ITER (tool) — large point-to-point bursts
      - send_message multicast (tool)       — one large message to a few peers
      - broadcast_message (manager)         — fan-out (every 4th iteration)
      - has_unread_messages (manager)       — unread probe
      - get_messages + mark_messages_read   — drain + ack the (large) direct inbox
      - get_broadcast_messages + mark       — drain + ack broadcast inbox

    Member state machine (own row, cheap writes):
      - update_member_status (DAO)           — READY <-> BUSY
      - update_member_execution_status (DAO) — IDLE -> ... -> IDLE loop

    Task churn (light, bounded pool):
      - view_task list / claimable, list_members (tool) — small-board reads
      - create_task (tool)                   — rare replenishment
      - update_task / claim / complete / reset — pool churn + status writes
    """
    name = backend.member_name
    db = backend.db
    task_mgr = backend.task_manager
    msg_mgr = backend.message_manager
    msg_payload = _make_payload(name, MSG_PAYLOAD_SIZE)
    task_payload = _make_payload(name)
    rng = random.Random(hash(name) & 0xFFFFFFFF)

    for i in range(ITERATIONS):
        # ===== Messaging: the dominant, large-payload load =====
        for k in range(MSGS_PER_ITER):
            target = rng.choice(peers)
            await _timed(
                metrics,
                "send_message",
                name,
                lambda tgt=target, kk=k: toolset["send_message"].invoke(
                    {"to": tgt, "content": msg_payload, "summary": f"{name} #{i}.{kk}"}
                ),
            )
        if len(peers) >= 3:
            group = rng.sample(peers, 3)
            await _timed(
                metrics,
                "send_multicast",
                name,
                lambda g=group: toolset["send_message"].invoke(
                    {"to": g, "content": msg_payload, "summary": f"{name} multicast #{i}"}
                ),
            )
        if i % 4 == 0:
            await _timed(metrics, "broadcast", name, lambda: msg_mgr.broadcast_message(content=msg_payload))

        # Drain + ack both inboxes — heavy reads now (many large unread rows).
        await _timed(metrics, "has_unread", name, lambda: msg_mgr.has_unread_messages())
        inbox = await _timed(
            metrics,
            "read_inbox",
            name,
            lambda: msg_mgr.get_messages(to_member_name=name, unread_only=True),
        )
        # Pass the raw message objects — the manager owns the read-state
        # semantics (direct is_read rows vs the single broadcast watermark)
        # and collapses multiple broadcasts before hitting the DAO.
        if inbox:
            await _timed(metrics, "mark_read", name, lambda msgs=inbox: msg_mgr.mark_messages_read(msgs, name))
        bcast = await _timed(
            metrics,
            "read_broadcast",
            name,
            lambda: msg_mgr.get_broadcast_messages(member_name=name, unread_only=True),
        )
        if bcast:
            await _timed(
                metrics,
                "mark_broadcast_read",
                name,
                lambda msgs=bcast: msg_mgr.mark_messages_read(msgs, name),
            )

        # ===== Member state machine writes on own row (cheap) =====
        m_status = MEMBER_STATUS_CYCLE[i % len(MEMBER_STATUS_CYCLE)]
        await _timed(
            metrics,
            "member_status",
            name,
            lambda s=m_status: db.member.update_member_status(name, TEAM_NAME, s.value),
        )
        e_status = EXEC_STATUS_CYCLE[i % len(EXEC_STATUS_CYCLE)]
        await _timed(
            metrics,
            "member_exec",
            name,
            lambda s=e_status: db.member.update_member_execution_status(name, TEAM_NAME, s.value),
        )

        # ===== Task churn: light reads + bounded-pool status writes =====
        if i % 3 == 0:
            await _timed(metrics, "view_task", name, lambda: toolset["view_task"].invoke({"action": "list"}))
            await _timed(metrics, "list_members", name, lambda: toolset["list_members"].invoke({}))
        if rng.random() < 0.1:
            await _timed(
                metrics,
                "create_task",
                name,
                lambda: toolset["create_task"].invoke(
                    {"tasks": [{"title": f"{name} task {i}", "content": task_payload}]}
                ),
            )

        claimable = await _timed(
            metrics,
            "view_task",
            name,
            lambda: toolset["view_task"].invoke({"action": "claimable"}),
        )
        task_id = _pick_claimable(claimable, rng)
        if not task_id:
            continue
        # Edit while still PENDING (update_task only allows edits on
        # pending / blocked tasks), then race to claim it.
        await _timed(metrics, "task_update", name, lambda tid=task_id: task_mgr.update_task(tid, content=task_payload))
        claim = await _timed(
            metrics,
            "claim_task",
            name,
            lambda tid=task_id: toolset["claim_task"].invoke({"task_id": tid, "status": "claimed"}),
        )
        if not (claim and claim.success):
            continue
        # Mostly reset the won task back to the pool (keeps it non-empty),
        # sometimes complete it — both terminal status-write paths run.
        if rng.random() < 0.3:
            await _timed(metrics, "task_complete", name, lambda tid=task_id: task_mgr.complete(tid))
        else:
            await _timed(metrics, "task_reset", name, lambda tid=task_id: task_mgr.reset(tid))


def _pick_claimable(output: ToolOutput | None, rng: random.Random) -> str | None:
    """Extract a random claimable task_id from a view_task(claimable) result."""
    if output is None or not output.success or not output.data:
        return None
    tasks = output.data.get("tasks", [])
    if not tasks:
        return None
    return rng.choice(tasks).get("task_id")


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _percentile(sorted_values: list[float], pct: float) -> float:
    """Nearest-rank percentile over an already-sorted list (seconds)."""
    if not sorted_values:
        return 0.0
    rank = max(0, min(len(sorted_values) - 1, int(round(pct / 100.0 * len(sorted_values))) - 1))
    return sorted_values[rank]


def _stat_row(label: str, samples: list[float], slow_count: int) -> str:
    """Format one latency row (samples must be pre-sorted ascending, seconds)."""
    count = len(samples)
    avg = sum(samples) / count if count else 0.0
    return (
        f"{label:<20}{count:>8}{avg * 1000:>10.2f}"
        f"{_percentile(samples, 50) * 1000:>10.2f}{_percentile(samples, 95) * 1000:>10.2f}"
        f"{_percentile(samples, 99) * 1000:>10.2f}{samples[-1] * 1000:>10.2f}{slow_count:>8}"
    )


def _group_lines(title: str, labels: list[str], metrics: Metrics, wall_seconds: float) -> list[str]:
    """Render one op group (reads or writes) with per-op rows + a subtotal.

    The subtotal aggregates every sample across the group's ops, so its
    avg / percentiles describe the group as a whole and its throughput is the
    group's calls-per-second.
    """
    if not labels:
        return []
    pooled: list[float] = []
    group_slow = 0
    rows: list[str] = []
    for label in labels:
        samples = sorted(metrics.latencies[label])
        slow_here = sum(1 for tn, _, _ in metrics.slow if tn == label)
        rows.append(_stat_row(label, samples, slow_here))
        pooled.extend(samples)
        group_slow += slow_here
    pooled.sort()
    group_tput = len(pooled) / wall_seconds if wall_seconds > 0 else 0.0
    return [
        f"-- {title} ({len(pooled)} calls, {group_tput:.1f}/s) --",
        f"{'op':<20}{'count':>8}{'avg(ms)':>10}{'p50(ms)':>10}"
        f"{'p95(ms)':>10}{'p99(ms)':>10}{'max(ms)':>10}{'>1s':>8}",
        *rows,
        _stat_row("SUBTOTAL", pooled, group_slow),
    ]


def _report(metrics: Metrics, wall_seconds: float) -> None:
    """Print the latency distribution, split into writes vs reads, + tallies.

    The benchmark report is the script's primary deliverable and is written
    to stdout so it stays visible regardless of the log config's level gate.
    The report is split into two groups because they live on different
    concurrency paths: writes serialise through the one process-wide write
    lock (and pay its queuing tax), reads run concurrently on the WAL reader
    pool. Seeing them apart makes the write-lock bottleneck obvious.
    The required per-call slow-path alerts still go through
    ``team_logger.warning`` (see ``Metrics.record``); this is only the
    end-of-run summary.
    """
    total = metrics.total_calls
    throughput = total / wall_seconds if wall_seconds > 0 else 0.0
    write_labels = sorted(lbl for lbl in metrics.latencies if lbl not in READ_OPS)
    read_labels = sorted(lbl for lbl in metrics.latencies if lbl in READ_OPS)
    lines = [
        "=" * 86,
        f"Team-tool DB stress: team_size={TEAM_SIZE} iterations={ITERATIONS} "
        f"payload={PAYLOAD_SIZE}B slow_threshold={SLOW_THRESHOLD_S:.1f}s",
        f"Total calls={total} wall={wall_seconds:.3f}s throughput={throughput:.1f} calls/s "
        f"slow(>{SLOW_THRESHOLD_S:.1f}s)={len(metrics.slow)} errors={len(metrics.errors)}",
        *_group_lines("WRITES (serialized through the write lock)", write_labels, metrics, wall_seconds),
        *_group_lines("READS (WAL-concurrent, no write lock)", read_labels, metrics, wall_seconds),
        "=" * 86,
    ]
    print("\n".join(lines))

    if metrics.errors:
        team_logger.error("Recorded %d tool-call errors; first few:", len(metrics.errors))
        for tool_name, member_name, err in metrics.errors[:5]:
            team_logger.error("  tool=%s member=%s err=%s", tool_name, member_name, err)


def _verdict(metrics: Metrics) -> int:
    """0 when no tool call raised; 1 otherwise. Slow calls do not fail."""
    if metrics.errors:
        return 1
    if metrics.total_calls == 0:
        team_logger.error("No tool calls were recorded — stress run did nothing")
        return 1
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def run_stress() -> Metrics:
    """Assemble the environment, run all workers, report, and return metrics."""
    t = make_translator("cn")
    session_id = f"tools_stress_{uuid.uuid4().hex[:8]}"
    token = set_session_id(session_id)
    tmpdir = Path(tempfile.mkdtemp(prefix="team_tools_stress_"))
    db_path = tmpdir / "team.db"
    db = TeamDatabase(DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=str(db_path)))
    metrics = Metrics()

    try:
        await db.initialize()
        member_names = [f"member-{i:02d}" for i in range(TEAM_SIZE)]
        await _seed_team(db, t, member_names)

        backends = {name: _make_backend(name, False, db) for name in member_names}
        toolsets = {name: _make_toolset(backends[name], t) for name in member_names}
        roster = [LEADER_NAME, *member_names]

        print(f"Starting stress: {TEAM_SIZE} workers x {ITERATIONS} iterations against {db_path}")
        wall_start = time.perf_counter()
        await asyncio.gather(
            *(
                _worker(backends[name], [m for m in roster if m != name], toolsets[name], metrics)
                for name in member_names
            )
        )
        wall_seconds = time.perf_counter() - wall_start
        _report(metrics, wall_seconds)
        return metrics
    finally:
        reset_session_id(token)
        await db.close()
        shutil.rmtree(tmpdir, ignore_errors=True)


async def main() -> int:
    """Script entry point: run the stress and translate metrics to an exit code."""
    metrics = await run_stress()
    return _verdict(metrics)


if __name__ == "__main__":
    import sys

    sys.exit(asyncio.run(main()))
