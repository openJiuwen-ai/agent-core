# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Team Event Types Module

This module defines team event type constants for use with Messager.
Events are published via Messager.publish() with topic_id as event type
and session_id as team_name for team isolation.
"""

from enum import Enum
from typing import (
    Any,
    Dict,
    Optional,
    Type,
)

from pydantic import BaseModel, Field

from openjiuwen.agent_teams.workflow.engine.progress import PhasePlan


class TeamTopic(str, Enum):
    """Topic categories for team event routing."""

    TEAM = "team"
    TASK = "task"
    MESSAGE = "message"

    def build(self, session_id: str, team_name: str) -> str:
        """Build the final topic string.

        Args:
            session_id: The session identifier.
            team_name: The team identifier (human-chosen unique name).

        Returns:
            Topic string in the format "session:{session_id}:team:{team_name}:{topic}".
        """
        return f"session:{session_id}:team:{team_name}:{self.value}"


def swarmflow_human_reply_topic(
    session_id: str, team_name: str, run_id: str | None = None
) -> str:
    """Topic for a real person's reply to a swarmflow human-session turn.

    A dedicated channel (not the shared ``TeamTopic.TEAM``) so the swarmflow run's
    reply subscriber never collides with the leader's team-event subscription on
    the same messager. When ``run_id`` is given, the topic is run-scoped so
    concurrent runs under the same session+team never cross-resolve a reply;
    ``None`` falls back to the legacy session+team scope (single-run safe).
    """
    if run_id:
        return f"session:{session_id}:team:{team_name}:run:{run_id}:swarmflow_human_reply"
    return f"session:{session_id}:team:{team_name}:swarmflow_human_reply"


def format_swarmflow_human_reply_target(
    correlation_id: str, run_id: str | None = None
) -> str:
    """Build the ``HumanAgentMessage.target`` for a swarmflow human reply.

    Legacy (no run_id): ``swarmflow:<correlation_id>`` — corr may contain colons
    (``{phase}:{label}:{turn}``).

    Run-scoped: ``swarmflow:<run_id>:<correlation_id>`` — first colon separates
    run_id (never contains colons) from corr.
    """
    if run_id:
        return f"swarmflow:{run_id}:{correlation_id}"
    return f"swarmflow:{correlation_id}"


def parse_swarmflow_human_reply_target(rest: str) -> tuple[str | None, str]:
    """Parse the body after ``swarmflow:`` into ``(run_id, correlation_id)``.

    Engine correlation ids are ``{phase}:{label}:{turn}`` (two colons). Run-scoped
    targets prepend ``<run_id>:`` (run ids never contain colons). Distinguish by
    colon count in ``rest``:

    * 0 or 2 colons — legacy, entire ``rest`` is the correlation id.
    * 1 or ≥3 colons — run-scoped, ``split(":", 1)``.
    """
    colon_count = rest.count(":")
    if colon_count == 1 or colon_count >= 3:
        run_id, corr = rest.split(":", 1)
        return run_id, corr
    return None, rest


class TeamEvent:
    """Team event types for cross-process communication

    These events are published via Messager.publish() where:
    - event_type is used as topic_id
    - team_name is used as session_id for team isolation
    """

    # Team lifecycle events
    CREATED = "team_created"
    CLEANED = "team_cleaned"
    STANDBY = "team_standby"
    TEAM_COMPLETED = "team_completed"

    # Member lifecycle events
    MEMBER_SPAWNED = "member_spawned"
    MEMBER_RESTARTED = "member_restarted"
    MEMBER_STATUS_CHANGED = "member_status_changed"
    MEMBER_EXECUTION_CHANGED = "member_execution_changed"
    MEMBER_SHUTDOWN = "member_shutdown"
    MEMBER_CANCELED = "member_canceled"

    # Collaboration events
    PLAN_APPROVAL = "plan_approval"
    TOOL_APPROVAL_RESULT = "tool_approval_result"

    # Reliability events
    ANOMALY_DETECTED = "anomaly_detected"

    # Messaging events
    MESSAGE = "message"
    BROADCAST = "broadcast"

    # Task events
    TASK_CREATED = "task_created"
    TASK_PLAN_REQUEST = "task_plan_request"
    TASK_PLAN_RESPONSE = "task_plan_response"
    TASK_UPDATED = "task_updated"
    TASK_CLAIMED = "task_claimed"
    TASK_STARTED = "task_started"
    TASK_COMPLETED = "task_completed"
    TASK_CANCELLED = "task_cancelled"
    TASK_UNBLOCKED = "task_unblocked"
    TASK_RELEASED = "task_released"
    TASK_REVOKED = "task_revoked"
    TASK_LIST_DRAINED = "task_list_drained"
    # Verify gate (F_59): author submits for review, reviewer passes / fails
    TASK_SUBMITTED_FOR_REVIEW = "task_submitted_for_review"
    TASK_VERIFIED = "task_verified"
    TASK_REVISION_REQUESTED = "task_revision_requested"
    # Review voting (F_62): a reviewer recorded a vote; verdict pending
    TASK_REVIEW_VOTE = "task_review_vote"

    # Swarmflow orchestration progress (a swarmflow run feeding the spectator leader)
    WORKFLOW_PROGRESS = "workflow_progress"
    # A real person's reply to a swarmflow human-session turn (routed in via interact)
    WORKFLOW_HUMAN_REPLY = "workflow_human_reply"

    # Worktree events
    WORKTREE_CREATED = "worktree_created"
    WORKTREE_REMOVED = "worktree_removed"

    # Workspace events
    WORKSPACE_ARTIFACT_UPDATED = "workspace_artifact_updated"
    WORKSPACE_CONFLICT = "workspace_conflict"
    WORKSPACE_LOCK_REQUEST = "workspace_lock_request"
    WORKSPACE_LOCK_RESPONSE = "workspace_lock_response"


# ============== Event Message Schemas ==============
# These schemas are used for Messager.publish() messages
class BaseEventMessage(BaseModel):
    """Base class for all team event messages

    All events include team_name for routing and tracking purposes.
    member_name is optional — present on member-scoped events.
    """
    team_name: str = Field(..., description="Team identifier for event routing")
    member_name: Optional[str] = Field(default=None, description="Member identifier, present on member-scoped events")


class TeamCreatedEvent(BaseEventMessage):
    """Event published when a team is created"""
    display_name: str = Field(..., description="Team display label")
    leader_member_name: str = Field(..., description="Leader member name")
    created: int = Field(..., description="Creation timestamp")


class TeamCleanedEvent(BaseEventMessage):
    """Event published when a team is cleaned up"""
    pass


class TeamStandbyEvent(BaseEventMessage):
    """Event published when a persistent team enters standby between rounds."""
    pass


class TeamCompletedEvent(BaseEventMessage):
    """Event published when the whole team has reached a completed state.

    All three conditions hold at once: every task is terminal, every member
    (including the leader) is in a settled status, and no direct
    (point-to-point) message is left unread by any member. Broadcast
    messages are excluded from the unread check. Team-scoped — member_name
    stays at its default None.
    """
    member_count: int = Field(..., description="Total team member count at completion time")
    task_count: int = Field(..., description="Total task count at completion time")


class MemberSpawnedEvent(BaseEventMessage):
    """Event published when a team member is spawned"""


class MemberRestartedEvent(BaseEventMessage):
    """Event published when a member process is restarted after failure"""
    reason: str = Field(default="health_check_failure", description="Reason for restart")
    restart_count: int = Field(default=1, description="How many times this member has been restarted")


class MemberStatusChangedEvent(BaseEventMessage):
    """Event published when a member's status changes"""
    old_status: str = Field(..., description="Previous status")
    new_status: str = Field(..., description="New status")


class MemberExecutionChangedEvent(BaseEventMessage):
    """Event published when a member's execution status changes"""
    old_status: str = Field(..., description="Previous execution status")
    new_status: str = Field(..., description="New execution status")


class MemberShutdownEvent(BaseEventMessage):
    """Event published when a member is shut down"""
    force: bool = Field(..., description="Force member shut down")


class MemberCanceledEvent(BaseEventMessage):
    """Event published when a member is canceled"""


class PlanApprovalEvent(BaseEventMessage):
    """Event published when a plan is approved/rejected"""
    approved: bool = Field(..., description="Whether the plan was approved")


class ToolApprovalResultEvent(BaseEventMessage):
    """Event published when leader approves or rejects one tool call."""
    tool_call_id: str = Field(..., description="Interrupted tool call ID")
    approved: bool = Field(..., description="Whether the tool call was approved")
    feedback: str = Field(default="", description="Leader feedback for the teammate")
    auto_confirm: bool = Field(default=False, description="Whether to auto-confirm future same-name tool calls")


class MessageEvent(BaseEventMessage):
    """Event published when a point-to-point message is sent"""
    message_id: str = Field(..., description="Message unique identifier")
    from_member_name: str = Field(..., description="Sender member name")
    to_member_name: str = Field(..., description="Receiver member name")


class BroadcastEvent(BaseEventMessage):
    """Event published when a broadcast message is sent"""
    message_id: str = Field(..., description="Message unique identifier")
    from_member_name: str = Field(..., description="Sender member name")


class TaskCreatedEvent(BaseEventMessage):
    """Event published when a task is created"""
    task_id: str = Field(..., description="Task unique identifier")
    status: str = Field(..., description="Initial task status")


class TaskPlanRequestEvent(BaseEventMessage):
    """Event published when a member submits an execution plan for approval."""
    task_id: str = Field(..., description="Task unique identifier")
    status: str = Field(default="planning", description="Task status after submission")
    plan_id: Optional[str] = Field(default=None, description="Member plan submission identifier")
    member_plan_md: Optional[str] = Field(default=None, description="Path to submitted member plan")
    tool_call_id: str = Field(default="", description="submit_plan tool call ID when available")


class TaskPlanResponseEvent(BaseEventMessage):
    """Event published when the leader approves or rejects a member execution plan."""
    task_id: str = Field(..., description="Task unique identifier")
    approved: bool = Field(..., description="Whether the member plan was approved")
    status: str = Field(..., description="Task status after approval decision")
    plan_id: Optional[str] = Field(default=None, description="Member plan submission identifier")
    feedback: str = Field(default="", description="Leader feedback")
    tool_call_id: str = Field(default="", description="submit_plan tool call ID when available")


class TaskUpdatedEvent(BaseEventMessage):
    """Event published when a task is updated"""
    task_id: str = Field(..., description="Task unique identifier")


class TaskClaimedEvent(BaseEventMessage):
    """Event published when a task is claimed by a member"""
    task_id: str = Field(..., description="Task unique identifier")


class TaskStartedEvent(BaseEventMessage):
    """Event published when a scheduled task begins execution.

    Distinct from ``TaskClaimedEvent`` (ownership/assignment): in scheduled
    dispatch a task is assigned at PENDING and only later moves to IN_PROGRESS
    when the scheduler dispatches it to the assignee.
    """
    task_id: str = Field(..., description="Task unique identifier")


class TaskCompletedEvent(BaseEventMessage):
    """Event published when a task is completed"""
    task_id: str = Field(..., description="Task unique identifier")


class TaskCancelledEvent(BaseEventMessage):
    """Event published when a task is cancelled"""
    task_id: str = Field(..., description="Task unique identifier")


class TaskUnblockedEvent(BaseEventMessage):
    """Event published when a task becomes unblocked"""
    task_id: str = Field(..., description="Task unique identifier")


class TaskReleasedEvent(BaseEventMessage):
    """Event published when a claimed task is reset back to pending.

    Fired by ``TeamTaskManager.reset`` when a member's claim is released
    (member cancellation / leader reassignment). The task re-enters the
    claimable pool, so idle teammates are nudged the same way they are
    for a ``TASK_UNBLOCKED`` event.
    """
    task_id: str = Field(..., description="Task unique identifier")


class TaskRevokedEvent(BaseEventMessage):
    """Event published when a member's claimed task is reassigned away.

    Fired by ``TeamTaskManager.reassign`` and carries the *former*
    assignee in ``member_name``. Distinct from ``TASK_RELEASED`` (which
    tells idle teammates the task re-entered the claimable pool): this is
    a targeted notice to the member who lost the task, so its dispatcher
    can steer that member off the now-foreign work. The new assignee is
    notified separately via ``TASK_CLAIMED``.
    """
    task_id: str = Field(..., description="Task unique identifier")


class TaskSubmittedForReviewEvent(BaseEventMessage):
    """Event published when an author submits a task for verification.

    Fired by ``TeamTaskManager.complete`` when the completed task carries
    reviewers (``IN_PROGRESS -> IN_REVIEW``). ``member_name`` is the author.
    The framework dispatches / notifies the reviewers listed in ``reviewer``.
    """
    task_id: str = Field(..., description="Task unique identifier")
    reviewer: list[str] = Field(default_factory=list, description="Reviewer member names to notify")


class TaskVerifiedEvent(BaseEventMessage):
    """Event published when a reviewer passes a task (IN_REVIEW -> COMPLETED).

    ``member_name`` is the author (the task's assignee), so the completion
    unblocks downstream tasks the same way a direct completion does.
    """
    task_id: str = Field(..., description="Task unique identifier")


class TaskRevisionRequestedEvent(BaseEventMessage):
    """Event published when a reviewer fails a task (IN_REVIEW -> IN_PROGRESS).

    Rework loop: ``member_name`` is the author, who still holds the task and is
    steered back to revise it; ``feedback`` carries the reviewer's guidance.
    """
    task_id: str = Field(..., description="Task unique identifier")
    feedback: str = Field(default="", description="Reviewer feedback directing the rework")


class TaskReviewVoteEvent(BaseEventMessage):
    """Event published when a reviewer records a vote (scheduled dispatch).

    Under scheduled dispatch ``verify_task`` only persists the vote — the
    task stays ``IN_REVIEW`` and the leader-side scheduler tallies votes and
    settles the verdict. ``member_name`` is the author (consistent with the
    other verify-gate events); ``reviewer`` is the voter. The counts snapshot
    the tally after this vote so observers need no extra read.
    """
    task_id: str = Field(..., description="Task unique identifier")
    reviewer: str = Field(..., description="Member who cast this vote")
    decision: str = Field(..., description="Vote decision: pass or fail")
    review_round: int = Field(..., description="Review round the vote belongs to")
    pass_count: int = Field(..., description="Distinct reviewers currently voting pass in this round")
    fail_count: int = Field(..., description="Distinct reviewers currently voting fail in this round")
    reviewer_count: int = Field(..., description="Total reviewers assigned to the task")


class TaskListDrainedEvent(BaseEventMessage):
    """Event published when every task in the team task list is terminal.

    Fired only when at least one task exists and all tasks are in a terminal
    status (completed / cancelled). Team-scoped — member_name stays at its
    default None.
    """
    task_count: int = Field(..., description="Total number of tasks in the all-terminal task list")


class WorkflowProgressTeamEvent(BaseEventMessage):
    """Published as a swarmflow run emits progress; consumed by the leader.

    A single event type carries every progress kind (discriminated by ``kind``,
    the engine's ``ProgressKind`` string value) so one handler method renders
    all of them. The spectator leader narrates these to the user — it does not
    drive the workflow. ``team_name`` routes the event on the team topic;
    ``member_name`` stays None (the run is team-scoped, not member-scoped).
    """

    kind: str = Field(..., description="Progress kind: workflow_started / phase / "
                                       "agent_started / agent_completed / agent_failed / "
                                       "workflow_completed / workflow_failed / "
                                       "log / ...")
    run_id: Optional[str] = Field(
        default=None, description="Unique run identifier, set by SwarmflowTool for all events of one run"
    )
    workflow_name: Optional[str] = Field(default=None, description="The swarmflow script's META name")
    description: Optional[str] = Field(default=None, description="The swarmflow script's META description")
    phase: Optional[str] = Field(default=None, description="Current phase title, when applicable")
    label: Optional[str] = Field(default=None, description="Agent call label, on agent_* kinds")
    prompt: Optional[str] = Field(default=None, description="Rendered agent prompt, on agent_started")
    model: Optional[str] = Field(default=None, description="Model hint for the agent call, on agent_started")
    outcome: Optional[str] = Field(default=None, description="Short result preview, on agent_completed")
    text: Optional[str] = Field(default=None, description="Free narration text, on all kinds")
    phases: Optional[list[PhasePlan]] = Field(
        default=None, description="Static phase plan from META, on workflow_started"
    )
    correlation_id: Optional[str] = Field(
        default=None,
        description="Precomputed session-turn id ({phase}:{label}:{turn}), on AGENT_STARTED for "
                    "agent_session / human_session / human turns (NOT plain agent(), which keys "
                    "only on agent_id); on human_prompt / human_replied to route a pending human "
                    "turn's reply.",
    )
    node_type: Optional[str] = Field(
        default=None,
        description="Exact primitive type on agent_started: agent / agent_session / human / human_session. "
                    "Sole source of node kind: consumer derives kind=human if "
                    "node_type in {human, human_session}, else agent (None defaults to agent).",
    )
    agent_id: Optional[str] = Field(
        default=None, description="Deterministic resume-stable per-node id, on agent_*"
    )
    answer: Optional[str] = Field(
        default=None, description="Person's raw reply text, on human_replied"
    )


class WorktreeCreatedEvent(BaseEventMessage):
    """Published when a worktree is created or recovered."""
    worktree_name: str = Field(..., description="Worktree slug name")
    worktree_path: str = Field(..., description="Absolute path to worktree")
    existed: bool = Field(default=False, description="True if recovered from existing worktree")


class WorktreeRemovedEvent(BaseEventMessage):
    """Published when a worktree is removed."""
    worktree_name: str = Field(..., description="Worktree slug name")
    worktree_path: str = Field(..., description="Absolute path to worktree")


class WorkspaceArtifactEvent(BaseEventMessage):
    """Published when a workspace artifact is created or updated."""
    artifact_path: str = Field(..., description="Relative path within workspace")
    commit_sha: str | None = Field(default=None, description="Git commit SHA if versioned")


class WorkspaceConflictEvent(BaseEventMessage):
    """Published when a merge conflict or push failure is detected."""
    file_path: str = Field(..., description="Conflicting file path")
    conflicting_commit: str | None = Field(default=None, description="Conflicting commit SHA")


class WorkspaceLockRequestEvent(BaseEventMessage):
    """Lock request sent from remote node to leader."""
    action: str = Field(..., description="Lock action: 'acquire' or 'release'")
    file_path: str = Field(..., description="File to lock/unlock")
    holder_name: str | None = Field(default=None, description="Name of lock requester")
    timeout_seconds: int | None = Field(default=None, description="Lock timeout in seconds")


class WorkspaceLockResponseEvent(BaseEventMessage):
    """Lock response from leader to requesting node."""
    file_path: str = Field(..., description="File that was locked/unlocked")
    granted: bool = Field(..., description="Whether the lock was granted")
    holder: dict | None = Field(default=None, description="Current lock holder info if not granted")


class AnomalyDetectedEvent(BaseEventMessage):
    """Published when a reliability detector flags an unhealthy member state.

    Member-scoped: ``member_name`` (from BaseEventMessage) is the affected
    member. Carried across processes so the leader's reliability handler can
    route it through the remediation policy. ``kind`` and ``severity`` are the
    string values of ``AnomalyKind`` / ``Severity`` so this schema stays
    independent of the reliability package.
    """
    detector: str = Field(..., description="Detector identifier")
    kind: str = Field(..., description="AnomalyKind value")
    severity: str = Field(..., description="Severity value")
    summary: str = Field(..., description="One-line description for human/LLM")
    evidence: Dict[str, Any] = Field(default_factory=dict, description="Supporting evidence snapshot")
    peer_member: Optional[str] = Field(default=None, description="Peer member for team-level anomalies")


_EVENT_TYPE_MAP: Dict[str, Type[BaseEventMessage]] = {  # event_type -> model class
    TeamEvent.CREATED: TeamCreatedEvent,
    TeamEvent.CLEANED: TeamCleanedEvent,
    TeamEvent.STANDBY: TeamStandbyEvent,
    TeamEvent.TEAM_COMPLETED: TeamCompletedEvent,
    TeamEvent.MEMBER_SPAWNED: MemberSpawnedEvent,
    TeamEvent.MEMBER_RESTARTED: MemberRestartedEvent,
    TeamEvent.MEMBER_STATUS_CHANGED: MemberStatusChangedEvent,
    TeamEvent.MEMBER_EXECUTION_CHANGED: MemberExecutionChangedEvent,
    TeamEvent.MEMBER_SHUTDOWN: MemberShutdownEvent,
    TeamEvent.MEMBER_CANCELED: MemberCanceledEvent,
    TeamEvent.PLAN_APPROVAL: PlanApprovalEvent,
    TeamEvent.TOOL_APPROVAL_RESULT: ToolApprovalResultEvent,
    TeamEvent.MESSAGE: MessageEvent,
    TeamEvent.BROADCAST: BroadcastEvent,
    TeamEvent.TASK_CREATED: TaskCreatedEvent,
    TeamEvent.TASK_PLAN_REQUEST: TaskPlanRequestEvent,
    TeamEvent.TASK_PLAN_RESPONSE: TaskPlanResponseEvent,
    TeamEvent.TASK_UPDATED: TaskUpdatedEvent,
    TeamEvent.TASK_CLAIMED: TaskClaimedEvent,
    TeamEvent.TASK_STARTED: TaskStartedEvent,
    TeamEvent.TASK_COMPLETED: TaskCompletedEvent,
    TeamEvent.TASK_CANCELLED: TaskCancelledEvent,
    TeamEvent.TASK_UNBLOCKED: TaskUnblockedEvent,
    TeamEvent.TASK_RELEASED: TaskReleasedEvent,
    TeamEvent.TASK_REVOKED: TaskRevokedEvent,
    TeamEvent.TASK_SUBMITTED_FOR_REVIEW: TaskSubmittedForReviewEvent,
    TeamEvent.TASK_VERIFIED: TaskVerifiedEvent,
    TeamEvent.TASK_REVISION_REQUESTED: TaskRevisionRequestedEvent,
    TeamEvent.TASK_REVIEW_VOTE: TaskReviewVoteEvent,
    TeamEvent.TASK_LIST_DRAINED: TaskListDrainedEvent,
    TeamEvent.WORKFLOW_PROGRESS: WorkflowProgressTeamEvent,
    TeamEvent.WORKTREE_CREATED: WorktreeCreatedEvent,
    TeamEvent.WORKTREE_REMOVED: WorktreeRemovedEvent,
    TeamEvent.WORKSPACE_ARTIFACT_UPDATED: WorkspaceArtifactEvent,
    TeamEvent.WORKSPACE_CONFLICT: WorkspaceConflictEvent,
    TeamEvent.WORKSPACE_LOCK_REQUEST: WorkspaceLockRequestEvent,
    TeamEvent.WORKSPACE_LOCK_RESPONSE: WorkspaceLockResponseEvent,
    TeamEvent.ANOMALY_DETECTED: AnomalyDetectedEvent,
}

_EVENT_CLASS_MAP: Dict[Type[BaseEventMessage], str] = {  # model class -> event_type
    v: k for k, v in _EVENT_TYPE_MAP.items()
}


class EventMessage(BaseModel):
    """Wrapper that pairs a TeamEvent type with its event payload."""

    event_type: str = Field(..., description="Event type from TeamEvent constants")
    payload: Dict[str, Any] = Field(..., description="Raw event payload data")
    sender_id: str = Field(default="", description="Node ID of the sender, used to filter self-published messages")

    @classmethod
    def from_event(cls, event: BaseEventMessage) -> "EventMessage":
        """Construct an EventMessage from a concrete BaseEventMessage instance.

        Args:
            event: A concrete BaseEventMessage subclass instance.

        Raises:
            ValueError: If the event class is not recognized.
        """
        event_type = _EVENT_CLASS_MAP.get(type(event))
        if event_type is None:
            raise ValueError(f"Unknown event class: {type(event).__name__}")
        return cls(event_type=event_type, payload=event.model_dump())

    def get_payload(self) -> BaseEventMessage:
        """Deserialize payload to the concrete BaseEventMessage subclass based on event_type.

        Returns:
            The typed event message instance.

        Raises:
            ValueError: If event_type is not recognized.
        """
        cls = _EVENT_TYPE_MAP.get(self.event_type)
        if cls is None:
            raise ValueError(f"Unknown event_type: {self.event_type}")
        return cls.model_validate(self.payload)

    def serialize(self) -> bytes:
        """Serialize to UTF-8 encoded JSON bytes."""
        return self.model_dump_json().encode("utf-8")

    @classmethod
    def deserialize(cls, data: bytes) -> "EventMessage":
        """Deserialize from UTF-8 encoded JSON bytes.

        Args:
            data: UTF-8 encoded JSON bytes.
        """
        return cls.model_validate_json(data.decode("utf-8"))
