# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Artifact task validation and AgentServer request assembly helpers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from openjiuwen.rsi.schema import (
    ArtifactValidationResult,
    RsiTaskCreateRequest,
    RsiTaskEnvelope,
)


@dataclass(frozen=True, slots=True)
class ArtifactEngineRequest:
    """Provider-facing request shared by program and paper optimizers."""

    task_id: str
    run_dir: str
    artifact_path: str | None
    model_config: str
    max_iterations: int
    optimization_instruction: str | None


def _error(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _non_empty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate_artifact_task_request(request: RsiTaskCreateRequest) -> ArtifactValidationResult:
    """Validate common cross-field rules for an artifact task.

    This helper validates the public request shape only.  It deliberately does
    not read ``artifact_path`` or inspect provider-specific files; the selected
    Provider's ``validate_input`` method performs that work.
    """

    errors: list[dict[str, str]] = []

    if request.scenario != "artifact":
        errors.append(
            _error(
                "ARTIFACT_SCENARIO_REQUIRED",
                'artifact optimization requests must set scenario to "artifact"',
            )
        )
    if request.artifact_type not in ("program", "paper"):
        errors.append(
            _error(
                "ARTIFACT_TYPE_REQUIRED",
                'artifact optimization requests must set artifact_type to "program" or "paper"',
            )
        )
    if not _non_empty(request.name):
        errors.append(_error("NAME_REQUIRED", "task name must be a non-empty string"))
    if not isinstance(request.model_refs, Mapping):
        errors.append(_error("MODEL_REFS_INVALID", "model_refs must be a mapping"))
    elif not _non_empty(request.model_refs.get("optimizer")):
        errors.append(
            _error(
                "OPTIMIZER_MODEL_REQUIRED",
                'model_refs must contain a non-empty "optimizer" entry',
            )
        )
    if isinstance(request.max_iterations, bool) or not isinstance(request.max_iterations, int):
        errors.append(_error("MAX_ITERATIONS_INVALID", "max_iterations must be an integer"))
    elif request.max_iterations < 1:
        errors.append(_error("MAX_ITERATIONS_INVALID", "max_iterations must be at least 1"))

    if request.artifact_type == "program":
        if not _non_empty(request.artifact_path):
            errors.append(
                _error(
                    "PROGRAM_ARTIFACT_PATH_REQUIRED",
                    "program optimization requires a non-empty artifact_path",
                )
            )
        if request.optimization_instruction is not None:
            errors.append(
                _error(
                    "PROGRAM_INSTRUCTION_UNSUPPORTED",
                    "program optimization does not accept optimization_instruction",
                )
            )
    elif request.artifact_type == "paper":
        if not _non_empty(request.artifact_path) and not _non_empty(request.optimization_instruction):
            errors.append(
                _error(
                    "PAPER_INPUT_REQUIRED",
                    "paper optimization requires artifact_path or optimization_instruction",
                )
            )

    if request.dataset_file is not None:
        errors.append(
            _error(
                "DATASET_FILE_UNSUPPORTED",
                "dataset_file is not supported by artifact optimization",
            )
        )
    if request.search_width is not None:
        errors.append(
            _error(
                "SEARCH_WIDTH_UNSUPPORTED",
                "search_width is not supported by artifact optimization",
            )
        )

    return ArtifactValidationResult(valid=not errors, errors=errors)


def build_request(
    task: RsiTaskEnvelope,
    validation: ArtifactValidationResult,
) -> ArtifactEngineRequest:
    """Build the provider-facing request after public/provider validation.

    The function performs no file reads and starts no provider work.  It is
    intended for the AgentServer integration layer; Provider implementations
    must not implement or override it.
    """

    if not validation.valid:
        raise ValueError(validation.errors)
    if task.config.scenario != "artifact":
        raise ValueError([_error("ARTIFACT_SCENARIO_REQUIRED", 'task scenario must be "artifact"')])
    if task.config.artifact_type != task.artifact_type:
        raise ValueError([_error("ARTIFACT_TYPE_MISMATCH", "task envelope and config artifact_type must match")])
    if not isinstance(task.config.model_refs, Mapping) or not _non_empty(task.config.model_refs.get("optimizer")):
        raise ValueError([_error("OPTIMIZER_MODEL_REQUIRED", 'model_refs must contain a non-empty "optimizer" entry')])
    if isinstance(task.config.max_iterations, bool) or not isinstance(task.config.max_iterations, int):
        raise TypeError([_error("MAX_ITERATIONS_INVALID", "max_iterations must be an integer")])
    if task.config.max_iterations < 1:
        raise ValueError([_error("MAX_ITERATIONS_INVALID", "max_iterations must be at least 1")])

    config = task.config
    model_config = config.model_refs["optimizer"]
    return ArtifactEngineRequest(
        task_id=task.task_id,
        run_dir=task.run_dir,
        artifact_path=config.artifact_path,
        model_config=model_config,
        max_iterations=config.max_iterations,
        optimization_instruction=(config.optimization_instruction if task.artifact_type == "paper" else None),
    )


__all__ = ["ArtifactEngineRequest", "build_request", "validate_artifact_task_request"]
