# coding: utf-8
"""Canonical trajectory Store contract tests."""

from __future__ import annotations

import json

import pytest

from openjiuwen.agent_evolving.trajectory.model import Trajectory
from openjiuwen.agent_evolving.trajectory.schema import (
    CASE_ID,
    MEMBER_ID,
    SESSION_ID,
    TEAM_ID,
    TRAJECTORY_ID,
    TRAJECTORY_SCHEMA_VERSION,
    TRAJECTORY_SCHEMA_VERSION_ATTR,
    TRAJECTORY_SOURCE,
)
from openjiuwen.agent_evolving.trajectory.spans import attributes_from_map
from openjiuwen.agent_evolving.trajectory.store import FileTrajectoryStore, InMemoryTrajectoryStore


def _trajectory(
    trajectory_id: str,
    *,
    session_id: str = "session-1",
    team_id: str | None = None,
    member_id: str | None = None,
    case_id: str | None = None,
    source: str = "offline",
) -> Trajectory:
    attributes = {
        TRAJECTORY_ID: trajectory_id,
        TRAJECTORY_SCHEMA_VERSION_ATTR: TRAJECTORY_SCHEMA_VERSION,
        SESSION_ID: session_id,
        TRAJECTORY_SOURCE: source,
    }
    if team_id is not None:
        attributes[TEAM_ID] = team_id
    if member_id is not None:
        attributes[MEMBER_ID] = member_id
    if case_id is not None:
        attributes[CASE_ID] = case_id
    return Trajectory.from_otlp(
        {
            "resourceSpans": [
                {
                    "resource": {"attributes": attributes_from_map(attributes)},
                    "scopeSpans": [
                        {
                            "scope": {"name": "test"},
                            "spans": [
                                {
                                    "traceId": "1" * 32,
                                    "spanId": trajectory_id.encode().hex()[:16].ljust(16, "0"),
                                    "name": "llm.call",
                                }
                            ],
                        }
                    ],
                }
            ]
        }
    )


@pytest.mark.parametrize("store_factory", [InMemoryTrajectoryStore, pytest.param(lambda: None, id="file")])
def test_store_round_trip_and_stable_filters(store_factory, tmp_path) -> None:
    store = FileTrajectoryStore(tmp_path) if store_factory() is None else store_factory()
    first = _trajectory("first", team_id="team-a", case_id="case-a", source="online")
    second = _trajectory("second", member_id="member-b", case_id="case-b")
    store.save(first)
    store.save(second)

    loaded = store.load("first")
    assert loaded is not None
    assert loaded.to_otlp() == first.to_otlp()
    assert [item.trajectory_id for item in store.query()] == ["first", "second"]
    assert [item.trajectory_id for item in store.query(session_id="session-1", team_id="team-a")] == ["first"]
    assert [item.trajectory_id for item in store.query(member_id="member-b", case_id="case-b")] == ["second"]
    assert [item.trajectory_id for item in store.query(source="online")] == ["first"]


@pytest.mark.parametrize("store_factory", [InMemoryTrajectoryStore, pytest.param(lambda: None, id="file")])
def test_store_versions_are_isolated(store_factory, tmp_path) -> None:
    store = FileTrajectoryStore(tmp_path) if store_factory() is None else store_factory()
    store.save(_trajectory("v1"), version="one")
    store.save(_trajectory("v2"), version="two")

    assert store.load("v1", version="two") is None
    assert [item.trajectory_id for item in store.query(version="one")] == ["v1"]
    assert [item.trajectory_id for item in store.query(version="two")] == ["v2"]


@pytest.mark.parametrize("version", ["", ".", "..", "a/b", "a\\b"])
def test_store_rejects_unsafe_version(version, tmp_path) -> None:
    for store in (InMemoryTrajectoryStore(), FileTrajectoryStore(tmp_path)):
        with pytest.raises(ValueError, match="version"):
            store.query(version=version)


def test_store_rejects_untyped_trajectory_payload(tmp_path) -> None:
    for store in (InMemoryTrajectoryStore(), FileTrajectoryStore(tmp_path)):
        with pytest.raises(TypeError, match="canonical"):
            store.save({"resourceSpans": []})  # type: ignore[arg-type]


@pytest.mark.parametrize("store_factory", [InMemoryTrajectoryStore, pytest.param(lambda: None, id="file")])
def test_store_upgrades_legacy_otlp_wrapper_to_canonical(store_factory, tmp_path) -> None:
    from openjiuwen.agent_evolving.trajectory.types import trajectory_from_steps

    store = FileTrajectoryStore(tmp_path) if store_factory() is None else store_factory()
    legacy = trajectory_from_steps(
        execution_id="legacy-wrapper",
        steps=[],
        session_id="session-legacy",
    )

    store.save(legacy)

    loaded = store.load("legacy-wrapper")
    assert isinstance(loaded, Trajectory)
    assert loaded.session_id == "session-legacy"


def test_store_rejects_legacy_wrapper_without_otlp_payload(tmp_path) -> None:
    from openjiuwen.agent_evolving.trajectory.types import Trajectory as LegacyTrajectory

    for store in (InMemoryTrajectoryStore(), FileTrajectoryStore(tmp_path)):
        with pytest.raises(ValueError, match="OTLP payload"):
            store.save(LegacyTrajectory())


def test_store_metadata_reads_only_canonical_source() -> None:
    trajectory = Trajectory.from_otlp(
        {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": attributes_from_map(
                            {
                                TRAJECTORY_ID: "legacy-source",
                                SESSION_ID: "session-1",
                                "source": "legacy-online",
                            }
                        )
                    },
                    "scopeSpans": [],
                }
            ]
        }
    )
    store = InMemoryTrajectoryStore()

    store.save(trajectory)

    assert store.query(source="legacy-online") == []
    assert [item.trajectory_id for item in store.query(source="offline")] == ["legacy-source"]


def test_file_store_loads_oldest_duplicate_and_skips_invalid_records(tmp_path) -> None:
    store = FileTrajectoryStore(tmp_path)
    first = _trajectory("same", case_id="first")
    second = _trajectory("same", case_id="second")
    store.save(first)
    path = tmp_path / "trajectories_default.jsonl"
    with path.open("a", encoding="utf-8") as stream:
        stream.write("not-json\n")
    store.save(second)

    loaded = store.load("same")
    assert loaded is not None
    assert loaded.resource_attributes[CASE_ID] == "first"
    assert [item.resource_attributes[CASE_ID] for item in store.query()] == ["first", "second"]


def test_file_store_upgrades_all_historical_resource_aliases(tmp_path) -> None:
    path = tmp_path / "trajectories_default.jsonl"
    path.write_text(
        json.dumps(
            {
                "resourceSpans": [
                    {
                        "resource": {
                            "attributes": [
                                {"key": "openjiuwen.trajectory.id", "value": {"stringValue": "alias-id"}},
                                {"key": "session.id", "value": {"stringValue": "alias-session"}},
                                {"key": "team_id", "value": {"stringValue": "alias-team"}},
                                {"key": "source", "value": {"stringValue": "alias-source"}},
                            ]
                        },
                        "scopeSpans": [],
                    }
                ]
            }
        )
        + "\n",
        encoding="utf-8",
    )
    store = FileTrajectoryStore(tmp_path)

    loaded = store.load("alias-id")
    assert loaded is not None
    assert loaded.session_id == "alias-session"
    assert loaded.team_id == "alias-team"
    assert loaded.resource_attributes[TRAJECTORY_SOURCE] == "alias-source"


def test_file_store_upgrades_historical_jsonl_on_read(tmp_path) -> None:
    path = tmp_path / "trajectories_default.jsonl"
    path.write_text(
        json.dumps(
            {
                "execution_id": "legacy-id",
                "session_id": "legacy-session",
                "steps": [
                    {
                        "kind": "llm",
                        "detail": {
                            "messages": [{"role": "user", "content": "hello"}],
                            "response": {"role": "assistant", "content": "hi"},
                        },
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    store = FileTrajectoryStore(tmp_path)

    loaded = store.load("legacy-id")
    assert loaded is not None
    assert loaded.session_id == "legacy-session"
    assert loaded.trajectory_id == "legacy-id"


def test_file_store_writes_only_canonical_otlp(tmp_path) -> None:
    store = FileTrajectoryStore(tmp_path)
    store.save(_trajectory("canonical"))

    record = json.loads((tmp_path / "trajectories_default.jsonl").read_text(encoding="utf-8"))
    assert set(record) == {"resourceSpans"}
    assert "steps" not in record


def test_file_store_logs_model_dump_failure_before_string_fallback(tmp_path, caplog) -> None:
    class BrokenModel:
        def model_dump(self) -> dict[str, object]:
            raise RuntimeError("dump failed")

        def __str__(self) -> str:
            return "fallback-value"

    payload = _trajectory("fallback").to_otlp()
    payload["resourceSpans"][0]["custom"] = BrokenModel()
    store = FileTrajectoryStore(tmp_path)

    store.save(Trajectory.from_otlp(payload))

    record = json.loads((tmp_path / "trajectories_default.jsonl").read_text(encoding="utf-8"))
    assert record["resourceSpans"][0]["custom"] == "fallback-value"
    assert "model_dump" in caplog.text
