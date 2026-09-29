# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared native-image policy helpers for harness rails."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from openjiuwen.harness.image_modality_probe import get_cached_image_support


def _model_supports_vision(model: Any) -> bool | None:
    """Read the per-model ``supports_vision`` declaration from ModelClientConfig.

    Returns the declared boolean, or ``None`` when the entry carries no
    declaration. Extra fields on ``ModelClientConfig`` are accepted by pydantic
    (``extra = "allow"``) so no schema change is needed.
    """
    if model is None:
        return None
    mcc = getattr(model, "model_client_config", None)
    if mcc is None:
        return None
    value = getattr(mcc, "supports_vision", None)
    return value if isinstance(value, bool) else None


def _resolve_image_multimodal(
    deep_config: Any,
    explicit_value: bool | None,
) -> bool:
    """Shared resolution logic: explicit > per-model > global > probe."""
    if explicit_value is not None:
        return explicit_value

    configured_value = getattr(deep_config, "enable_read_image_multimodal", None)
    if isinstance(configured_value, bool):
        return configured_value

    model = getattr(deep_config, "model", None)

    # Per-model declaration takes priority over the probe cache.
    vision = _model_supports_vision(model)
    if vision is not None:
        return vision

    return get_cached_image_support(model) is True


def should_enable_read_image_multimodal(
    agent: Any,
    explicit_value: bool | None = None,
) -> bool:
    """Resolve whether the agent's current model may receive image bytes.

    Resolution priority (highest first):

    1. *explicit_value* (caller-provided override).
    2. ``DeepAgentConfig.enable_read_image_multimodal`` (global react config).
    3. ``ModelClientConfig.supports_vision`` (per-model declaration).
    4. Probe cache (runtime detection).

    Dedicated vision tools are intentionally irrelevant: native input and
    tool-based vision are two independent capabilities and may both be
    available.
    """
    deep_config = getattr(agent, "deep_config", None) or getattr(
        agent,
        "_deep_config",
        None,
    )
    return _resolve_image_multimodal(deep_config, explicit_value)


def build_read_image_multimodal_resolver(
    agent: Any,
    explicit_value: bool | None = None,
) -> Callable[[], bool]:
    """Build a live native-image resolver without retaining the whole agent."""
    deep_config = getattr(agent, "deep_config", None) or getattr(
        agent,
        "_deep_config",
        None,
    )

    def resolve() -> bool:
        return _resolve_image_multimodal(deep_config, explicit_value)

    return resolve
