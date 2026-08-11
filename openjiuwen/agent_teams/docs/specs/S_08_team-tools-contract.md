# Team Tools Contract

## `TeamBackend` lifecycle callbacks

`TeamBackend.__init__` accepts `on_team_built` and `on_team_cleaned`
callbacks for checkpoint DB lifecycle state. `on_team_built` fires after
`build_team()` creates the team DB row and initial members; `TeamAgent`
persists `db_state=created` and flushes the checkpoint. `on_team_cleaned`
fires immediately after `clean_team()` deletes the team DB row, before
best-effort filesystem cleanup and event publishing; `TeamAgent` persists
`db_state=cleaned`, flushes the checkpoint, and latches
`TeamAgentState.team_cleaned`. Tool `invoke(**kwargs)` must not receive or
mutate the session directly; checkpoint lifecycle writes stay behind the
`TeamBackend -> TeamAgent` callback boundary.

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | `openjiuwen/agent_teams/tools/` |
| 最近一次修订日期 | 2026-08-10 |
| 关联 feature | F_10_temporary-leader-clean-team-stream-end.md、F_13_human-agent-send-message.md、F_24_agent-time-awareness.md、F_38_team-teammate-worktree-isolation-agenttool.md、F_55_create-task-atomic-graph-and-depended-by-contract.md、F_57_tool-variants-and-templated-descriptions.md、F_59_condition-named-task-state-machine-with-verify-gate.md、F_62_scheduled-dispatch-runtime-and-review-voting.md、F_64_message-channel-policy-and-content-size-guard.md、F_75_fork-context-inheritance.md、F_76_leader-progressive-policy-disclosure.md |

## 范围 / 边界

本规约只管 **`agent_teams/tools/` 这一层暴露给 LLM 的"团队工具集合"**：
工厂入口、`ToolCard` 命名、描述文本如何分发到磁盘、按角色筛选的逻辑、
`teammate_mode` / `exclude_tools` / `lang` 这几个工厂参数的语义。

**不管**的事情：

- `TeamBackend` / `TeamTaskManager` / `TeamMessageManager` 的内部实现——
  那是后端层，工具只是它们的 LLM 适配器。工具不直接读写 `TeamDatabase`。
- `TeamToolRail` 怎么把工具挂到 DeepAgent——那是 `rails/team_tool_rail.py`
  的事，本规约只规定它必须经过 `create_team_tools` 这一道门。
- `WorkspaceMetaTool` / `EnterWorktreeTool` / `ExitWorktreeTool`——
  workspace 工具属于 `team_workspace/` 子系统，worktree 工具属于
  `harness/tools/worktree`。只有 `WorkspaceMetaTool` 会由 `TeamToolRail`
  在工厂返回值之上 **追加**挂载；team 场景不再把
  `EnterWorktreeTool` / `ExitWorktreeTool` 作为成员工具暴露，也不能通过
  `exclude_tools` 改写这条边界。
- 系统提示词（`prompts/`）。提示词写"角色身份与决策原则"，工具描述写
  "操作过程"，两边内容禁止重复——这是分层契约。
- 运行时硬编码字符串（dispatcher 通知 / 默认 desc）走 `agent_teams/i18n.py`，
  本规约只管工具描述文本。

## 不变量

下列断言在任意时刻必须为真。违反任何一条都属于设计退化，不是"暂时绕一下"。

1. **唯一工厂**：从 `agent_teams.tools` 拿团队工具的合法路径只有
   `create_team_tools(...)`。其他模块不得直接 `import` 具体工具类
   （`BuildTeamTool` 等）来自行装配——绕开工厂就绕开了角色筛选、`teammate_mode`
   门禁、`exclude_tools` 屏蔽和 `_wrap_invoke_with_logging` 包装。
2. **ToolCard ID 全局前缀**：每个团队工具的 `ToolCard.id` 形如
   `team.{name}`。`name` 在团队工具集合内全局唯一，不允许跨角色重名。
   下游（rails、日志、UI 标签、Runner.resource_mgr）按 `team.` 前缀解析。
3. **ID 在 inprocess spawn 模式下需要再追加 team/member 后缀**：
   `Runner.resource_mgr` 是进程全局的；多成员共进程时 `TeamToolRail.init`
   的 `qualify_ids=True` 分支会把每个 ID 改写成
   `team.{name}.{team_name}.{member_name}`。这是 ID 命名约定的合法**扩展**
   而不是**例外**——在 ResourceManager 层看到的 ID 仍然以 `team.` 开头。
4. **描述文本是行为契约，不是 feature 摘要**。每条工具描述必须显式覆盖
   "什么时候调"、"什么时候不要调"、"昂贵操作的代价信号"。把工具说成
   "send a message" 是简介；说成"广播是 team-size 线性的，少用"才是契约。
5. **长描述住 Markdown，参数串住 dict**：超过几行的 `_desc` 一律落到
   `tools/locales/descs/<lang>/<domain>/<desc_key>.md`；参数描述 / 短串留在
   `locales/<lang>.py` 的 `STRINGS`。Markdown 优先级高于 dict——同一
   `_desc` 同时存在两处时 Markdown 覆盖 dict（迁移完成后必须删 dict 项）。
   `desc_key` 通常等于工具 `name`，但**同一工具的不同形态各有自己的 key**
   （`send_message` / `send_message_scheduled`）——形态类自己选 key，见不变量 18。
6. **缺失即报错，不静默回退**：`Translator` 找不到 `_desc` 抛
   `FileNotFoundError`，找不到普通键抛 `KeyError`，找不到 `{{slot}}` 对应的
   片段文件抛 `FileNotFoundError`（消息带期望路径），渲染后仍残留 `{{` 抛
   `ValueError`——全部在构造期就炸；不允许返回空串、占位符、英文兜底等
   "善意"行为。特别地：**禁止把不完整的 kwargs 交给 `PromptTemplate.format`**，
   因为 `PromptAssembler.prompt_assemble` 会把缺失的 key 原样回填成 `{{key}}`
   字面量，静默泄漏给 LLM。槽由 loader 从模板自身枚举并强制填满。
   **唯一的例外是 capability 槽**：调用方用 `t(desc_key, omit={"<slot>"})`
   点名哪些槽因能力关闭而收敛为空串（`fork_usage` 是范本）。这不是"缺失回退"
   ——省略的槽由调用方显式列出，没被点名的槽照旧必须有片段文件，缺了照旧炸。
   omit 用的信号必须与塑造 schema 的信号同源，见不变量 12。
7. **每条 ToolCard 描述都过 Translator**。工具构造器拿到的是同一个
   `t: Translator` 闭包，`ToolCard.description` 必须由 `t(name)` 提供，
   不许在构造器里写硬编码字面量。
8. **计划与审批工具的模式门控**：`approve_plan` 只在
   `teammate_mode == "plan_mode"` 才进入 leader 工具集；`submit_plan` 只在
   `teammate_mode == "plan_mode"` 才进入 teammate 工具集；`build_mode` 下默认不包含。
   但当 `team_permissions_enabled=True` 时，`approve_tool` 在 `build_mode` 下也保留——
   leader 需用它解决 teammate ASK interrupt。human_agent 工具集不受实际影响。
9. **`exclude_tools` 是减法，不是注册口**。它从角色集合里**移除**给定名字，
   不能通过它注册新工具。新工具靠的是工厂里的静态 `all_tools` 字典。
10. **角色集合互相对称**：
    - leader = `LEADER_ONLY_TOOLS ∪ SHARED_TOOLS`（**dispatch-invariant**：leader 的
      工具*名字*不随 dispatch mode 变，`create_task` 只是换了形态）
    - teammate = `MEMBER_TOOLS_BY_DISPATCH[dispatch_mode]`，即
      `autonomous → MEMBER_ONLY_TOOLS ∪ SHARED_TOOLS`、
      `scheduled → MEMBER_ONLY_TOOLS_SCHEDULED ∪ SHARED_TOOLS`。未知 dispatch
      值直接 `KeyError`（下标而非 `.get`）——静默回退会把自主认领路径交给一个
      调度模式的成员。
    - human_agent = 显式枚举的 `HUMAN_AGENT_TOOLS`，**不沿用** `SHARED_TOOLS`。
    Human agent 不得拿到 `claim_task`——它属于**自主认领模式**的 teammate 路径，
    人类化身只能等 leader 通过 `create_task(..., assignee=...)` / `update_task(assignee=...)`
    显式指派；可运行的人类预指派任务由后端进入执行态，不要求人类自行认领。同理
    `scheduled` 的 teammate 也拿不到 `claim_task`：那里根本没有认领这回事。
    Human agent **可以**拿到 `send_message`，但其使用规则由 `team_hitt`
    prompt section 强约束：仅在用户当轮 Inbox 输入里明确下达「转告 /
    通知 / 回复 `<member>`」时调用，禁止自主发声。选择 prompt 而非
    `invoke()` 内的 caller-role 分支，是因为「该不该转发」属语义判断，
    适合 LLM 在 prompt 引导下处理；工具实现保持单一职责。
11. **`view_task` 是唯一对 leader / teammate / human_agent 三方都开放的
    任务读取工具**。autonomous 的 teammate 用 `claim_task`，human_agent 与
    scheduled 的 teammate 用 `member_complete_task`，leader 用 `update_task`
    ——三个写入路径不互相替代，**禁止用同一个工具按调用方角色分支**。哪个
    写入路径被注册是**装配期**的集合选择（不变量 10），不是 `invoke()` 里的
    `if`。`send_message` 是另一个三方共享的名字，但它有两个形态（不变量 18）；
    human_agent 视角下的语义约束在 prompt 层（见上一条），不在 `invoke()` 内。
12. **每个 role_type 是独立 spawn 工具**（`spawn_teammate` /
    `spawn_human_agent` / `spawn_bridge_agent` / `spawn_external_cli`），
    schema 扁平、`invoke` 直线、无 role 分支。能力关闭时对应工具**根本不
    注册**到 leader 工具集——`create_team_tools` 按 `hitt_enabled()` /
    `bridge_enabled()` / `external_cli_kinds()` 门控；`invoke` 内保留同名
    防御性检查作为运行时降级兜底并给出明确指引（如 `enable_hitt=False`）。
    human 成员的 `model_name` / `prompt` 直接不在 `spawn_human_agent`
    的 schema 中暴露（schema 即契约），无需运行时拒绝。`spawn_teammate`
    始终注册。
    后端的能力门是工具显式校验，不是隐藏断言。
    **门控的粒度可以细到属性**：`spawn_teammate` 永远注册，但上下文继承
    （F_75）是可选能力——`fork_enabled()`（即 `TeamAgentSpec.enable_fork`）
    为 False 时它的 `fork` / `fork_source` / `compact` 三个属性不进 schema，
    `checkpoint` 工具整个不注册。**schema、描述、工具注册三者必须由同一个
    信号门控**：描述里的 `{{fork_usage}}` 槽与这些属性同生同灭（不变量 6），
    否则 leader 会围绕一个它填不了的参数反复权衡——读得到、用不了的提示词
    比缺失的提示词更糟。属性级门控同样保留 `invoke` 内的防御性检查：MCP
    客户端直接调 `invoke`、不校验 schema，被省略的属性必须在那里被拒，且
    要拒在写成员行之前。
13. **每个 `TeamTool.invoke` 必须返回 `ToolOutput`，永不抛**。工具内部
    `try / except` 捕获后端异常，落 `team_logger.error`，转成
    `ToolOutput(success=False, error=...)` 返回；不允许把 `Exception`
    透出到 ability 层。
14. **`map_result` 是 LLM 看到的唯一文本**：工厂的 `_wrap_invoke_with_logging`
    会在 `invoke` 返回后调用 `tool.map_result(output)`，把结果包成
    `MappedToolOutput`，其 `__str__` 返回该文本。`ToolOutput.data` 仍保留
    给事件 / 日志等程序消费者用。新工具如果不显式覆盖 `map_result`，
    就只会得到 `json.dumps(data)` 的兜底——意味着 token 浪费，应当显式覆盖。
    `view_task` 的 list / detail 两级输出都把 `updated_at` 经
    `timefmt.format_time_context` 渲染为「绝对本地时间 + 相对差」，给 LLM
    任务停留时长的时间感（`map_result` 签名不可加参，内部取 `get_current_time()`，
    用 `updated_at is not None` 守卫 `exclude_none` 剔除的情况）。
15. **Card / Config 分层**：`ToolCard` 只承载可序列化的 `id` / `name` /
    `description` / `input_params`；`teammate_mode` / `model_config_allocator` /
    `on_teammate_created` 这类运行时句柄属于工具实例的私有字段，禁止下沉
    到 `ToolCard`。
16. **一成员一活跃任务，改派不取消成员**：一个成员同一时刻至多持有一个**活跃**任务。
    "活跃" = `{PLANNING, IN_PROGRESS, IN_REVIEW}`——三个 owned 非终态处境（plan 闸 / 执行 /
    verify 闸），两模式统一（见 F_59）。
    `ClaimTaskTool`（teammate 自认领）、`UpdateTaskTool`（leader 指派）、
    `TeamTaskManager.start_task`（调度器开工）在写状态前经
    `TeamTaskManager.get_other_active_task_id(member, exclude_task_id)`（DB 层单列 `task_id`
    投影 + `LIMIT 1` 的存在性探测，`status IN (PLANNING, IN_PROGRESS, IN_REVIEW)`，不物化该成员
    的全部活跃行）校验，命中即拒绝（`exclude_task_id` 放行幂等 re-claim / re-assign / re-start
    同一任务）；`UpdateTaskTool` 的校验落在 reassignment reset **之前**，
    拒绝时原任务与当前 owner 均不受扰动。改派执行中任务走
    `TeamTaskManager.reassign`——DAO 层**原子 CAS 交换 assignee**（任务全程 IN_PROGRESS，
    不经 PENDING），发 `TASK_REVOKED` 通知原 owner、`TASK_CLAIMED` 通知新 owner，
    **不发 `TASK_RELEASED`**（不会 spurious 唤醒空闲 teammate）、**不再** `cancel_member`
    （那会连带取消原 owner 的其它 claim 与 in-flight round）。见 F_54 / F_56。
17. **取消 / 编辑执行中任务用任务级信号，不取消成员**：cancel 一个执行中任务、或改其
    标题 / 内容，都**不再** `cancel_member`。cancel 发 `TASK_CANCELLED`（带 `member_name`）
    让该成员经 `on_task_cancelled` 停手；编辑**保持任务 IN_PROGRESS**（DAO 放开 PLANNING /
    IN_PROGRESS 可编辑，仅 IN_REVIEW 锁——验证期内容冻结），发 `TASK_UPDATED`（带 `member_name`）
    让该成员经 `on_task_updated` 重看后继续。**仍在团队中的**人类成员持有的任务对 cancel /
    reassign / **改标题·内容**三者都 leader-immutable（HITT 锁，key 在
    `TeamBackend.is_live_human_agent` / `live_human_agent_names`，**不是**裸 role——见
    `S_07` 运行约束 3），故这些信号的 `member_name` 永不指向一个**在队**的 human avatar。
    leader `shutdown_member` 让人类退队后锁即解除，其遗留任务按普通遗留任务 cancel /
    reassign，此时信号可以携带那个已退队人类的 `member_name`——它的 avatar 已无 runtime，
    没有 handler 消费，不产生行为。见 F_56。
18. **工具形态在装配期选择，不在 `invoke` 里分支**（F_57）。一个*形态*保持
    `ToolCard.id` / `name` 不变，替换 schema、描述与行为。形态选择发生在
    `create_team_tools` 构造 `all_tools` 的那一刻，用**模块级字面量表**
    （`_CREATE_TASK_CLASS` / `_SEND_MESSAGE_CLASS` / `_MEMBER_COMPLETE_DESC_KEY`）
    查表——形态是闭集，缺失组合抛 `KeyError`，不做注册表、不静默回退。
    形态间怎么共享代码**取决于共享的是数据还是行为**，用最轻的手段：`create_task`
    的两个形态各自独立，只共享模块级纯函数（`_task_node_schema` / `_validate_task_batch`），
    `invoke` / `map_result` 各写一遍；`send_message` 的两个形态共享 `_SendMessageBase`，
    因为共享的是真实投递行为（`_send` / `_multicast` / `_broadcast`），子类只有自己的
    `to` schema 与一条直线 `_dispatch`。**不为「形态就该有基类」的对称感去造 `_XxxBase`**。
    无论哪种，形态子类里都**零形态分支**，因此不变量 12（schema 扁平、invoke 直线、
    无 role 分支）对每个形态仍成立。
    `send_message` 的形态是 `(dispatch_mode, leader|member)` 二维：scheduled 下
    leader 仍要广播 / 多播 / `_auto_start_members`，只有成员侧收敛成
    `ReportToLeaderTool`（`to` 收成 `enum ["leader", "user"]`——`"leader"` 是
    **角色占位符**，工具在投递时经 `TeamBackend.resolve_leader_member_name()`
    翻译成真实 leader member_name。schema 不泄漏具体身份、构造期也不需要知道
    leader 是谁；leader 名不从装配链传，而是从 `team_info` DB 行（build_team
    写的 source of truth）查一次并缓存——leader 一个 team 内不变）。
    **这个 role 维度必须由类的选择吸收，不能塞进描述模板**——leader 与成员共享
    `ToolCard.name`，若按 dispatch 挑描述片段，scheduled 的 leader 会拿到与其
    行为相反的描述。`ReportToLeaderTool` 的 enum 是给宿主 LLM 的契约，
    `_dispatch` 里的白名单是给 MCP 客户端的执法（`mcp/server.py` 直接
    `invoke`，不校验 schema）——两层都必须有，不是重复。
    `SendMessageTool` 还在工具边界拒绝 leader `to = "user"`：leader 的普通文本输出
    已直接展示给用户，写入 user pseudo-recipient mailbox 会产生无人标记已读的消息。
    teammate / human_agent 发给 `user` 的既有语义保持不变。
    参数描述的**复用是免费的**（形态同 `name` → `t(tool, "param")` 同 key），
    **扩展就是加 key**；语义变了的同名参数用新的 desc_key 命名空间
    （`send_message_scheduled.to`）。**绝不让 resolver 学会 variant**。
19. **`create_task` 的 `assignee` 与任务图同一事务落库**（F_57 / F_59 / F_62）。scheduled
    形态的 `assignee` **必填**（scheduled 成员不自主认领，未指定 assignee 的 scheduled 任务不会被调度），随 `TaskGraphSpec` →
    `NewTaskSpec` 进同一次 `mutate_dependency_graph`。**每个任务一律 seed `PENDING`**
    （携带 assignee）：无依赖 → 停在 `PENDING(assignee)`（"已指派未开始"），带依赖 →
    refresh pass 翻成 `BLOCKED(assignee)`。指派与开工是两个事件，故创建期**不再** seed
    执行态。**例外**：`assignee` 是仍在队的人类成员且任务经 refresh 后可运行时，
    `TeamTaskManager` 在图事务提交后立即调用 `start_task()`，让任务进入 `IN_PROGRESS`
    （或 plan-mode 的 `PLANNING`）；依赖解除时同样由 `TeamTaskManager._handle_unblocked_tasks`
    对刚解锁的人类预指派任务执行这一步，且保留 `TASK_UNBLOCKED` 作为依赖解除事实。
    DAO 仍只负责 `BLOCKED <-> PENDING` 的依赖图刷新。
    禁止「建图后逐个 `assign()`、失败的记为 deferred」——那会给原子的
    `create_task` 引入不可回滚的部分成功态。`PENDING(assignee)` 的开工（build 成员 →
    `IN_PROGRESS`、plan 成员 → `PLANNING`，DAO `start_task(to_status=...)` 参数化）由
    `TeamTaskManager.start_task` 完成，调用方是 `agent/scheduling/` 的 `TeamScheduler`
    （见 `S_22`），或上述 human-agent 后端编排路径。scheduled 形态额外带可选 `max_review_rounds`（整数 ≥1，验证返工轮数
    上限，须伴随 `reviewer`，亦可经 `update_task` 事后设置）。
20. **verify 闸：reviewer 由 leader 指派，裁决策略按生效分发模式二分**（F_59 / F_62）。任务的
    `reviewer` 列（JSON member 名列表）经 `create_task` / `update_task` 由 leader 指派，可多个，
    须是真实成员且 ≠ `assignee`（不可自审）。author 经 `member_complete_task` /
    `claim_task(completed)` 完成时，`complete()` 按有无 reviewer 分流：有 → `IN_PROGRESS →
    IN_REVIEW`（发 `TASK_SUBMITTED_FOR_REVIEW`，不解依赖、author 不释放，同一 UPDATE 里
    `review_round += 1` 开新轮）；无 → 直接 `COMPLETED`。`verify_task` 守卫两种模式相同：任务须
    `IN_REVIEW`、caller ∈ `reviewer` 列且 ≠ author；进 autonomous / scheduled / human_agent
    成员集，不进 leader 集。裁决语义按 dispatch_mode（静态 spec 配置）二分，**描述随语义
    分离**（desc_key 形态 `_VERIFY_TASK_DESC_KEY`：`verify_task` / `verify_task_scheduled`）：
    - **autonomous** — 首裁即决：`pass` → `IN_REVIEW → COMPLETED`（解依赖，发 `TASK_VERIFIED`）
      / `fail` → `IN_REVIEW → IN_PROGRESS`（返工，`feedback` 定向 author，发
      `TASK_REVISION_REQUESTED`）。
    - **scheduled**（F_62）— 只记一票：追加票行（`team_review_vote_*`，重复调用 = 改票取最新）
      + 发 `TASK_REVIEW_VOTE`（带票数快照），**不翻转状态**；判定与翻转由 leader 调度器经
      `TeamTaskManager.settle_review(decision, feedback)`（无 reviewer 资格守卫、`IN_REVIEW`
      源态 CAS）完成，阈值/轮数/停摆语义见 `S_22`。
    `view_task(action=in_review)` 是 reviewer 拉取待验证任务的入口。
21. **dispatch_mode 是静态 spec 配置，`build_team` 不选择协调模式**（F_62）。系统提示词与
    工具描述按模式在构建期装配、每套一份、互不混写；`build_team` 的运行时开关只有
    `enable_hitt` 与 `enable_task_verification`（后者是提示词驱动的"验证预期"开关，
    **且本身受 dispatch 门控**，见不变量 21b；覆盖值随 spec 记录的 `dispatch_mode`
    一起写进 `team_info` 行——行是记录，spec 是运行时真相）。

21a. **`build_team` 的返回值承载 leader 的全部协同准则**（[[F_76]]）。leader 的系统提示词只留
    一段 bootstrap（身份 + build_team/swarmflow 分流 + "先建队"），role / workflow / dispatch /
    lifecycle / HITT / inbound-tags 全部由 `map_result` 附在建队结果之后下发，内容经
    `prompts.build_leader_policy_disclosure(...)` 按本次调用选定的模式裁剪。三条约束：
    **只在成功路径附加**；**HITT 段按 `output.data["enable_hitt"]` 即实际生效值 gate，不是 spec
    天花板**；`BuildTeamTool` 因此需要构造期拿到 `language` / `lifecycle` / `teammate_mode` /
    `team_mode` / `dispatch_mode`（`create_team_tools` 的 `team_mode` 参数即为此新增，它不改变
    任何工具的 schema）。这不违反不变量 21——模式仍由 spec 静态决定，`build_team` 只是**读**它
    来决定披露哪一套。

    - **`build_team` 因此必须对已存在的团队幂等**：它是准则的唯一交付点，所以"团队已经在了"不能
      是失败。`TeamBackend.build_team` 开头查团队行，命中则走 `_reattach_team` 接管——**写零行、
      注册零人**（名册与成员配置是既成事实，teammate 由 `recover_team` 从那份名册拉起），生效的
      verification flag **从行里读回而非重算**（团队既有配置优先于本次调用的能力参数），
      `on_team_built` 照常触发（本 session 的 checkpoint 必须记下团队行存在），
      **不发 `TeamCreated`**（什么都没被创建）。返回值首行区分 `Team created:` /
      `Existing team taken over:`，准则正文两条路径逐字相同。
      **要覆盖的场景是 `NEW_TEAM_IN_SESSION`**（新 session 接手已有团队）：child agent session 共享
      team session id，所以换 session 等于换掉 leader 的整段对话历史，那条带准则的 tool result
      不在了，而团队、名册、任务都还在。
    - **`COLD_RECOVER` 则相反：必须拒绝，不是幂等服务**。它是同一个 session 继续跑，历史恢复，
      准则还在其中，且那条 tool result 由 `team_policy` 重注入保证**不会被压缩掉**——再调一次
      永远拿不到新信息，只白烧一轮。`BuildTeamTool.invoke` 在 `backend.rejects_rebuild()` 为真时
      直接返回失败。判据是 `_history_restored`（由 `TeamAgent.recover_from_session` 置位，该方法
      即冷恢复入口）**与**团队行仍在，**两个条件缺一不可**：恢复出来的 leader 若在本轮被
      `CoordinationKernel.start` 的"上次清理没做完"分支执行了 `clean_team`，团队行已消失，
      那时它确实需要重新建队。

21b. **verify 闸是 dispatch 门控的能力，`update_task` 与 `build_team` 双层执法**（[[F_76]]）。`reviewer` /
    `max_review_rounds` 两个属性只在 `dispatch_mode == "scheduled"` 时进 `update_task` 的 schema，
    描述里的 `{{update_task_verify_gate}}` 槽用**同一个信号**门控——参数与讲这个参数的散文一起出现、
    一起消失。第二层在 `invoke`：偷传（MCP 客户端不过 schema）**报错拒掉，不静默剥离**，且在任何写库
    之前。静默剥离在这里是有害的——leader 会以为验证已开启，然后一直等一个永远不来的裁决。门控的实质
    理由是 autonomous 没有 `TeamScheduler` 唤起验证者，被推进 `IN_REVIEW` 的任务会永久停住并占死
    assignee 唯一的活跃任务名额。**不拆成两个工具类**：两形态 `invoke` 的 209 行里 177 行逐字相同且
    全是有状态行为，按本 spec 的形态判据应走属性级门控（同 `spawn_teammate` 的 fork），不是独立类。
    与不变量 21 不冲突——模式仍由 spec 静态决定，工具只是读它来决定挂不挂这两个属性。

    **`build_team` 的 `enable_task_verification` 走同一道门**：这个开关唯一能开关的东西就是
    verify 闸，而闸只在 scheduled 下存在（三个消费点——`create_task` 的 scheduled 形态剥 reviewer、
    `update_task` 剥 reviewer、`TeamScheduler._reconcile_reviews` 短路——没有一个在 autonomous 下
    可达）。所以 autonomous 时它既不进 schema，`{{build_team_verify_gate}}` 那节散文也一起消失，
    `invoke` 对偷传同样报错拒掉。**scheduled 时返回结果必须回带实际生效值**
    （`data["enable_task_verification"]` + `map_result` 的 `task_verification=`）：spec 天花板会把
    leader 要的 `True` 收窄成 `False`（`effective = spec and (arg if arg is not None else True)`），
    而这个开关**不像 `enable_hitt` / `enable_bridge` 那样撞天花板就 `raise_error`，是静默收窄**——
    回带生效值是 leader 唯一能发现自己没拿到验证闸的通道，否则它会围绕一个不存在的闸去配 reviewer。

22. **`send_message` 的 `content` 有硬上限，超限即拒、且提示如何改走文件通道**（F_64）。
    `tool_message.MAX_CONTENT_CHARS`（当前 2000）以**字符**计——不依赖 tokenizer、不按语言
    分支，这个界只需量级正确。校验落在 `_SendMessageBase.invoke` 里、**`_dispatch` 之前**：
    一处覆盖两个形态与全部三条投递路径（unicast / multicast / broadcast），也覆盖不校验
    schema 的 MCP 客户端（`mcp/server.py` 直接 `invoke`）。**这是通道纪律的执法，不是防
    DoS**——提示词说"成型产物落盘、消息只传路径"，但只有拒绝才真正阻止 LLM 把整份报告塞进
    消息；错误文案必须给出可执行的下一步（`write_file` → `.team/` → 重发路径 + 摘要），
    只说"太长"会让 LLM 原地重试或把正文拆成多条消息。
    **没有收件人豁免**——规则约束的是内容形态，不是收件人，`user` 也一样受限（用户经自己的
    助手 agent 读交接文件）。校验因此不看 `to`，是一条纯粹的长度判断。
    上限值同时出现在共用片段 `descs/<lang>/fragments/artifact_handoff_policy.md` 里（片段目录
    不按领域分，见「描述文本路径约定」）——
    让 LLM 事先知道界在哪，省掉一次撞墙往返；`test_tool_message.py` 断言描述里的数字与
    常量一致，防止两者漂移。

## 接口契约

### `create_team_tools` — 唯一工厂入口

```python
def create_team_tools(
    *,
    role: str,
    agent_team: TeamBackend,
    teammate_mode: str = "build_mode",
    dispatch_mode: str = "autonomous",
    on_teammate_created: Callable[[str], Awaitable[None]] | None = None,
    model_config_allocator: Callable[[str | None], "Allocation" | None] | None = None,
    exclude_tools: set[str] | None = None,
    lang: str = "cn",
) -> list[Tool]:
    """Build the role-appropriate team tool list and return them wrapped."""
```

参数语义：

| 参数 | 取值 | 行为 |
|---|---|---|
| `role` | `"leader"` / `"teammate"` / `"human_agent"` | 决定基础工具集——分别为 `LEADER_TOOLS` / `MEMBER_TOOLS_BY_DISPATCH[dispatch_mode]` / `HUMAN_AGENT_TOOLS`。其它字符串当作 teammate 走（落入 else 分支）。新增角色必须显式补一个集合常量，不要靠 fall-through。 |
| `dispatch_mode` | `"autonomous"` / `"scheduled"` | 任务如何到达成员。选择 `create_task` / `send_message` 的形态、`member_complete_task` 的 desc_key，以及成员工具集。未知值抛 `KeyError`。见不变量 18。 |
| `agent_team` | `TeamBackend` | 后端句柄，所有写操作（`build_team` / `spawn_*` / 任务 / 消息）通过它走，不绕过去直接打数据库或 messager。 |
| `teammate_mode` | `"build_mode"` / `"plan_mode"` | plan_mode 门禁。非 plan_mode 时从 allowed 集合里减掉 `approve_plan` / `submit_plan`，且未启用 team permissions 时也减掉 `approve_tool`。 |
| `on_teammate_created` | `Callable[[str], Awaitable[None]]` | leader 用 `send_message` 时若发现成员未启动，自动 startup 的回调；不传则没有 auto-start 行为。teammate / human_agent 不消费这个回调。 |
| `model_config_allocator` | `Callable[[str \| None], Allocation \| None]` | leader 的 `spawn_teammate` 调它选 model；不传则 spawn 出来的 teammate 无 allocation，由后端兜底。teammate 不消费。 |
| `exclude_tools` | `set[str]` 或 `None` | **减法**——从该角色 allowed 集合里再减一遍。不存在于 allowed 的名字静默忽略（因为减法对空集是恒等）。 |
| `lang` | `"cn"` / `"en"` | 选语言加载 `_desc`，缺省 `"cn"`。其它字符串走 cn 兜底（`make_translator` 内部 if/else）。 |

返回值：

- 顺序按工厂内 `all_tools` 字典声明序遍历后过滤；调用方不应该依赖具体顺序。
- 每个返回 `Tool` 的 `invoke` 已被 `_wrap_invoke_with_logging` 包过，
  调用一次会经历：debug 日志 → 原 `invoke` → `map_result` → 包成
  `MappedToolOutput`。

错误语义：

- 构造期 `Translator` 缺 `_desc` 抛 `FileNotFoundError`，缺普通键抛
  `KeyError`——发生在 `t("name")` 这一步，调用方应当让它向上传播，
  不要捕获后退化成"工具不可用"。
- `role` 不是 `"leader"` / `"human_agent"` 时静默走 teammate 分支。
  调用方传 `"leader_x"` 这种拼错名字的字符串拿到的是 teammate 工具集——
  这是工厂当前行为，不是契约保护，调用前应自己校验。
- `agent_team` 必须在调用前 `build_team` 完成；工具实例化只读 `agent_team`
  的属性引用（`task_manager` / `message_manager`），运行期再调后端方法。

### `Translator` 协议

```python
Translator = Callable[..., str]
# t(tool: str, key: str = "_desc", **kwargs: str) -> str
```

- `t(desc_key)` 等价 `t(desc_key, "_desc")`——返回描述文本，`{{slot}}` 已由
  loader 从 `fragments/` 填满（不接受 `**kwargs`）。
- `t(tool, "param_name")` 返回该参数 schema 的描述串。
- `t(tool, "nested.sub")` 用点号表示嵌套 schema 的参数键。
- `**kwargs` 只走 `STRINGS` 的 `str.format_map`（`{key}` 单大括号），**仅用于
  运行时错误消息**（如 `update_task.error_human_agent_locked_edit`）。参数描述
  一律是字面量。两条插值路径不要混：md 用 `{{slot}}`，dict 用 `{key}`。

### 描述文本路径约定

```
openjiuwen/agent_teams/tools/locales/
├── __init__.py                          # make_translator + _desc_index / _load_desc
│                                        #   / _slots_of / _load_fragment (均 @cache)
├── cn.py                                # STRINGS dict (cn)
├── en.py                                # STRINGS dict (en)
└── descs/
    ├── cn/<domain>/<desc_key>.md        # 优先于 STRINGS["<desc_key>._desc"]
    ├── cn/fragments/<slot>.md           # 共享片段，填 <desc_key>.md 里的 {{slot}}
    ├── en/<domain>/<desc_key>.md
    └── en/fragments/<slot>.md
```

`<domain>` 对齐工具实现文件：`team` / `member` / `task` / `message` / `async_task` /
`workflow` / `workspace` / `common`。两种语言的领域布局逐字相同。

- 文件名 = `desc_key`。默认等于工具 `name`（`build_team` →
  `descs/cn/team/build_team.md`）；**同一工具的不同形态各有自己的 key**
  （`send_message_scheduled.md`），由形态类在构造时选定（不变量 18）。
- **领域目录只是给人看的分组，`desc_key` 仍是扁平且全局唯一的命名空间**。解析走
  `_desc_index(lang)`——按语言递归扫一次建 `desc_key → 路径` 索引并缓存，调用方
  永远不写领域前缀，挪动一份描述的归属不需要改任何代码。两份文件同名 → 建索引时
  `ValueError`（否则模型读到哪一份取决于目录遍历顺序）。`fragments/` 不进索引：
  slot 名与 `desc_key` 是两套命名空间，且片段本就跨领域复用。
- Markdown 文件存在即覆盖 dict 中同 key 的 `_desc`。迁移完一条描述后
  **必须**把 dict 里的 `_desc` 项删掉，避免出现两个 source-of-truth。
- 占位符使用 `{{slot}}` 双大括号（`PromptTemplate`），**只允许出现在 md 里**，
  且只能由 `fragments/<slot>.md` 填充。片段是与形态无关的公共散文，可跨工具
  与跨形态复用（`artifact_handoff_policy` 同时服务两个 `send_message` 形态）。
  槽由 loader 从模板自身枚举、强制填满，缺一个就构造期炸（不变量 6）。
- **capability 槽**：描述一个可选能力的槽，由构造工具的一方传
  `t(desc_key, omit={"<slot>"})` 收敛为空串（`spawn_teammate` 的
  `fork_usage`）。omit 名单由调用方显式给出，且必须与塑造 schema 的是同一个
  信号——参数与讲这个参数的散文永远同生同灭（不变量 12）。渲染后统一 strip，
  所以位于文末的槽被省略时不留空行。
- 短描述（参数 / 短 `_desc`）保留在 `cn.py` / `en.py` 的 `STRINGS` 字典，
  不强制迁出去；多行长文本一律落 Markdown。**随场景变化的文案属于 md 槽
  或新 desc_key，不属于插值的 `STRINGS` 值。**

### 角色级工具集合

| 集合常量 | 成员 |
|---|---|
| `LEADER_ONLY_TOOLS` | `build_team`, `clean_team`, `spawn_teammate`, `spawn_human_agent`, `spawn_bridge_agent`, `spawn_external_cli`, `shutdown_member`, `approve_plan`, `approve_tool`, `create_task`, `update_task`, `swarmflow`, `async_tasks_list`, `async_task_output`, `async_task_cancel` |
| `MEMBER_ONLY_TOOLS` | `claim_task`, `submit_plan` |
| `MEMBER_ONLY_TOOLS_SCHEDULED` | `member_complete_task`, `submit_plan` |
| `SHARED_TOOLS` | `view_task`, `send_message`, `checkpoint`（gate `fork_enabled()`），`workspace_meta`（后者是死条目——不在 `all_tools`，由 `TeamToolRail.init` 单独 append） |
| `HUMAN_AGENT_TOOLS` | `view_task`, `member_complete_task`, `send_message` |
| `LEADER_TOOLS` | `LEADER_ONLY_TOOLS ∪ SHARED_TOOLS`（dispatch-invariant） |
| `MEMBER_TOOLS_BY_DISPATCH["autonomous"]` | `MEMBER_ONLY_TOOLS ∪ SHARED_TOOLS` |
| `MEMBER_TOOLS_BY_DISPATCH["scheduled"]` | `MEMBER_ONLY_TOOLS_SCHEDULED ∪ SHARED_TOOLS` |
| `MEMBER_TOOLS` | `MEMBER_TOOLS_BY_DISPATCH["autonomous"]` 的别名（向后兼容） |

`workspace_meta` 不在以上任何集合里：它由 `TeamToolRail.init` 在
`workspace_manager is not None` 时 **追加**注册（leader / teammate / human_agent
都吃同一份）。Team 场景下 `enter_worktree` / `exit_worktree` 不再作为成员工具追加；
worktree 隔离只通过 spawn 时的 `isolation="worktree"` 由 leader / 宿主创建。

### `teammate_mode` 的精确门禁

```python
if teammate_mode != "plan_mode":
    excluded = {"approve_plan", "submit_plan"}
    if not team_permissions_enabled:
        excluded.add("approve_tool")
    allowed = allowed - excluded
```

- `teammate_mode` 取 `"plan_mode"` 时 leader 拿到计划审批工具，teammate 拿到
  `submit_plan`。
- 其它取值（含默认 `"build_mode"`）剥离计划相关工具；`approve_tool` 在 team
  permissions 开启时保留给 leader 处理成员 ASK 中断。
- human_agent 不受此门禁的实际影响——它本来就不在这些集合里。

### `create_task` 的依赖表示契约（F_55）

`create_task` 的一次调用只有两个使用场景，边的表示方式随场景固定：

1. **批量创建任务图/子图**：批内任务之间的依赖**只用 `depends_on`** 表示，
   目标可以是同批任务（顺序无关，允许前向引用）或看板已有任务。
2. **楔入已有依赖链**：`depended_by` 列出需要等待新任务的**已有**任务——
   这是它唯一的用途。`depended_by` 指向同批任务是同一条边的冗余表示，
   在工具边界直接拒绝（错误信息教调用方改用对方的 `depends_on`）。

实现不变量：

- 整次调用经 `TeamTaskManager.add_graph` 折成**一次**
  `mutate_dependency_graph` 原子事务——全批成功或整体失败，失败时
  `ToolOutput.error` 透传底层真实 reason（环 / ID 冲突 / 引用不存在），
  不允许再出现"三选一猜测文案"。
- 工具边界校验：title/content 必填、批内 `task_id` 不重复、
  `depended_by` 不指向批内任务。存在性 / 环 / 终态拒绝留给 DAO 层。

## 数据结构

### `TeamTool`（基类）

```python
class TeamTool(Tool, ABC):
    def map_result(self, output: ToolOutput) -> str: ...
    async def stream(self, inputs, **kwargs): raise NotImplementedError
```

- 抽象基类。所有团队工具继承它，`invoke` 由各子类自己实现。
- 不支持 streaming——`stream` 显式抛，避免被通用调用路径误调用。

### `MappedToolOutput`

```python
class MappedToolOutput(ToolOutput):
    _mapped_content: str = PrivateAttr(default="")

    @classmethod
    def from_output(cls, output: ToolOutput, mapped_content: str) -> "MappedToolOutput": ...

    def __str__(self) -> str: return self._mapped_content
```

- `_wrap_invoke_with_logging` 构造它包住 `invoke` 的返回值。
- ability 层最终把工具结果转成 `ToolMessage.content` 是通过 `str(result)`，
  这里覆盖 `__str__` 是该约定的入口。`data` / `success` / `error`
  从原 `ToolOutput` 拷过来，程序消费路径无变化。

### `ToolCard`（每个团队工具构造时填）

| 字段 | 取值 | 说明 |
|---|---|---|
| `id` | `f"team.{name}"` | 全局命名空间。inprocess 下进一步追加 `.{team_name}.{member_name}`。 |
| `name` | 工具短名（`build_team` / `view_task` …） | 跨角色全局唯一；与 Markdown 描述文件名一一对应。 |
| `description` | `t(name)` 返回值 | 不允许硬编码字面量。Markdown 文件优先。 |
| `input_params` | JSON Schema | 所有 property `description` 也走 `t(name, "<param>")`。 |

### 工具内部 → 后端的依赖

工具不直接持 `TeamDatabase`：

```
BuildTeamTool          → TeamBackend
CleanTeamTool          → TeamBackend
SpawnMemberTool        → TeamBackend (+ model_config_allocator)
ShutdownMemberTool     → TeamBackend
ApprovePlanTool        → TeamBackend
ApproveToolCallTool    → TeamBackend
ListMembersTool        → TeamBackend
TaskCreateTool         → TeamBackend.task_manager
ScheduledTaskCreateTool→ TeamBackend (task_manager + member_exists 校验 assignee)
UpdateTaskTool         → TeamBackend (+ task_manager 通过 backend 取)
ViewTaskToolV2         → TeamTaskManager
ClaimTaskTool          → TeamTaskManager
MemberCompleteTaskTool → TeamTaskManager
SendMessageTool        → TeamMessageManager (+ TeamBackend roster check + on_teammate_created)
ReportToLeaderTool     → TeamMessageManager (+ TeamBackend.resolve_leader_member_name()：投递时把 "leader" 角色词解析成真实名，读 team_info DB 行并缓存；team 行缺失则 to="leader" 在 invoke 时软失败，非构造期)
```

事件发布与状态迁移由 manager 集中负责；工具只是把入参打包后转发给
manager / backend。新增工具如果发现"我得绕过 manager 直接写表"，
说明 manager 缺方法——补 manager，不要在工具里偷偷打数据库。

### `TeamBackend.on_team_cleaned` — `clean_team` 成功回调契约

`TeamBackend.__init__` 接受一个 keyword-only 可选参数
`on_team_cleaned: Callable[[], Awaitable[None]] | None`。语义固定：

- **仅在 `clean_team()` 成功路径触发**：成功日志 + `TeamCleanedEvent` 发布之后、
  `return True` 之前。early `return False`（成员未全部 SHUTDOWN）**不触发**——
  那条路径团队没被清理，round 也不结束。
- **best-effort**：回调抛异常只 `team_logger.error`、不上抛——一个接线 bug 不能
  把一次成功清理倒灌成工具错误。
- **接线方**：`AgentConfigurator.setup_team_backend` 从 `setup_infra` 透传，
  `TeamAgent` 注入 `_mark_team_cleaned`，在 leader 本轮内同步把
  `TeamAgentState.team_cleaned` 置位，供 `StreamController` 在 round-end 关流。
  对每个角色都接线，但 `clean_team` 是 `LEADER_ONLY_TOOLS`，回调只可能在 leader
  上触发。详见 F_10 / S_02。

### 静态注册表是新工具的唯一注入路径

```python
all_tools = {
    # build_team also discloses the leader's collaboration policy (F_76), so it
    # takes the assembly parameters that decide which variant to render.
    "build_team": BuildTeamTool(
        agent_team, t,
        language=lang, lifecycle=lifecycle,
        teammate_mode=teammate_mode, team_mode=team_mode, dispatch_mode=dispatch_mode,
    ),
    "clean_team": CleanTeamTool(agent_team, t),
    ...
}
```

新增工具的步骤是固定四件事：

1. 在对应领域文件写 `XxxTool(TeamTool)` 子类；`ToolCard.id="team.<name>"`。
2. 在 `create_team_tools` 内的 `all_tools` dict 加一条。
3. 把名字加到 `LEADER_ONLY_TOOLS` / `MEMBER_ONLY_TOOLS` /
   `MEMBER_ONLY_TOOLS_SCHEDULED` / `SHARED_TOOLS` / `HUMAN_AGENT_TOOLS` 里需要它的
   那个集合（互斥，不重复加）。
4. 写 `_desc`：长描述放 `locales/descs/<lang>/<domain>/<desc_key>.md`（领域目录对齐它的
   实现文件），参数串放 `locales/<lang>.py`。两边语言都补齐——不允许只补 cn。

给**已有工具**加一个形态（不是新工具）时是另外四件事：

1. 让两个形态共享代码——**先问共享的是数据还是行为**。共享纯数据 / 纯函数（schema 构造、
   批次校验）就抽模块级函数、两个类各写自己的 `invoke`（`create_task` 走这条）；共享一坨
   有状态的投递 / 执行行为才抽 `_XxxBase(TeamTool, ABC)`、差异点声明成抽象方法
   （`send_message` 走这条）。不要默认上基类。
2. 两个类各自声明 schema 与 `desc_key`；`ToolCard.id` / `name` **保持不变**。
3. 在 `tool_factory.py` 的形态表里登记；`all_tools` 那一行改成查表构造。
4. 补 `descs/<lang>/<domain>/<新 desc_key>.md`（cn + en）；公共段落抽成 `fragments/<slot>.md`
   给两个形态共用；只为新参数加 `STRINGS` key，其余复用原 `tool.*` 命名空间。

无论哪条路，`tests/.../test_tool_variants.py::test_every_toolset_assembles` 的
笛卡尔冒烟都会替你抓住漏写的语言 / 片段 / key。

没有"动态注册表"。`exclude_tools` 是减法，不是注入口；想新增能力请走
上面四步，不要走 `kwargs`、不要 monkey-patch、不要在 `TeamToolRail`
追加除 workspace 之外的工具（workspace 有明确的子系统归属）。

## 与其它 spec 的关系

- **`prompts/` 子系统**：分层契约——工具描述写"操作过程 / 调用顺序 /
  反模式"，系统提示词写"角色身份 / 决策原则 / 状态迁移"。两边 i18n 走
  各自的 `locales/`，不互通。新增长文本前先判断归属，再选目录。
- **`rails/team_tool_rail.py`**：`TeamToolRail` 是工厂的唯一调用方，
  也是 `workspace_meta` 的追加挂载点。`TeamToolRail.init` 与本工厂的
  契约：必须用关键字参数透传 `role` / `teammate_mode` / `lang`，
  不允许 rail 自行重写工具集合。
- **`agent_teams/i18n.py`**：运行时硬编码字符串（dispatcher 通知、
  默认 desc 等）走 `t(key)` 这一组；本规约的描述文本是另一条路径。
  两者读不同的源，不要把工具描述塞进 `i18n.py` 的 `STRINGS`。
- **`schema/task.py` / `schema/team.py`**：工具返回的结构化 `data` 必须
  来自 schema 层的 Pydantic 模型（`TaskSummary` / `TaskDetail` /
  `TaskListResult` / `MemberOpResult` / `TaskCreateResult` /
  `TaskOpResult`），不允许在工具里现场拼裸 dict。工具 → manager 的
  批量创建入参 / 出参也是 schema 层模型（`TaskGraphSpec` /
  `TaskGraphResult`）。
- **`runtime/`**：`Runner.resource_mgr` 是工具注册的下游消费者；
  `qualify_team_tool_ids` 在 inprocess 下扩展 ID 命名是为了不冲突，
  不要在 runtime 层另立解析规则——所有 `team.` 前缀的认知都在这条
  spec 里定义。
