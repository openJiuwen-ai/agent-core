# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Team SQLModel definitions and dynamic model factories.

Static tables: Team, TeamMember.
Dynamic (per-session) tables: TeamTask, TeamTaskDependency, TeamMessage, MessageReadStatus.
Configuration: DatabaseType, DatabaseConfig.
"""

import copy
from typing import Dict, Optional, cast

from sqlmodel import SQLModel, Field
from sqlmodel.main import SQLModelMetaclass

from openjiuwen.agent_teams.spawn.context import get_session_id


# ----------------- Static Table Models -----------------

class Team(SQLModel, table=True):
    """Team info table model"""
    __tablename__ = "team_info"

    team_name: str = Field(primary_key=True)
    display_name: str = Field(nullable=False)
    leader_member_name: str = Field(nullable=False)
    desc: Optional[str] = Field(default=None, nullable=True)
    prompt: Optional[str] = Field(default=None, nullable=True)
    created: int = Field(nullable=False)
    # Bumped on every roster-affecting write so consumers (e.g. TeamRail
    # prompt cache) can probe a single column for change detection.
    updated_at: Optional[int] = Field(default=None, nullable=True)


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
    prompt: Optional[str] = Field(default=None, nullable=True)
    model_config_json: Optional[str] = Field(default=None, nullable=True)
    # Set on roster mutations only (create_member).  Status / execution
    # status updates intentionally do NOT bump this column because they
    # do not change how the # 成员关系 prompt section is rendered.
    updated_at: Optional[int] = Field(default=None, nullable=True)


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
    team_name: str = Field(nullable=False, foreign_key="team_info.team_name", ondelete="CASCADE", index=True)
    title: str = Field(nullable=False)
    content: str = Field(nullable=False)
    status: str = Field(nullable=False, index=True)
    assignee: Optional[str] = Field(default=None, nullable=True, index=True)
    updated_at: Optional[int] = Field(default=None, nullable=True, index=True)

    def brief(self) -> dict:
        """Return a lightweight summary (id + title + status) for write-op responses."""
        return {"task_id": self.task_id, "title": self.title, "status": self.status}


class TeamTaskDependencyBase(SQLModel):
    """Base class for task dependency tables (one per session)"""
    __abstract__ = True

    team_name: str = Field(nullable=False, foreign_key="team_info.team_name", ondelete="CASCADE", index=True)
    resolved: Optional[bool] = Field(default=False, nullable=True, index=True)


class TeamMessageBase(SQLModel):
    """Base class for team message table (one per session)"""
    __abstract__ = True

    message_id: str = Field(primary_key=True)
    team_name: str = Field(nullable=False, foreign_key="team_info.team_name", ondelete="CASCADE", index=True)
    from_member_name: str = Field(nullable=False)
    to_member_name: Optional[str] = Field(default=None, nullable=True, index=True)
    content: str = Field(nullable=False)
    timestamp: int = Field(nullable=False, index=True)
    broadcast: bool = Field(nullable=False, index=True)
    # Read state for direct (point-to-point) messages only.  Broadcast rows
    # carry NULL here because per-recipient read state for broadcasts lives
    # in MessageReadStatus (high-water mark by timestamp); a single bool on
    # the message row cannot represent "read by A, unread by B".  Writers
    # must enforce this — see ``create_message``.
    is_read: Optional[bool] = Field(default=False, nullable=True, index=True)


class MessageReadStatusBase(SQLModel):
    """Base class for message read status table (one per session)

    Tracks which broadcast message each member has read up to.
    Each member has one record per team, storing the timestamp of the broadcast message they have read.
    """
    __abstract__ = True

    member_name: str = Field(primary_key=True)
    team_name: str = Field(primary_key=True, foreign_key="team_info.team_name", ondelete="CASCADE")
    read_at: Optional[int] = Field(default=None, nullable=True, index=True)


# ============== Dynamic Model Caches & Factories ==============

_task_models: Dict[str, type[TeamTaskBase]] = {}
_task_dependency_models: Dict[str, type[TeamTaskDependencyBase]] = {}
_message_models: Dict[str, type[TeamMessageBase]] = {}
_message_read_status_models: Dict[str, type[MessageReadStatusBase]] = {}


def _get_task_model() -> type[TeamTaskBase]:
    """Get or create dynamic task model for current session"""
    session_id = get_session_id()
    if session_id not in _task_models:
        class_name = f"TeamTask_{session_id}"
        table_name = f"team_task_{session_id}"

        attrs = {
            "__tablename__": table_name
        }

        model_cls = SQLModelMetaclass(class_name, (TeamTaskBase,), attrs, table=True)

        _task_models[session_id] = cast(type[TeamTaskBase], model_cls)

    return _task_models[session_id]


def _get_task_dependency_model() -> type[TeamTaskDependencyBase]:
    """Get or create dynamic task dependency model for current session"""
    session_id = get_session_id()
    if session_id not in _task_dependency_models:
        class_name = f"TeamTaskDependency_{session_id}"
        table_name = f"team_task_dependency_{session_id}"

        attrs = {
            "__tablename__": table_name,
            "__annotations__": {}
        }

        attrs["__annotations__"]["task_id"] = str
        attrs["task_id"] = Field(nullable=False, foreign_key=f"team_task_{session_id}.task_id", ondelete="CASCADE",
                                 primary_key=True)

        attrs["__annotations__"]["depends_on_task_id"] = str
        attrs["depends_on_task_id"] = Field(nullable=False, foreign_key=f"team_task_{session_id}.task_id",
                                            ondelete="CASCADE", primary_key=True)

        model_cls = SQLModelMetaclass(class_name, (TeamTaskDependencyBase,), attrs, table=True)

        _task_dependency_models[session_id] = cast(type[TeamTaskDependencyBase], model_cls)

    return _task_dependency_models[session_id]


def _get_message_model() -> type[TeamMessageBase]:
    """Get or create dynamic message model for current session"""
    session_id = get_session_id()
    if session_id not in _message_models:
        class_name = f"TeamMessage_{session_id}"
        table_name = f"team_message_{session_id}"

        attrs = {
            "__tablename__": table_name
        }

        model_cls = SQLModelMetaclass(class_name, (TeamMessageBase,), attrs, table=True)

        _message_models[session_id] = cast(type[TeamMessageBase], model_cls)

    return _message_models[session_id]


def _get_message_read_status_model() -> type[MessageReadStatusBase]:
    """Get or create dynamic message read status model for current session"""
    session_id = get_session_id()
    if session_id not in _message_read_status_models:
        class_name = f"MessageReadStatus_{session_id}"
        table_name = f"message_read_status_{session_id}"

        attrs = {
            "__tablename__": table_name,
            "__annotations__": dict(MessageReadStatusBase.__annotations__),
        }
        for field_name, field_info in MessageReadStatusBase.model_fields.items():
            attrs[field_name] = copy.deepcopy(field_info)

        model_cls = SQLModelMetaclass(class_name, (MessageReadStatusBase,), attrs, table=True)

        _message_read_status_models[session_id] = cast(type[MessageReadStatusBase], model_cls)

    return _message_read_status_models[session_id]
