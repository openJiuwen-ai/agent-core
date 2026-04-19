# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Auto Harness prompt section 构建。"""

from __future__ import annotations

from pathlib import Path
from typing import List

from openjiuwen.core.single_agent.prompts.builder import PromptSection


_IDENTITY_PATH = Path(__file__).parent / "identity.md"


def _load_identity() -> str:
    """加载 identity.md 内容。"""
    return _IDENTITY_PATH.read_text(encoding="utf-8")


def build_auto_harness_sections(
    *,
    ci_gate_rules: str = "",
    wisdom: str = "",
) -> List[PromptSection]:
    """构建 Auto Harness Agent 的 prompt sections。

    Args:
        ci_gate_rules: CI 门控规则文本（来自 ci_gate.yaml）。
        wisdom: 经验库合成的活跃上下文。

    Returns:
        PromptSection 列表，注入到 SystemPromptBuilder。
    """
    sections: List[PromptSection] = []

    # Identity section（最高优先级）
    identity_text = _load_identity()
    sections.append(PromptSection(
        name="auto_harness_identity",
        content={"cn": identity_text, "en": identity_text},
        priority=10,
    ))

    # CI Gate 规则
    if ci_gate_rules:
        sections.append(PromptSection(
            name="auto_harness_ci_gate",
            content={
                "cn": f"## CI 门控规则\n\n{ci_gate_rules}",
                "en": f"## CI Gate Rules\n\n{ci_gate_rules}",
            },
            priority=20,
        ))

    # 经验库活跃上下文
    if wisdom:
        sections.append(PromptSection(
            name="auto_harness_wisdom",
            content={
                "cn": f"## 经验库\n\n{wisdom}",
                "en": f"## Experience Library\n\n{wisdom}",
            },
            priority=30,
        ))

    return sections
