# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Agent Team Status Module

This module defines status enums and state transitions for team members and tasks.
"""

from enum import Enum
from typing import Dict, List


class MemberStatus(str, Enum):
    """Member status enum - simple status for team members

    States:
        UNSTARTED: Member has been created but not yet started
        STARTING: Member agent process is being spawned (transitional;
            only the first startup path can transition UNSTARTED→STARTING,
            acting as a CAS guard against duplicate spawn)
        READY: Member is ready to receive tasks
        BUSY: Member is currently processing a task
        PAUSED: Member coroutine has exited at the end of a round
            (lifecycle-driven, persistent team idle); state preserved and
            recoverable via resume (returns to READY).
        STOPPED: Member runtime has been torn down by an external
            ``stop_coordination`` (team-not-disbanded teardown). State is
            preserved on the persistence layer and the member is
            expected to be re-spawned by ``recover_team``. Distinct from
            PAUSED in *why* it stopped (external stop_team vs natural
            round end) and distinct from SHUTDOWN in that the team is
            still considered live.
        RESTARTING: Member process is being restarted after failure
        SHUTDOWN_REQUESTED: Member has received shutdown request
        SHUTDOWN: Member has been shut down
        ERROR: Member is in error state
    """
    UNSTARTED = "unstarted"
    STARTING = "starting"
    READY = "ready"
    BUSY = "busy"
    PAUSED = "paused"
    STOPPED = "stopped"
    RESTARTING = "restarting"
    SHUTDOWN_REQUESTED = "shutdown_requested"
    SHUTDOWN = "shut_down"
    ERROR = "error"


# There are two thresholds on a member's way out, and they are deliberately
# NOT the same one. Conflating them is how a departing member ends up either
# stranding its work or never being told it was removed.
#
# MEMBER_DEPARTED_STATUSES — the leader has released the member: shutdown is
# requested, or the member is already gone. Guards that protect a member's
# in-flight work lift here, at the *request*, not at the terminal state. The
# HITT task lock (``UpdateTaskTool``) is the one that matters: an avatar wedged
# mid-round may never reach SHUTDOWN, so waiting for the terminal state would
# strand the task it holds forever — nobody left to finish it, and the leader
# forbidden from touching it.
#
# MEMBER_UNREACHABLE_STATUSES — the member is actually gone. Message delivery
# stops only here, NOT at the request: ``TeamBackend.shutdown_member`` writes
# SHUTDOWN_REQUESTED *before* it sends the shutdown notice, so a member that is
# merely on its way out must still be delivered to. Cut delivery at the request
# and the one message that never arrives is the notice saying it was removed —
# for a human member, that notice is what its controller reads.
#
# PAUSED / STOPPED / ERROR / RESTARTING are in neither set: those members still
# belong to the team and are expected back, so both the work guards and the
# delivery path keep treating them as ordinary members.
MEMBER_DEPARTED_STATUSES: frozenset[MemberStatus] = frozenset(
    {
        MemberStatus.SHUTDOWN_REQUESTED,
        MemberStatus.SHUTDOWN,
    }
)

MEMBER_UNREACHABLE_STATUSES: frozenset[MemberStatus] = frozenset({MemberStatus.SHUTDOWN})


# State transition table for MemberStatus
MEMBER_TRANSITIONS: Dict[MemberStatus, List[MemberStatus]] = {
    MemberStatus.UNSTARTED: [
        MemberStatus.STARTING,
        MemberStatus.READY,
        MemberStatus.SHUTDOWN,
        MemberStatus.ERROR,
    ],
    MemberStatus.STARTING: [
        MemberStatus.READY,
        MemberStatus.UNSTARTED,
        MemberStatus.SHUTDOWN,
        MemberStatus.ERROR,
    ],
    MemberStatus.READY: [
        MemberStatus.READY,
        MemberStatus.BUSY,
        MemberStatus.PAUSED,
        MemberStatus.STOPPED,
        MemberStatus.SHUTDOWN_REQUESTED,
        MemberStatus.SHUTDOWN,
        MemberStatus.ERROR,
    ],
    MemberStatus.BUSY: [
        MemberStatus.READY,
        MemberStatus.PAUSED,
        MemberStatus.STOPPED,
        MemberStatus.SHUTDOWN_REQUESTED,
        MemberStatus.ERROR,
    ],
    MemberStatus.PAUSED: [
        MemberStatus.READY,
        MemberStatus.RESTARTING,
        MemberStatus.STOPPED,
        MemberStatus.SHUTDOWN_REQUESTED,
        MemberStatus.SHUTDOWN,
        MemberStatus.ERROR,
    ],
    MemberStatus.STOPPED: [
        MemberStatus.READY,
        MemberStatus.RESTARTING,
        MemberStatus.SHUTDOWN_REQUESTED,
        MemberStatus.SHUTDOWN,
        MemberStatus.ERROR,
    ],
    MemberStatus.RESTARTING: [
        MemberStatus.READY,
        MemberStatus.STOPPED,
        MemberStatus.ERROR,
        MemberStatus.SHUTDOWN,
    ],
    MemberStatus.SHUTDOWN_REQUESTED: [
        MemberStatus.SHUTDOWN,
        MemberStatus.ERROR,
    ],
    MemberStatus.SHUTDOWN: [
        MemberStatus.RESTARTING,
    ],
    MemberStatus.ERROR: [
        MemberStatus.RESTARTING,
        MemberStatus.READY,
        MemberStatus.STOPPED,
        MemberStatus.SHUTDOWN_REQUESTED,
        MemberStatus.SHUTDOWN,
    ],
}


# Statuses a member can rest in when it has no active work. Consumed by the
# team-completion check. Co-located with MemberStatus as its single source of
# truth: status.py has no dependencies, whereas TASK_TERMINAL_STATUSES lives in
# tools/database/graph.py only to avoid an import edge from the SQL layer back
# into status.py.
MEMBER_SETTLED_STATUSES = frozenset(
    {
        MemberStatus.READY.value,
        MemberStatus.PAUSED.value,
        MemberStatus.STOPPED.value,
        MemberStatus.SHUTDOWN.value,
    }
)


class ExecutionStatus(str, Enum):
    """Execution status enum - detailed status for task execution

    States:
        IDLE: Not executing any task
        STARTING: Task execution is starting
        RUNNING: Task is actively running
        CANCEL_REQUESTED: Cancellation has been requested
        CANCELLING: Task is being cancelled
        CANCELLED: Task was cancelled
        COMPLETING: Task is completing
        COMPLETED: Task completed successfully
        FAILED: Task failed
        TIMED_OUT: Task timed out
    """
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    COMPLETING = "completing"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


class MemberMode(str, Enum):
    """Member mode enum - defines how members interact with tasks

    Modes:
        BUILD_MODE: Members can claim and complete tasks directly (default)
        PLAN_MODE: Members need leader approval before completing tasks
    """
    BUILD_MODE = "build_mode"
    PLAN_MODE = "plan_mode"


# State transition table for ExecutionStatus
EXECUTION_TRANSITIONS: Dict[ExecutionStatus, List[ExecutionStatus]] = {
    ExecutionStatus.IDLE: [ExecutionStatus.STARTING],
    ExecutionStatus.STARTING: [
        ExecutionStatus.RUNNING,
        ExecutionStatus.CANCEL_REQUESTED,
        ExecutionStatus.CANCELLING,
        ExecutionStatus.FAILED,
        ExecutionStatus.TIMED_OUT,
    ],
    ExecutionStatus.RUNNING: [
        ExecutionStatus.CANCEL_REQUESTED,
        ExecutionStatus.CANCELLING,
        ExecutionStatus.COMPLETING,
        ExecutionStatus.FAILED,
        ExecutionStatus.TIMED_OUT,
    ],
    ExecutionStatus.CANCEL_REQUESTED: [
        ExecutionStatus.CANCELLING,
        ExecutionStatus.CANCELLED,
        ExecutionStatus.FAILED,
        ExecutionStatus.TIMED_OUT,
    ],
    ExecutionStatus.CANCELLING: [
        ExecutionStatus.CANCELLED,
        ExecutionStatus.FAILED,
        ExecutionStatus.TIMED_OUT,
    ],
    ExecutionStatus.CANCELLED: [ExecutionStatus.IDLE],
    ExecutionStatus.COMPLETING: [
        ExecutionStatus.COMPLETED,
        ExecutionStatus.FAILED,
        ExecutionStatus.TIMED_OUT,
    ],
    ExecutionStatus.COMPLETED: [ExecutionStatus.IDLE],
    ExecutionStatus.FAILED: [ExecutionStatus.IDLE],
    ExecutionStatus.TIMED_OUT: [ExecutionStatus.IDLE],
}


class TaskStatus(Enum):
    """Task status enum for team tasks.

    States name a *condition* the task rests in ("the task IS ___"); the
    events that move between them (claim / start / submit / approve / verify)
    are transitions, not states. The two optional gates — ``PLANNING``
    (before execution) and ``IN_REVIEW`` (after execution) — are structurally
    identical mirrors: a member submits an artifact, an actor decides, pass
    advances and fail loops back.

    Both dispatch modes converge on the same execution condition: a teammate
    self-``claim`` (autonomous) and the scheduler ``start`` (scheduled) are
    two transitions into the *same* ``IN_PROGRESS`` state — the mode
    difference belongs to the transition, not the state. "Assigned but not yet
    started" (scheduled) stays representable as ``PENDING`` with an assignee.

    States:
        PENDING: To do — waiting to be claimed (autonomous) or assigned-but-
            not-yet-started (scheduled, when it carries an assignee).
        BLOCKED: Dependencies unmet.
        PLANNING: Plan gate — the member is preparing a plan and awaiting
            leader approval (optional, PLAN_MODE only).
        IN_PROGRESS: Executing. Unifies the former CLAIMED / STARTED /
            PLAN_APPROVED execution nodes.
        IN_REVIEW: Verify gate — the member submitted results and a reviewer
            is verifying them (optional, present when the task has reviewers).
        COMPLETED: Terminal — verified / accepted.
        CANCELLED: Terminal.
    """
    PENDING = "pending"
    BLOCKED = "blocked"
    PLANNING = "planning"
    IN_PROGRESS = "in_progress"
    IN_REVIEW = "in_review"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


# State transition table for TaskStatus. A single superset covering both
# dispatch modes and both optional gates. Which path a task walks is decided by
# which method acts on it (claim/start into IN_PROGRESS, submit_plan into
# PLANNING, member_complete into IN_REVIEW/COMPLETED), not by branching on
# dispatch mode. ``PLANNING`` and ``IN_REVIEW`` are the two mirror gates, each
# with a self / back-edge for its rework loop.
TASK_TRANSITIONS: Dict[TaskStatus, List[TaskStatus]] = {
    TaskStatus.PENDING: [
        TaskStatus.PLANNING,      # plan_mode: reserve for planning
        TaskStatus.IN_PROGRESS,   # claim (autonomous) / start (scheduled)
        TaskStatus.BLOCKED,
        TaskStatus.CANCELLED,
    ],
    TaskStatus.BLOCKED: [
        TaskStatus.PENDING,
        TaskStatus.CANCELLED,
    ],
    TaskStatus.PLANNING: [
        TaskStatus.PLANNING,      # submit / reject rework loop
        TaskStatus.IN_PROGRESS,   # approve_plan
        TaskStatus.PENDING,       # reset
        TaskStatus.BLOCKED,
        TaskStatus.CANCELLED,
    ],
    TaskStatus.IN_PROGRESS: [
        TaskStatus.IN_REVIEW,     # member_complete_task (has reviewer)
        TaskStatus.COMPLETED,     # member_complete_task (no reviewer)
        TaskStatus.PENDING,       # reset
        TaskStatus.BLOCKED,
        TaskStatus.CANCELLED,
    ],
    TaskStatus.IN_REVIEW: [
        TaskStatus.COMPLETED,     # verify pass
        TaskStatus.IN_PROGRESS,   # verify fail — rework loop
        TaskStatus.PENDING,       # reset
        TaskStatus.CANCELLED,
    ],
    TaskStatus.COMPLETED: [],
    TaskStatus.CANCELLED: [],
}


def is_valid_transition(
    current_status: Enum,
    new_status: Enum,
    transitions: Dict[Enum, List[Enum]]
) -> bool:
    """Check if a state transition is valid

    Args:
        current_status: Current status
        new_status: Target status
        transitions: Transition table for the status type

    Returns:
        True if transition is valid, False otherwise
    """
    if current_status not in transitions:
        return False
    return new_status in transitions[current_status]
