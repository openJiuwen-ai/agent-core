# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Evolution protocol prompt section."""

from __future__ import annotations

from openjiuwen.harness.prompts.builder import PromptSection
from openjiuwen.harness.prompts.sections import SectionName

EVOLUTION_PROTOCOL_PROMPT_CN = """## 演进协议

### 演进信号判别
- 候选信号：用户纠正理解、步骤顺序、工具选择、前置检查或输出格式；用户给出“以后这里要...”等可复用反馈；
  agent 发现当前 Skill 指令导致错误、遗漏或低效；工具失败后形成可复用恢复步骤；用户重复补充同类约束。
- 非候选信号：一次性任务变化、一次性偏好、无可复用策略的普通工具失败、无法可靠归因到 Skill 的情况。

### 结束前演进自检
- 最终回复前，检查本轮是否有可复用的 Skill 改进机会；不要输出自检过程。
- 没有候选信号或只有弱信号时，直接正常结束，不要提演进。
- 有中高置信度候选时，先交付本轮任务结果，再用一句话征求用户同意。
- 用户已明确要求演进、沉淀、以后记住或优化该 Skill 时，可直接进入 review 流程。

### 工具流程
- 所有演进工具都必须使用顶层 subject envelope，例如 {"kind":"skill","name":"..."}。
- 用户同意后，调用 prepare_skill_evolution(user_confirmed=true, user_intent=...)。
- 随后使用 evolve_review_task(evolution_review_ref=...) 和返回的 evolution_review_ref。
- 从 evolve_review_task.data.output.proposal_selection_for_submission 读取 selection，
  再调用 evolve_skill_experiences；不要复制或改写 proposal 内容。
- 不要直接编辑 SKILL.md、evolutions.json 或 evolution/*.md；mutation tools 执行前可能需要用户审批。
"""

EVOLUTION_PROTOCOL_PROMPT_EN = """## Evolution Protocol

### Signal Rubric
- Candidate signals: user corrections to understanding, step order, tool choice, preflight checks, or output format;
  reusable feedback such as "do X here next time"; self-identified Skill instruction gaps that caused errors,
  omissions, or inefficiency; reusable recovery steps after tool failures; repeated user constraints.
- Non-candidate signals: one-off task changes, one-off preferences, ordinary tool failures without a reusable
  strategy, or cases that cannot be reliably attributed to a Skill.

### End-of-turn Evolution Check
- Before the final response, privately check whether this turn produced a reusable Skill improvement opportunity.
  Do not output the check process.
- If there is no candidate signal or only a weak signal, finish normally and do not mention evolution.
- If there is a medium- or high-confidence candidate, deliver the task result first, then ask for user consent
  in one sentence.
- If the user explicitly asked to evolve, capture, remember for next time, or optimize the Skill, enter the review
  flow directly.

### Tool Flow
- Use top-level subject envelopes for all evolution tools, for example {"kind":"skill","name":"..."}.
- After consent, call prepare_skill_evolution(user_confirmed=true, user_intent=...).
- Then use evolve_review_task(evolution_review_ref=...) with the returned evolution_review_ref.
- Read selection from evolve_review_task.data.output.proposal_selection_for_submission, then call evolve_skill_experiences;
  do not copy or rewrite proposal content.
- Do not edit SKILL.md, evolutions.json, or evolution/*.md directly; mutation tools may require approval.
"""

EVOLUTION_PROTOCOL_PROMPT = {
    "cn": EVOLUTION_PROTOCOL_PROMPT_CN,
    "en": EVOLUTION_PROTOCOL_PROMPT_EN,
}

TEAM_EVOLUTION_PROTOCOL_PROMPT_CN = """## 团队 Skill 演进关注点

- 优先判断问题属于 handoff、delegation、shared context、member trajectory mismatch，还是 collaboration protocol gap。
- 区分 role-local change 与 whole-swarm change：只影响某个成员角色时写入角色局部约束；影响团队协作协议时才写入整体 swarm 指令。
- 检查 leader/member 交接、任务声明、共享状态假设和结果汇总是否有可复用改进。
- 不要因为使用了 team rail 就把普通 skill 写成 swarm-skill；subject.kind 必须来自目标 Skill 定义。
"""

TEAM_EVOLUTION_PROTOCOL_PROMPT_EN = """## Team Skill Evolution Focus

- First classify whether the issue is handoff, delegation, shared context, member trajectory mismatch, or a collaboration protocol gap.
- Distinguish role-local change from whole-swarm change: write role-local constraints when only one member role is affected; update the whole swarm only for team protocol changes.
- Check leader/member handoff, task claiming, shared-state assumptions, and result aggregation for reusable improvements.
- Do not write a regular skill as swarm-skill just because the team rail is active; subject.kind must come from the target Skill definition.
"""

TEAM_EVOLUTION_PROTOCOL_PROMPT = {
    "cn": TEAM_EVOLUTION_PROTOCOL_PROMPT_CN,
    "en": TEAM_EVOLUTION_PROTOCOL_PROMPT_EN,
}


def build_evolution_protocol_section(language: str = "cn") -> PromptSection:
    """Build the stable agent-facing evolution protocol section."""
    content = EVOLUTION_PROTOCOL_PROMPT.get(language, EVOLUTION_PROTOCOL_PROMPT_CN)
    return PromptSection(
        name=SectionName.EVOLUTION_PROTOCOL,
        content={language: content},
        priority=86,
    )


def build_team_evolution_protocol_section(language: str = "cn") -> PromptSection:
    """Build the team/swarm-specific evolution protocol section."""
    content = TEAM_EVOLUTION_PROTOCOL_PROMPT.get(language, TEAM_EVOLUTION_PROTOCOL_PROMPT_CN)
    return PromptSection(
        name=SectionName.EVOLUTION_TEAM_PROTOCOL,
        content={language: content},
        priority=87,
    )


__all__ = [
    "EVOLUTION_PROTOCOL_PROMPT",
    "TEAM_EVOLUTION_PROTOCOL_PROMPT",
    "build_evolution_protocol_section",
    "build_team_evolution_protocol_section",
]
