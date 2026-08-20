# coding: utf-8
"""Tests for the canonical immutable trajectory model."""

from __future__ import annotations

import pytest

from openjiuwen.agent_evolving.trajectory.model import Trajectory
from openjiuwen.agent_evolving.trajectory.schema import (
    MEMBER_ID,
    SESSION_ID,
    TEAM_ID,
    TRAJECTORY_ID,
)


def _attribute(key: str, value: str) -> dict[str, object]:
    return {"key": key, "value": {"stringValue": value}}


def _payload() -> dict[str, object]:
    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        _attribute(TRAJECTORY_ID, "trajectory-1"),
                        _attribute(SESSION_ID, "session-1"),
                        _attribute(TEAM_ID, "team-1"),
                        _attribute(MEMBER_ID, "member-1"),
                    ]
                },
                "scopeSpans": [],
            }
        ]
    }


def test_from_otlp_owns_payload_and_round_trips_without_aliasing() -> None:
    payload = _payload()
    trajectory = Trajectory.from_otlp(payload)

    payload["resourceSpans"][0]["resource"]["attributes"].append(_attribute("changed", "yes"))
    exported = trajectory.to_otlp()
    exported["resourceSpans"][0]["resource"]["attributes"].append(_attribute("changed-again", "yes"))

    assert trajectory.trajectory_id == "trajectory-1"
    assert trajectory.session_id == "session-1"
    assert trajectory.team_id == "team-1"
    assert trajectory.member_id == "member-1"
    assert "changed" not in {item["key"] for item in trajectory.to_otlp()["resourceSpans"][0]["resource"]["attributes"]}
    assert "changed-again" not in {
        item["key"] for item in trajectory.to_otlp()["resourceSpans"][0]["resource"]["attributes"]
    }


def test_trajectory_is_read_only() -> None:
    trajectory = Trajectory.from_otlp(_payload())

    with pytest.raises((AttributeError, TypeError)):
        trajectory.trajectory_id = "changed"  # type: ignore[misc]
    with pytest.raises(AttributeError, match="immutable"):
        del trajectory.trajectory_id


@pytest.mark.parametrize("payload", [{}, {"resourceSpans": []}, {"resourceSpans": None}])
def test_from_otlp_rejects_invalid_payload(payload: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        Trajectory.from_otlp(payload)


@pytest.mark.parametrize("trajectory_id", [None, ""])
def test_from_otlp_requires_non_empty_trajectory_id(trajectory_id: str | None) -> None:
    payload = _payload()
    attributes = payload["resourceSpans"][0]["resource"]["attributes"]
    trajectory_attribute = next(item for item in attributes if item["key"] == TRAJECTORY_ID)
    if trajectory_id is None:
        attributes.remove(trajectory_attribute)
    else:
        trajectory_attribute["value"] = {"stringValue": trajectory_id}

    with pytest.raises(ValueError, match="trajectory_id"):
        Trajectory.from_otlp(payload)


def test_trajectory_root_exports_only_canonical_contract() -> None:
    import openjiuwen.agent_evolving.trajectory as trajectory

    assert set(trajectory.__all__) == {
        "Trajectory",
        "TrajectorySpanProcessor",
        "TrajectoryStore",
        "InMemoryTrajectoryStore",
        "FileTrajectoryStore",
    }
    assert not hasattr(trajectory, "TrajectoryStep")
    assert not hasattr(trajectory, "TrajectoryBuilder")
    assert not hasattr(Trajectory.from_otlp(_payload()), "otlp_trace")


@pytest.mark.parametrize(
    ("name", "hint"),
    [
        ("TrajectoryStep", "canonical Trajectory spans"),
        ("TrajectoryBuilder", "trajectory.offline.TrajectoryBuilder"),
        ("TrajectoryExtractor", "trajectory.offline.TrajectoryExtractor"),
        ("TracerTrajectoryExtractor", "trajectory.offline.TrajectoryExtractor"),
        ("UpdateKey", "agent_evolving.types.UpdateKey"),
        ("Updates", "agent_evolving.types.Updates"),
    ],
)
def test_removed_trajectory_exports_provide_migration_hints(name: str, hint: str) -> None:
    import openjiuwen.agent_evolving.trajectory as trajectory

    with pytest.raises(AttributeError, match=hint):
        getattr(trajectory, name)


def test_unknown_trajectory_export_uses_standard_attribute_error() -> None:
    import openjiuwen.agent_evolving.trajectory as trajectory

    with pytest.raises(AttributeError, match="has no attribute 'unknown'"):
        getattr(trajectory, "unknown")


def test_schema_exposes_canonical_and_rl_fields_only() -> None:
    import openjiuwen.agent_evolving.trajectory.schema as schema

    removed = {
        "TRAJECTORY_END_REASON",
        "TRAJECTORY_PARENT_ID",
        "TRAJECTORY_TASK_HASH",
        "TRAJECTORY_INCOMPLETE",
        "TRAJECTORY_INVOKE_TYPE",
        "TRAJECTORY_STEP_KIND",
        "TRAJECTORY_TRACE_ID",
        "LEGACY_TRAJECTORY_ID",
        "LEGACY_SESSION_ID",
        "LEGACY_TEAM_ID",
        "LEGACY_MEMBER_ID",
    }
    assert all(not hasattr(schema, name) for name in removed)
    assert removed.isdisjoint(schema.__all__)
    for name in (
        "RL_PROMPT_TOKEN_IDS",
        "RL_COMPLETION_TOKEN_IDS",
        "RL_LOGPROBS",
        "RL_REWARD",
        "RL_FINAL_REWARD",
        "RL_REWARD_SOURCE",
        "RL_ROLLOUT_ID",
        "RL_ATTEMPT_SEQ",
        "RL_TOKEN_SOURCE",
    ):
        assert hasattr(schema, name)


def test_migration_semantic_keys_are_isolated_from_current_conventions() -> None:
    from openjiuwen.agent_evolving.trajectory import legacy_semconv
    from openjiuwen.extensions.observability import semconv as observability_semconv

    migration_keys = (
        "LEGACY_GEN_AI_INPUT_MESSAGES",
        "LEGACY_GEN_AI_OUTPUT_MESSAGES",
        "LEGACY_GEN_AI_TOOL_CALL_ID",
        "LEGACY_GEN_AI_TOOL_CALL_ARGUMENTS",
        "LEGACY_GEN_AI_TOOL_CALL_RESULT",
        "LEGACY_GEN_AI_USAGE_INPUT_TOKENS",
        "LEGACY_GEN_AI_USAGE_OUTPUT_TOKENS",
        "LEGACY_TRAJECTORY_STEP_KIND",
        "LEGACY_STEP_META",
    )
    assert all(hasattr(legacy_semconv, name) for name in migration_keys)
    assert all(not hasattr(observability_semconv, name) for name in migration_keys)


def test_schema_team_identity_values_match_observability_without_runtime_dependency() -> None:
    from openjiuwen.extensions.observability import semconv

    assert MEMBER_ID == semconv.AT_MEMBER_ID
    assert SESSION_ID == semconv.AT_SESSION_ID
    assert TEAM_ID == semconv.AT_TEAM_ID


def test_model_rejects_legacy_only_resource_identity() -> None:
    payload = {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        _attribute("openjiuwen.trajectory.id", "legacy-id"),
                        _attribute("openjiuwen.session.id", "legacy-session"),
                        _attribute("openjiuwen.team.id", "legacy-team"),
                        _attribute("openjiuwen.member.id", "legacy-member"),
                    ]
                },
                "scopeSpans": [],
            }
        ]
    }

    with pytest.raises(ValueError, match="trajectory_id"):
        Trajectory.from_otlp(payload)
