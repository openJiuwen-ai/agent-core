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
        READY: Member is ready to receive tasks
        BUSY: Member is currently processing a task
        RESTARTING: Member process is being restarted after failure
        SHUTDOWN_REQUESTED: Member has received shutdown request
        SHUTDOWN: Member has been shut down
        ERROR: Member is in error state
    """
    UNSTARTED = "unstarted"
    READY = "ready"
    BUSY = "busy"
    RESTARTING = "restarting"
    SHUTDOWN_REQUESTED = "shutdown_requested"
    SHUTDOWN = "shut_down"
    ERROR = "error"


# State transition table for MemberStatus
MEMBER_TRANSITIONS: Dict[MemberStatus, List[MemberStatus]] = {
    MemberStatus.UNSTARTED: [
        MemberStatus.READY,
        MemberStatus.SHUTDOWN,
        MemberStatus.ERROR,
    ],
    MemberStatus.READY: [
        MemberStatus.BUSY,
        MemberStatus.SHUTDOWN_REQUESTED,
        MemberStatus.SHUTDOWN,
        MemberStatus.ERROR,
    ],
    MemberStatus.BUSY: [
        MemberStatus.READY,
        MemberStatus.SHUTDOWN_REQUESTED,
        MemberStatus.ERROR,
    ],
    MemberStatus.RESTARTING: [
        MemberStatus.READY,
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
        MemberStatus.SHUTDOWN_REQUESTED,
        MemberStatus.SHUTDOWN,
    ],
}


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
    """Task status enum for team tasks

    States:
        PENDING: Task is waiting to be claimed
        CLAIMED: Task has been claimed by a member
        PLAN_APPROVED: Task plan has been approved (only for PLAN_MODE members)
        COMPLETED: Task has been completed
        CANCELLED: Task was cancelled
        BLOCKED: Task is blocked due to dependencies
    """
    PENDING = "pending"
    CLAIMED = "claimed"
    PLAN_APPROVED = "plan_approved"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"


# State transition table for TaskStatus
TASK_TRANSITIONS: Dict[TaskStatus, List[TaskStatus]] = {
    TaskStatus.PENDING: [
        TaskStatus.CLAIMED,
        TaskStatus.BLOCKED,
        TaskStatus.CANCELLED,
    ],
    TaskStatus.CLAIMED: [
        TaskStatus.PLAN_APPROVED,
        TaskStatus.COMPLETED,
        TaskStatus.CANCELLED,
        TaskStatus.BLOCKED,
        TaskStatus.PENDING,
    ],
    TaskStatus.PLAN_APPROVED: [
        TaskStatus.COMPLETED,
        TaskStatus.PENDING,
        TaskStatus.CANCELLED,
    ],
    TaskStatus.BLOCKED: [
        TaskStatus.PENDING,
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
