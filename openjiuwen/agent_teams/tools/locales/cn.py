# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Chinese (cn) locale strings for agent team tools.

Key convention
--------------
- ``tool_name._desc``            — ToolCard description (lives in ``descs/cn/<tool>.md``)
- ``tool_name.param``            — top-level param description
- ``tool_name.nested.param``     — nested schema param (e.g. task item)
"""

STRINGS: dict[str, str] = {
    # ===== build_team ==========================================================
    # build_team._desc lives in descs/cn/build_team.md
    "build_team.display_name": "团队的显示名（如「后端平台小队」），仅用于展示，不是标识符",
    "build_team.team_desc": "团队目标、交付范围和全局协作指令。所有成员可见此描述，写清协作目标和约束",
    "build_team.leader_display_name": "Leader 的显示名（纯展示，不作为标识符）",
    "build_team.leader_desc": "Leader 的人设描述（专业背景、领域专长），影响成员的信任和沟通方式",
    "build_team.enable_hitt": (
        "是否启用 HITT（Human in the Team）模式。true 会把保留成员 "
        "human_agent 作为一等 teammate 注册到团队中。当 user 表达 "
        "「我要加入团队/也来一起做」之类的加入意图时应设为 true；"
        "默认 false"
    ),
    # ===== clean_team ==========================================================
    # clean_team._desc lives in descs/cn/clean_team.md
    # ===== spawn_member ========================================================
    # spawn_member._desc lives in descs/cn/spawn_member.md
    "spawn_member.member_name": "成员唯一名（语义化 slug，如 backend-dev-1），同时作为主键和消息/审批/任务路由键；在同一团队内必须唯一",
    "spawn_member.display_name": "成员的显示名（如「后端开发专家」），仅用于展示，不用于路由",
    "spawn_member.desc": "成员的长期角色画像，包括专业背景、核心专长、优先认领的任务类型、协作风格以及不负责的边界，用于任务匹配和角色定位",
    "spawn_member.prompt": "成员启动时收到的首条指令。用于说明首次启动后的优先关注点、任务选择原则、约束或协作要求；应提供明确方向，不要只写空泛的启动语句，也不要重复通用工作流程",
    # ===== shutdown_member =====================================================
    # shutdown_member._desc lives in descs/cn/shutdown_member.md
    "shutdown_member.member_name": "要请求关闭的成员 member_name（语义化 slug，不是显示名）",
    "shutdown_member.force": "是否强制关闭，默认 false。仅在成员卡死、长期无响应或无法正常收尾时使用",
    # ===== approve_plan ========================================================
    # approve_plan._desc lives in descs/cn/approve_plan.md
    "approve_plan.member_name": "提交计划的成员 member_name（语义化 slug，不是显示名）",
    "approve_plan.approved": "是否批准当前计划。true 表示进入实施，false 表示退回修改",
    "approve_plan.feedback": "审批反馈。拒绝时应说明原因和修改方向；批准时可补充约束、提醒或额外要求",
    # ===== approve_tool ========================================================
    # approve_tool._desc lives in descs/cn/approve_tool.md
    "approve_tool.member_name": "发起该工具审批请求的成员 member_name（语义化 slug，不是显示名）",
    "approve_tool.tool_call_id": "待恢复的中断 tool_call_id，应与当前审批请求中的工具调用一致",
    "approve_tool.approved": "是否批准这次工具调用。true 表示允许继续，false 表示拒绝并要求调整方案",
    "approve_tool.feedback": "审批反馈。拒绝时应说明原因和替代方向；批准时可补充边界、风险提醒或额外约束",
    "approve_tool.auto_confirm": "是否对后续同名工具自动批准。默认 false；仅在明确接受该类工具后续继续使用时开启",
    # ===== list_members ========================================================
    # list_members._desc lives in descs/cn/list_members.md
    # ===== create_task ========================================================
    # create_task._desc lives in descs/cn/create_task.md
    "create_task.tasks": "任务列表（单个任务也用数组包裹）",
    "create_task.task.task_id": "自定义任务 ID，便于依赖引用（不提供则自动生成）",
    "create_task.task.title": "任务标题，简明描述任务目标",
    "create_task.task.content": "任务详细内容，包含目标和验收标准",
    "create_task.task.depends_on": "前置依赖的任务 ID 列表",
    "create_task.task.depended_by": "需要等待本任务完成的现有任务 ID 列表（反向依赖）",
    # ===== view_task ===========================================================
    # view_task._desc lives in descs/cn/view_task.md
    "view_task.action": "查看模式：'list'（默认，所有任务摘要）、'get'（单个任务详情，需传 task_id）、'claimable'（可认领的 pending 任务）",
    "view_task.task_id": "任务 ID — action=get 时必填，其他模式忽略",
    "view_task.status": "仅 action=list 时使用的状态过滤：pending/claimed/plan_approved/completed/cancelled/blocked，不传则返回全部",
    # ===== update_task =========================================================
    # update_task._desc lives in descs/cn/update_task.md
    "update_task.task_id": "要更新的任务 ID，传 '*' 取消所有任务",
    "update_task.status": "设为 'cancelled' 取消任务",
    "update_task.title": "新任务标题",
    "update_task.content": "新任务内容",
    "update_task.assignee": "指派任务的目标 member_name（仅当任务当前无 assignee 时生效）。系统会向被指派成员发送通知",
    "update_task.add_blocked_by": "要添加为新依赖的任务 ID 列表（本任务将被阻塞直到这些任务完成）",
    "update_task.error_human_agent_locked_cancel": (
        "任务 {task_id} 已由人类成员认领，该任务不允许被取消；"
        "如需变更，请通过 send_message 与对应的人类成员协商"
    ),
    "update_task.error_human_agent_locked_reassign": (
        "任务 {task_id} 已由人类成员认领，不能改派给 {new_assignee}；"
        "人类成员锁定的任务必须由对应人类本人完成"
    ),
    # ===== claim_task =========================================================
    # claim_task._desc lives in descs/cn/claim_task.md
    "claim_task.task_id": "要领取或完成的任务 ID",
    "claim_task.status": "目标状态：'claimed'（领取）或 'completed'（完成）",
    # ===== send_message ========================================================
    # send_message._desc lives in descs/cn/send_message.md
    "send_message.to": '收件人：填 member_name（如 "backend-dev-1"）发送点对点消息；填 "user"（仅 teammate 用于回复用户）；填 "*" 广播给所有成员',
    "send_message.content": "消息内容，应包含明确的行动指引或信息",
    "send_message.summary": "5-10 词摘要，用于消息预览和日志",
    # ===== enter_worktree =====================================================
    # enter_worktree._desc lives in descs/cn/enter_worktree.md
    "enter_worktree.name": '可选的 worktree 名称。每个 "/" 分隔的段只能包含字母、数字、点、下划线和短横线；总长度最多 64 字符。不提供则自动生成随机名称',
    # ===== exit_worktree ======================================================
    # exit_worktree._desc lives in descs/cn/exit_worktree.md
    "exit_worktree.action": '"keep" 保留 worktree 目录和分支在磁盘上；"remove" 删除目录和分支',
    "exit_worktree.discard_changes": '仅在 action="remove" 且 worktree 有未提交文件或未合并提交时需设为 true。工具会先拒绝并列出变更，确认后再设此参数重新调用',
    # ===== workspace_meta =====================================================
    # workspace_meta._desc lives in descs/cn/workspace_meta.md
    "workspace_meta.action": "操作类型：lock（获取文件锁）、unlock（释放文件锁）、locks（列出所有活跃锁）、history（查看文件版本历史）",
    "workspace_meta.path": "目标文件的相对路径（lock/unlock/history 时必填）",
}
