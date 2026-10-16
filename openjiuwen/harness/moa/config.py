# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Small, explicit configuration for DeepAgent reference consultation."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.foundation.llm.system_one import JevSystemOneClient
from openjiuwen.harness.moa._validation import (
    validate_optional_budget,
    validate_positive_int,
    validate_positive_timeout,
)


@dataclass(frozen=True)
class VerifierConfig:
    """Scoring cadence and optional consultation budgets."""

    interval: int = 3
    n_evaluations: int = 3
    # Maximum consultation batches per outer agent invocation. None means unlimited;
    # set a finite value (e.g. 3) in production. Each batch calls all configured
    # reference models, and failed attempts also consume the batch budget.
    # For aggregator_on_demand: MoAConfig(..., verifier=VerifierConfig(max_reference_calls=3)).
    max_reference_calls: int | None = None
    max_verifier_calls: int | None = None

    def __post_init__(self) -> None:
        validate_positive_int("verifier interval", self.interval)
        validate_positive_int("verifier n_evaluations", self.n_evaluations)
        validate_optional_budget("max_reference_calls", self.max_reference_calls)
        validate_optional_budget("max_verifier_calls", self.max_verifier_calls)


@dataclass(frozen=True, kw_only=True)
class JevConfig:
    """Direct consultation decisions through a caller-owned System One client.

    Each checkpoint spends one decision attempt, including failed attempts.
    ``timeout`` bounds the complete checkpoint, including todo reads and any
    client retries. The client remains owned and closed by the caller.
    """

    client: JevSystemOneClient
    interval: int = 3
    recent_messages: int = 5
    timeout: float = 30.0
    max_jev_calls: int | None = None
    max_reference_calls: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.client, JevSystemOneClient):
            raise build_error(
                StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR,
                error_msg="JevConfig requires a preconstructed JevSystemOneClient",
            )
        validate_positive_int("JEV interval", self.interval)
        validate_positive_int("JEV recent_messages", self.recent_messages)
        validate_positive_timeout("JEV timeout", self.timeout)
        validate_optional_budget("JEV max_jev_calls", self.max_jev_calls)
        validate_optional_budget("JEV max_reference_calls", self.max_reference_calls)


@dataclass(frozen=True)
class MoAConfig:
    """Reference models and routing policy for DeepAgent."""

    reference_models: tuple[Model, ...]
    mode: Literal["aggregator_on_demand", "verifier_routed", "jev_routed"] = "aggregator_on_demand"
    verifier: VerifierConfig = field(default_factory=VerifierConfig)
    scorer: Any = None
    max_reference_chars: int = 12_000
    max_advice_chars: int = 48_000
    reference_timeout: float = 60.0
    consultation_skill_dir: str | None = None
    jev: JevConfig | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        try:
            references = tuple(self.reference_models)
        except TypeError as exc:
            raise build_error(
                StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR,
                error_msg="reference_models must be an iterable of reference models",
                cause=exc,
            ) from exc
        object.__setattr__(self, "reference_models", references)
        if not self.reference_models:
            raise build_error(
                StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR, error_msg="MoA requires at least one reference model"
            )
        if self.mode not in ("aggregator_on_demand", "verifier_routed", "jev_routed"):
            raise build_error(StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR, error_msg=f"unsupported MoA mode: {self.mode}")
        if not isinstance(self.verifier, VerifierConfig):
            raise build_error(StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR, error_msg="verifier must be a VerifierConfig")
        if self.mode == "jev_routed":
            if not isinstance(self.jev, JevConfig):
                raise build_error(StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR, error_msg="jev_routed requires a JevConfig")
            if self.scorer is not None or self.verifier != VerifierConfig():
                raise build_error(
                    StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR,
                    error_msg="JEV and verifier routing are mutually exclusive; use only JevConfig in jev_routed",
                )
        elif self.jev is not None:
            raise build_error(
                StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR,
                error_msg="JEV and verifier routing are mutually exclusive; jev is only supported in jev_routed",
            )
        if self.mode == "verifier_routed" and self.scorer is None:
            raise build_error(StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR, error_msg="verifier_routed requires a scorer")
        validate_positive_int("max_reference_chars", self.max_reference_chars)
        validate_positive_int("max_advice_chars", self.max_advice_chars)
        validate_positive_timeout("reference_timeout", self.reference_timeout)
        if self.consultation_skill_dir is not None:
            if self.mode != "aggregator_on_demand":
                raise build_error(
                    StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR,
                    error_msg="consultation_skill_dir is only supported in aggregator_on_demand mode",
                )
            if not isinstance(self.consultation_skill_dir, str) or not self.consultation_skill_dir.strip():
                raise build_error(
                    StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR,
                    error_msg="consultation_skill_dir must be a nonempty skill directory path",
                )
            try:
                directory = Path(self.consultation_skill_dir).expanduser().resolve()
                valid_directory = directory.name == "consult-reference-models" and (directory / "SKILL.md").is_file()
            except (OSError, RuntimeError, ValueError) as exc:
                raise build_error(
                    StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR,
                    error_msg="consultation_skill_dir must resolve to a readable skill directory",
                    cause=exc,
                ) from exc
            if not valid_directory:
                raise build_error(
                    StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR,
                    error_msg="consultation_skill_dir must be a consult-reference-models directory with SKILL.md",
                )
            object.__setattr__(self, "consultation_skill_dir", str(directory))
