# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Chinese (cn) locale strings for agent team tools.

Key convention
--------------
- ``tool_name._desc``            — ToolCard description (lives in ``descs/cn/<domain>/<tool>.md``)
- ``tool_name.param``            — top-level param description
- ``tool_name.nested.param``     — nested schema param (e.g. task item)
"""

STRINGS: dict[str, str] = {
    # ===== build_team ==========================================================
    # build_team._desc lives in descs/cn/team/build_team.md
    "build_team.display_name": "团队的显示名（如「后端平台小队」），仅用于展示，不是标识符",
    "build_team.team_desc": "团队目标、交付范围和全局协作指令。所有成员可见此描述，写清协作目标和约束",
    "build_team.leader_display_name": "Leader 的显示名（纯展示，不作为标识符）",
    "build_team.leader_desc": "Leader 的角色描述（专业背景、领域专长），影响成员的信任和沟通方式",
    "build_team.enable_hitt": (
        "本次实例是否启用 HITT（Human in the Team）模式。可选 true / false / 不传。"
        "不传：继承 TeamAgentSpec.enable_hitt（spec 层能力天花板）。"
        "true：本次显式启用，要求 spec.enable_hitt=True，否则报错。"
        "false：本次显式禁用，spawn 任何 human_agent 的请求都会被拒绝，"
        "predefined_members 中声明的 HUMAN_AGENT 成员也会被跳过。"
        "用户表达「我要加入团队」时设为 true；明确不需要人类协作时设为 false"
    ),
    "build_team.enable_task_verification": (
        "本团队实例是否启用验证闸。可选 true / false / 不传（继承 TeamAgentSpec 配置）。"
        "开启后 teammate 完成配了 reviewer 的任务将进入 IN_REVIEW 等验证者裁决；"
        "关闭则直接标记完成，且 create_task / update_task 里写的 reviewer 会被忽略。"
        "用户配置为 false 时本参数不生效，实际生效值见返回结果的 task_verification"
    ),
    # ===== checkpoint ==========================================================
    # checkpoint._desc lives in descs/cn/member/checkpoint.md
    "checkpoint.name": "快照名（语义化 slug，如 code-ready）。后续 fork 通过此名引用",
    "checkpoint.description": "可选描述，说明为何在此打快照",
    "checkpoint.duplicate": (
        "⚠️ 快照保存失败：快照名 '{name}' 已被 {created_by} 占用（{description}）。"
        "你本次的快照**没有建立**，后续 fork 拿不到你的上下文。"
        "请用一个新的名字（例如 '{name}-v2'，或在名字末尾加序号/业务后缀）"
        "**立即再次调用 checkpoint** 完成保存——不要用纯文本说明代替实际调用。"
        "仅当你原本就是要继承那个已存在的快照、而不是保存自己的新快照时，才不需要重新调用。"
    ),
    # ===== clean_team ==========================================================
    # clean_team._desc lives in descs/cn/team/clean_team.md
    # ===== spawn_teammate ======================================================
    # spawn_teammate._desc lives in descs/cn/member/spawn_teammate.md
    "spawn_teammate.member_name": (
        "[公开] 成员唯一名（语义化 slug，如 backend-dev-1，DNS label 风格 kebab-case）。"
        "**首字符必须是小写英文字母（a-z），其后仅允许小写字母、数字（0-9）和连字符（-）**；"
        "禁止大写字母、下划线、空白、中文及其他非 ASCII 字符。"
        "同时作为主键和消息/审批/任务路由键，在同一团队内必须唯一"
    ),
    "spawn_teammate.display_name": (
        "[公开] 成员的显示名（如「后端开发专家」），仅用于展示，不用于路由。"
        "会注入所有其他成员的 system prompt 并由 list_members 返回，禁止写入私密信息"
    ),
    "spawn_teammate.desc": (
        "[公开] 成员的长期角色画像，包括专业背景、核心专长、"
        "优先认领的任务类型、协作风格以及不负责的边界，用于任务匹配和角色定位。"
        "会注入所有其他成员的 system prompt 并由 list_members 返回，"
        "禁止写入对成员的内部考量、敏感目标或机密策略"
    ),
    "spawn_teammate.prompt": (
        "[私有，仅该成员自己可见] 成员的长期工作约定，注入该成员自己的 system prompt："
        "稳定遵循的工作风格、技术偏好、协作约束，"
        "以及只该让本成员知道的隐藏目标或敏感细节。"
        "不要写当前批次任务，也不要写'开始工作''查看任务列表'这类空泛启动语句"
    ),
    "spawn_teammate.model_name": (
        "可选。建议该成员使用的模型名称（如 gpt-4、claude-sonnet-4 等）；"
        "未指定时由系统自动选择合适的模型"
    ),
    "spawn_teammate.permissions": (
        "收窄该 teammate 的工具权限（只能收紧，不能放宽）。"
        "键为工具名，值为权限级别：'allow'、'ask' 或 'deny'。"
        "示例：{\"bash\": \"deny\", \"write_file\": \"ask\"}"
    ),
    "spawn_teammate.fork": (
        "从已有成员继承上下文，跳过重复的文件读取和搜索。"
        "true：继承调用者当前全部上下文。"
        "字符串（如 'code-ready'）：从该名称的 checkpoint 快照继承上下文。"
        "不传则新成员从空上下文启动。所有继承的消息中，SystemMessage 会被自动剥离——"
        "源成员的角色身份不会泄漏给新成员"
    ),
    "spawn_teammate.fork_source": (
        "上下文来源成员名。不填默认从 leader 取。填某 teammate 名（如 'understander'）"
        "则从该成员取上下文。该成员必须已通过 spawn_teammate 拉起来，且为 in-process 模式"
    ),
    "spawn_teammate.fork_mode": (
        "保留 checkpoint 的哪一侧。可选值：'full'（源成员全部上下文，fork=true 时的默认）、"
        "'before'（checkpoint 之前的消息，命名 fork 时的默认）、"
        "'after'（从 checkpoint 起的消息）、"
        "'keep_before_compact_after'（保留前、把后压缩为摘要）、"
        "'keep_after_compact_before'（保留后、把前压缩为摘要）。仅配合命名 checkpoint fork 使用"
    ),
    # ===== spawn_human_agent ===================================================
    # spawn_human_agent._desc lives in descs/cn/member/spawn_human_agent.md
    "spawn_human_agent.member_name": (
        "[公开] 人类成员唯一名（语义化 slug，如 product-owner，DNS label 风格 kebab-case）。"
        "**首字符必须是小写英文字母（a-z），其后仅允许小写字母、数字（0-9）和连字符（-）**；"
        "禁止大写字母、下划线、空白、中文及其他非 ASCII 字符。"
        "同时作为主键和消息/审批/任务路由键，在同一团队内必须唯一"
    ),
    "spawn_human_agent.display_name": (
        "[公开] 人类成员的显示名（如「产品负责人」），仅用于展示，不用于路由。"
        "会注入所有其他成员的 system prompt 并由 list_members 返回，禁止写入私密信息"
    ),
    "spawn_human_agent.desc": (
        "[公开] 人类成员的角色画像与职责范围，用于展示与持久化描述，"
        "并注入其他成员的 system prompt、由 list_members 返回。"
        "真人通过 HumanAgentInbox 驱动该成员；模型与启动提示由框架内置模板托管，无需在此提供"
    ),
    # ===== spawn_bridge_agent ==================================================
    # spawn_bridge_agent._desc lives in descs/cn/member/spawn_bridge_agent.md
    "spawn_bridge_agent.member_name": (
        "[公开] 桥接成员唯一名（语义化 slug，如 remote-claude-1，DNS label 风格 kebab-case）。"
        "**首字符必须是小写英文字母（a-z），其后仅允许小写字母、数字（0-9）和连字符（-）**；"
        "禁止大写字母、下划线、空白、中文及其他非 ASCII 字符。"
        "同时作为主键和消息/审批/任务路由键，在同一团队内必须唯一"
    ),
    "spawn_bridge_agent.display_name": (
        "[公开] 桥接成员的显示名（如「远程 Claude」），仅用于展示，不用于路由。"
        "会注入所有其他成员的 system prompt 并由 list_members 返回，禁止写入私密信息"
    ),
    "spawn_bridge_agent.desc": (
        "[公开] 桥接成员的对外花名册描述，仅供其他成员在 list_members / 团队 roster 中识别该成员。"
        "可选；会注入其他成员的 system prompt 并由 list_members 返回，禁止写入私密信息"
    ),
    "spawn_bridge_agent.prompt": (
        "[私有] 远程 agent 据此扮演角色的系统提示词（该成员自己的私有工作设定），"
        "**必填**：通过 adapter.connect 下发给远程，远程据此充当本成员。仅本成员自己可见，不进他人花名册"
    ),
    "spawn_bridge_agent.mailbox_inject_mode": (
        "控制团队消息被自动转发给远程 agent 时的形态："
        "'passthrough'（默认）= 仅加最简发送者前缀直传；"
        "'rephrase' = 包装完整发送者上下文（角色、描述、相关任务）"
    ),
    "spawn_bridge_agent.protocol": (
        "协议标识（如 'a2a' / 'acp' / 'claudecode'）。"
        "目前作为元数据保留，用于后续 BridgeProtocolAdapter 适配器查找；空字符串表示尚未绑定适配器"
    ),
    "spawn_bridge_agent.adapter_config": (
        "协议适配器配置（如 endpoint、auth、relay_timeout_s 等），"
        "原样透传给 BridgeProtocolAdapter.connect。结构由具体适配器实现自行定义"
    ),
    "spawn_bridge_agent.model_name": (
        "可选。本地调度 LLM 的模型名称（如 gpt-4、claude-sonnet-4 等）；"
        "未指定时由系统自动选择。注意远程 agent 的模型在其自身侧，不由此字段控制"
    ),
    # ===== spawn_external_cli ===================================================
    # spawn_external_cli._desc lives in descs/cn/member/spawn_external_cli.md
    "spawn_external_cli.member_name": (
        "[公开] CLI 成员唯一名（语义化 slug，如 cli-coder-1，DNS label 风格 kebab-case）。"
        "**首字符必须是小写英文字母（a-z），其后仅允许小写字母、数字（0-9）和连字符（-）**；"
        "禁止大写字母、下划线、空白、中文及其他非 ASCII 字符。"
        "同时作为主键和消息/审批/任务路由键，在同一团队内必须唯一"
    ),
    "spawn_external_cli.display_name": (
        "[公开] CLI 成员的显示名（如「Claude CLI 编码助手」），仅用于展示，不用于路由。"
        "会注入所有其他成员的 system prompt 并由 list_members 返回，禁止写入私密信息"
    ),
    "spawn_external_cli.desc": (
        "[公开] 该 CLI 成员的对外花名册描述，仅供其他成员在 list_members / 团队 roster 中识别。"
        "可选；会注入其他成员的 system prompt 并由 list_members 返回，禁止写入私密信息"
    ),
    "spawn_external_cli.prompt": (
        "[私有] 该 CLI 成员的私有系统提示词，CLI 据此扮演本成员角色。**必填**。"
        "仅本成员自己可见，不进他人花名册"
    ),
    "spawn_external_cli.cli_agent": (
        "要拉起的第三方 CLI agent 类型标识，如 'claude'（claudecode）或 'codex'。"
        "取值必须命中 spec.external_cli_agents 中预先声明的某条静态配置——"
        "具体启动命令、工作目录、MCP 注入等都在那条配置里，本字段只负责按名引用"
    ),
    "spawn_external_cli.model_name": (
        "可选。仅当用户明确指定该第三方 Agent 使用的模型名称时填写。"
        "你不得自行选择、推断或补全；用户未明确指定时必须省略，使该 Agent 使用其自身默认模型"
    ),
    "spawn_external_cli.fallback_model_name": (
        "必填。必须从团队模型池中选择，并根据该第三方 Agent 支持的模型调用协议选择兼容模型。"
        "该第三方 Agent 使用自身默认模型但认证不可用时，将使用此模型自动回退；"
        "仅对运行时明确报告的认证失败生效。模型不存在、协议不兼容或该 Agent 不支持认证回退时，"
        "仍可使用其自身默认模型，但不启用自动回退"
    ),
    # ===== shutdown_member =====================================================
    # shutdown_member._desc lives in descs/cn/member/shutdown_member.md
    "shutdown_member.member_name": "要请求关闭的成员 member_name（语义化 slug，不是显示名）",
    "shutdown_member.force": "是否强制关闭，默认 false。仅在成员卡死、长期无响应或无法正常收尾时使用",
    # ===== approve_plan ========================================================
    # approve_plan._desc lives in descs/cn/member/approve_plan.md
    "approve_plan.plan_id": "成员提交的一版执行计划 ID；Leader 使用该字段精确审批某一版计划",
    "approve_plan.approved": "是否批准当前计划。true 表示进入实施，false 表示退回修改",
    "approve_plan.feedback": "审批反馈。拒绝时应说明原因和修改方向；批准时可补充约束、提醒或额外要求",
    # ===== submit_plan ==========================================================
    "submit_plan._desc": "在 plan_mode 任务执行前提交已写好的执行计划 Markdown 文件",
    "submit_plan.task_id": "执行前需要提交计划的任务 ID",
    "submit_plan.plan_id": "可选。成员计划 ID；不传时系统自动生成。Leader 后续用该 plan_id 审批",
    "submit_plan.plan_path": "成员已经写好的 Markdown 计划文件路径；系统会复制为受管快照供 Leader 审批",
    # ===== approve_tool ========================================================
    # approve_tool._desc lives in descs/cn/member/approve_tool.md
    "approve_tool.member_name": "发起该工具审批请求的成员 member_name（语义化 slug，不是显示名）",
    "approve_tool.tool_call_id": "待恢复的中断 tool_call_id，应与当前审批请求中的工具调用一致",
    "approve_tool.approved": "是否批准这次工具调用。true 表示允许继续，false 表示拒绝并要求调整方案",
    "approve_tool.feedback": "审批反馈。拒绝时应说明原因和替代方向；批准时可补充边界、风险提醒或额外约束",
    "approve_tool.auto_confirm": "是否对后续同名工具自动批准。默认 false；仅在明确接受该类工具后续继续使用时开启",
    # ===== list_members ========================================================
    # list_members._desc lives in descs/cn/member/list_members.md
    # ===== create_task ========================================================
    # create_task._desc lives in descs/cn/task/create_task.md
    "create_task.tasks": "任务列表（单个任务也用数组包裹）",
    "create_task.task.task_id": "自定义任务 ID，便于依赖引用（不提供则自动生成）",
    "create_task.task.title": "任务标题，简明描述任务目标",
    "create_task.task.content": "任务详细内容，包含目标和验收标准",
    # Both create_task variants expose assignee. Autonomous treats it as
    # optional; scheduled requires it.
    "create_task.task.assignee": (
        "承担该任务的成员名称；该成员必须已存在且不能是 leader。自主模式可选，未填写则进入公共认领池；"
        "调度模式必填，成员不会自主认领"
    ),
    "create_task.task.depends_on": "前置依赖的任务 ID 列表；可引用本次调用中一起创建的任务或已有任务",
    "create_task.task.depended_by": "需要等待本任务完成的已有任务 ID 列表（反向依赖）；不得引用本次调用创建的任务——批内依赖一律用对方的 depends_on 表示",
    "create_task.task.reviewer": (
        "该任务的验证者列表（可选，可多个），每一项为包含 type/instruction "
        "的对象。type 可选值：verifier / inspector / challenger。"
        "reviewer_id 由系统按类型自动编号，无需填写。"
    ),
    "create_task.task.reviewer_type": "验证者类型：verifier / inspector / challenger",
    "create_task.task.reviewer_id": "验证者标识名称（系统自动编号，无需手动填写）",
    "create_task.task.reviewer_instruction": "验证侧重点的补充描述（verifier：验证方法指引；inspector：打分维度表；challenger：不需要）",
    "create_task.task.max_review_rounds": (
        "该任务验证返工的轮数上限（可选，整数 ≥1，需同时配 reviewer）；不传用团队默认值。"
        "验证不通过会打回重做开新一轮，超过上限后不再自动打回，而是升级给你处置"
    ),
    # ===== view_task ===========================================================
    # view_task._desc lives in descs/cn/task/view_task.md
    "view_task.action": (
        "查看模式：'list'（默认，所有任务摘要）、'get'（单个任务详情，需传 task_id）、"
        "'claimable'（可认领的 pending 任务）、'in_review'（指派给你验证、正在 in_review 的任务）"
    ),
    "view_task.task_id": "任务 ID — action=get 时必填，其他模式忽略",
    "view_task.status": (
        "仅 action=list 时使用的状态过滤："
        "pending/blocked/planning/in_progress/in_review/completed/cancelled，不传则返回全部"
    ),
    # ===== update_task =========================================================
    # update_task._desc lives in descs/cn/task/update_task.md
    "update_task.task_id": "要更新的任务 ID，传 '*' 取消所有任务",
    "update_task.status": "设为 'cancelled' 取消任务",
    "update_task.title": "新任务标题",
    "update_task.content": "新任务内容",
    "update_task.assignee": "指派任务的目标 member_name（仅当任务当前无 assignee 时生效）。系统会向被指派成员发送通知",
    "update_task.reviewer": (
        "设置该任务的验证者列表（传空列表清除验证），每一项为包含 type/instruction "
        "的对象。type 可选值：verifier / inspector / challenger。"
        "reviewer_id 由系统按类型自动编号，无需填写。"
    ),
    "update_task.reviewer_id": "验证者标识名称（系统自动编号，无需手动填写）",
    "update_task.reviewer_instruction": "验证侧重点的补充描述（verifier：验证方法指引；inspector：打分维度表；challenger：不需要）",
    "update_task.reviewer_type": "验证者类型：verifier / inspector / challenger",
    "update_task.max_review_rounds": (
        "设置该任务验证返工的轮数上限（整数 ≥1，任务需已配或同时配 reviewer）。"
        "超过上限后验证失败不再自动打回，而是升级给你处置"
    ),
    "update_task.add_blocked_by": "要添加为新依赖的任务 ID 列表（本任务将被阻塞直到这些任务完成）",
    "update_task.error_human_agent_locked_cancel": (
        "任务 {task_id} 由仍在团队中的人类成员认领，不允许取消；请通过 send_message 与其协商。"
        "若其确实无法继续，可先用 shutdown_member(force=false) 让其退出团队，退出后该任务即可取消或改派"
    ),
    "update_task.error_human_agent_locked_reassign": (
        "任务 {task_id} 由仍在团队中的人类成员认领，不能改派给 {new_assignee}；该任务须由这位人类本人完成。"
        "若其确实无法继续，可先用 shutdown_member(force=false) 让其退出团队，退出后该任务即可改派"
    ),
    "update_task.error_human_agent_locked_edit": (
        "任务 {task_id} 由仍在团队中的人类成员认领，不允许修改其标题/内容；请通过 send_message 与其协商。"
        "若其确实无法继续，可先用 shutdown_member(force=false) 让其退出团队，退出后该任务即可取消或改派"
    ),
    # ===== claim_task =========================================================
    # claim_task._desc lives in descs/cn/task/claim_task.md
    "claim_task.task_id": "要领取或完成的任务 ID",
    "claim_task.status": "目标状态：'claimed'（领取）或 'completed'（完成）",
    # ===== member_complete_task ===============================================
    # member_complete_task._desc lives in descs/cn/task/member_complete_task.md
    "member_complete_task.task_id": "要标记完成的任务 ID（必须是 leader 已经指派给你的任务）",
    "member_complete_task.note": "可选的完成说明，便于团队了解你的执行结果或后续注意事项",
    # ===== verify_task ========================================================
    # verify_task._desc lives in descs/cn/task/verify_task.md
    "verify_task.task_id": "要验证的任务 ID（必须是指派给你验证、当前处于 in_review 的任务）",
    "verify_task.decision": "验证结论：verifier/challenger 投 'pass'/'fail'；inspector 投 0~1 的浮点分数（如 '0.85'）",
    "verify_task.feedback": "验证反馈（打回时会定向发给 author 指导返工，通过时可选）",
    # ===== send_message ========================================================
    # send_message._desc lives in descs/cn/message/send_message.md
    "send_message.to": (
        '单个收件人：填 member_name（如 "backend-dev-1"）发送点对点 DM/私聊，仅你与该成员可见；'
        '填 "user"（仅 teammate 用于回复用户，leader 调用会被拒绝）；'
        '填 "*" 广播到团队频道 channel，所有成员可见——一次广播会唤醒每一个成员各跑一轮 '
        "LLM 交互，开销与团队规模成正比，仅用于全员必须知晓的公告，务必慎用。"
        "多播不要填写本字段，改用 targets"
    ),
    "send_message.targets": (
        '多播收件人数组（如 ["m1","m2"]）：同一份内容分别发给每个成员，'
        "开销随接收人数线性增长，同等规模下比广播更贵，仅在必要时使用；"
        '禁止包含 "*" 或 "user"。不能与 to 同时填写'
    ),
    "send_message.content": "消息内容，应包含明确的行动指引或信息",
    "send_message.summary": "5-10 词摘要，用于消息预览和日志",
    "send_message.error_leader_to_user": "Leader 不能 send_message 给 'user'。请直接用普通回复输出给用户。",
    "send_message.error_content_too_long": (
        "'content' 过长（{actual} 字符，上限 {limit}）：这个体量的内容是产物，不是消息。"
        "先用 write_file 把正文写到团队共享产物目录（见团队信息块「团队共享工作空间」的最终产物目录）下的文件，再重发本消息，"
        "content 里只写文件路径加一两句摘要。不要为了绕过本限制而把正文拆成多条消息。"
    ),
    # ===== send_message_scheduled (scheduled-mode member variant) ==============
    # send_message_scheduled._desc lives in descs/cn/message/send_message_scheduled.md
    # ``content`` / ``summary`` are reused verbatim from the send_message keys
    # above — only the recipient semantics differ, so only ``to`` is redefined.
    "send_message_scheduled.to": (
        '收件人：只能是 "leader"（角色名，系统自动投递给真正的 Leader；'
        '用于汇报进展、完成结果、阻塞、改派请求）'
        '或 "user"（仅当收到的消息来源是 user 时用于回复）。'
        "本模式下不能发给其他成员，也不支持多播和广播"
    ),
    # NOTE: worktree tools (enter_worktree / exit_worktree) live in
    # ``openjiuwen.harness.tools.worktree`` and resolve their description
    # / param schema via ``harness.prompts.tools`` providers — no entries
    # in this dict.
    # ===== workspace_meta =====================================================
    # workspace_meta._desc lives in descs/cn/workspace/workspace_meta.md
    "workspace_meta.action": "操作类型：lock（获取文件锁）、unlock（释放文件锁）、locks（列出所有活跃锁）、history（查看文件版本历史）",
    "workspace_meta.path": "目标文件的相对路径（lock/unlock/history 时必填）",
    # ===== swarmflow / structured_output ======================================
    # swarmflow._desc lives in descs/cn/workflow/swarmflow.md
    # structured_output._desc lives in descs/cn/common/structured_output.md (无固定参数，schema 动态)
    "swarmflow.script_path": (
        "磁盘上的 swarmflow 脚本文件路径——一个 Python 模块，含顶层 META（纯字面量）与 "
        "async def run(args)，脚本体用 from swarmflow import 引入 agent()/parallel()/pipeline() "
        "等原语。与内联 script 同为当前已接通执行的来源，适合已落盘 / 需反复迭代或 resume 的脚本。"
    ),
    "swarmflow.script": (
        "自包含的内联 swarmflow 脚本源码（免去先写盘）。必须以顶层 META（纯字面量，无变量 / 函数调用 / "
        "f-string）开头，后跟 async def run(args)，脚本体用 agent()/parallel()/pipeline()/phase() 等原语。"
        "已接通执行——简单场景优先用它，框架会自动把源码落到该工作流的 journal 目录再运行。"
    ),
    "swarmflow.name": (
        "已保存 / 具名 swarmflow 工作流的名称，解析为一个自包含脚本来运行。"
        "接口已就位、执行推进中——当前请改用 script_path。"
    ),
    "swarmflow.resume_id": (
        "要续跑 / 控制的上次运行 run_id。单独传用于断点续跑（内容未变的 agent() 调用瞬时复用缓存）；"
        "配合 action 参数控制正在运行的工作流——action='pause' 暂停、'resume' 恢复、'stop' 停止（不停 session）。"
    ),
    "swarmflow.action": (
        "对已有运行的控制动作：'pause' 暂停、'resume' 恢复、'stop' 停止（需同时传 resume_id）。"
    ),
    "swarmflow.args": (
        "传给脚本 async def run(args) 的可选参数，作为**字符串**原样传入（如研究问题、目标路径）。"
        "脚本内自行解析（需结构化输入可在 run 里 json.loads）。"
    ),
    "swarmflow_worker.schema": (
        "你是一名单次执行的 swarmflow 工作节点。阅读用户消息中的任务，完成工作，"
        "然后**必须**调用 `structured_output` 工具**恰好一次**，传入符合其输入 schema "
        "的结构化结果。重要提示：`structured_output` 是**唯一**的结果提交方式——如果你"
        "不调用它，任务被视为失败，你的文本输出将被丢弃。禁止将结果作为纯文本输出"
        "——结果只能通过工具调用被捕获。调用 `structured_output` 后立即停止。"
    ),
    "swarmflow_worker.free": (
        "你是一名单次执行的 swarmflow 工作节点。阅读用户消息中的任务，完成工作，"
        "并将答案作为你的最终消息返回。"
    ),
    "structured_output.reminder": (
        "【重要提醒】你必须通过调用 `structured_output` 工具来提交结果，不要把结果"
        "写在文本中。这是唯一的结果提交方式，不调用该工具=任务失败。"
    ),
    # ===== async control tools (list / output / cancel) =======================
    # async_tasks_list._desc / async_task_output._desc / async_task_cancel._desc
    # live in descs/cn/async_task/*.md
    "async_task_output.task_id": "要查询的后台任务 id（来自启动工具返回的 task_id）。",
    "async_task_output.block": (
        "是否阻塞等待任务进入终态：true 时轮询至完成/失败或超时，默认 false 立即返回当前状态。"
    ),
    "async_task_output.timeout": "block=true 时的最大等待毫秒数（默认 30000，上限 600000）。",
    "async_task_cancel.task_id": "要取消的后台任务 id。",
}
