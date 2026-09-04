# coding: utf-8

from openjiuwen.agent_teams.organization.schema import (
    OrgTask,
    OrgTaskAggregationConfig,
    OrgTaskAggregationMode,
    OrgTaskCreator,
    OrgTaskFailureCode,
    OrgTaskStatus,
    ORG_TASK_LEGACY_STATUS_FAILURE_CODES,
    ORG_TASK_TERMINAL_STATUS_VALUES,
    default_root_aggregation,
)


def test_default_root_aggregation_is_hierarchical():
    config = default_root_aggregation("root-1")
    assert config.mode == OrgTaskAggregationMode.HIERARCHICAL
    assert config.final_output_task_id == "root-1"
    assert config.summary_task_id is None


def test_org_task_status_terminal_only_completed_failed():
    assert ORG_TASK_TERMINAL_STATUS_VALUES == (
        OrgTaskStatus.COMPLETED.value,
        OrgTaskStatus.FAILED.value,
    )
    assert "WAITING_SOURCES" not in OrgTaskStatus.__members__
    assert "CANCELLED" not in OrgTaskStatus.__members__
    assert "EXPIRED" not in OrgTaskStatus.__members__
    assert OrgTaskFailureCode.CANCELLED.value == "CANCELLED"
    assert OrgTaskFailureCode.EXPIRED.value == "EXPIRED"


def test_org_task_legacy_status_failure_codes_shared_mapping():
    assert ORG_TASK_LEGACY_STATUS_FAILURE_CODES == {
        "CANCELLED": OrgTaskFailureCode.CANCELLED,
        "EXPIRED": OrgTaskFailureCode.EXPIRED,
    }
    for legacy_status in ORG_TASK_LEGACY_STATUS_FAILURE_CODES:
        assert legacy_status not in OrgTaskStatus.__members__


def test_org_task_brief_includes_aggregation_and_failure():
    task = OrgTask(
        task_id="task-1",
        root_task_id="task-1",
        created_by=OrgTaskCreator(
            creator_type="team_leader",
            creator_id="leader-a",
            organization_id="org-1",
            team_id="team-a",
        ),
        status=OrgTaskStatus.FAILED,
        created_at=1,
        updated_at=2,
        title="Root",
        description="Root task",
        aggregation=OrgTaskAggregationConfig(
            mode=OrgTaskAggregationMode.HIERARCHICAL,
            final_output_task_id="task-1",
        ),
        failure_code=OrgTaskFailureCode.EXECUTION_FAILED,
        failure_reason="worker crashed",
        failed_at=2,
    )
    brief = task.brief()
    assert brief["aggregation_mode"] == OrgTaskAggregationMode.HIERARCHICAL
    assert brief["failure_code"] == OrgTaskFailureCode.EXECUTION_FAILED
