# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Tests for TrajectoryStore implementations."""

import json
import tempfile
from pathlib import Path

import pytest
from pydantic import BaseModel

from openjiuwen.agent_evolving.trajectory.store import (
    FileTrajectoryStore,
    InMemoryTrajectoryStore,
)
from openjiuwen.agent_evolving.trajectory.types import (
    LLMCallDetail,
    ToolCallDetail,
    Trajectory,
    TrajectoryStep,
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
    """Factory for creating Trajectory."""
    return Trajectory(
        execution_id=exec_id,
        session_id=session_id,
        source=source,
        case_id=case_id,
        steps=steps or [make_step()],
        cost=None,
    )


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
        assert loaded.execution_id == "exec1"
        assert loaded.case_id == "case1"

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
        assert results[0].case_id == "case1"

    @staticmethod
    def test_query_with_source_filter():
        """Query filtering by source."""
        store = InMemoryTrajectoryStore()
        store.save(make_trajectory(exec_id="exec1", source="online"))
        store.save(make_trajectory(exec_id="exec2", source="offline"))

        results = store.query(source="online")

        assert len(results) == 1
        assert results[0].source == "online"

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
        assert loaded.case_id == "case2"


class TestFileTrajectoryStore:
    """Test FileTrajectoryStore."""

    @staticmethod
    @pytest.fixture
    def temp_dir():
        """Create temporary directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @staticmethod
    def test_save_and_load(temp_dir):
        """Save and load trajectory from file."""
        store = FileTrajectoryStore(temp_dir)
        traj = make_trajectory(exec_id="exec1", case_id="case1")

        store.save(traj)
        loaded = store.load("exec1")

        assert loaded is not None
        assert loaded.execution_id == "exec1"
        assert loaded.case_id == "case1"

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
        assert results[0].case_id == "case1"

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
        assert data["execution_id"] == "exec1"

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
        assert loaded.execution_id == "exec1"

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
        traj = Trajectory(
            execution_id="exec1",
            session_id="session1",
            steps=[step],
        )

        store.save(traj)
        loaded = store.load("exec1")

        assert len(loaded.steps) == 1
        loaded_step = loaded.steps[0]
        assert loaded_step.kind == "llm"
        assert loaded_step.detail is not None
        assert isinstance(loaded_step.detail, LLMCallDetail)
        assert loaded_step.detail.model == "gpt-4"
        assert loaded_step.meta.get("operator_id") == "op1"

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
        traj = Trajectory(
            execution_id="exec1",
            session_id="session1",
            steps=[step],
        )

        store.save(traj)
        loaded = store.load("exec1")

        assert len(loaded.steps) == 1
        loaded_step = loaded.steps[0]
        assert loaded_step.kind == "tool"
        assert loaded_step.detail is not None
        assert isinstance(loaded_step.detail, ToolCallDetail)
        assert loaded_step.detail.tool_name == "test_tool"
        assert loaded_step.detail.call_args == {"arg": "value"}
        assert loaded_step.detail.call_result == {"result": "success"}

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
        traj = Trajectory(
            execution_id="exec-pydantic",
            session_id="session1",
            steps=[step],
            meta={"summary": Payload(value="trajectory")},
        )

        store.save(traj)
        loaded = store.load("exec-pydantic")

        assert loaded is not None
        loaded_step = loaded.steps[0]
        assert loaded_step.detail is not None
        assert loaded_step.detail.call_args == {"value": "arg"}
        assert loaded_step.detail.call_result == {"value": "result"}
        assert loaded_step.meta["payload"] == {"value": "meta"}
        assert loaded.meta["summary"] == {"value": "trajectory"}
