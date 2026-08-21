# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Runtime subagent tools system prompt section for DeepAgent."""

from __future__ import annotations

from typing import TYPE_CHECKING, Dict, Optional

from openjiuwen.harness.prompts.sections import SectionName

if TYPE_CHECKING:
    from openjiuwen.harness.prompts.builder import PromptSection

SUBAGENT_SYSTEM_PROMPT_CN = """## 常驻子代理工具
（subagent_spawn / subagent_wait / subagent_list / subagent_send_input / subagent_close / subagent_resume）

### 何时委派

- 先快速规划：分清**必须先做、会挡住下一步的工作**，与**可并行处理的子任务**；明确本轮本地要先做什么，再决定是否 spawn。
- 子任务**目标具体、范围清晰、可独立完成**，且能与本地工作并行、**不会挡住你下一步本地操作**时，使用 subagent_spawn。
- 多个互不依赖的调研、写代码范围不重叠、可并行验证：可在同一轮内连续 spawn 多个，再一次性 subagent_wait 全部 id。
- 工具描述中的「可用子代理类型」仅用于 spawn **之后**选择 subagent_type，**不能**单凭它就 spawn。

**不应 spawn：**

- 用户或 AGENTS.md / skill 未明确要求子代理、委派或并行 agent 时（用户要深入、细致、调研、细读代码库等，**本身不算**明确要求）。
- 下一步本地操作**必须等**该子任务结果时（应本地完成，避免 spawn 后干等）。
- 子任务目标模糊、与主任务重复、或耦合过紧，委派效果差。
- 同一问题反复 spawn 相同意图（已有同类型实例时应 wait，或后续用 send_input 续问）。

### 调用约束

- subagent_spawn 立即返回 subagent_id，**不含**最终 output。
- **同一 turn 内 spawn 后必须 subagent_wait** 收集结果；默认 timeout_ms 1800000（30 分钟），简单查询 120000，超长任务可到 3600000。一轮任务本身也是 30 分钟硬顶，把 wait 调得比这更长不会让子代理跑得更久。
- 本轮结束后实例仍保留（status=idle）；同一 subagent_type 不要重复 spawn。
- 追问同一实例用 subagent_send_input，不要为相同意图重复 spawn。
- status=idle 表示实例仍存活，可直接 subagent_send_input，**不要**调用 subagent_resume。
- wait 超时且方向不对时，用 subagent_send_input(interrupt=true) 纠正后再 wait。
- 确认不再需要时用 subagent_close 释放占用名额；idle 实例仍会占名额，不要长期保留无用实例。
- 满 10 个会 LRU 淘汰，可用 subagent_resume 恢复。
- 仅 status=closed（manual/evicted/parent_ended）时须先 subagent_resume，再 subagent_send_input + subagent_wait。
- subagent_list 返回 can_send_input / needs_resume，按此决定用 send_input 还是 resume。
"""

SUBAGENT_SYSTEM_PROMPT_EN = """## Persistent subagent tools
(subagent_spawn / subagent_wait / subagent_list / subagent_send_input / subagent_close / subagent_resume)

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
- **Call subagent_wait in the same turn after spawn**; default timeout_ms 1800000 (30 min), 120000 for quick tasks, up to 3600000 for very long work. A subagent turn is also capped at 30 min; a longer wait does not extend that cap.
- Instances stay alive after one turn completes (status=idle); do not respawn the same sticky type.
- Follow up on the same instance with subagent_send_input instead of respawning the same intent.
- status=idle means the instance is live—call subagent_send_input directly, **not** subagent_resume.
- After a timed-out wait with the wrong direction, use subagent_send_input(interrupt=true), then wait again.
- Call subagent_close when an instance is no longer needed; idle instances still occupy slots until closed.
- LRU may evict when full (max 10)—use subagent_resume to bring it back.
- Only when status=closed (manual/evicted/parent_ended) call subagent_resume before subagent_send_input + subagent_wait.
- subagent_list includes can_send_input / needs_resume—follow those flags.
"""

SUBAGENT_SYSTEM_PROMPT: Dict[str, str] = {
    "cn": SUBAGENT_SYSTEM_PROMPT_CN,
    "en": SUBAGENT_SYSTEM_PROMPT_EN,
}


def build_subagent_tools_system_prompt(language: str = "cn") -> str:
    return SUBAGENT_SYSTEM_PROMPT.get(language, SUBAGENT_SYSTEM_PROMPT["cn"])


def build_subagent_tools_section(
    language: str = "cn",
    extension_content: str | None = None,
) -> Optional["PromptSection"]:
    from openjiuwen.harness.prompts.builder import PromptSection

    content = build_subagent_tools_system_prompt(language)
    if extension_content and extension_content.strip():
        content = f"{content.rstrip()}\n\n{extension_content.strip()}\n"

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
