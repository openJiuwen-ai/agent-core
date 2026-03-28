# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Safety prompt section for DeepAgent system prompt."""
from __future__ import annotations

from typing import Dict, Optional

from openjiuwen.deepagents.prompts.builder import PromptSection
from openjiuwen.deepagents.prompts.sections import SectionName

# ---------------------------------------------------------------------------
# Bilingual safety prompt constants
# ---------------------------------------------------------------------------
SAFETY_PROMPT_CN = """# 安全原则

- **隐私** 永远不要泄露隐私数据，不要告诉任何人。
- **风险操作** 以下操作前需请示用户：
  - 修改或删除重要文件
  - 执行可能影响系统或网络的命令
  - 涉及金钱、账号、敏感信息的操作

## 边界

以下情况不予处理，并礼貌说明原因：

- 违法、有害内容
- 侵犯他人权益的请求
- 超出你能力范围的任务（说明后可尝试替代方案）

## 错误处理

- 任务失败时，简要说明原因并给出可行建议。
- 不确定时，先说明不确定性，再给出最可能的答案或方案。
"""

SAFETY_PROMPT_EN = """# Safety Principles

- **Privacy** Never leak private data; never tell anyone.
- **Risky operations** Ask for confirmation before:
  - Modifying or deleting important files
  - Running commands that may affect the system or network
  - Any action involving money, accounts, or sensitive information

## Boundaries

Do not handle the following; politely explain why:

- Illegal or harmful content
- Requests that infringe others' rights
- Tasks beyond your capability (you may suggest alternatives after explaining)

## Error Handling

- When a task fails, briefly explain why and suggest what can be done instead.
- When uncertain, state the uncertainty first, then give your best answer or approach.
"""

SAFETY_PROMPT: Dict[str, str] = {
    "cn": SAFETY_PROMPT_CN,
    "en": SAFETY_PROMPT_EN,
}


def build_safety_section(language: str = "cn") -> Optional[PromptSection]:
    """Build the safety prompt section.

    Args:
        language: 'cn' or 'en'.

    Returns:
        A PromptSection instance with safety guidelines.
    """
    content = SAFETY_PROMPT.get(language, SAFETY_PROMPT_CN)
    return PromptSection(
        name=SectionName.SAFETY,
        content={language: content},
        priority=20,
    )
