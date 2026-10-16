# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Bilingual metadata for the optional reference-consultation tool."""

from __future__ import annotations

from typing import Any

from openjiuwen.harness.prompts.tools.base import ToolMetadataProvider


CONSULT_TOOL = "consult_reference_models"

_DESCRIPTIONS = {
    "cn": (
        "向只读参考模型征求建议。"
        '请先读取 skill_tool(skill_name="consult-reference-models")，了解何时值得咨询。'
        "至少完成一轮主模型行动后，再单独调用此工具。"
    ),
    "en": (
        "Ask read-only reference models for suggestions. "
        'First read skill_tool(skill_name="consult-reference-models") for consultation timing. '
        "Call alone after one action step."
    ),
}


class ConsultReferenceModelsMetadataProvider(ToolMetadataProvider):
    """Provide localized descriptions and the strict zero-argument schema."""

    def get_name(self) -> str:
        return CONSULT_TOOL

    def get_description(self, language: str = "cn") -> str:
        return _DESCRIPTIONS.get(language, _DESCRIPTIONS["cn"])

    def get_input_params(self, language: str = "cn") -> dict[str, Any]:
        del language
        return {"type": "object", "properties": {}, "required": [], "additionalProperties": False}
