# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Localized runtime guidance for Persistent-only capabilities."""

WINDOW_SWITCH_GUIDANCE = {
    "cn": (
        "开始回答或执行当前请求前，先检查是否出现以下转折：\n\n"
        "- 话题切换：讨论从一个话题转向另一个话题。\n"
        "- 调研转向实施：信息收集、问题定位或方案选择完成，准备开始实现。\n"
        "- 阶段交接：一轮实现与验证完成，准备推进后续工作。\n"
        "- 方向调整：新需求或新约束改变了后续方向。\n"
        "- 上下文整理：积累了大量讨论、探索或工具输出，后续工作只需要其中的结论和必要证据。\n\n"
        "出现这些转折时，先整理简明交接并调用 new_context，再继续新话题的回答或新阶段的工作。"
        "即使新问题可以直接回答，也不要跳过这一步；不必等待上下文接近耗尽，也不需要用户提醒。\n\n"
        "同一转折已经成功换窗后，继续当前目标，不要为它重复申请换窗。"
    ),
    "en": (
        "Before answering or acting on the current request, check for these transitions:\n\n"
        "- Topic change: the discussion moves from one topic to another.\n"
        "- Research to implementation: information gathering, diagnosis, or approach selection is complete "
        "and implementation is next.\n"
        "- Phase handoff: a round of implementation and verification is complete and further work is next.\n"
        "- Direction change: new requirements or constraints change the direction of subsequent work.\n"
        "- Context consolidation: extensive discussion, exploration, or tool output has accumulated, "
        "but subsequent work only needs its conclusions and necessary evidence.\n\n"
        "At these transitions, first prepare a concise handoff and call new_context, then continue answering "
        "the new topic or working on the next phase. Do not skip this step even if the new question can be "
        "answered directly. Do not wait until context is nearly exhausted or for the user to remind you.\n\n"
        "Once the switch for a transition has succeeded, continue the current objective without requesting "
        "another switch for that same transition."
    ),
}

WINDOW_MANAGEMENT_PROMPT = {
    "cn": (
        "# 上下文管理\n\n"
        "## 1. 管理目标\n\n"
        "保持上下文聚焦于当前目标，及时整理已经收敛的讨论、探索过程和工具输出。\n\n"
        "换窗只是整理工作记忆，不会重置任务、工作空间、文件或已完成的操作。旧窗口会归档，需要时可以检索。\n\n"
        "## 2. 何时主动换窗\n\n"
        + WINDOW_SWITCH_GUIDANCE["cn"]
        + "\n\n"
        "## 3. 为新窗口准备工作交接\n\n"
        "交接应简明、准确，帮助新窗口直接继续工作，包含：\n\n"
        "- 当前目标与下一步行动。\n"
        "- 已完成工作的关键结论、验证结果和产物位置。\n"
        "- 仍然适用的决策、要求与限制。\n"
        "- 未解决的问题、未完成操作的状态及待核实的信息。\n"
        "- 必要的文件或历史记录引用。\n\n"
        "保留结论与引用，不复述完整过程，不搬运冗长工具输出。"
        "话题切换时，以新话题为主，仅带入旧话题中仍需保留的信息。\n\n"
        "## 4. 换窗后继续工作\n\n"
        "new_context 只提交换窗请求。系统会在本轮工具完成后的安全边界校验并尝试切换；"
        "请求被接收不等于换窗已经成功。\n\n"
        "切换成功后，根据交接继续工作，沿用已有文件和操作结果，不重复执行已完成的操作，也不把换窗当作任务结束。\n\n"
        "## 5. 按需检索历史窗口\n\n"
        "需要核对旧话题、原始讨论、工具结果或决策依据时，使用 search_session_history 检索历史窗口。\n\n"
        "围绕具体问题查证必要信息，不必重新加载整个历史过程。"
        "将相关结论和证据用于当前工作，不把未经核实的信息当作事实。"
    ),
    "en": (
        "# Context management\n\n"
        "## 1. Objective\n\n"
        "Keep context focused on the current objective. Consolidate discussions, exploration, and tool output "
        "whose conclusions are already settled.\n\n"
        "Switching windows only organizes working memory. It does not reset the task, workspace, files, or "
        "completed operations. Previous windows are archived and can be searched when needed.\n\n"
        "## 2. When to switch proactively\n\n"
        + WINDOW_SWITCH_GUIDANCE["en"]
        + "\n\n"
        "## 3. Prepare a handoff for the new window\n\n"
        "Keep the handoff concise and accurate so the new window can continue directly. Include:\n\n"
        "- The current objective and next actions.\n"
        "- Key conclusions, verification results, and artifact locations from completed work.\n"
        "- Decisions, requirements, and constraints that still apply.\n"
        "- Unresolved questions, the status of unfinished operations, and information needing verification.\n"
        "- Necessary file or historical record references.\n\n"
        "Preserve conclusions and references, not the entire process or lengthy tool output. "
        "When topics change, focus on the new topic and carry over only information still needed from the old one.\n\n"
        "## 4. Continue after switching\n\n"
        "new_context only submits a switch request. After the current batch of tools completes, the system "
        "validates and attempts the switch at a safe boundary. Acceptance does not mean the switch has succeeded.\n\n"
        "After a successful switch, continue from the handoff using existing files and operation results. "
        "Do not repeat completed operations or treat switching as the end of the task.\n\n"
        "## 5. Search historical windows as needed\n\n"
        "Use search_session_history when you need to verify previous topics, original discussions, "
        "tool results, or the basis for a decision.\n\n"
        "Search for the information needed to answer a specific question rather than reloading the entire "
        "history. Apply relevant conclusions and evidence to the current work; do not treat unverified "
        "information as fact."
    ),
}
