# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared aggregation guidance for Organization prompts and tool descriptions."""

ROOT_AGGREGATION_GUIDANCE = {
    "cn": (
        "根据根任务要求和各团队实际承担的交付范围选择模式。不存在承担完整根交付的专业团队，且各团队分别提供并列、独立的领域结论，"
        "根任务需要汇集、编排或综合这些结论时，必须选择 SUMMARY_TEAM。无论内容多少、任务大小或是否存在冲突，"
        "均不得以节约时间、降低开销、只是收集或 Owner 能自行整理为由改选 HIERARCHICAL。"
        "只有某个专业团队承担完整根交付，其最终输出本身即可满足根任务，其他团队提供所需输入、组件或验证时，"
        "才选择 HIERARCHICAL。团队领域正交不改变这一条件。Owner 名义上的汇总职责不构成完整专业交付，"
        "也不得虚设最终集成团队。用户明确指定模式时，在运行时约束允许的范围内遵循。"
    ),
    "en": (
        "Select aggregation from root requirements and actual Team delivery scopes. When no specialist Team is "
        "responsible for the complete root deliverable and Teams provide parallel independent domain conclusions "
        "that must be collected, organized or synthesized, you must choose SUMMARY_TEAM. "
        "Do not switch to HIERARCHICAL because the output is short, the task is small, conflicts are absent, time or "
        "overhead can be reduced, collection is simple, or the Owner can compile the results. Choose HIERARCHICAL only "
        "when one specialist Team owns the complete root delivery and its final output itself satisfies the root; "
        "other Teams supply inputs, components or validation. Domain orthogonality does not change this condition. "
        "Nominal Owner aggregation is not complete specialist delivery; do not invent a final integrator. "
        "Honor an explicit user choice within runtime constraints. "
    ),
}
