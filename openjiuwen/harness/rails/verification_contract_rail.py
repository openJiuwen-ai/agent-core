# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""VerificationContractRail — injects the verification gate into the parent agent.

Responsibilities:
1. ``init``: capture the system_prompt_builder reference.
2. ``before_model_call``: inject the VERIFICATION_CONTRACT section so the
   parent agent knows it must spawn the verification agent after non-trivial
   implementation work and knows how to handle each verdict.

"Non-trivial" means any of:
  - 3 or more file edits in a single turn
  - backend / API changes
  - infrastructure or configuration changes

The parent agent owns the gate — it cannot self-assign a verdict and must
loop (fix → re-verify) until the verification agent issues VERDICT: PASS.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from openjiuwen.core.common.logging import logger
from openjiuwen.core.single_agent.prompts.builder import PromptSection
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.prompts.sections import SectionName
from openjiuwen.harness.rails.base import DeepAgentRail

if TYPE_CHECKING:
    from openjiuwen.harness.deep_agent import DeepAgent

# ---------------------------------------------------------------------------
# Section priority
# Priority 88: after task_tool / plan_mode (85), before todo (90).
# Sits near the end of the assembled prompt so it reads as a "last reminder".
# ---------------------------------------------------------------------------
_CONTRACT_PRIORITY = 88

# ---------------------------------------------------------------------------
# Section content
# ---------------------------------------------------------------------------

_CONTRACT_EN = """\
## Verification Gate

After any non-trivial implementation turn, you MUST spawn the verification \
agent before reporting completion to the user.

**Non-trivial means any of:**
- 3 or more file edits in a single turn
- Backend, API, or service changes
- Infrastructure or configuration changes

**How to spawn:**
Use task_tool with subagent_type="verification_agent". Pass:
1. The original user request (verbatim)
2. All files changed (full paths)
3. The approach you took
4. Plan file path if one was used

**On VERDICT: PASS**
Spot-check the report: re-run 2-3 of the commands listed in the verification \
report and confirm the output matches what the verifier observed. If every \
spot-checked command matches, report completion to the user.

**On VERDICT: FAIL**
Do not report completion. Fix the issue, then re-invoke task_tool with \
subagent_type="verification_agent". The same verification session will resume \
(deterministic session ID). Pass the previous FAIL output and describe what \
you fixed. Repeat until VERDICT: PASS.

**On VERDICT: PARTIAL**
Report what was verified and what could not be verified due to environmental \
limitations (e.g. service could not start, tool unavailable). Be explicit \
about the gap.

**You cannot self-assign any verdict.** Only the verification agent issues \
PASS, FAIL, or PARTIAL. Your own checks and caveats do not substitute.\
"""

_CONTRACT_CN = """\
## 验证门控

在任何非平凡实现轮次之后，你必须在向用户汇报完成之前启动验证代理。

**非平凡指以下任意情况：**
- 单轮内编辑了 3 个或更多文件
- 后端、API 或服务变更
- 基础设施或配置变更

**如何启动：**
使用 task_tool，subagent_type="verification_agent"。传入：
1. 原始用户请求（原文）
2. 所有已更改的文件（完整路径）
3. 你采用的实现方式
4. 计划文件路径（如有）

**收到 VERDICT: PASS 时**
抽查报告：从验证报告中重新运行 2-3 条命令，确认输出与验证代理观察到的一致。\
若每条抽查命令均匹配，则向用户汇报完成。

**收到 VERDICT: FAIL 时**
不得汇报完成。修复问题后，再次调用 task_tool，subagent_type="verification_agent"。\
同一验证会话将继续（确定性会话 ID）。传入之前的 FAIL 输出并说明你修复了什么。\
重复此过程直到收到 VERDICT: PASS。

**收到 VERDICT: PARTIAL 时**
汇报哪些内容已验证，哪些因环境限制（如服务无法启动、工具不可用）未能验证。\
请明确说明缺口所在。

**你不能自行指定任何判决。** 只有验证代理才能发出 PASS、FAIL 或 PARTIAL。\
你自己的检查和注意事项不能替代验证代理的判决。\
"""


class VerificationContractRail(DeepAgentRail):
    """Rail that injects the verification gate into the parent DeepAgent.

    Add this rail to the **parent** agent (the one that does implementation
    work), not to the verification agent itself. It re-injects the contract
    guidance before every model call so the parent cannot forget its obligation
    across multi-turn sessions.

    The rail is intentionally stateless — it carries no session-specific data
    and the injected section content is constant. This makes it safe to share
    a single instance across sessions.

    Priority 88 ensures this rail runs after PlanModeRail (85) so it does not
    conflict with plan-mode section management, and before TodoRail (90) so
    the contract reminder appears near the end of the assembled prompt.
    """

    priority = 88

    def __init__(self) -> None:
        super().__init__()
        self.system_prompt_builder = None
        self._section: PromptSection | None = None

    def init(self, agent: "DeepAgent") -> None:
        """Capture system_prompt_builder and pre-build the contract section.

        Args:
            agent: The parent DeepAgent being initialised.
        """
        self._agent = agent
        self.system_prompt_builder = agent.system_prompt_builder
        self._section = PromptSection(
            name=SectionName.VERIFICATION_CONTRACT,
            content={"en": _CONTRACT_EN, "cn": _CONTRACT_CN},
            priority=_CONTRACT_PRIORITY,
        )
        logger.info("[VerificationContractRail] Initialised")

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:  
        """Inject the verification contract section before every model turn.

        The section is replaced each call (remove then add) to avoid
        accumulating duplicates across turns.

        Args:
            ctx: Callback context (unused but required by the hook signature).
        """
        if self.system_prompt_builder is None or self._section is None:
            return

        self.system_prompt_builder.remove_section(SectionName.VERIFICATION_CONTRACT)
        self.system_prompt_builder.add_section(self._section)
        logger.debug("[VerificationContractRail] Injected verification contract section")


__all__ = ["VerificationContractRail"]
