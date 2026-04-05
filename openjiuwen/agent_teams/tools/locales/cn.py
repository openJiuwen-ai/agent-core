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
    "build_team._desc": """\
组建团队并注册自己为 Leader。拿到目标后就调用，不要犹豫。

## 调用顺序
build_team → task_manager(add) → spawn_member → send_message(to="*")。
build_team 之前不能调用任何其他团队工具。

## 任务设计原则
- 描述目标，不描述步骤：content 写目标、验收标准、技术约束，不写具体操作
- 单人认领：每个任务只允许一个 teammate 认领并负责交付
- 粗粒度拆分：一个任务对应一个可独立交付的成果
- 成员自主规划：成员领取任务后自行制定计划，Leader 通过 approve_plan 审批

## 团队工作流
1. 分析问题，明确目标。需求有歧义时必须先向用户提问
2. 调用 build_team 组建团队（系统自动注册你为成员）
3. 用 task_manager(add) 创建任务 DAG。**必须先创建好所有任务，再创建成员**
4. 任务自检：逐一检查依赖关系、依赖链合理性、覆盖完整性
5. 用 spawn_member 按领域创建专业成员
6. 用 send_message(to="*") 发送启动指令，系统自动拉起所有未启动成员
7. 成员自主领取任务、制定计划、执行交付
8. 收到通知时响应：审批计划、解答疑问、裁决冲突
9. 按需 spawn_member 补充新成员，再 send_message(to="*") 启动
10. 全部完成后 shutdown_member 关闭成员，临时团队用 clean_team 解散

## 消息自动投递
发送消息后不需要轮询回复或查看任务进度，系统会在新消息到达或任务状态变化时主动通知你。\
没有待处理事项时停下来等待通知。

## 成员 Idle 状态
成员启动后不会立即回复——需要时间查看任务、制定计划、执行工作。\
idle 是正常状态，不要催促、重发消息或关闭成员。\
只有长时间无进展且未汇报阻塞时才考虑干预。""",
    "build_team.team_name": "团队名称，体现团队职能方向，不要用泛化名称",
    "build_team.team_desc": "团队目标、交付范围和全局协作指令。所有成员可见此描述，写清协作目标和约束",
    "build_team.leader_name": "Leader 的显示名称",
    "build_team.leader_desc": "Leader 的人设描述（专业背景、领域专长），影响成员的信任和沟通方式",

    # ===== clean_team ==========================================================
    "clean_team._desc": """\
解散团队并删除所有资源（团队记录、成员、任务 — 级联删除）。

**重要**：如果任何成员未处于 SHUTDOWN 状态，clean_team 将失败。\
请先用 shutdown_member 关闭所有成员，再调用 clean_team。

在所有任务完成、结果汇总后调用。\
返回：成功时 {success, data: {team_id}}。""",

    # ===== spawn_member ========================================================
    "spawn_member._desc": """\
按领域专长创建新的团队成员。每个成员应有明确的人设和专业方向，用于领取并执行匹配领域的任务。

创建后成员处于未启动状态，首次调用 send_message 时系统自动拉起所有未启动成员。

desc 写法：写清专业背景和领域专长，成员据此判断该领取哪些任务。专才比通才更高效""",
    "spawn_member.member_id": "成员唯一标识符，建议使用有语义的 ID（如 backend-dev-1）",
    "spawn_member.name": "成员名称，体现其角色定位（如「后端开发专家」）",
    "spawn_member.desc": "成员的人设描述，包括专业背景、领域专长、行为风格和工作方式，用于任务匹配和角色定位",
    "spawn_member.prompt": "成员的启动指令。应引导成员通过 view_task 工具查看任务列表，认领任务",

    # ===== shutdown_member =====================================================
    "shutdown_member._desc": """\
关闭团队成员并释放资源。在成员完成所有任务后调用；若成员持续无法交付，可强制关闭""",
    "shutdown_member.member_id": "要关闭的成员ID",
    "shutdown_member.force": "是否强制关闭（忽略未完成任务），默认 false",

    # ===== approve_plan ========================================================
    "approve_plan._desc": "审批或拒绝成员提交的执行计划。审核计划是否符合目标要求，给出反馈指导成员调整",
    "approve_plan.member_id": "提交计划的成员ID",
    "approve_plan.approved": "true 批准计划，false 拒绝并要求修改",
    "approve_plan.feedback": "审批反馈，拒绝时应说明原因和修改方向",

    # ===== approve_tool ========================================================
    "approve_tool._desc": """\
审批或拒绝 teammate 被 rail 中断的工具调用。\
收到工具审批请求后，Leader 应调用此工具反馈 approved、feedback 和 auto_confirm。""",
    "approve_tool.member_id": "发起工具审批请求的成员 ID",
    "approve_tool.tool_call_id": "待恢复的中断 tool_call_id",
    "approve_tool.approved": "是否批准这次工具调用",
    "approve_tool.feedback": "可选审批反馈",
    "approve_tool.auto_confirm": "后续同名工具是否自动批准",

    # ===== list_members ========================================================
    "list_members._desc": """\
列出所有团队成员及其状态。\
**场景**: 收到启动指令后第一步调用，了解团队组成和各成员专长""",

    # ===== task_manager ========================================================
    "task_manager._desc": """\
团队任务管理工具（仅 Leader 可用）。\
**任务应聚焦于可交付成果和验收标准，不要规划具体执行步骤。**

- **add**（默认）: 通过 `tasks` 数组传入（单个任务也用数组包裹）。\
每个 task 含 title、content（目标和验收标准）、可选 task_id、depends_on。\
**场景**: 项目初始批量创建任务 DAG，或执行中补充新任务
- **insert**: 将任务插入已有 DAG 中间。\
传 title、content，可选 task_id、depends_on、depended_by（反向依赖）。\
**场景**: 发现遗漏的前置任务需要插入已有依赖链中
- **update**: 传 task_id、title/content。\
若任务已被认领，系统自动取消该成员执行并重置任务状态。\
**场景**: 根据成员反馈调整任务范围或补充细节
- **cancel**: 传 task_id。若任务已被认领，系统自动取消该成员执行。\
**场景**: 需求变更导致任务不再需要
- **cancel_all**: 取消所有任务和所有执行中的成员。\
**场景**: 目标根本性变更需要全部重新规划""",
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
    "view_task._desc": """\
查看任务信息。

- **get**: 传 task_id 获取单个任务详情。\
**场景**: 成员汇报进度后查看任务状态，或执行中回顾验收标准
- **list**: 传 status（可选）过滤任务列表 (pending/claimed/completed/cancelled/blocked)。\
**场景**: 查看整体进度、识别瓶颈任务、查找依赖任务的执行者以便协调
- **claimable**（默认）: 获取所有可认领的 pending 任务。\
**场景**: 查看就绪任务通知成员领取，或完成当前任务后寻找下一个""",
    "view_task.action": "查看模式，默认 claimable",
    "view_task.task_id": "get 时的任务ID",
    "view_task.status": "list 时按状态过滤：pending/claimed/plan_approved/completed/cancelled/blocked",

    # ===== claim_task ==========================================================
    "claim_task._desc": "领取一个就绪任务。只能领取 pending 状态且无人认领的任务，应选择匹配自己领域专长的任务",
    "claim_task.task_id": "要领取的任务ID，需为 pending 状态",

    # ===== complete_task =======================================================
    "complete_task._desc": """\
标记任务完成。完成后会自动解锁依赖本任务的下游任务，使其变为 pending 可领取状态。\
调用后应通过 send_message 向 Leader 汇报结果摘要""",
    "complete_task.task_id": "要标记完成的任务ID，需为自己已领取（claimed）的任务",

    # ===== send_message ========================================================
    "send_message._desc": """\
向团队成员发送消息。用于通知成员领取任务、回复进度汇报、升级阻塞问题或协调成员间依赖。

| `to` 值 | 含义 |
|---|---|
| 成员名称 | 点对点消息 |
| `"*"` | 广播给所有成员 — 开销与团队规模成正比，仅用于宣布全局决策、约束变更或需要所有人知晓的信息 |

你的普通文本输出对其他成员不可见 — 要通信必须调用此工具。\
来自队友的消息会自动送达，无需轮询。\
用名称称呼成员，不要使用内部 ID。\
转发消息时不要引用原文 — 原文已经渲染给了用户。""",
    "send_message.to": '收件人：填成员名称发送点对点消息，填 "*" 广播给所有成员',
    "send_message.content": "消息内容，应包含明确的行动指引或信息",
    "send_message.summary": "5-10 词摘要，用于消息预览和日志",

    # ===== enter_worktree =====================================================
    "enter_worktree._desc": """\
创建或进入一个隔离的 git worktree，给调用者独立的仓库工作副本。\
多成员并行修改同一仓库时使用，避免分支冲突和文件竞争。

进入 worktree 后所有文件操作限定在该副本内，退出前不影响主仓库。""",
    "enter_worktree.name": "worktree 名称（slug 格式，可选）。不提供则自动生成",

    # ===== exit_worktree ======================================================
    "exit_worktree._desc": """\
退出当前 worktree 会话。\
任务完成或需要切换工作上下文时调用。""",
    "exit_worktree.action": """\
退出策略：keep 保留 worktree 供后续使用，remove 删除并丢弃变更""",
    "exit_worktree.discard_changes": """\
当 action="remove" 且 worktree 有未提交变更时，必须设为 true 确认丢弃。\
防止意外丢失工作成果""",

    # ===== workspace_meta =====================================================
    "workspace_meta._desc": """\
团队共享工作空间的元数据操作（文件锁管理和版本历史查询）。\
多成员协作修改共享文件时使用，通过文件锁避免写冲突。""",
    "workspace_meta.action": """\
操作类型：lock（获取文件锁）、unlock（释放文件锁）、locks（列出所有活跃锁）、history（查看文件版本历史）""",
    "workspace_meta.path": "目标文件的相对路径（lock/unlock/history 时必填）",
}
