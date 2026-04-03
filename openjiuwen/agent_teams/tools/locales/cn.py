# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Chinese (cn) locale strings for agent team tools.

Key convention
--------------
- ``tool_name._desc``            — ToolCard description
- ``tool_name.param``            — top-level param description
- ``tool_name.nested.param``     — nested schema param (e.g. task item)
"""

STRINGS: dict[str, str] = {
    # ===== build_team ==========================================================
    "build_team._desc": (
        "组建团队，设置团队名称和协作目标。"
        "这是启动协作的第一步，必须在 spawn_member 和"
        "add_task 之前调用"
    ),
    "build_team.name": "团队名称，体现团队职能方向",
    "build_team.desc": "团队协作目标和任务范围的描述",
    "build_team.prompt": "团队级别的全局协作提示词，所有成员可见",
    "build_team.leader_name": "Leader 的显示名称",
    "build_team.leader_desc": "Leader 的人设描述（专业背景、领域专长）",

    # ===== clean_team ==========================================================
    "clean_team._desc": (
        "解散团队并清理所有资源。前置条件："
        "所有成员已通过 shutdown_member 关闭。"
        "在所有任务完成、结果汇总后调用"
    ),

    # ===== spawn_member ========================================================
    "spawn_member._desc": (
        "按领域专长创建新的团队成员。"
        "每个成员应有明确的人设和专业方向，"
        "用于领取并执行匹配领域的任务。"
        "创建后成员处于未启动状态，"
        "需调用 startup_members 统一拉起"
    ),
    "spawn_member.member_id": "成员唯一标识符，建议使用有语义的 ID（如 backend-dev-1）",
    "spawn_member.name": "成员名称，体现其角色定位（如「后端开发专家」）",
    "spawn_member.desc": "成员的人设描述，包括专业背景、领域专长、行为风格和工作方式，用于任务匹配和角色定位",
    "spawn_member.prompt": "成员的启动指令。应引导成员通过 view_task 工具查看任务列表，认领任务",

    # ===== shutdown_member =====================================================
    "shutdown_member._desc": (
        "关闭团队成员并释放资源。"
        "在成员完成所有任务后调用；"
        "若成员持续无法交付，可强制关闭"
    ),
    "shutdown_member.member_id": "要关闭的成员ID",
    "shutdown_member.force": "是否强制关闭（忽略未完成任务），默认 false",

    # ===== approve_plan ========================================================
    "approve_plan._desc": "审批或拒绝成员提交的执行计划。审核计划是否符合目标要求，给出反馈指导成员调整",
    "approve_plan.member_id": "提交计划的成员ID",
    "approve_plan.approved": "true 批准计划，false 拒绝并要求修改",
    "approve_plan.feedback": "审批反馈，拒绝时应说明原因和修改方向",

    # ===== approve_tool ========================================================
    "approve_tool._desc": "审批或拒绝 teammate 被 rail 中断的工具调用。收到工具审批请求后，Leader 应调用此工具反馈 approved、feedback 和 auto_confirm。",
    "approve_tool.member_id": "发起工具审批请求的成员 ID",
    "approve_tool.tool_call_id": "待恢复的中断 tool_call_id",
    "approve_tool.approved": "是否批准这次工具调用",
    "approve_tool.feedback": "可选审批反馈",
    "approve_tool.auto_confirm": "后续同名工具是否自动批准",

    # ===== list_members ========================================================
    "list_members._desc": "列出所有团队成员及其状态，用于评估团队人员构成和是否需要创建新成员",

    # ===== task_manager ========================================================
    "task_manager._desc": (
        "团队任务管理工具（仅Leader可用）。"
        "action: add（添加）、insert（插入已有DAG）、update（更新）、cancel（取消）、cancel_all（全部取消）。"
        "add 时通过 tasks 数组传入，支持单个或批量；"
        "insert 用于将任务插入已有 DAG 中间，支持 depended_by 反向依赖"
    ),
    "task_manager.action": "操作类型，默认 add",
    "task_manager.tasks": "add 时传入的任务列表（单个任务也用数组包裹）",
    "task_manager.task_id": "任务ID（insert 时为自定义ID，update/cancel 时为目标任务ID）",
    "task_manager.title": "任务标题（insert/update 使用）",
    "task_manager.content": "任务内容（insert/update 使用）",
    "task_manager.depends_on": "insert 时的前置依赖任务ID列表",
    "task_manager.depended_by": "insert 时需要等待本任务完成的现有任务ID列表（反向依赖）",
    # nested _task_schema
    "task_manager.task.task_id": "自定义任务ID，便于依赖引用",
    "task_manager.task.title": "任务标题，简明描述任务目标",
    "task_manager.task.content": "任务详细内容，包含执行说明和验收标准",
    "task_manager.task.depends_on": "前置依赖的任务ID列表",

    # ===== view_task ===========================================================
    "view_task._desc": "查看任务信息。action: get（单个任务详情）、list（按状态列出任务）、claimable（默认，可认领任务）",
    "view_task.action": "查看模式，默认 claimable",
    "view_task.task_id": "get 时的任务ID",
    "view_task.status": "list 时按状态过滤：pending/claimed/plan_approved/completed/cancelled/blocked",

    # ===== claim_task ==========================================================
    "claim_task._desc": "领取一个就绪任务。只能领取 pending 状态且无人认领的任务，应选择匹配自己领域专长的任务",
    "claim_task.task_id": "要领取的任务ID，需为 pending 状态",

    # ===== complete_task =======================================================
    "complete_task._desc": (
        "标记任务完成。"
        "完成后会自动解锁依赖本任务的下游任务，"
        "使其变为 pending 可领取状态。"
        "调用后应通过 send_message 向 Leader 汇报"
        "结果摘要"
    ),
    "complete_task.task_id": "要标记完成的任务ID，需为自己已领取（claimed）的任务",

    # ===== send_message ========================================================
    "send_message._desc": "向指定成员发送点对点消息。用于通知成员领取任务、回复进度汇报、升级阻塞问题或协调成员间依赖",
    "send_message.content": "消息内容，应包含明确的行动指引或信息",
    "send_message.to_member": "接收者的成员ID",

    # ===== broadcast_message ===================================================
    "broadcast_message._desc": "向所有团队成员广播消息。用于宣布全局决策、约束变更或需要所有人知晓的信息",
    "broadcast_message.content": "广播内容，所有成员都会收到",
}
