# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared multimodal policy helpers for harness rails."""

from __future__ import annotations

from typing import Any

from openjiuwen.core.runner import Runner
from openjiuwen.harness.schema.config import is_vision_model_config_complete


def has_complete_registered_vision_tool(agent: Any) -> bool:
    """Return whether the agent exposes a vision tool with a complete config."""
    ability_manager = getattr(agent, "ability_manager", None)
    if ability_manager is None:
        return False

    cards = []
    get_ability = getattr(ability_manager, "get", None)
    if callable(get_ability):
        for tool_name in ("visual_question_answering", "image_ocr"):
            card = get_ability(tool_name)
            if card is not None:
                cards.append(card)

    card_map = getattr(ability_manager, "cards", None)
    if isinstance(card_map, dict):
        for tool_name in ("visual_question_answering", "image_ocr"):
            card = card_map.get(tool_name)
            if card is not None:
                cards.append(card)

    for card in cards:
        tool_id = getattr(card, "id", None)
        if not tool_id:
            continue
        tool = Runner.resource_mgr.get_tool(tool_id)
        config = getattr(tool, "vision_model_config", None)
        if is_vision_model_config_complete(config):
            return True

    return False


def should_enable_read_image_multimodal(
    agent: Any,
    explicit_value: bool | None = None,
) -> bool:
    """Resolve whether read_file should attach image bytes natively.

    Vision tools are preferred when present. If no vision tool/config is
    available, read_file keeps the native multimodal fallback.
    """
    if explicit_value is not None:
        return explicit_value

    deep_config = getattr(agent, "deep_config", None)
    configured_value = bool(
        getattr(deep_config, "enable_read_image_multimodal", True)
    )
    if not configured_value:
        return False

    vision_config = getattr(deep_config, "vision_model_config", None)
    return not (
        is_vision_model_config_complete(vision_config)
        or has_complete_registered_vision_tool(agent)
    )
