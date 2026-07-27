# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Team SQLModel definitions and dynamic model factories.

Static tables: Team, TeamMember.
Dynamic (per-session) tables: TeamTask, TeamTaskDependency, TeamMessage,
MessageReadStatus, TeamTaskReviewVote.
Configuration: DatabaseType, DatabaseConfig.
"""

import copy
import hashlib
import json
from typing import Dict, Optional, cast

from sqlalchemy import BigInteger, Index
from sqlmodel import SQLModel, Field
from sqlmodel.main import SQLModelMetaclass

from openjiuwen.agent_teams.context import get_session_id

TEAM_DYNAMIC_TABLE_PREFIXES = (
    "team_task_dependency_",
    "team_task_",
    "team_message_",
    "message_read_status_",
    "team_review_vote_",
)
TEAM_STATIC_TABLES_TO_CLEAR = (
    "team_info",
    "team_member",
)


# ----------------- Static Table Models -----------------

class Team(SQLModel, table=True):
    """Team info table model"""
    __tablename__ = "team_info"

    team_name: str = Field(primary_key=True)
    display_name: str = Field(nullable=False)
    leader_member_name: str = Field(nullable=False)
    desc: Optional[str] = Field(default=None, nullable=True)
    prompt: Optional[str] = Field(default=None, nullable=True)
    # Effective dispatch mode chosen by the leader at build_team time
    # ("autonomous" / "scheduled", F_62). Persisted so cold recovery — which
    # rebuilds the leader backend from the spec ceiling — restores the same
    # per-instance choice (scheduler activation, verify_task policy).
    dispatch_mode: str = Field(default="autonomous", nullable=False)
    # Effective "verification expected" flag chosen at build_team time (F_62).
    enable_task_verification: bool = Field(default=False, nullable=False)
    created: int = Field(sa_type=BigInteger, nullable=False)
    # Bumped on every roster-affecting write so consumers (e.g.
    # TeamPolicyRail prompt cache) can probe a single column for change
    # detection.
    updated_at: Optional[int] = Field(default=None, sa_type=BigInteger, nullable=True)


class TeamMember(SQLModel, table=True):
    """Team member table model"""
    __tablename__ = "team_member"

    member_name: str = Field(primary_key=True)
    team_name: str = Field(primary_key=True, foreign_key="team_info.team_name", ondelete="CASCADE")
    display_name: str = Field(nullable=False)
    desc: Optional[str] = Field(default=None, nullable=True)
    agent_card: str = Field(nullable=False)
    status: str = Field(nullable=False)
    execution_status: Optional[str] = Field(default=None, nullable=True)
    mode: str = Field(nullable=False)
    # Member role, persisted so cold-recovery can rebuild the right
    # runtime (tools / rails / prompt sections) without inferring from
    # leader-process memory. Stores the ``TeamRole`` enum value
    # (``leader`` / ``teammate`` / ``human_agent``). Hard-coded to the
    # ``teammate`` string literal rather than ``TeamRole.TEAMMATE.value``
    # because ``schema.team`` already imports this module's parent
    # subtree — pulling ``TeamRole`` in here closes a circular import.
    # Keep in sync with ``TeamRole.TEAMMATE`` if that enum value ever
    # changes. Older DB files created before this column existed get
    # the same backfilled default via the schema-migration step in
    # ``database/engine.py``.
    role: str = Field(nullable=False, default="teammate")
    prompt: Optional[str] = Field(default=None, nullable=True)
    options: Optional[str] = Field(
        default=None,
        nullable=True,
        description=(
            "JSON object for extensible member configuration. "
            "Current shape: {model_ref: {model_name, model_index}, "
            "worktree: {isolation, path}, permissions_override: {bash: deny, ...}}"
        ),
    )
    # Set on roster mutations only (create_member).  Status / execution
    # status updates intentionally do NOT bump this column because they
    # do not change how the # 成员关系 prompt section is rendered.
    updated_at: Optional[int] = Field(default=None, sa_type=BigInteger, nullable=True)


# ============== Dynamic Table Base Classes (abstract) ==============

class TeamTaskBase(SQLModel):
    """Base class for task tables (one per session).

    ``updated_at`` stores the millisecond wall-clock timestamp of the last
    status transition (create, claim, assign, reset, approve_plan, cancel,
    complete, or unblock). Its semantic meaning is bound to the current
    ``status``: e.g. when status=claimed it represents when the task was
    claimed; when status=completed it represents the completion time. Pure
    title/content edits do not bump this column — it tracks the state
    lifecycle, not arbitrary writes.
    """
    __abstract__ = True

    task_id: str = Field(primary_key=True)
    # No index on team_name: these are per-session physical tables, so
    # team_name cardinality is ~1 and a secondary index never helps a read
    # while it costs a B-tree write on every INSERT (A1). The FK stays for
    # cascade semantics.
    team_name: str = Field(nullable=False, foreign_key="team_info.team_name", ondelete="CASCADE")
    title: str = Field(nullable=False)
    content: str = Field(nullable=False)
    # status keeps a standalone index (get_team_tasks / recovery sweep /
    # cancel_all filter by status alone). assignee folds into the composite
    # (assignee, status) injected in _get_task_model (D4) — get_tasks_by_assignee
    # queries assignee=? AND status=?. updated_at drops its index: no query ever
    # filters or orders by it (it is a returned field only), so the index was
    # pure write overhead bumped on every status transition.
    status: str = Field(nullable=False, index=True)
    assignee: Optional[str] = Field(default=None, nullable=True)
    # Verify-gate reviewers: a JSON-encoded list of member names (or NULL). A
    # task carrying reviewers routes through IN_REVIEW on completion. Stored as
    # JSON text rather than a normalized join table because v1 verification is
    # team-scale (small boards, "reviewer contains X" resolved by an in-memory
    # filter over the status-indexed IN_REVIEW rows), and the list also backs
    # the voting mechanism (vote rows live in team_review_vote_*). See F_59/F_62.
    reviewer: Optional[str] = Field(default=None, nullable=True)
    # Review round counter (F_62): bumped atomically with every
    # IN_PROGRESS -> IN_REVIEW flip so votes of a superseded round are
    # naturally void (vote rows record the round they were cast in). 0 means
    # the task never entered review.
    review_round: int = Field(default=0, nullable=False)
    # Per-task review-round ceiling (F_62): when a round beyond this ceiling
    # still fails, the scheduler escalates to the leader instead of looping
    # rework. NULL falls back to ``TeamAgentSpec.default_max_review_rounds``.
    max_review_rounds: Optional[int] = Field(default=None, nullable=True)
    updated_at: Optional[int] = Field(default=None, sa_type=BigInteger, nullable=True)

    def brief(self) -> dict:
        """Return a lightweight summary (id + title + status) for write-op responses."""
        return {"task_id": self.task_id, "title": self.title, "status": self.status}

    def reviewers(self) -> list[str]:
        """Parse the ``reviewer`` JSON column into a member-name list.

        Returns an empty list when unset or malformed — an absent reviewer set
        means "no verify gate", which the completion path treats as direct
        COMPLETED.
        """
        raw = self.reviewer
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            return []
        return [str(name) for name in parsed] if isinstance(parsed, list) else []


class TeamTaskDependencyBase(SQLModel):
    """Base class for task dependency tables (one per session)"""
    __abstract__ = True

    # No index on team_name (A1) — see TeamTaskBase.
    team_name: str = Field(nullable=False, foreign_key="team_info.team_name", ondelete="CASCADE")
    resolved: Optional[bool] = Field(default=False, nullable=True, index=True)


class TeamTaskReviewVoteBase(SQLModel):
    """Base class for review vote tables (one per session). See F_62.

    Append-only vote facts for the verify gate under scheduled dispatch: a
    reviewer changing their mind INSERTs a new row and the tally takes each
    reviewer's latest row (highest ``id``) within the task's current
    ``review_round`` — no UPDATE path, no lost-update window, and the full
    voting history stays auditable. Verdict policy (threshold math) lives in
    the leader-side scheduler, not here.
    """
    __abstract__ = True

    id: Optional[int] = Field(default=None, primary_key=True)
    # No index on team_name (A1) — see TeamTaskBase.
    team_name: str = Field(nullable=False, foreign_key="team_info.team_name", ondelete="CASCADE")
    task_id: str = Field(nullable=False)
    review_round: int = Field(nullable=False)
    reviewer: str = Field(nullable=False)
    decision: str = Field(nullable=False)
    feedback: Optional[str] = Field(default=None, nullable=True)
    created_at: int = Field(sa_type=BigInteger, nullable=False)


class TeamMessageBase(SQLModel):
    """Base class for team message table (one per session)"""
    __abstract__ = True

    message_id: str = Field(primary_key=True)
    # No index on team_name (A1). The four columns below drop their weak
    # single-column indexes in favour of two composite indexes injected per
    # dynamic table in ``_get_message_model`` (A2): a 2-value boolean index
    # (broadcast / is_read) is near-useless standalone, and the real queries
    # want composites — so INSERT B-tree writes go from 6 (PK + 5 secondary)
    # down to 3 (PK + 2 composite). See the composite definitions there.
    team_name: str = Field(nullable=False, foreign_key="team_info.team_name", ondelete="CASCADE")
    from_member_name: str = Field(nullable=False)
    to_member_name: Optional[str] = Field(default=None, nullable=True)
    content: str = Field(nullable=False)
    timestamp: int = Field(sa_type=BigInteger, nullable=False)
    broadcast: bool = Field(nullable=False)
    protocol: str = Field(
        default="plain",
        nullable=False,
        description=(
            "Message format: 'plain' for normal text, 'json' for structured "
            "payloads (e.g. approval results). Used by mailbox drain to "
            "selectively admit interrupt-resolving messages while deferring others."
        ),
    )
    # Read state for direct (point-to-point) messages only.  Broadcast rows
    # carry NULL here because per-recipient read state for broadcasts lives
    # in MessageReadStatus (high-water mark by timestamp); a single bool on
    # the message row cannot represent "read by A, unread by B".  Writers
    # must enforce this — see ``create_message``.
    is_read: Optional[bool] = Field(default=False, nullable=True)
    # Framework-only delivery payload (JSON object, F_63). Three rules keep
    # this from rotting into a junk drawer:
    #   1. meta is the single source of truth for a templated message: its
    #      ``content`` is the empty string, and no second rendition of the
    #      message exists anywhere. A delivery-time expansion failure
    #      synthesizes a fallback line from meta itself, so consumers must
    #      tolerate rows whose content is empty and whose meta is set.
    #   2. framework-only: meta shapes *how a message is rendered at delivery*
    #      and never carries business facts (task truth lives in the task
    #      table, votes in the vote table). The send_message tool does not
    #      expose it.
    #   3. orthogonal to ``protocol``: protocol="json" stays the machine
    #      side-channel (approval payloads bypassing LLM rendering);
    #      templated messages are ordinary "plain" LLM-facing text.
    # Shape: {"template": <prompts/<lang>/<key>.md>, "refs": {"task": id,
    # "member": name}, "params": {<scalar>}}. See ``message_template.py``.
    meta: Optional[str] = Field(default=None, nullable=True)


class MessageReadStatusBase(SQLModel):
    """Base class for message read status table (one per session)

    Tracks which broadcast message each member has read up to.
    Each member has one record per team, storing the timestamp of the broadcast message they have read.
    """
    __abstract__ = True

    member_name: str = Field(primary_key=True)
    team_name: str = Field(primary_key=True, foreign_key="team_info.team_name", ondelete="CASCADE")
    read_at: Optional[int] = Field(default=None, sa_type=BigInteger, nullable=True, index=True)


# ============== Session ID Sanitization ==============

def _sanitize_session_id_for_table(session_id: str) -> str:
    """Return a fixed-length, SQL-safe hex suffix derived from session_id.

    Uses BLAKE2s (digest_size=8 → 16 hex chars) — a general-purpose hash
    designed for non-cryptographic use cases. Faster than SHA-256, FIPS-safe,
    and 64 bits of output is more than sufficient to make collisions negligible
    across any realistic number of concurrent sessions.
    The cache dictionaries still use the raw session_id as their key so
    _clear_session_model_cache remains correct.
    """
    return hashlib.blake2s(session_id.encode(), digest_size=8).hexdigest()


# ============== Dynamic Model Caches & Factories ==============

_task_models: Dict[str, type[TeamTaskBase]] = {}
_task_dependency_models: Dict[str, type[TeamTaskDependencyBase]] = {}
_message_models: Dict[str, type[TeamMessageBase]] = {}
_message_read_status_models: Dict[str, type[MessageReadStatusBase]] = {}
_review_vote_models: Dict[str, type[TeamTaskReviewVoteBase]] = {}


def _get_task_model() -> type[TeamTaskBase]:
    """Get or create dynamic task model for current session"""
    session_id = get_session_id()
    if session_id not in _task_models:
        suffix = _sanitize_session_id_for_table(session_id)
        class_name = f"TeamTask_{suffix}"
        table_name = f"team_task_{suffix}"

        # Composite (assignee, status) for get_tasks_by_assignee
        # (assignee=? AND status=?) — replaces the standalone assignee index
        # (D4). Kept in sync with the migration in ``database/engine.py``.
        attrs = {
            "__tablename__": table_name,
            "__table_args__": (
                Index(f"ix_{table_name}_assignee_status", "assignee", "status"),
            ),
        }

        model_cls = SQLModelMetaclass(class_name, (TeamTaskBase,), attrs, table=True)

        _task_models[session_id] = cast(type[TeamTaskBase], model_cls)

    return _task_models[session_id]


def _get_task_dependency_model() -> type[TeamTaskDependencyBase]:
    """Get or create dynamic task dependency model for current session"""
    session_id = get_session_id()
    if session_id not in _task_dependency_models:
        suffix = _sanitize_session_id_for_table(session_id)
        class_name = f"TeamTaskDependency_{suffix}"
        table_name = f"team_task_dependency_{suffix}"
        task_table_name = f"team_task_{suffix}"

        attrs = {
            "__tablename__": table_name,
            "__annotations__": {}
        }

        attrs["__annotations__"]["task_id"] = str
        attrs["task_id"] = Field(
            nullable=False,
            foreign_key=f"{task_table_name}.task_id",
            ondelete="CASCADE",
            primary_key=True,
        )

        attrs["__annotations__"]["depends_on_task_id"] = str
        attrs["depends_on_task_id"] = Field(
            nullable=False,
            foreign_key=f"{task_table_name}.task_id",
            ondelete="CASCADE",
            primary_key=True,
        )

        model_cls = SQLModelMetaclass(class_name, (TeamTaskDependencyBase,), attrs, table=True)

        _task_dependency_models[session_id] = cast(type[TeamTaskDependencyBase], model_cls)

    return _task_dependency_models[session_id]


def _get_message_model() -> type[TeamMessageBase]:
    """Get or create dynamic message model for current session"""
    session_id = get_session_id()
    if session_id not in _message_models:
        suffix = _sanitize_session_id_for_table(session_id)
        class_name = f"TeamMessage_{suffix}"
        table_name = f"team_message_{suffix}"

        # Two composite indexes (A2), replacing the dropped single-column
        # to_member_name / timestamp / broadcast / is_read indexes. Names carry
        # the table suffix so every per-session table gets its own. Kept in
        # sync with the migration in ``database/engine.py``.
        #   - inbox: get_messages (to_member_name=? AND is_read=? ORDER BY
        #     timestamp) — equality + equality + ordered, no filesort.
        #   - broadcast: get_broadcast_messages / has_unread broadcast branch
        #     (broadcast=1 ORDER BY timestamp) and the has_unread direct probe
        #     (broadcast=0 is the leading-column prefix).
        attrs = {
            "__tablename__": table_name,
            "__table_args__": (
                Index(f"ix_{table_name}_inbox", "to_member_name", "is_read", "timestamp"),
                Index(f"ix_{table_name}_bcast_ts", "broadcast", "timestamp"),
            ),
        }

        model_cls = SQLModelMetaclass(class_name, (TeamMessageBase,), attrs, table=True)

        _message_models[session_id] = cast(type[TeamMessageBase], model_cls)

    return _message_models[session_id]


def _get_message_read_status_model() -> type[MessageReadStatusBase]:
    """Get or create dynamic message read status model for current session"""
    session_id = get_session_id()
    if session_id not in _message_read_status_models:
        suffix = _sanitize_session_id_for_table(session_id)
        class_name = f"MessageReadStatus_{suffix}"
        table_name = f"message_read_status_{suffix}"

        attrs = {
            "__tablename__": table_name,
            "__annotations__": dict(MessageReadStatusBase.__annotations__),
        }
        for field_name, field_info in MessageReadStatusBase.model_fields.items():
            attrs[field_name] = copy.deepcopy(field_info)

        model_cls = SQLModelMetaclass(class_name, (MessageReadStatusBase,), attrs, table=True)

        _message_read_status_models[session_id] = cast(type[MessageReadStatusBase], model_cls)

    return _message_read_status_models[session_id]


def _get_review_vote_model() -> type[TeamTaskReviewVoteBase]:
    """Get or create the dynamic review-vote model for the current session.

    Table name deliberately avoids the ``team_task_`` prefix so the
    task-table migration sweep in ``database/engine.py`` never
    misclassifies it.
    """
    session_id = get_session_id()
    if session_id not in _review_vote_models:
        suffix = _sanitize_session_id_for_table(session_id)
        class_name = f"TeamTaskReviewVote_{suffix}"
        table_name = f"team_review_vote_{suffix}"

        # Composite (task_id, review_round): the only read is "votes of one
        # task's current round" (tally + dispatch bookkeeping), so a single
        # composite serves it and INSERT pays 2 B-tree writes (PK + 1).
        attrs = {
            "__tablename__": table_name,
            "__table_args__": (
                Index(f"ix_{table_name}_task_round", "task_id", "review_round"),
            ),
        }

        model_cls = SQLModelMetaclass(class_name, (TeamTaskReviewVoteBase,), attrs, table=True)

        _review_vote_models[session_id] = cast(type[TeamTaskReviewVoteBase], model_cls)

    return _review_vote_models[session_id]


def _clear_session_model_cache(session_id: str) -> None:
    """Clear cached dynamic models for a session so they are rebuilt on next access."""
    _task_models.pop(session_id, None)
    _task_dependency_models.pop(session_id, None)
    _message_models.pop(session_id, None)
    _message_read_status_models.pop(session_id, None)
    _review_vote_models.pop(session_id, None)
