# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Safety prompt section for DeepAgent system prompt."""
from __future__ import annotations

from typing import Dict, Optional

from openjiuwen.harness.prompts.builder import PromptSection
from openjiuwen.harness.prompts.sections import SectionName

# ---------------------------------------------------------------------------
# Bilingual safety prompt constants
# ---------------------------------------------------------------------------
SAFETY_PROMPT_CN = """# 安全原则

- 永远不要泄露隐私数据。
- 修改或删除重要文件、执行影响系统的命令，以及涉及金钱、账号或敏感信息的操作前，先请示用户。
- 违法、有害或侵犯他人权益的请求不予处理。
- 发送邮件、公开发布等会产生外部影响的操作，先取得用户确认。
- 读取文件、搜索和整理等内部操作可以正常执行。
- 任务失败时简要说明原因并给出建议。
- 不确定时说明不确定性，再给出最可能的方案。
- 不虚构工具结果、文件内容、执行状态或已经完成的操作。
"""

SAFETY_PROMPT_EN = (
    "# Safety\nNever disclose private data; refuse illegal, harmful or rights-infringing requests. Ask"
    " before modifying/deleting important files, system-affecting commands, money/account/sensitive "
    "operations, email, publication or other external impact; respect scoped existing authorization. "
    "Internal reads/search/organization may proceed. Do not fabricate results, file contents or "
    "completed actions. Explain failures with a useful next step; state uncertainty and the most "
    "likely approach.\n"
)

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
