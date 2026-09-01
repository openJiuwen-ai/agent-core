# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for the public artifact optimization provider contract."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import Literal, get_type_hints

import pytest

from openjiuwen.rsi.artifact_rsi import (
    ArtifactProvider,
    PaperArtifactProvider,
    ProgramArtifactProvider,
    build_request,
    validate_artifact_task_request,
)
from openjiuwen.rsi.artifact_rsi.request import ArtifactEngineRequest
from openjiuwen.rsi.events import EngineEventSink, EventNode, EventProgress, EventStatus, emit
from openjiuwen.rsi.schema import (
    ArtifactRef,
    ArtifactValidationResult,
    EngineReport,
    EngineResult,
    EngineState,
    RsiTaskCreateRequest,
    RsiTaskEnvelope,
    RsiTreeNode,
    RsiUsage,
    RsiUsageTokens,
    TreeResponse,
)


def _request(**overrides: object) -> RsiTaskCreateRequest:
    values: dict[str, object] = {
        "scenario": "artifact",
        "artifact_type": "program",
        "name": "program optimization",
        "artifact_path": "/tmp/program",
        "optimization_instruction": None,
        "dataset_file": None,
        "search_width": None,
        "model_refs": {"optimizer": "optimizer-model"},
        "max_iterations": 3,
    }
    values.update(overrides)
    return RsiTaskCreateRequest(**values)  # type: ignore[arg-type]


def _envelope(request: RsiTaskCreateRequest | None = None) -> RsiTaskEnvelope:
    config = request or _request()
    return RsiTaskEnvelope(
        task_id="task-001",
        run_dir="/tmp/rsi/task-001",
        artifact_type="program",
        config=config,
    )


def test_valid_program_request() -> None:
    result = validate_artifact_task_request(_request())

    assert result == ArtifactValidationResult(valid=True, errors=[])


def test_valid_paper_request_can_use_instruction_without_path() -> None:
    result = validate_artifact_task_request(
        _request(
            artifact_type="paper",
            artifact_path=None,
            optimization_instruction="Improve the experiment section",
        )
    )

    assert result.valid is True
    assert result.errors == []


def test_program_request_requires_path_and_rejects_instruction() -> None:
    result = validate_artifact_task_request(
        _request(
            artifact_path=" ",
            optimization_instruction="rewrite it",
        )
    )

    assert result.valid is False
    assert [error["code"] for error in result.errors] == [
        "PROGRAM_ARTIFACT_PATH_REQUIRED",
        "PROGRAM_INSTRUCTION_UNSUPPORTED",
    ]


def test_paper_request_requires_path_or_instruction() -> None:
    result = validate_artifact_task_request(
        _request(
            artifact_type="paper",
            artifact_path=None,
            optimization_instruction=" ",
        )
    )

    assert result.valid is False
    assert {error["code"] for error in result.errors} == {"PAPER_INPUT_REQUIRED"}


def test_artifact_request_rejects_harness_only_fields() -> None:
    result = validate_artifact_task_request(
        _request(dataset_file="cases.json", search_width=2),
    )

    assert result.valid is False
    assert {error["code"] for error in result.errors} == {
        "DATASET_FILE_UNSUPPORTED",
        "SEARCH_WIDTH_UNSUPPORTED",
    }


def test_artifact_request_validates_model_and_iteration() -> None:
    result = validate_artifact_task_request(
        _request(model_refs={}, max_iterations=0),
    )

    assert result.valid is False
    assert {error["code"] for error in result.errors} == {
        "OPTIMIZER_MODEL_REQUIRED",
        "MAX_ITERATIONS_INVALID",
    }


def test_build_request_maps_program_fields_without_artifact_type() -> None:
    task = _envelope()

    request = build_request(task, ArtifactValidationResult(valid=True, errors=[]))

    assert request == ArtifactEngineRequest(
        task_id="task-001",
        run_dir="/tmp/rsi/task-001",
        artifact_path="/tmp/program",
        model_config="optimizer-model",
        max_iterations=3,
        optimization_instruction=None,
    )
    assert not hasattr(request, "artifact_type")


def test_build_request_maps_paper_instruction() -> None:
    config = _request(
        artifact_type="paper",
        artifact_path=None,
        optimization_instruction="Improve clarity",
    )
    task = RsiTaskEnvelope(
        task_id="paper-001",
        run_dir="/tmp/rsi/paper-001",
        artifact_type="paper",
        config=config,
    )

    request = build_request(task, ArtifactValidationResult(valid=True, errors=[]))

    assert request.artifact_path is None
    assert request.optimization_instruction == "Improve clarity"
    assert request.task_id == "paper-001"


def test_build_request_rejects_failed_validation() -> None:
    validation = ArtifactValidationResult(
        valid=False,
        errors=[{"code": "INPUT_INVALID", "message": "invalid input"}],
    )

    with pytest.raises(ValueError) as exc_info:
        build_request(_envelope(), validation)

    assert exc_info.value.args[0] == validation.errors


def test_build_request_rejects_envelope_config_type_mismatch() -> None:
    paper_config = _request(
        artifact_type="paper",
        artifact_path=None,
        optimization_instruction="Improve clarity",
    )
    task = RsiTaskEnvelope(
        task_id="task-001",
        run_dir="/tmp/rsi/task-001",
        artifact_type="program",
        config=paper_config,
    )

    with pytest.raises(ValueError, match="ARTIFACT_TYPE_MISMATCH"):
        build_request(task, ArtifactValidationResult(valid=True, errors=[]))


def test_event_types_are_fixed_and_nodes_use_common_shape() -> None:
    usage = RsiUsage(
        tokens=RsiUsageTokens(input=10, output=5, cache_hit=2),
        cost_estimate=0.1,
        call_count=1,
    )
    node = RsiTreeNode(
        node_id="node-001",
        iteration=1,
        parent_id=None,
        type="root",
        adopted=True,
        score=None,
        summary="initial artifact",
        snapshot_artifact_id=None,
        reason=None,
        failure_class=None,
        changes=[],
        extra={"program": {}},
    )

    status = EventStatus(status="running")
    progress = EventProgress(
        iteration=1,
        total_iterations=3,
        score=0.5,
        baseline=0.4,
        usage=usage,
    )
    node_event = EventNode(node=node)

    assert status.event_type == "status"
    assert progress.event_type == "progress"
    assert node_event.event_type == "node"
    with pytest.raises(TypeError):
        EventStatus(status="running", event_type="progress")  # type: ignore[call-arg]


@pytest.mark.asyncio
async def test_emit_awaits_callback_and_skips_none() -> None:
    events: list[EventStatus] = []
    event = EventStatus(status="completed")

    async def on_event(received: EventStatus) -> None:
        events.append(received)

    await emit(on_event, event)
    await emit(None, event)

    assert events == [event]


class _ProgramProvider:
    artifact_type = "program"

    def validate_input(self, artifact_path: str | None) -> ArtifactValidationResult:
        return ArtifactValidationResult(valid=True, errors=[])

    async def run(self, request: ArtifactEngineRequest, on_event=None) -> EngineResult:
        return EngineResult("task", "completed", None, None, None)

    async def pause(self, task_id: str, on_event=None) -> EngineResult:
        return EngineResult(task_id, "paused", None, None, None)

    async def resume(self, request: ArtifactEngineRequest, on_event=None) -> EngineResult:
        return EngineResult(request.task_id, "completed", None, None, None)

    def read_state(self, task_id: str) -> EngineState:
        raise NotImplementedError

    def read_report(self, task_id: str) -> EngineReport:
        raise NotImplementedError

    def get_tree(self, task_id: str) -> TreeResponse:
        raise NotImplementedError

    def locate_artifact(self, task_id: str, artifact_id: str | None = None) -> ArtifactRef:
        raise NotImplementedError

    async def terminate(self, task_id: str, on_event=None) -> EngineResult:
        return EngineResult(task_id, "terminated", None, None, None)


def test_provider_protocol_is_structurally_implementable() -> None:
    provider = _ProgramProvider()

    assert isinstance(provider, ArtifactProvider)
    assert isinstance(provider, ProgramArtifactProvider)
    assert get_type_hints(ProgramArtifactProvider)["artifact_type"] == Literal["program"]
    assert get_type_hints(PaperArtifactProvider)["artifact_type"] == Literal["paper"]


def test_public_dataclasses_are_frozen() -> None:
    result = ArtifactValidationResult(valid=True, errors=[])

    with pytest.raises(FrozenInstanceError):
        result.valid = False  # type: ignore[misc]


def test_shared_contracts_are_defined_at_rsi_root() -> None:
    assert EventStatus.__module__ == "openjiuwen.rsi.events"
    assert RsiTaskCreateRequest.__module__ == "openjiuwen.rsi.schema"
    assert EngineEventSink is not None


def test_artifact_package_does_not_reexport_internal_structures() -> None:
    from openjiuwen.rsi import artifact_rsi

    assert not hasattr(artifact_rsi, "ArtifactEngineRequest")
    assert not hasattr(artifact_rsi, "RsiTreeNode")
    assert not hasattr(artifact_rsi, "EventNode")
