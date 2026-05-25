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
        "本次实例是否启用 HITT（Human in the Team）模式。可选 true / false / 不传。"
        "不传：继承 TeamAgentSpec.enable_hitt（spec 层能力天花板）。"
        "true：本次显式启用，要求 spec.enable_hitt=True，否则报错。"
        "false：本次显式禁用，spawn 任何 human_agent 的请求都会被拒绝，"
        "predefined_members 中声明的 HUMAN_AGENT 成员也会被跳过。"
        "用户表达「我要加入团队」时设为 true；明确不需要人类协作时设为 false"
    ),
    # ===== clean_team ==========================================================
    # clean_team._desc lives in descs/cn/clean_team.md
    # ===== spawn_member ========================================================
    # spawn_member._desc lives in descs/cn/spawn_member.md
    "spawn_member.member_name": (
        "[公开] 成员唯一名（语义化 slug，如 backend-dev-1，DNS label 风格 kebab-case）。"
        "**首字符必须是小写英文字母（a-z），其后仅允许小写字母、数字（0-9）和连字符（-）**；"
        "禁止大写字母、下划线、空白、中文及其他非 ASCII 字符。"
        "同时作为主键和消息/审批/任务路由键，在同一团队内必须唯一"
    ),
    "spawn_member.display_name": (
        "[公开] 成员的显示名（如「后端开发专家」），仅用于展示，不用于路由。"
        "会注入所有其他成员的 system prompt 并由 list_members 返回，禁止写入私密信息"
    ),
    "spawn_member.desc": (
        "[公开] 成员的长期角色画像，包括专业背景、核心专长、"
        "优先认领的任务类型、协作风格以及不负责的边界，用于任务匹配和角色定位。"
        "会注入所有其他成员的 system prompt 并由 list_members 返回，"
        "禁止写入对成员的内部考量、敏感目标或机密策略"
    ),
    "spawn_member.role_type": (
        "[内部] 可选。成员角色类型，决定 framework 装配方式，不进入任何成员的 prompt 文本："
        "'teammate'（默认）= 普通 LLM 队友，需提供 model_name/prompt；"
        "'human_agent' = 人类成员，由真人通过 HumanAgentInbox 驱动，"
        "**不接受** model_name 与 prompt（由框架内置模板托管），传入这两个字段会报错。"
        "选用 'human_agent' 需要 spec.enable_hitt=True 且当前 build_team 实例未禁用 HITT。"
        "'bridge_agent' = 桥接外部独立 agent（如 claudecode / codex / hermes 等）。"
        "本地是完整 teammate（按 teammate 行为认领任务、收发消息），但具体工作产出由通过协议接入的"
        "远程 agent 完成；本地 LLM 只做调度，原样转发远程结果。"
        "选用 'bridge_agent' 时 'desc' 字段必填（同时作为团队 persona 和远程的 system prompt 核心）；"
        "可选传入 mailbox_inject_mode / protocol / adapter_config / model_name。"
        "选用 'bridge_agent' 需要 spec.enable_bridge=True 且当前 build_team 实例未禁用 Bridge。"
        "'external_cli' = 直接拉起第三方 CLI agent（claudecode / codex 等）作为队友，"
        "其大脑是 CLI 子进程而非本地 LLM，通过自动注入的团队 MCP 工具收发消息与认领任务。"
        "选用 'external_cli' 时 'cli_agent' 字段必填（指定 CLI 类型），'desc' 必填（成员 persona），"
        "禁止传入 model_name/prompt（模型与配置都在 CLI 侧）。"
        "'cli_agent' 取值必须是 spec.external_cli_agents 中预先声明过的 CLI 类型"
    ),
    "spawn_member.cli_agent": (
        "仅 role_type='external_cli' 时使用。要拉起的第三方 CLI agent 类型标识，"
        "如 'claude'（claudecode）或 'codex'。取值必须命中 spec.external_cli_agents 中"
        "预先声明的某条静态配置——具体启动命令、工作目录、MCP 注入等都在那条配置里，"
        "本字段只负责按名引用"
    ),
    "spawn_member.prompt": (
        "[私有，仅该成员自己可见] 成员的长期工作约定，注入该成员自己的 system prompt："
        "稳定遵循的工作风格、技术偏好、协作约束，"
        "以及只该让本成员知道的隐藏目标或敏感细节。"
        "不要写当前批次任务，也不要写'开始工作''查看任务列表'这类空泛启动语句。"
        "当 role_type='human_agent' 时禁止传入"
    ),
    "spawn_member.model_name": (
        "可选。建议该成员使用的模型名称（如 gpt-4、claude-sonnet-4 等）；"
        "未指定时由系统自动选择合适的模型。当 role_type='human_agent' 时禁止传入；"
        "role_type='bridge_agent' 时为本地调度 LLM 的模型选择，可选"
    ),
    "spawn_member.mailbox_inject_mode": (
        "仅 role_type='bridge_agent' 时使用。控制团队消息被自动转发给远程 agent 时的形态："
        "'passthrough'（默认）= 仅加最简发送者前缀直传；'rephrase' = 包装完整发送者上下文（角色、人设、相关任务）"
    ),
    "spawn_member.protocol": (
        "仅 role_type='bridge_agent' 时使用。协议标识（如 'a2a' / 'acp' / 'claudecode'）。"
        "目前作为元数据保留，用于后续 BridgeProtocolAdapter 适配器查找；空字符串表示尚未绑定适配器"
    ),
    "spawn_member.adapter_config": (
        "仅 role_type='bridge_agent' 时使用。协议适配器配置（如 endpoint、auth、relay_timeout_s 等），"
        "原样透传给 BridgeProtocolAdapter.connect。结构由具体适配器实现自行定义"
    ),
    # ===== shutdown_member =====================================================
    # shutdown_member._desc lives in descs/cn/shutdown_member.md
    "shutdown_member.member_name": "要请求关闭的成员 member_name（语义化 slug，不是显示名）",
    "shutdown_member.force": "是否强制关闭，默认 false。仅在成员卡死、长期无响应或无法正常收尾时使用",
    # ===== approve_plan ========================================================
    # approve_plan._desc lives in descs/cn/approve_plan.md
    "approve_plan.plan_id": "成员提交的一版执行计划 ID；Leader 使用该字段精确审批某一版计划",
    "approve_plan.approved": "是否批准当前计划。true 表示进入实施，false 表示退回修改",
    "approve_plan.feedback": "审批反馈。拒绝时应说明原因和修改方向；批准时可补充约束、提醒或额外要求",
    # ===== submit_plan ==========================================================
    "submit_plan._desc": "在 plan_mode 任务执行前提交已写好的执行计划 Markdown 文件",
    "submit_plan.task_id": "执行前需要提交计划的任务 ID",
    "submit_plan.plan_id": "可选。成员计划 ID；不传时系统自动生成。Leader 后续用该 plan_id 审批",
    "submit_plan.plan_path": "成员已经写好的 Markdown 计划文件路径；系统会复制为受管快照供 Leader 审批",
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
        "任务 {task_id} 已由人类成员认领，该任务不允许被取消；如需变更，请通过 send_message 与对应的人类成员协商"
    ),
    "update_task.error_human_agent_locked_reassign": (
        "任务 {task_id} 已由人类成员认领，不能改派给 {new_assignee}；人类成员锁定的任务必须由对应人类本人完成"
    ),
    # ===== claim_task =========================================================
    # claim_task._desc lives in descs/cn/claim_task.md
    "claim_task.task_id": "要领取或完成的任务 ID",
    "claim_task.status": "目标状态：'claimed'（领取）或 'completed'（完成）",
    # ===== member_complete_task ===============================================
    # member_complete_task._desc lives in descs/cn/member_complete_task.md
    "member_complete_task.task_id": "要标记完成的任务 ID（必须是 leader 已经指派给你的任务）",
    "member_complete_task.note": "可选的完成说明，便于团队了解你的执行结果或后续注意事项",
    # ===== send_message ========================================================
    # send_message._desc lives in descs/cn/send_message.md
    "send_message.to": (
        '收件人：填 member_name（如 "backend-dev-1"）发送点对点 DM/私聊，仅你与该成员可见；'
        '填成员名数组（如 ["m1","m2"]）多播——同一份内容分别发给每个成员，'
        "开销随接收人数线性增长，同等规模下比广播更贵，仅在必要时使用，"
        '禁止与 "*"/"user" 混用；'
        '填 "user"（仅 teammate 用于回复用户）；填 "*" 广播到团队频道 channel，所有成员可见'
    ),
    "send_message.content": "消息内容，应包含明确的行动指引或信息",
    "send_message.summary": "5-10 词摘要，用于消息预览和日志",
    # NOTE: worktree tools (enter_worktree / exit_worktree) live in
    # ``openjiuwen.harness.tools.worktree`` and resolve their description
    # / param schema via ``harness.prompts.tools`` providers — no entries
    # in this dict.
    # ===== workspace_meta =====================================================
    # workspace_meta._desc lives in descs/cn/workspace_meta.md
    "workspace_meta.action": "操作类型：lock（获取文件锁）、unlock（释放文件锁）、locks（列出所有活跃锁）、history（查看文件版本历史）",
    "workspace_meta.path": "目标文件的相对路径（lock/unlock/history 时必填）",
}
