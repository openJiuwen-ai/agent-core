# coding: utf-8

from openjiuwen.agent_teams.organization.schema import (
    ORG_SUMMARY_CAPABILITY,
    ORG_SUMMARY_TASK_TYPE,
    ORG_STATIC_TABLE_NAMES,
    OrgSummaryExecution,
    OrgSummaryExecutionStatus,
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


def test_org_task_status_includes_waiting_sources_and_terminals():
    assert OrgTaskStatus.WAITING_SOURCES.value == "WAITING_SOURCES"
    assert ORG_TASK_TERMINAL_STATUS_VALUES == (
        OrgTaskStatus.COMPLETED.value,
        OrgTaskStatus.FAILED.value,
    )
    assert OrgTaskStatus.WAITING_SOURCES.value not in ORG_TASK_TERMINAL_STATUS_VALUES
    assert "CANCELLED" not in OrgTaskStatus.__members__
    assert "EXPIRED" not in OrgTaskStatus.__members__
    assert OrgTaskFailureCode.CANCELLED.value == "CANCELLED"
    assert OrgTaskFailureCode.EXPIRED.value == "EXPIRED"
    assert OrgTaskFailureCode.SUMMARY_PROVISION_FAILED.value == "SUMMARY_PROVISION_FAILED"


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


def test_summary_execution_model_and_static_table_registration():
    assert ORG_SUMMARY_TASK_TYPE == "organization.summary"
    assert ORG_SUMMARY_CAPABILITY == "summary"
    assert "org_summary_execution" in ORG_STATIC_TABLE_NAMES
    execution = OrgSummaryExecution(
        execution_id="exec-1",
        organization_id="org-1",
        root_task_id="root-1",
        summary_task_id="summary-1",
        summary_team_id=None,
        status=OrgSummaryExecutionStatus.PROVISIONING,
        created_at=10,
    )
    assert execution.status is OrgSummaryExecutionStatus.PROVISIONING
    assert set(OrgSummaryExecutionStatus) == {
        OrgSummaryExecutionStatus.PROVISIONING,
        OrgSummaryExecutionStatus.WAITING_SOURCES,
        OrgSummaryExecutionStatus.RUNNING,
        OrgSummaryExecutionStatus.COMPLETED,
        OrgSummaryExecutionStatus.FAILED,
        OrgSummaryExecutionStatus.RELEASED,
    }
