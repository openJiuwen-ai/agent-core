# coding: utf-8
"""Tests for the read-only historical trajectory conversion boundary."""

from __future__ import annotations

import pytest

from openjiuwen.agent_evolving.trajectory.legacy import is_legacy_record, upgrade_legacy_record
from openjiuwen.agent_evolving.trajectory.model import Trajectory
from openjiuwen.agent_evolving.trajectory.schema import (
    MEMBER_ID,
    SESSION_ID,
    TEAM_ID,
    TRAJECTORY_ID,
    TRAJECTORY_SOURCE,
)
from openjiuwen.agent_evolving.trajectory.spans import (
    iter_spans,
    read_llm_messages,
    read_rl_fields,
    read_span_error,
    read_tool_call,
    read_usage,
)


def test_upgrade_legacy_steps_returns_canonical_trajectory() -> None:
    trajectory = upgrade_legacy_record(
        {
            "execution_id": "legacy-1",
            "source": "offline",
            "session_id": "session-1",
            "steps": [
                {
                    "kind": "llm",
                    "detail": {
                        "model": "gpt-test",
                        "messages": [{"role": "user", "content": "hello"}],
                        "response": {"role": "assistant", "content": "hi"},
                    },
                }
            ],
        }
    )

    assert isinstance(trajectory, Trajectory)
    assert trajectory.trajectory_id == "legacy-1"
    assert trajectory.session_id == "session-1"
    assert trajectory.to_otlp()["resourceSpans"]


def test_upgrade_legacy_otlp_aliases_is_single_directional() -> None:
    trajectory = upgrade_legacy_record(
        {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": [
                            {"key": "openjiuwen.trajectory.id", "value": {"stringValue": "old-id"}},
                            {"key": "openjiuwen.session.id", "value": {"stringValue": "old-session"}},
                        ]
                    },
                    "scopeSpans": [],
                }
            ]
        }
    )

    attributes = trajectory.to_otlp()["resourceSpans"][0]["resource"]["attributes"]
    keys = {item["key"] for item in attributes}
    assert trajectory.trajectory_id == "old-id"
    assert trajectory.session_id == "old-session"
    assert TRAJECTORY_ID in keys
    assert SESSION_ID in keys
    assert "openjiuwen.trajectory.id" not in keys
    assert "openjiuwen.session.id" not in keys


@pytest.mark.parametrize(
    ("alias", "canonical"),
    [
        ("openjiuwen.trajectory.id", TRAJECTORY_ID),
        ("openjiuwen.session_id", SESSION_ID),
        ("openjiuwen.session.id", SESSION_ID),
        ("openjiuwen.team.id", TEAM_ID),
        ("openjiuwen.member.id", MEMBER_ID),
        ("session.id", SESSION_ID),
        ("session_id", SESSION_ID),
        ("team_id", TEAM_ID),
        ("member_id", MEMBER_ID),
        ("source", TRAJECTORY_SOURCE),
    ],
)
def test_is_legacy_record_detects_every_resource_alias(alias: str, canonical: str) -> None:
    attributes = [{"key": alias, "value": {"stringValue": "old-value"}}]
    if canonical != TRAJECTORY_ID:
        attributes.append({"key": TRAJECTORY_ID, "value": {"stringValue": "trajectory-id"}})
    record = {
        "resourceSpans": [
            {
                "resource": {"attributes": attributes},
                "scopeSpans": [],
            }
        ]
    }

    assert is_legacy_record(record)
    upgraded = upgrade_legacy_record(record)
    assert upgraded.resource_attributes[canonical] == "old-value"
    assert alias not in upgraded.resource_attributes


def test_upgrade_legacy_record_requires_trajectory_id() -> None:
    record = {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [{"key": "source", "value": {"stringValue": "legacy-source"}}]
                },
                "scopeSpans": [],
            }
        ]
    }

    with pytest.raises(ValueError, match="trajectory_id"):
        upgrade_legacy_record(record)


def test_upgrade_legacy_otlp_aliases_gives_canonical_values_precedence() -> None:
    record = {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        {"key": TRAJECTORY_ID, "value": {"stringValue": "canonical-id"}},
                        {"key": "openjiuwen.trajectory.id", "value": {"stringValue": "old-id"}},
                        {"key": SESSION_ID, "value": {"stringValue": "canonical-session"}},
                        {"key": "session.id", "value": {"stringValue": "old-session"}},
                    ]
                },
                "scopeSpans": [],
            }
        ]
    }

    upgraded = upgrade_legacy_record(record)
    assert upgraded.trajectory_id == "canonical-id"
    assert upgraded.session_id == "canonical-session"
    keys = set(upgraded.resource_attributes)
    assert "openjiuwen.trajectory.id" not in keys
    assert "session.id" not in keys


def test_upgrade_legacy_mapping_attributes_gives_canonical_values_precedence() -> None:
    record = {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": {
                        TRAJECTORY_ID: {"stringValue": "canonical-id"},
                        "openjiuwen.trajectory.id": {"stringValue": "old-id"},
                        SESSION_ID: {"stringValue": "canonical-session"},
                        "session_id": {"stringValue": "old-session"},
                    }
                },
                "scopeSpans": [],
            }
        ]
    }

    assert is_legacy_record(record)
    upgraded = upgrade_legacy_record(record)
    assert upgraded.trajectory_id == "canonical-id"
    assert upgraded.session_id == "canonical-session"
    assert set(upgraded.resource_attributes).isdisjoint({"openjiuwen.trajectory.id", "session_id"})


def test_upgrade_legacy_steps_preserves_canonical_consumer_fields() -> None:
    trajectory = upgrade_legacy_record(
        {
            "execution_id": "legacy-fields",
            "steps": [
                {
                    "kind": "llm",
                    "detail": {
                        "model": "gpt-test",
                        "messages": [{"role": "user", "content": "hello"}],
                        "response": {"role": "assistant", "content": "hi"},
                        "usage": {"prompt_tokens": 2, "completion_tokens": 1},
                    },
                    "prompt_token_ids": [1, 2],
                    "completion_token_ids": [3],
                    "logprobs": [-0.1],
                },
                {
                    "kind": "tool",
                    "detail": {
                        "tool_name": "search",
                        "tool_call_id": "call-1",
                        "call_args": {"query": "hello"},
                        "call_result": {"answer": "hi"},
                    },
                    "error": "tool failed",
                },
            ],
        }
    )

    llm_span, tool_span = list(iter_spans(trajectory))
    assert read_llm_messages(llm_span) == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]
    assert read_usage(llm_span) == {"prompt_tokens": 2, "completion_tokens": 1}
    assert read_rl_fields(llm_span) == {
        "prompt_token_ids": [1, 2],
        "completion_token_ids": [3],
        "logprobs": [-0.1],
    }
    assert read_tool_call(tool_span) == {
        "name": "search",
        "id": "call-1",
        "input": {"query": "hello"},
        "output": {"answer": "hi"},
        "error": {"status": "STATUS_CODE_ERROR", "message": "tool failed"},
    }
    assert read_span_error(tool_span) == {
        "status": "STATUS_CODE_ERROR",
        "message": "tool failed",
    }
