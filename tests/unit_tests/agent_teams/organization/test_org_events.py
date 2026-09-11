# coding: utf-8

import pytest

from openjiuwen.agent_teams.organization.events import (
    OrgEvent,
    OrgEventMessage,
    OrgSummaryCompletedEvent,
    OrgSummaryProvisionFailedEvent,
    OrgSummaryProvisionedEvent,
    OrgSummarySourceFailedEvent,
    OrgSummarySourcesReadyEvent,
)


@pytest.mark.parametrize(
    ("event", "event_type"),
    [
        (
            OrgSummaryProvisionedEvent(
                organization_id="org-1",
                root_task_id="root-1",
                summary_task_id="summary-1",
                summary_team_id="team-summary-1",
            ),
            OrgEvent.SUMMARY_PROVISIONED,
        ),
        (
            OrgSummaryProvisionFailedEvent(
                organization_id="org-1",
                root_task_id="root-1",
                summary_task_id="summary-1",
                failure_reason="launcher failed",
            ),
            OrgEvent.SUMMARY_PROVISION_FAILED,
        ),
        (
            OrgSummarySourcesReadyEvent(
                organization_id="org-1",
                summary_task_id="summary-1",
            ),
            OrgEvent.SUMMARY_SOURCES_READY,
        ),
        (
            OrgSummarySourceFailedEvent(
                organization_id="org-1",
                summary_task_id="summary-1",
                source_task_id="child-1",
                failure_reason="execution crashed",
            ),
            OrgEvent.SUMMARY_SOURCE_FAILED,
        ),
        (
            OrgSummaryCompletedEvent(
                organization_id="org-1",
                root_task_id="root-1",
                summary_task_id="summary-1",
            ),
            OrgEvent.SUMMARY_COMPLETED,
        ),
    ],
)
def test_summary_lifecycle_events_roundtrip(event, event_type):
    message = OrgEventMessage.from_event(event)
    assert message.event_type == event_type
    restored = message.get_payload()
    assert type(restored) is type(event)
    assert restored.model_dump() == event.model_dump()
