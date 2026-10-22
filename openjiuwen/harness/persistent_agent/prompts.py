# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Localized runtime guidance for Persistent-only capabilities."""

WINDOW_MANAGEMENT_PROMPT = {
    "cn": (
        "## 上下文管理\n\n"
        "持续推进任务，同时保持上下文聚焦于当前目标。已经收敛的探索、被排除的方案和冗长工具输出，"
        "不必一直占据后续工作的上下文。\n\n"
        "当工作进入一个自然断点，例如问题已经定位、方案已经确定，或一轮实现与验证已经完成，"
        "而接下来仍有实质工作要继续时，判断是否适合以更精简的上下文接续任务。"
        "如果前面的过程已可归结为少量结论和证据，应主动调用 new_context，不必等待上下文接近耗尽，"
        "也不需要用户提醒。\n\n"
        "为新窗口留下足够的工作交接：当前目标与进展、关键决策和约束、尚未解决的问题、下一步行动，"
        "以及必要的文件或证据引用。交接应帮助你接着做，而不是重新讲述整个过程。需要原始细节时，"
        "再通过 search_session_history 查证。\n\n"
        "换窗只是整理工作记忆，不是重新开始或结束任务。保持已有文件和操作结果，继续未完成的工作，"
        "不重复已完成的操作。不要仅因步骤编号变化就换窗；若下一步仍紧密依赖当前细节，"
        "或任务已经可以直接完成，则继续当前窗口。"
    ),
    "en": (
        "## Context management\n\n"
        "Keep making progress while keeping context focused on the current objective. Exploration that has "
        "converged, discarded approaches, and lengthy tool output need not remain in context for all later work.\n\n"
        "At a natural breakpoint, such as locating the cause of a problem, settling on an approach, or completing "
        "a round of implementation and verification, consider whether to continue with a more focused context "
        "if substantive work remains. When the preceding work can be reduced to a few conclusions and evidence "
        "references, proactively call new_context. Do not wait until context is nearly exhausted or for the user "
        "to remind you.\n\n"
        "Leave a sufficient handoff for the new window: the current objective and progress, key decisions and "
        "constraints, unresolved questions, next actions, and necessary file or evidence references. The handoff "
        "should enable you to continue, not retell the entire process. Use search_session_history to verify "
        "original details when needed.\n\n"
        "Switching windows organizes working memory; it does not restart or end the task. Preserve existing "
        "files and operation results, continue unfinished work, and do not repeat completed operations. Do not "
        "switch merely because a step number changes. Stay in the current window if the next action still "
        "depends closely on its details or the task can already be completed directly."
    ),
}
