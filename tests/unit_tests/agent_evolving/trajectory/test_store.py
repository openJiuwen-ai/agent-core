# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Tests for TrajectoryStore implementations."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import BaseModel

from openjiuwen.agent_evolving.trajectory.semconv import (
    GEN_AI_INPUT_MESSAGES,
    GEN_AI_OPERATION_NAME,
    GEN_AI_OUTPUT_MESSAGES,
    GEN_AI_REQUEST_MODEL,
    GEN_AI_USAGE_INPUT_TOKENS,
    GEN_AI_USAGE_OUTPUT_TOKENS,
    OJ_SESSION_ID,
    TRAJECTORY_END_REASON,
    TRAJECTORY_ID,
    TRAJECTORY_SCHEMA_VERSION,
    TRAJECTORY_SCHEMA_VERSION_ATTR,
)
from openjiuwen.agent_evolving.trajectory.store import (
    FileTrajectoryStore,
    InMemoryTrajectoryStore,
)
from openjiuwen.agent_evolving.trajectory.types import (
    LegacyTrajectory,
    LLMCallDetail,
    ToolCallDetail,
    Trajectory,
    TrajectoryStep,
    to_legacy_trajectory,
)


def make_step(kind="llm", detail=None, error=None, meta=None):
    """Factory for creating TrajectoryStep."""
    return TrajectoryStep(
        kind=kind,
        error=error,
        detail=detail,
        meta=meta or {},
    )


def make_llm_step(operator_id="op1", messages=None):
    """Factory for creating LLM step with detail."""
    detail = LLMCallDetail(
        model="gpt-4",
        messages=messages or [{"role": "user", "content": "hello"}],
    )
    return make_step(kind="llm", detail=detail, meta={"operator_id": operator_id})


def make_tool_step(tool_name="test_tool", call_args=None, call_result=None):
    """Factory for creating Tool step with detail."""
    detail = ToolCallDetail(
        tool_name=tool_name,
        call_args=call_args,
        call_result=call_result,
    )
    return make_step(kind="tool", detail=detail, meta={"operator_id": tool_name})


def make_trajectory(
    exec_id="exec1",
    session_id="session1",
    source="offline",
    case_id=None,
    steps=None,
):
    """Factory for creating legacy step-view trajectories."""
    return LegacyTrajectory(
        execution_id=exec_id,
        session_id=session_id,
        case_id=case_id,
        steps=steps or [make_step()],
        source=source,
        cost=None,
    )


def otlp_value(value):
    """Build a small OTLP AnyValue for tests."""
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int):
        return {"intValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    if isinstance(value, str):
        return {"stringValue": value}
    if isinstance(value, list):
        return {"arrayValue": {"values": [otlp_value(item) for item in value]}}
    if isinstance(value, dict):
        return {
            "kvlistValue": {
                "values": [
                    {"key": str(key), "value": otlp_value(item)}
                    for key, item in value.items()
                ]
            }
        }
    return {"stringValue": str(value)}


def otlp_attr(key, value):
    return {"key": key, "value": otlp_value(value)}


class TestInMemoryTrajectoryStore:
    """Test InMemoryTrajectoryStore."""

    @staticmethod
    def test_save_and_load():
        """Save and load trajectory."""
        store = InMemoryTrajectoryStore()
        traj = make_trajectory(exec_id="exec1", case_id="case1")

        store.save(traj)
        loaded = store.load("exec1")

        assert loaded is not None
        legacy = to_legacy_trajectory(loaded)
        assert legacy.execution_id == "exec1"
        assert legacy.case_id == "case1"

    @staticmethod
    def test_load_nonexistent():
        """Load non-existent trajectory returns None."""
        store = InMemoryTrajectoryStore()

        result = store.load("nonexistent")

        assert result is None

    @staticmethod
    def test_query_all():
        """Query returns all trajectories."""
        store = InMemoryTrajectoryStore()
        store.save(make_trajectory(exec_id="exec1"))
        store.save(make_trajectory(exec_id="exec2"))

        results = store.query()

        assert len(results) == 2

    @staticmethod
    def test_query_with_filters():
        """Query with filters."""
        store = InMemoryTrajectoryStore()
        store.save(make_trajectory(exec_id="exec1", case_id="case1"))
        store.save(make_trajectory(exec_id="exec2", case_id="case2"))

        results = store.query(case_id="case1")

        assert len(results) == 1
        assert to_legacy_trajectory(results[0]).case_id == "case1"

    @staticmethod
    def test_query_with_source_filter():
        """Query filtering by source."""
        store = InMemoryTrajectoryStore()
        store.save(make_trajectory(exec_id="exec1", source="online"))
        store.save(make_trajectory(exec_id="exec2", source="offline"))

        results = store.query(source="online")

        assert len(results) == 1
        assert to_legacy_trajectory(results[0]).source == "online"

    @staticmethod
    def test_version_isolation():
        """Different versions are isolated."""
        store = InMemoryTrajectoryStore()
        traj1 = make_trajectory(exec_id="exec1")
        traj2 = make_trajectory(exec_id="exec1")

        store.save(traj1, version="v1")
        store.save(traj2, version="v2")

        v1_result = store.load("exec1", version="v1")
        v2_result = store.load("exec1", version="v2")

        # Both should exist independently
        assert v1_result is not None
        assert v2_result is not None

    @staticmethod
    def test_query_empty_store():
        """Query empty store returns empty list."""
        store = InMemoryTrajectoryStore()

        results = store.query()

        assert results == []

    @staticmethod
    def test_overwrite_existing():
        """Saving with same exec_id overwrites."""
        store = InMemoryTrajectoryStore()
        traj1 = make_trajectory(exec_id="exec1", case_id="case1")
        traj2 = make_trajectory(exec_id="exec1", case_id="case2")

        store.save(traj1)
        store.save(traj2)

        loaded = store.load("exec1")
        assert to_legacy_trajectory(loaded).case_id == "case2"


class TestFileTrajectoryStore:
    """Test FileTrajectoryStore."""

    @staticmethod
    @pytest.fixture
    def temp_dir(tmp_path: Path):
        """Create temporary directory."""
        return tmp_path

    @staticmethod
    def test_save_and_load(temp_dir):
        """Save and load trajectory from file."""
        store = FileTrajectoryStore(temp_dir)
        traj = make_trajectory(exec_id="exec1", case_id="case1")

        store.save(traj)
        loaded = store.load("exec1")

        assert loaded is not None
        legacy = to_legacy_trajectory(loaded)
        assert legacy.execution_id == "exec1"
        assert legacy.case_id == "case1"

    @staticmethod
    def test_load_legacy_step_record_returns_current_trajectory(temp_dir):
        """Old step-based JSONL records are adapted to the current Trajectory type."""
        record = {
            "execution_id": "legacy-exec",
            "source": "offline",
            "case_id": "case-old",
            "session_id": "session-old",
            "cost": {"input_tokens": 3, "output_tokens": 5},
            "meta": {"member_id": "member-old"},
            "steps": [
                {
                    "kind": "llm",
                    "detail": {
                        "model": "legacy-model",
                        "messages": [{"role": "user", "content": "old prompt"}],
                        "response": {"role": "assistant", "content": "old answer"},
                        "usage": {"prompt_tokens": 3, "completion_tokens": 5},
                    },
                    "meta": {"operator_id": "legacy-op"},
                }
            ],
        }
        file_path = temp_dir / "trajectories_default.jsonl"
        file_path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
        store = FileTrajectoryStore(temp_dir)

        loaded = store.load("legacy-exec")
        queried = store.query(case_id="case-old")

        assert isinstance(loaded, Trajectory)
        assert not isinstance(loaded, LegacyTrajectory)
        assert loaded.otlp_trace is not None
        legacy = to_legacy_trajectory(loaded)
        assert legacy.execution_id == "legacy-exec"
        assert legacy.case_id == "case-old"
        assert legacy.session_id == "session-old"
        assert legacy.cost == {"input_tokens": 3, "output_tokens": 5}
        assert legacy.meta == {"member_id": "member-old"}
        assert len(queried) == 1
        assert to_legacy_trajectory(queried[0]).execution_id == "legacy-exec"
        assert isinstance(legacy.steps[0].detail, LLMCallDetail)
        assert legacy.steps[0].detail.model == "legacy-model"

    @staticmethod
    def test_load_nonexistent(temp_dir):
        """Load non-existent trajectory returns None."""
        store = FileTrajectoryStore(temp_dir)

        result = store.load("nonexistent")

        assert result is None

    @staticmethod
    def test_query_all(temp_dir):
        """Query returns all trajectories."""
        store = FileTrajectoryStore(temp_dir)
        store.save(make_trajectory(exec_id="exec1"))
        store.save(make_trajectory(exec_id="exec2"))

        results = store.query()

        assert len(results) == 2

    @staticmethod
    def test_query_with_filters(temp_dir):
        """Query with filters."""
        store = FileTrajectoryStore(temp_dir)
        store.save(make_trajectory(exec_id="exec1", case_id="case1"))
        store.save(make_trajectory(exec_id="exec2", case_id="case2"))

        results = store.query(case_id="case1")

        assert len(results) == 1
        assert to_legacy_trajectory(results[0]).case_id == "case1"

    @staticmethod
    def test_version_creates_different_files(temp_dir):
        """Different versions create different files."""
        store = FileTrajectoryStore(temp_dir)
        traj = make_trajectory(exec_id="exec1")

        store.save(traj, version="v1")
        store.save(traj, version="v2")

        assert (temp_dir / "trajectories_v1.jsonl").exists()
        assert (temp_dir / "trajectories_v2.jsonl").exists()

    @staticmethod
    def test_file_format_is_jsonl(temp_dir):
        """File is JSONL format."""
        store = FileTrajectoryStore(temp_dir)
        traj = make_trajectory(exec_id="exec1", case_id="case1")

        store.save(traj)

        file_path = temp_dir / "trajectories_default.jsonl"
        with open(file_path, "r") as f:
            lines = f.readlines()

        assert len(lines) == 1
        data = json.loads(lines[0])
        assert list(data) == ["resourceSpans"]

    @staticmethod
    def test_otlp_trace_is_saved_as_primary_jsonl_payload(temp_dir):
        """Trace-generated trajectories save the OTLP object directly."""
        store = FileTrajectoryStore(temp_dir)
        otlp_trace = {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": [
                            otlp_attr(TRAJECTORY_ID, "traj-1"),
                            otlp_attr(TRAJECTORY_SCHEMA_VERSION_ATTR, TRAJECTORY_SCHEMA_VERSION),
                            otlp_attr(OJ_SESSION_ID, "session1"),
                            otlp_attr(TRAJECTORY_END_REASON, "success"),
                        ]
                    },
                    "scopeSpans": [
                        {
                            "scope": {
                                "name": "openjiuwen.agent_evolving.trajectory",
                                "version": TRAJECTORY_SCHEMA_VERSION,
                            },
                            "spans": [
                                {
                                    "traceId": "0" * 32,
                                    "spanId": "1" * 16,
                                    "name": "llm.call",
                                    "kind": "SPAN_KIND_CLIENT",
                                    "startTimeUnixNano": "1000000",
                                    "endTimeUnixNano": "2000000",
                                    "status": {"code": "STATUS_CODE_OK"},
                                    "attributes": [
                                        otlp_attr(GEN_AI_OPERATION_NAME, "chat"),
                                        otlp_attr(GEN_AI_REQUEST_MODEL, "test-model"),
                                        otlp_attr(
                                            GEN_AI_INPUT_MESSAGES,
                                            [{"role": "user", "content": "hello"}],
                                        ),
                                        otlp_attr(
                                            GEN_AI_OUTPUT_MESSAGES,
                                            [{"role": "assistant", "content": "hi"}],
                                        ),
                                        otlp_attr(GEN_AI_USAGE_INPUT_TOKENS, 2),
                                        otlp_attr(GEN_AI_USAGE_OUTPUT_TOKENS, 3),
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ]
        }
        traj = Trajectory(otlp_trace=otlp_trace)

        store.save(traj)

        file_path = temp_dir / "trajectories_default.jsonl"
        data = json.loads(file_path.read_text(encoding="utf-8").strip())
        assert list(data) == ["resourceSpans"]
        assert "execution_id" not in data
        loaded = store.load("traj-1")
        assert loaded is not None
        legacy = to_legacy_trajectory(loaded)
        assert legacy.execution_id == "traj-1"
        assert legacy.session_id == "session1"
        assert legacy.steps[0].detail.model == "test-model"
        assert legacy.cost == {"input_tokens": 2, "output_tokens": 3}

    @staticmethod
    def test_query_preserves_empty_otlp_resource_spans(temp_dir):
        """OTLP payloads with an empty resourceSpans list are still valid records."""
        record = {"resourceSpans": []}
        file_path = temp_dir / "trajectories_default.jsonl"
        file_path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
        store = FileTrajectoryStore(temp_dir)

        results = store.query()

        assert len(results) == 1
        assert results[0].otlp_trace == record

    @staticmethod
    def test_query_preserves_otlp_without_resource_attributes(temp_dir):
        """OTLP payloads without resource attributes should not be discarded."""
        record = {"resourceSpans": [{"resource": {}, "scopeSpans": []}]}
        file_path = temp_dir / "trajectories_default.jsonl"
        file_path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
        store = FileTrajectoryStore(temp_dir)

        results = store.query()

        assert len(results) == 1
        assert results[0].otlp_trace == record

    @staticmethod
    def test_query_empty_file(temp_dir):
        """Query empty file returns empty list."""
        store = FileTrajectoryStore(temp_dir)

        results = store.query()

        assert results == []

    @staticmethod
    def test_query_nonexistent_file(temp_dir):
        """Query non-existent file returns empty list."""
        store = FileTrajectoryStore(temp_dir)

        results = store.query(version="nonexistent")

        assert results == []

    @staticmethod
    def test_append_to_existing_file(temp_dir):
        """Save appends to existing file."""
        store = FileTrajectoryStore(temp_dir)
        store.save(make_trajectory(exec_id="exec1"))
        store.save(make_trajectory(exec_id="exec2"))

        results = store.query()

        assert len(results) == 2

    @staticmethod
    def test_load_latest_with_duplicate_ids(temp_dir):
        """Load returns first match (earlier saved)."""
        store = FileTrajectoryStore(temp_dir)
        traj1 = make_trajectory(exec_id="exec1", case_id="case1")
        traj2 = make_trajectory(exec_id="exec1", case_id="case2")

        store.save(traj1)
        store.save(traj2)

        loaded = store.load("exec1")

        # Should find the first one
        assert loaded is not None
        assert to_legacy_trajectory(loaded).execution_id == "exec1"

    @staticmethod
    def test_handles_corrupted_json(temp_dir):
        """Query handles corrupted JSON lines gracefully."""
        file_path = temp_dir / "trajectories_default.jsonl"
        with open(file_path, "w") as f:
            f.write('{"valid": "json"}\n')
            f.write("invalid json line\n")
            f.write(
                '{"execution_id": "exec1", "session_id": "s1", '
                '"source": "offline", "steps": []}\n'
            )

        store = FileTrajectoryStore(temp_dir)
        results = store.query()

        # Should skip corrupted line but load valid ones
        assert len(results) == 1

    @staticmethod
    def test_dict_to_trajectory_logs_deserialization_failure():
        """Corrupt records produce a warning instead of silent None."""
        with patch("openjiuwen.agent_evolving.trajectory.store.logger") as mock_logger:
            result = FileTrajectoryStore._dict_to_trajectory({"valid": "json"})

        assert result is None
        mock_logger.warning.assert_called_once()
        assert "Failed to deserialize trajectory record" in mock_logger.warning.call_args.args[0]

    @staticmethod
    def test_query_logs_corrupt_json_line(temp_dir):
        """Corrupt JSONL lines emit a warning during query."""
        file_path = temp_dir / "trajectories_default.jsonl"
        with open(file_path, "w") as f:
            f.write("not-json\n")
            f.write(
                '{"execution_id": "exec1", "session_id": "s1", '
                '"source": "offline", "steps": []}\n'
            )

        store = FileTrajectoryStore(temp_dir)
        with patch("openjiuwen.agent_evolving.trajectory.store.logger") as mock_logger:
            results = store.query()

        assert len(results) == 1
        assert any(
            "Skipping corrupt JSONL line" in str(call.args[0])
            for call in mock_logger.warning.call_args_list
        )

    @staticmethod
    def test_load_logs_deserialization_failure(temp_dir):
        """load() warns when a matched record cannot be deserialized."""
        file_path = temp_dir / "trajectories_default.jsonl"
        file_path.write_text(
            '{"execution_id": "exec-broken", "steps": "not-a-list"}\n',
            encoding="utf-8",
        )
        store = FileTrajectoryStore(temp_dir)

        with patch("openjiuwen.agent_evolving.trajectory.store.logger") as mock_logger:
            loaded = store.load("exec-broken")

        assert loaded is None
        mock_logger.warning.assert_called_once()
        args = mock_logger.warning.call_args.args
        assert "Failed to deserialize trajectory record" in args[0]
        assert "exec-broken" in args

    @staticmethod
    def test_roundtrip_with_llm_step(temp_dir):
        """Roundtrip preserves LLM step data with detail."""
        store = FileTrajectoryStore(temp_dir)
        step = TrajectoryStep(
            kind="llm",
            detail=LLMCallDetail(
                model="gpt-4",
                messages=[{"role": "user", "content": "hello"}],
                response={"role": "assistant", "content": "hi"},
                usage={"prompt_tokens": 10, "completion_tokens": 5},
            ),
            meta={"operator_id": "op1", "span_name": "test_span"},
        )
        traj = LegacyTrajectory(
            execution_id="exec1",
            session_id="session1",
            steps=[step],
        )

        store.save(traj)
        loaded = store.load("exec1")

        legacy = to_legacy_trajectory(loaded)
        assert len(legacy.steps) == 1
        loaded_step = legacy.steps[0]
        assert loaded_step.kind == "llm"
        assert loaded_step.detail is not None
        assert isinstance(loaded_step.detail, LLMCallDetail)
        assert loaded_step.detail.model == "gpt-4"
        assert loaded_step.meta.get("operator_id") == "op1"

    @staticmethod
    def test_roundtrip_preserves_legacy_rl_fields(temp_dir):
        """Saving old step-based trajectories should preserve RL training fields."""
        store = FileTrajectoryStore(temp_dir)
        step = TrajectoryStep(
            kind="llm",
            detail=LLMCallDetail(
                model="gpt-4",
                messages=[{"role": "user", "content": "hello"}],
                response={"role": "assistant", "content": "hi"},
            ),
            reward=0.7,
            prompt_token_ids=[1, 2],
            completion_token_ids=[3, 4],
            logprobs=[-0.1, -0.2],
        )
        traj = LegacyTrajectory(
            execution_id="exec-rl",
            session_id="session-rl",
            steps=[step],
        )

        store.save(traj)
        loaded = store.load("exec-rl")

        assert loaded is not None
        loaded_step = to_legacy_trajectory(loaded).steps[0]
        assert loaded_step.reward == 0.7
        assert loaded_step.prompt_token_ids == [1, 2]
        assert loaded_step.completion_token_ids == [3, 4]
        assert loaded_step.logprobs == [-0.1, -0.2]

    @staticmethod
    def test_roundtrip_with_tool_step(temp_dir):
        """Roundtrip preserves Tool step data with detail."""
        store = FileTrajectoryStore(temp_dir)
        step = TrajectoryStep(
            kind="tool",
            detail=ToolCallDetail(
                tool_name="test_tool",
                call_args={"arg": "value"},
                call_result={"result": "success"},
                tool_description="A test tool",
            ),
            meta={"operator_id": "test_tool"},
        )
        traj = LegacyTrajectory(
            execution_id="exec1",
            session_id="session1",
            steps=[step],
        )

        store.save(traj)
        loaded = store.load("exec1")

        legacy = to_legacy_trajectory(loaded)
        assert len(legacy.steps) == 1
        loaded_step = legacy.steps[0]
        assert loaded_step.kind == "tool"
        assert loaded_step.detail is not None
        assert isinstance(loaded_step.detail, ToolCallDetail)
        assert loaded_step.detail.tool_name == "test_tool"
        assert loaded_step.detail.call_args == {"arg": "value"}
        assert loaded_step.detail.call_result == {"result": "success"}

    @staticmethod
    def test_roundtrip_preserves_tool_reward(temp_dir):
        """Saving old step-based tool trajectories should preserve reward."""
        store = FileTrajectoryStore(temp_dir)
        step = TrajectoryStep(
            kind="tool",
            detail=ToolCallDetail(
                tool_name="test_tool",
                call_args={"arg": "value"},
                call_result={"result": "success"},
            ),
            reward=0.5,
            meta={"operator_id": "test_tool"},
        )
        traj = LegacyTrajectory(
            execution_id="exec-tool-rl",
            session_id="session1",
            steps=[step],
        )

        store.save(traj)
        loaded = store.load("exec-tool-rl")

        assert loaded is not None
        loaded_step = to_legacy_trajectory(loaded).steps[0]
        assert loaded_step.kind == "tool"
        assert loaded_step.reward == 0.5

    @staticmethod
    def test_save_serializes_pydantic_tool_payloads(temp_dir):
        """File store should serialize Pydantic payloads inside tool details."""

        class Payload(BaseModel):
            value: str

        store = FileTrajectoryStore(temp_dir)
        step = TrajectoryStep(
            kind="tool",
            detail=ToolCallDetail(
                tool_name="test_tool",
                call_args=Payload(value="arg"),
                call_result=Payload(value="result"),
            ),
            meta={"operator_id": "test_tool", "payload": Payload(value="meta")},
        )
        traj = LegacyTrajectory(
            execution_id="exec-pydantic",
            session_id="session1",
            steps=[step],
            meta={"summary": Payload(value="trajectory")},
        )

        store.save(traj)
        loaded = store.load("exec-pydantic")

        assert loaded is not None
        legacy = to_legacy_trajectory(loaded)
        loaded_step = legacy.steps[0]
        assert loaded_step.detail is not None
        assert loaded_step.detail.call_args == {"value": "arg"}
        assert loaded_step.detail.call_result == {"value": "result"}
        assert loaded_step.meta["payload"] == {"value": "meta"}
        assert legacy.meta["summary"] == {"value": "trajectory"}
