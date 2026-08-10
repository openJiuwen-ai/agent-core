# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Runtime subagent tools system prompt section for DeepAgent."""

from __future__ import annotations

from typing import TYPE_CHECKING, Dict, Optional

from openjiuwen.harness.prompts.sections import SectionName

if TYPE_CHECKING:
    from openjiuwen.harness.prompts.builder import PromptSection

SUBAGENT_SYSTEM_PROMPT_CN = """## 常驻子代理工具（subagent_spawn / subagent_wait / subagent_list / subagent_send_input / subagent_close / subagent_resume）

### 何时委派

- 先快速规划整体任务：区分**关键路径上的阻塞步骤**与可并行的**侧车子任务**；明确你本轮应本地立即做什么，再决定是否 spawn。
- 在子任务**具体、有界、自洽**，且能与本地工作并行、**不阻塞你的下一步本地动作**时，使用 subagent_spawn。
- 多个互不依赖的信息收集、互不重叠写范围的编码切片、可并行验证项：可在同一轮内连续 spawn 多个，再一次性 subagent_wait 全部 id。
- 工具描述中的「可用子代理类型」仅帮助 spawn **之后**选择 subagent_type，**不构成** spawn 授权。

**不应 spawn：**

- 用户或 AGENTS.md / skill 未明确要求子代理、委派或并行 agent 工作时（深度、细致、调研、细读代码库等诉求本身**不算**授权）。
- 下一步本地动作**依赖**该子任务结果时（关键路径阻塞项应本地完成，避免 spawn 后空等）。
- 子任务过于模糊、与主任务重复、或耦合过紧导致委派质量差。
- 同一未决问题反复 spawn 相同意图（sticky 类型应 wait 已有实例，或后续用 send_input 续问）。

### 调用约束

- subagent_spawn 立即返回 subagent_id，**不含**最终 output。
- **同一 turn 内 spawn 后必须 subagent_wait** 收集结果；research / coding 类任务传**分钟级** timeout_ms。
- 一轮结束后实例仍常驻；同一 sticky 类型不要重复 spawn。
- 追问同一实例用 subagent_send_input，不要为相同意图重复 spawn。
- wait 超时且方向错误时，用 subagent_send_input(interrupt=true) 纠偏后再 wait。
- 确认不再需要时用 subagent_close 释放名额；**已完成任务的实例仍会占名额**，不要长期保留无用实例。
- 满 10 个会 LRU 淘汰，可用 subagent_resume 拉回。
- close 或淘汰后必须先 subagent_resume，再 subagent_send_input + subagent_wait。
- subagent_list 可查看存活子代理与容量占用。
"""

SUBAGENT_SYSTEM_PROMPT_EN = """## Persistent subagent tools (subagent_spawn / subagent_wait / subagent_list / subagent_send_input / subagent_close / subagent_resume)

### When to delegate

- Plan first: separate **critical-path blockers** from **parallel sidecar** work; decide what you must do locally this turn before spawning.
- Use subagent_spawn when the subtask is **concrete, bounded, self-contained**, can run **in parallel with useful local work**, and does **not block your immediate next local step**.
- For independent research questions, disjoint code-edit slices, or parallel verification: spawn multiple agents in one turn, then subagent_wait on **all** ids at once.
- The available agent types in the spawn tool description only help pick `subagent_type` **after** spawning is authorized—they never authorize spawning by themselves.

**Do NOT spawn when:**

- The user or AGENTS.md / skill did not explicitly ask for sub-agents, delegation, or parallel agent work (depth, thoroughness, research, or codebase analysis alone is **not** permission).
- Your **very next local step** depends on that subtask (keep blocking critical-path work local).
- The subtask is vague, duplicates main-task work, or is too tightly coupled to delegate well.
- You would respawn the same unresolved sticky type—wait the live instance instead, or follow up with send_input when available.

### Usage constraints

- subagent_spawn returns subagent_id immediately and does **not** include the final output.
- **Call subagent_wait in the same turn after spawn**; use minute-scale timeout_ms for research/coding tasks.
- Instances stay alive after one turn completes; do not respawn the same sticky type.
- Follow up on the same instance with subagent_send_input instead of respawning the same intent.
- After a timed-out wait with the wrong direction, use subagent_send_input(interrupt=true), then wait again.
- Call subagent_close when an instance is no longer needed; **completed subagents still occupy slots** until closed—do not keep them around longer than necessary.
- LRU may evict when full (max 10)—use subagent_resume to bring it back.
- After close or eviction, call subagent_resume before subagent_send_input + subagent_wait.
- Use subagent_list to inspect live subagents and capacity usage.
"""

SUBAGENT_SYSTEM_PROMPT: Dict[str, str] = {
    "cn": SUBAGENT_SYSTEM_PROMPT_CN,
    "en": SUBAGENT_SYSTEM_PROMPT_EN,
}


def build_subagent_tools_system_prompt(language: str = "cn") -> str:
    return SUBAGENT_SYSTEM_PROMPT.get(language, SUBAGENT_SYSTEM_PROMPT["cn"])


def build_subagent_tools_section(language: str = "cn") -> Optional["PromptSection"]:
    from openjiuwen.harness.prompts.builder import PromptSection

    content = build_subagent_tools_system_prompt(language)
    return PromptSection(
        name=SectionName.SUBAGENT_TOOLS,
        content={language: content},
        priority=85,
    )


__all__ = [
    "SUBAGENT_SYSTEM_PROMPT",
    "build_subagent_tools_section",
    "build_subagent_tools_system_prompt",
]
