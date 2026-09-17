# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared native-image policy helpers for harness rails."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from openjiuwen.harness.image_modality_probe import get_cached_image_support


def should_enable_read_image_multimodal(
    agent: Any,
    explicit_value: bool | None = None,
) -> bool:
    """Resolve whether the agent's current model may receive image bytes.

    A boolean configuration is authoritative. ``None`` is auto mode and uses
    the probe cache for the agent's current main model. Dedicated vision tools
    are intentionally irrelevant: native input and tool-based vision are two
    independent capabilities and may both be available.
    """
    if explicit_value is not None:
        return explicit_value

    deep_config = getattr(agent, "deep_config", None) or getattr(
        agent,
        "_deep_config",
        None,
    )
    configured_value = getattr(deep_config, "enable_read_image_multimodal", None)
    if isinstance(configured_value, bool):
        return configured_value

    model = getattr(deep_config, "model", None)
    return get_cached_image_support(model) is True


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
        if explicit_value is not None:
            return explicit_value

        configured_value = getattr(
            deep_config,
            "enable_read_image_multimodal",
            None,
        )
        if isinstance(configured_value, bool):
            return configured_value

        model = getattr(deep_config, "model", None)
        return get_cached_image_support(model) is True

    return resolve
