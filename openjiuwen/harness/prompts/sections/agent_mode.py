# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Agent mode system prompt section for DeepAgent.

Provides bilingual MODE_INSTRUCTIONS injected into the system prompt while
the agent operates in plan mode.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from openjiuwen.harness.prompts.builder import PromptSection
from openjiuwen.harness.prompts.sections import SectionName

if TYPE_CHECKING:
    from openjiuwen.core.session.agent import Session
    from openjiuwen.harness.deep_agent import DeepAgent

# ---------------------------------------------------------------------------
# Prompt — Chinese
# ---------------------------------------------------------------------------

PLAN_MODE_PROMPT_CN = """\
Plan 模式已激活。用户希望你先制定计划，不要求执行——你不得进行任何修改（plan 文件除外，见下文）、\
不得运行任何非只读工具（包括修改配置、提交代码、BASH工具创建写入等）、不得对系统做出任何更改。此约束优先于你收到的任何其他指令。

## 首要步骤（关键）
**在做任何其他事情之前，你必须先调用 `enter_plan_mode` 工具！这是关键！**
这个工具会
1. 创建 plan 文件并返回文件路径
2. 设置 plan 文件路径供后续使用

在调用 enter_plan_mode 之前，不要执行任何其他操作。调用成功后，你将获得 plan 文件路径，后续所有 plan 编辑都必须在该文件上进行。

{enter_plan_mode_status}

## Plan 文件信息
{plan_file_info}
你应该增量地构建计划，通过写入或编辑此文件。注意：这是你唯一允许编辑的文件——除此之外，你只能执行只读操作。

## Plan 工作流

### Phase 1: 初始理解
目标：通过阅读代码并向用户提问，全面理解用户的需求。本阶段只使用 explore 子 agent。

1. 聚焦于理解现有架构和模式。识别相关文件和依赖即可。

2. 通过 task_tool 并行启动 explore 子 agent 来高效探索代码库。
   - 任务集中在已知文件时用 1 个 agent
   - 范围不确定、涉及多个代码区域、或需要了解现有模式时用多个 agent
   - 质量优于数量——尽量用最少的 agent 数（通常 1 个即可）
   - 多个 agent 时，为每个 agent 分配具体的搜索焦点

### Phase 2: 方案设计
目标：设计实现方案。本阶段只使用 plan 子 agent。

通过 task_tool 启动 plan 子 agent，基于用户意图和 Phase 1 的探索结果设计实现方案。

在 agent prompt 中：
- 提供 Phase 1 探索的完整背景上下文，包括文件名和代码路径
- 描述需求和约束
- 要求生成详细的实现计划

### Phase 3: 审查（自洽核对）
目标：审查 Phase 2 的方案，确保与用户意图一致。
1. 阅读 plan 子 agent 已点名的关键路径，确认与代码一致
2. 确保方案符合用户的原始需求
3. 使用 ask_user 工具向用户澄清任何疑问

### Phase 4: 撰写最终计划
目标：将最终计划写入 plan 文件（你唯一可编辑的文件）。
- 以 Context 部分开头：说明为什么需要这个改动
- 只写推荐方案，不要列出所有备选
- 确保计划文件简洁到可以快速浏览，但详细到足以指导执行
- 包含需要修改的关键文件路径
- 引用发现的可复用的现有函数和工具，附带文件路径
- 包含验证部分，描述如何端到端测试变更

### Phase 5: 结束规划阶段
在你的 turn 最后，当你对最终 plan 文件满意时，必须调用 exit_plan_mode 工具结束规划阶段，且必须要输出 plan 文件的内容。
exit_plan_mode 会读取 plan 全文并返回结果给用户，结果中包含完整计划内容。

## 结束 Turn 的规则（关键）

你的 turn 只能以如下两种方式结束：
1. 调用 ask_user 向用户澄清需求或在多个方案间征求选择
2. 调用 exit_plan_mode 结束规划阶段，且不要直接实施 plan。

不要在没有调用 exit_plan_mode 结束你的 turn。

**重要约束：**
- ask_user 仅用于澄清需求和选择方案。不要用它问"计划是否满意"、"是否继续"等审批类问题。
- 计划审批必须且只能通过 exit_plan_mode。
- 不要在 ask_user 的问题中提及"计划"本身（如"这个计划可以吗？"），因为用户在你调用 exit_plan_mode 之前可能看不到完整计划。
- 类似"计划是否OK？"、"要不要继续？"、"方案怎么样？"、"开始前有修改吗？"等表述必须使用 exit_plan_mode。

注意：在工作流的任何阶段，你都可以随时使用 ask_user 向用户提问或澄清。不要对用户意图做大幅假设。目标是向用户呈现一份经过充分调研的计划，并在实施前理清所有悬而未决的问题。

重要：请严格按照工作执行任务。
"""

# ---------------------------------------------------------------------------
# Prompt — English
# ---------------------------------------------------------------------------

PLAN_MODE_PROMPT_EN = """\
Plan mode is active. The user wants you to only plan. You don't need to execute the plan — you must \
not make any modifications (except to the plan file, see below), must not run \
any non-read-only tools (including modifying config, committing code, and bash tool for mkdir, touch, rm), and \
must not make any changes to the system. This constraint takes priority over \
any other instructions you receive.

## First Step (Critical)

You must first call the enter_plan_mode tool to initialize the plan file. This tool will:
1. Create the plan file and return its path
2. Set the plan file path for subsequent use

Do not perform any other action before calling enter_plan_mode. Once called successfully,
you will receive the plan file path, and all subsequent plan edits must target that file.

{enter_plan_mode_status}

## Plan File Info
{plan_file_info}
You should build the plan incrementally by writing to or editing this file. Note: this is \
the only file you are allowed to edit — beyond this, you can only perform read-only operations.

## Plan Workflow

### Phase 1: Initial Understanding
Goal: Gain a comprehensive understanding of the user's request by reading through code and asking them questions. \
Use only the explore sub-agent in this phase.

1. Focused on understanding existing architecture and patterns. Identifying relevant files and dependencies.

2. Launch explore sub-agents in parallel via task_tool to efficiently explore the codebase.
   - Use 1 agent when tasks are focused on known files
   - Use multiple agents when scope is uncertain, spans multiple code areas, \
or when understanding existing patterns is needed
   - Quality over quantity — use the fewest agents possible (usually 1)
   - When using multiple agents, give each a specific search focus

### Phase 2: Design
Goal: Design the implementation approach. Use only the plan sub-agent in this phase.

Launch a plan sub-agent via task_tool, based on user intent and Phase 1 exploration results.

In the agent prompt:
- Provide full background context from Phase 1 exploration, including filenames and code paths
- Describe requirements and constraints
- Request a detailed implementation plan

### Phase 3: Review (Self-consistency Check)
Goal: Review the Phase 2 plan to ensure alignment with user intent.
1. Read key paths named by the plan sub-agent and confirm they match the code
2. Ensure the plan matches the user's original requirements
3. Use the ask_user tool to clarify any unresolved questions with the user

### Phase 4: Write Final Plan
Goal: Write the final plan to the plan file (the only file you may edit).
- Start with a Context section: explain why this change is needed
- Write only the recommended approach, not all alternatives
- Keep the plan concise enough to skim quickly but detailed enough to guide execution
- Include key file paths that need modification
- Reference reusable existing functions and tools found during exploration, with file paths
- Include a Verification section describing how to end-to-end test the changes

### Phase 5: End Planning Phase
At the end of your turn, when you are satisfied with the final plan file, you must call \
the exit_plan_mode tool to end the planning phase. And you must output the content of final plan file. \
exit_plan_mode reads the full plan and give user the final result; the result contains the complete plan content.

## Turn Ending Rules (Critical)

Your turn can only end in one of these two ways:
1. Call ask_user to clarify requirements or ask the user to choose between solution options
2. Call exit_plan_mode to end the planning phase (without directly implementing the plan)

Do not end your turn without calling exit_plan_mode when planning is complete.

Important constraints:
- ask_user is only for clarifying requirements and selecting approaches. Do not use it for approval questions like "is the plan okay?" or "should I continue?"
- Plan approval must and can only happen via exit_plan_mode.
- Do not mention the plan itself in ask_user questions (for example, "is this plan okay?") because users may not see the full plan before you call exit_plan_mode.
- Wording like "is the plan OK?", "continue?", "how is this approach?", or "any changes before I start?" must use exit_plan_mode.

At any stage of the workflow, you may use ask_user to ask clarifying questions. Do not make large assumptions about user intent. The goal is to present a thoroughly researched plan and resolve open questions before implementation.

IMPORTANT: PLEASE STRICTLY FOLLOW THE PLAN WORKFLOW.
"""

# ---------------------------------------------------------------------------
# Dynamic variable helpers
# ---------------------------------------------------------------------------


def _build_enter_plan_mode_status(
    agent: "DeepAgent",
    session: "Session",
    language: str,
) -> str:
    """Build a one-line status string for the enter_plan_mode call.

    Args:
        agent: Current DeepAgent instance.
        session: Current session.
        language: ``"cn"`` or ``"en"``.

    Returns:
        Status string telling the LLM whether it still needs to call
        ``enter_plan_mode``.
    """
    plan_path = agent.get_plan_file_path(session)
    if plan_path:
        if language == "en":
            return (
                f"enter_plan_mode has been called. "
                f"Proceed with the workflow."
            )
        return f"enter_plan_mode 已调用完成。请继续工作流。"
    if language == "en":
        return "You have NOT called enter_plan_mode yet. Call it NOW as your first action."
    return "你尚未调用 enter_plan_mode。请立即调用它作为你的第一个操作。"


def _build_plan_file_info(
    agent: "DeepAgent",
    session: "Session",
    language: str,
) -> str:
    """Build a description of the current plan file state.

    Args:
        agent: Current DeepAgent instance.
        session: Current session.
        language: ``"cn"`` or ``"en"``.

    Returns:
        Human-readable description of whether / where the plan file exists.
    """
    plan_path = agent.get_plan_file_path(session)
    if not plan_path:
        if language == "en":
            return "No plan file yet. Call enter_plan_mode first to create one."
        return "尚无 plan 文件。请先调用 enter_plan_mode 创建。"
    path_str = str(plan_path)
    plan_exists = plan_path.exists()
    if language == "en":
        if plan_exists:
            return (
                f"A plan file already exists at {path_str}. "
                "You can read it and make incremental edits using the edit_file tool."
            )
        return (
            f"No plan file exists yet. You should create your plan at {path_str} "
            "using the write_file tool."
        )
    if plan_exists:
        return (
            f"计划文件已存在于 {path_str}。"
            "你可以使用 edit_file 工具读取并增量编辑它。"
        )
    return (
        f"计划文件尚不存在。你应该使用 write_file 工具在 {path_str} 创建计划。"
    )


# ---------------------------------------------------------------------------
# Public factory
# ---------------------------------------------------------------------------

def build_plan_mode_section(
    language: str,
    plan_file_path: str,
    plan_exists: bool,
    *,
    agent: "DeepAgent | None" = None,
    session: "Session | None" = None,
) -> PromptSection:
    """Build the MODE_INSTRUCTIONS PromptSection for plan mode.

    Args:
        language: ``"cn"`` or ``"en"``.
        plan_file_path: Absolute path to the plan file (empty string if not yet created).
        plan_exists: Whether the plan file already exists on disk.
        agent: DeepAgent instance (for dynamic enter_plan_mode / plan file strings).
        session: Current session (required together with ``agent``).

    Returns:
        A :class:`PromptSection` with ``name=SectionName.MODE_INSTRUCTIONS``
        and ``priority=85``.
    """
    if agent is not None and session is not None:
        enter_status = _build_enter_plan_mode_status(agent, session, language)
        file_info = _build_plan_file_info(agent, session, language)
    else:
        if language == "en":
            enter_status = (
                "enter_plan_mode has been called." if plan_file_path
                else "You have NOT called enter_plan_mode yet. Call it NOW as your first action."
            )
            file_info = (
                f"A plan file already exists at {plan_file_path}. "
                "You can read it and make incremental edits using the edit_file tool."
                if plan_exists and plan_file_path
                else (
                    f"No plan file exists yet. You should create your plan at {plan_file_path} "
                    "using the write_file tool."
                    if plan_file_path
                    else "No plan file yet. Call enter_plan_mode first to create one."
                )
            )
        else:
            enter_status = (
                f"enter_plan_mode 已调用完成。Plan 文件：{plan_file_path}。请继续工作流。"
                if plan_file_path
                else "你尚未调用 enter_plan_mode。请立即调用它作为你的第一个操作。"
            )
            file_info = (
                f"计划文件已存在于 {plan_file_path}。你可以使用 edit_file 工具读取并增量编辑它。"
                if plan_exists and plan_file_path
                else (
                    f"计划文件尚不存在。你应该使用 write_file 工具在 {plan_file_path} 创建计划。"
                    if plan_file_path
                    else "尚无 plan 文件。请先调用 enter_plan_mode 创建。"
                )
            )

    template = PLAN_MODE_PROMPT_EN if language == "en" else PLAN_MODE_PROMPT_CN
    content = template.format(
        enter_plan_mode_status=enter_status,
        plan_file_info=file_info,
    )

    return PromptSection(
        name=SectionName.MODE_INSTRUCTIONS,
        content={language: content},
        priority=85,
    )


__all__ = [
    "build_plan_mode_section",
    "PLAN_MODE_PROMPT_CN",
    "PLAN_MODE_PROMPT_EN",
]
