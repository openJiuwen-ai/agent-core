# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Organization schemas and DB table models."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import Table
from sqlmodel import Field as SQLField
from sqlmodel import SQLModel

ORG_STATIC_TABLE_NAMES = (
    "org_info",
    "org_leader",
    "org_task",
    "org_leader_message",
    "org_leader_message_receipt",
    "org_task_event",
    "org_task_review",
    "org_task_source",
)


class OrgTaskStatus(StrEnum):
    OPEN = "OPEN"
    DELEGATED = "DELEGATED"
    CLAIMED = "CLAIMED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


ORG_TASK_TERMINAL_STATUS_VALUES = (
    OrgTaskStatus.COMPLETED.value,
    OrgTaskStatus.FAILED.value,
)

# Metadata key written by create_task(repairs_task_id=...); single create entry is the param.
ORG_TASK_REPAIRS_TASK_ID_KEY = "repairs_task_id"
# Optional repair-budget keys on the repaired task's metadata (enforced on create).
ORG_TASK_RETRY_COUNT_KEY = "retry_count"
ORG_TASK_RETRY_LIMIT_KEY = "retry_limit"


class OrgTaskFailureCode(StrEnum):
    EXECUTION_FAILED = "EXECUTION_FAILED"
    SOURCE_FAILED = "SOURCE_FAILED"
    SUMMARY_PROVISION_FAILED = "SUMMARY_PROVISION_FAILED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


# Legacy terminal statuses persisted before FAILED + failure_code. Shared by DB
# migration and read-path projection so both stay on one mapping.
ORG_TASK_LEGACY_STATUS_FAILURE_CODES: dict[str, OrgTaskFailureCode] = {
    "CANCELLED": OrgTaskFailureCode.CANCELLED,
    "EXPIRED": OrgTaskFailureCode.EXPIRED,
}


class OrgTaskAggregationMode(StrEnum):
    HIERARCHICAL = "HIERARCHICAL"
    SUMMARY_TEAM = "SUMMARY_TEAM"


class OrgAssignmentType(StrEnum):
    UNASSIGNED = "unassigned"
    CLAIMED = "claimed"
    DELEGATED = "delegated"


class OrgCreatorType(StrEnum):
    CLIENT = "client"
    TEAM_LEADER = "team_leader"


class OrgTaskReviewStatus(StrEnum):
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    NEEDS_REVISION = "NEEDS_REVISION"


class OrgTaskCreator(BaseModel):
    creator_type: str = "client"
    creator_id: str
    organization_id: str
    team_id: str | None = None


class OrgAssignment(BaseModel):
    assignment_type: OrgAssignmentType = OrgAssignmentType.UNASSIGNED
    team_id: str | None = None
    leader_id: str | None = None
    assigned_by_team_id: str | None = None
    assigned_at: int | None = None


class OrgTaskOutputSpec(BaseModel):
    spec_type: str = "inline"
    spec_uri: str | None = None
    description: str | None = None
    inline_rules: list[str] = Field(default_factory=list)


class OrgTaskOutputContext(BaseModel):
    result_uri: str | None = None
    result_hash: str | None = None
    result_type: str | None = None
    description: str | None = None


class OrgTaskAggregationConfig(BaseModel):
    mode: OrgTaskAggregationMode = OrgTaskAggregationMode.HIERARCHICAL
    summary_task_id: str | None = None
    summary_team_id: str | None = None
    final_output_task_id: str | None = None


class OrgTask(BaseModel):
    task_id: str
    parent_task_id: str | None = None
    root_task_id: str
    created_by: OrgTaskCreator
    status: OrgTaskStatus = OrgTaskStatus.OPEN
    created_at: int
    updated_at: int
    title: str
    description: str
    task_type: str | None = None
    required_capabilities: list[str] = Field(default_factory=list)
    assignment: OrgAssignment = Field(default_factory=OrgAssignment)
    aggregation: OrgTaskAggregationConfig | None = None
    output_spec: OrgTaskOutputSpec | None = None
    output_context: OrgTaskOutputContext | None = None
    output_abstract: str | None = None
    failure_code: OrgTaskFailureCode | None = None
    failure_reason: str | None = None
    failed_at: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def brief(self) -> dict[str, Any]:
        payload = {
            "task_id": self.task_id,
            "parent_task_id": self.parent_task_id,
            "root_task_id": self.root_task_id,
            "status": self.status,
            "title": self.title,
            "task_type": self.task_type,
            "required_capabilities": self.required_capabilities,
            "assignment": self.assignment.model_dump(),
            "updated_at": self.updated_at,
        }
        if self.aggregation is not None:
            payload["aggregation_mode"] = self.aggregation.mode
        if self.failure_code is not None:
            payload["failure_code"] = self.failure_code
        return payload


class OrgTaskReview(BaseModel):
    review_id: str
    task_id: str
    reviewer_team_id: str
    review_status: OrgTaskReviewStatus = OrgTaskReviewStatus.PENDING
    verdict: str | None = None
    required_changes: list[str] = Field(default_factory=list)
    created_at: int
    updated_at: int


class OrgTaskSource(BaseModel):
    summary_task_id: str
    source_task_id: str
    source_role: str | None = None
    required: bool = True
    created_at: int


class OrgLeaderHandle(BaseModel):
    organization_id: str
    team_id: str
    leader_id: str
    leader_member_name: str | None = None
    capabilities: list[str] = Field(default_factory=list)


class OrganizationSpec(BaseModel):
    organization_id: str
    display_name: str | None = None
    description: str | None = None
    owner_team_id: str | None = None
    owner_leader_id: str | None = None
    leaders: list[OrgLeaderHandle] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class OrgInfoRecord(SQLModel, table=True):
    __tablename__ = "org_info"

    organization_id: str = SQLField(primary_key=True)
    display_name: str | None = None
    description: str | None = None
    metadata_json: str | None = None
    created_at: int
    updated_at: int


class OrgLeaderRecord(SQLModel, table=True):
    __tablename__ = "org_leader"

    organization_id: str = SQLField(primary_key=True)
    team_id: str = SQLField(primary_key=True)
    leader_id: str = SQLField(primary_key=True)
    leader_member_name: str | None = None
    capabilities_json: str | None = None
    created_at: int
    updated_at: int


class OrgTaskRecord(SQLModel, table=True):
    __tablename__ = "org_task"

    task_id: str = SQLField(primary_key=True)
    organization_id: str = SQLField(index=True)
    parent_task_id: str | None = SQLField(default=None, index=True)
    root_task_id: str = SQLField(index=True)
    creator_type: str
    creator_id: str
    creator_team_id: str | None = None
    status: str = SQLField(index=True)
    created_at: int
    updated_at: int
    title: str
    description: str
    task_type: str | None = SQLField(default=None, index=True)
    required_capabilities_json: str | None = None
    assignment_type: str = SQLField(default=OrgAssignmentType.UNASSIGNED.value, index=True)
    assigned_team_id: str | None = SQLField(default=None, index=True)
    assigned_leader_id: str | None = None
    assigned_by_team_id: str | None = None
    assigned_at: int | None = None
    aggregation_json: str | None = None
    output_spec_json: str | None = None
    output_context_json: str | None = None
    output_abstract: str | None = None
    failure_code: str | None = None
    failure_reason: str | None = None
    failed_at: int | None = None
    metadata_json: str | None = None


class OrgLeaderMessageRecord(SQLModel, table=True):
    __tablename__ = "org_leader_message"

    message_id: str = SQLField(primary_key=True)
    organization_id: str = SQLField(index=True)
    from_team_id: str = SQLField(index=True)
    from_leader_id: str
    to_team_id: str | None = SQLField(default=None, index=True)
    to_leader_id: str | None = None
    content: str
    created_at: int
    metadata_json: str | None = None


class OrgLeaderMessageReceiptRecord(SQLModel, table=True):
    __tablename__ = "org_leader_message_receipt"

    message_id: str = SQLField(primary_key=True)
    recipient_team_id: str = SQLField(primary_key=True)
    organization_id: str = SQLField(index=True)
    recipient_leader_id: str | None = None
    handled_at: int | None = SQLField(default=None, index=True)
    handling_result_json: str | None = None
    created_at: int


class OrgTaskEventRecord(SQLModel, table=True):
    __tablename__ = "org_task_event"

    event_id: str = SQLField(primary_key=True)
    organization_id: str = SQLField(index=True)
    event_type: str = SQLField(index=True)
    task_id: str | None = SQLField(default=None, index=True)
    team_id: str | None = SQLField(default=None, index=True)
    leader_id: str | None = None
    payload_json: str | None = None
    created_at: int


class OrgTaskReviewRecord(SQLModel, table=True):
    __tablename__ = "org_task_review"

    review_id: str = SQLField(primary_key=True)
    task_id: str = SQLField(index=True)
    reviewer_team_id: str = SQLField(index=True)
    review_status: str = SQLField(index=True)
    verdict: str | None = None
    required_changes_json: str | None = None
    created_at: int
    updated_at: int


class OrgTaskSourceRecord(SQLModel, table=True):
    __tablename__ = "org_task_source"

    summary_task_id: str = SQLField(primary_key=True)
    source_task_id: str = SQLField(primary_key=True)
    source_role: str | None = None
    required: bool = True
    created_at: int


def default_root_aggregation(task_id: str) -> OrgTaskAggregationConfig:
    """Return the default HIERARCHICAL aggregation config for a root task."""

    return OrgTaskAggregationConfig(
        mode=OrgTaskAggregationMode.HIERARCHICAL,
        final_output_task_id=task_id,
    )


def org_static_tables() -> list[Table]:
    """Return organization static tables from the global SQLModel registry."""
    return [
        table
        for name, table in SQLModel.metadata.tables.items()
        if name in ORG_STATIC_TABLE_NAMES
    ]


__all__ = [
    "ORG_STATIC_TABLE_NAMES",
    "ORG_TASK_LEGACY_STATUS_FAILURE_CODES",
    "ORG_TASK_TERMINAL_STATUS_VALUES",
    "OrgAssignment",
    "OrgAssignmentType",
    "OrgCreatorType",
    "OrgInfoRecord",
    "OrgLeaderHandle",
    "OrgLeaderMessageRecord",
    "OrgLeaderMessageReceiptRecord",
    "OrgLeaderRecord",
    "OrganizationSpec",
    "OrgTask",
    "OrgTaskAggregationConfig",
    "OrgTaskAggregationMode",
    "OrgTaskCreator",
    "OrgTaskEventRecord",
    "OrgTaskFailureCode",
    "OrgTaskOutputContext",
    "OrgTaskOutputSpec",
    "OrgTaskRecord",
    "OrgTaskReview",
    "OrgTaskReviewRecord",
    "OrgTaskReviewStatus",
    "OrgTaskSource",
    "OrgTaskSourceRecord",
    "OrgTaskStatus",
    "default_root_aggregation",
    "org_static_tables",
]
