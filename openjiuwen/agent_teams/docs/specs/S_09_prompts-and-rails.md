# Prompts 与 Team Rails

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | `openjiuwen/agent_teams/prompts/`, `openjiuwen/agent_teams/rails/` |
| 最近一次修订日期 | 2026-08-13 |
| 关联 feature | `F_18_hide-human-agent-role-from-teammate.md`、`F_25_external-cli-hardening-and-gemini.md`、`F_50_hitt-contract-roster-split-and-finish-md-externalization.md`、`F_51_external-cli-inbound-xml-and-tag-notice-relocation.md`、`F_52_unify-member-roster-and-static-sections.md`、`F_68_member-identity-out-of-prompt-prefix.md`、`F_70_team-context-into-history.md`、`F_72_nested-team-note-inside-annotated-block.md`、`F_73_avatar-controller-channel-separation.md`、`F_76_leader-progressive-policy-disclosure.md`、`F_78_steering-batch-quota-hook.md`、`F_80_fork-identity-conversion.md` |

## 范围 / 边界

**管：**

- `agent_teams/prompts/` 下系统提示词的全部产出路径：模板加载、占位符装配、`PromptSection` 构造。
- `agent_teams/prompts/messages.py` 与 `agent_teams/team_context.py`：团队状态（自身身份 / 团队元数据 / 成员名册）的消息正文、名册 diff、投递时机与持久化基线。
- `agent_teams/rails/` 下三个团队级 Rail（`TeamPolicyRail` / `TeamToolApprovalRail` / `TeamPermissionRail`）及 team-specific confirmation payload models（`TeamConfirmPayload` / `TeamPermissionConfirmResponse`）的契约、注入时机、与 DeepAgent rail registry 的交互。
- prompts 子模块的 `cn/` `en/` 双语模板布局，以及与 `agent_teams/i18n.py`（运行时硬编码字符串）的边界。

**不管：**

- `TeamToolRail` 的工具注册行为（属于 `tools/` 子系统的 spec）。
- 模板正文写作风格（Markdown 内容由模板自身维护，spec 只规定装配契约）。
- DeepAgent 内置的非团队 rail（safety / sys_operation / context-engineering 等）的实现，本 spec 只描述它们与团队 rail 的协作位面。
- `SystemPromptBuilder` 与 `PromptSection` 自身的实现（属于 core 的 spec）。

## 不变量

### 装配路径

1. **唯一装配路径 `sections.build_team_*_section`**：每片内容独立产出 `PromptSection`，读 `prompts/<lang>/*.md`。由 `TeamPolicyRail` 合并进 `SystemPromptBuilder`（进程内成员），或经 `build_team_member_system_prompt` 渲染成独立字符串（外部 CLI 成员）。模板正文修改即时生效。
1a. **leader 的团队 section 走渐进式披露（[[F_76]]）**：进 builder 的 leader 团队 section 只有两个——`team_bootstrap`（P:11，身份 + `{{collaboration_mechanism}}` capability 槽 + "先调 build_team"）与 `team_extra`（P:17，调用方自定义指令，**不是团队协同准则**且必须建队前生效）。其余 section 由 `sections.build_leader_policy_disclosure(...)` 用**同一套 `build_team_static_sections` + `SystemPromptBuilder` 装配**渲染成文本，经 `tools/tool_team.BuildTeamTool.map_result` 附在建队结果之后下发。四条硬约束：
   - **披露只在成功路径**：`build_team` 失败时 leader 还在 bootstrap 路上，此时该重读的是分流规则而不是一份不存在的团队的规则。
   - **HITT gate 读实际生效值**：`map_result` 取 `output.data["enable_hitt"]`（`build_team` 解析 spec 天花板 × 本次选择后的结果），**不是**构造期的 spec `hitt_enabled`。这正是把披露挪到 `build_team` 才能修掉的偏差。
   - **只有 LEADER 走这条道**：其余角色的协同约定 spawn 时即固定，也没有 `build_team` 可挂载，一律走原来的全量静态集。分流在 `TeamPolicyRail._build_static_sections` 的一个 `role == LEADER` 分支，不下推到各 builder。
   - **披露内容会被压缩，须重注入**：`core.context_engine.processor.compressor.util.build_team_policy_reinjected_messages`（在 `FullCompactProcessor` 注册名 `team_policy`）在 full compact 后原样重注入那条 tool result，**不得截断**（返回 `list[UserMessage]` 绕开 `state_snapshot_max_chars`）。core 侧按工具名 `build_team` 匹配，**不 import `agent_teams`**——与既有 `TEAM_TOOL_CALL_NAMES` 同法，依赖方向不变。

1b. **模式相关的内容按模式拆模板，不在共用模板里加 caveat（[[F_76]]）**：`leader_policy.md` 全模式共用，因此任何"这条在 A 模式如此、在 B 模式如彼"的内容都不属于它。「任务状态流转」原本就违反这条（同一节并列描述自主认领与调度框架两套驱动），已拆成 `task_state_autonomous.md` / `task_state_scheduled.md`，由 `build_team_task_state_section`（P:16，LEADER only）按 `dispatch_mode` 挑版。
   - **autonomous 版不得出现验证闸，连警告也不行**：该模式的 `create_task` 不暴露 `reviewer`，且没有调度 runtime 去唤起验证者（`TeamScheduler` 只在 `dispatch_mode == "scheduled"` 的 leader 上构造），被硬推进 `in_review` 的任务会永久停在那里并占住该成员唯一的活跃任务名额。但模板**不写"本模式没有验证闸"这类说明**——点名一个不存在的能力正是让模型去够它的原因，与 `fork_usage` 槽关闭时整节消失同理。门控理由属于 spec 与代码注释，不属于提示词。
   - **这是一次真实回归的修复**：`leader_policy.md` 的该节曾是模式感知措辞（"在 `create_task` schema 暴露 `reviewer` 的调度形态里，也可以创建时直接设置"），被一次 scheduled-only 的改动抹平为无条件的 `create_task(reviewer=[...])`，正好污染了 autonomous。`tests/unit_tests/agent_teams/test_team_policy_rail.py::TestTeamTaskStateSection::test_autonomous_documents_no_verify_gate` 是这条的回归闸。

2. **role policy 由 `build_team_role_section` 直接读**：`sections.build_team_role_section` 按角色 `load_template` 出 `leader_policy`（LEADER）/ `human_agent_policy`（HUMAN_AGENT）/ `teammate_policy`（TEAMMATE、BRIDGE_AGENT；`workspace_prompt_variant="external"` 时为 `teammate_policy_external`）markdown 塞进 role section。没有独立的 policy 装配层（`policy.py` 已删）。**模板本身纯静态无占位符**——`leader_policy` 的 `{{collaboration_mechanism}}` 槽已随 [[F_76]] 移交 `leader_bootstrap` 独占并从 `leader_policy.md` 物理删除；一个槽只允许一个消费者。**HUMAN_AGENT 必须有自己的一份，不能落回 teammate 版**：teammate 契约里"收到 `from="user"` 必须 `send_message(to="user")` 作答"是无条件义务，而 avatar 的对话对方是**控制者**（另一个真人，纯文本输出即可直达）——复用会让 avatar 把控制者的问话答给团队侧的 `user`。同理执行模式行（plan/build）对 HUMAN_AGENT 不渲染：avatar 从不自主规划或认领。
3. **生产路径就是 rail 注入**：`TeamHarness.build` 走 `TeamPolicyRail`。早期的 `policy.build_system_prompt` + `system_prompt.md` 壳模板老路径与 `role_policy` 中间层都仅测试在用，已随 desc/prompt 归一移除（测试迁移到 `load_template` / `build_team_member_system_prompt`）。

### Section / 文件落位

4. **`TeamPolicyRail` 是团队 section 名的唯一发行方**：所有团队相关 section 名集中在 `TeamSectionName` 类常量里，priority 取值集中在该 rail 的注释表里。其他模块不得 hardcode `"team_*"` section 名。
5. **section name 全局唯一**：`SystemPromptBuilder._sections` 是 `dict[str, PromptSection]`，同名 add 直接覆盖。团队 section 与 harness 内置 section（safety / capabilities / runtime / ...）必须不冲突。
6. **section priority 单调约定**：进 builder 的团队 section 占 11–18（含 `team_bootstrap` P:11、HITT 契约 / bridge 自契约 P:12、`team_task_state` P:16、inbound 说明 section P:18）；harness 内置 section 排在 0–10、20–60、70–99，priority 升序拼接。相同 priority 顺序由插入序决定。`team_bootstrap` 与 `team_role` 同占 P:11 且互斥——leader 只出前者，其余角色只出后者。**团队侧不再有任何 prompt attachment**（[[F_70]]）——`team_identity`（P:10）只在外部 CLI 的静态 prompt 里出现，`team_info` / `team_members` 已不是 section。

6a. **系统提示词前缀里不放 per-member 内容**：进 builder 的团队 section 必须对同一 team、同一角色的所有成员逐字一致，否则每个成员各自占一份 prompt 前缀 KV cache，成员一多缓存命中率就塌。**当前唯一的 per-member 内容**是成员自己的身份（`member_name` / `display_name` / 私有工作区路径 / 私有工作约定，见 `prompts/messages.build_identity_text`）；进程内成员经**对话历史**收到它（见不变量 13），只有外部 CLI 成员把它内联成静态 `team_identity` section（P:10，`include_member_specific=True`）——那份 prompt 是独立进程的一次性快照，不与兄弟成员共享前缀。例外仅两处，都因角色本身单例而不构成放大：`hitt_human_agent` / `bridge_agent` 模板的 `{{self_line}}`。新增 section 前先问它是否 per-member——是就走历史消息，不要塞进 builder。

7. **role-specific section 在不应出现的角色下返回 `None`**：`build_team_workflow_section` / `build_team_lifecycle_section` / `build_team_task_state_section` 在 `role != LEADER` 时返回 `None`（它们仍**只为 LEADER 产出**，只是自 [[F_76]] 起这份产出流向 `build_team` 的 ToolResult 而非 builder）；`build_team_hitt_section` 在 `hitt_enabled` 为 False（或角色无 HITT 版）时返回 `None`；`build_team_bridge_section` 在 `role != BRIDGE_AGENT` 时返回 `None`。**禁止用空字符串占位**——返回 `None` 等价于不挂 section。
   - **HITT 是单一静态契约（[[F_52]]）**：`build_team_hitt_section` 出 roster-agnostic 的规则段，进 system prompt builder（P:12，静态、KV 稳定），gate 用 `hitt_enabled`（capability flag，HITT 一开即 present，无需先 spawn 人类）。人类成员不再有独立名册段——他们在名册消息里标 `[human]`（撤销 [[F_50]] 的 `team_hitt_roster`）。
   - **Bridge 只有 avatar 自契约（[[F_52]]）**：bridge 成员在 peer 眼里就是普通 teammate（名册消息里不标记、无 peer 向说明段），只有 avatar 本人（`role == BRIDGE_AGENT`）拿 `bridge_agent` 自契约；已删 `bridge_leader` / `bridge_teammate` 模板。
   - **TEAMMATE 默认走 anonymous 变体（F_18）**：`_hitt_template_name` 对 `role == TEAMMATE` 默认选 `hitt_teammate_anonymous`——**无 `[human]` 引用、无 "real humans" 标签**的 role-neutral 契约。开关 `TeamAgentSpec.expose_human_agents_to_teammates=True` 切回 `hitt_teammate`（引用 `[human]` 标签）。该开关**同时**门控名册消息里 `[human]` 标记对 teammate 的可见性（`mark_humans`）：LEADER / HUMAN_AGENT 恒见，TEAMMATE 仅 expose 时见。
8. **多数 `cn/` `en/` 模板为纯文本**：policy / workflow / lifecycle / inbound_tags / HITT leader・teammate・anonymous 不含占位符，`load_template` 原样返回。**例外**：`bridge_agent` 与 `hitt_human_agent` 用 `{{self_line}}`（自身名字行），由 builder `load_template(...).format({"self_line": ...})` 渲染（`{{ }}` 定界符），builder 仅在 BRIDGE_AGENT / HUMAN_AGENT 时才算 `self_line`。（早期 `system_prompt.md` 壳模板随 legacy `build_system_prompt` 一并移除。）

### 双语 / i18n

9. **`cn/` `en/` 双语模板必须成对存在**：每个文件名两边都要有；`load_template` 按 `(name, language)` 分别 `@cache`。
10. **`prompts/` 与 `agent_teams/i18n.py` 严格分离**：长文本（角色策略 / 工作流 / 生命周期 / HITT）一律走 `prompts/`，运行时硬编码字符串（dispatcher 通知、default desc 等）走 `i18n.py`。新增字符串前必须先决定归属，不得混用。
11. **`load_template` 默认语言 `"cn"`**：缺省 `language` 参数等价于 `"cn"`，与 `core.single_agent.prompts.builder.DEFAULT_LANGUAGE` 一致。

### 缓存与团队状态投递

12. **`@cache` 永不失效**：`loader._load(name, language)` 用 `functools.cache` 终身缓存解析后的 `PromptTemplate`。运行进程不会感知文件改动；调试热更需重启进程或清 `_load.cache_clear()`。

13. **团队状态走对话历史，不走 attachment，也不进系统提示词（[[F_70]]）**：成员自身身份、团队元数据、成员名册由 `team_context.TeamContextTracker` 在**数据第一次出现以及此后每次变化**时插入一条消息，正文由 `prompts/messages.py` 渲染、`inbound_render` 包成 `<team-context>` / `<team-event kind="roster"|"roster-change">`。五条硬约束：
    - **只插入，不删除、不重写**。名册变化只发增量（joined / left / changed），不重发全量；`team_info` 变更追加新的一条，旧那条留在历史里作为当时的事实。
    - **落位有两条通道，且都不按位置定位**（[[F_70]] D1c）。**禁止**任何"记住上次看到哪条消息、下次从它之后找起"的方案——无论用下标还是消息 id 锚：上下文压缩会重写整段历史，下标之后已不是同一条消息，而消息 id 锚被压缩抹掉后只能退化，等于给一条本可以不存在的路径养一套失效处理。
      - **主通道 `on_user_message`**：core 的 `AgentCallbackEvent.ON_USER_MESSAGE` 在被消费的那**批**输入（新一轮 query / 整批 follow-up / 整批 steering / resume）**拼成一条 user message 之前**触发，rail 就地改 `ctx.inputs.parts`（可变 `list[str]`），把待发内容 `insert(0, ...)` 到最前面。这是能把输入当作"输入"来处理的唯一时刻；写进去之后它们就是一条普通历史，会被压缩搬动。**钩子拿到的是列表而不是拼好的字符串**（[[F_71]]）：一条 entry 就是一条完整输入，rail 因此可以整条剔除（见不变量 13a），拼好了就只剩解析正文一条路。
      - **兜底 `before_model_call`**：只**追加**一条独立 user message，不定位。state 也可能在一轮的 tool-loop 中途出现（leader 的 `build_team` 在中途建队、写自己的 member 行和名册），此时没有输入可搭车，而下一条输入可能很久才来。尾部是唯一不需要定位、也不受"压缩重写了它前面的历史"影响的位置。
      - 两条通道共用同一个 tracker，谁先拿到 pending 就谁投递；**已在历史里的消息永远不改写**——改一条旧消息会让它之后的 KV cache 全部作废。
    - **同一次投递里产生的 `<team-context>` 正文合并成一个标签**：身份与团队元数据都是"关于团队的既成事实"，各包一个标签是把同一类东西说两遍。分别在不同调用上产生时自然是两条消息，不合并。
    - **身份 = `member_name` + `display_name` + 私有工作区路径 + 私有工作约定**：判据是"per-member、spawn 时固定、此后恒定"，凡满足的都进这一段正文，不另开通道。`display_name` 必须**读自己那行 member 行**——名册每行都以两个名字标识成员，少了它成员认不出哪一行是自己，而构造期的值只是 spec 默认（leader 的真实标签由 `build_team(leader_display_name=...)` 写入 DB），所以身份通道**等自己那行存在**才发：teammate 在 spawn 时就有行，leader 则在建队后那次调用上拿到。私有工作区路径带一句用途说明（区分于团队共享工作空间、不作为新 skill 的创建目标）——只给路径模型分不清分工。公开 `desc` 仍然不进自己的身份（见不变量 18a）。
    - **`enable_fork=True` 团队的身份块加 `<identity>` 内壳 + 能力声明（[[F_80]]）**：`TeamContextTracker._fork_capable = team_backend.fork_enabled()`（`getattr` 兜底，无该方法的 backend / 外部 runtime 走 off 路径）。开启时 `build_identity_text(fork_capable=True)` 在正文顶部加恒定能力声明（「你是拥有身份转换能力的成员，当前身份以本块及转换通知为准」），`pending_text` 改调 `inbound_render.render_team_context_with_identity` 把身份包进 `<identity>`。**关闭时输出与改造前逐字一致**（原 `render_team_context` + `fork_capable=False`），非 fork 团队前缀 KV 与模型输入零变化。`<identity>` 内壳在**结构层**渲染、只转义最内层正文——`<team-context>` 正文的 `html.escape` 会吃掉嵌套标签，嵌套必须由渲染函数拼接而非塞进 body。
    - **投递进度基线必须持久化在成员自己的 child `AgentSession`**（state key `team_prompt_context`，字段 `identity_emitted` / `team_info_mtime` / `roster_mtime` / `roster`）。`TeamPolicyRail` **每一轮都会被重建**（round 结束 native 进 TERMINATED，下次 start 重新 `RailSpec.build`），基线留内存等于每轮重发；pause/resume、stop→start 只是更严重的版本。该 state 与成员的对话历史存在同一个 agent-session 桶里，由同一次 `AgentStorage.save` 落盘，故两者不会漂移。
    - **先投递、后 `commit`**：`pending_text()` 只渲染不推进，`commit()` 由调用方在投递成功后调用；反过来写会在投递失败时永久丢掉一条公告。tracker **不持锁**——一个成员的 rail 钩子、CLI `send` 与事件补偿都在同一条协程上。
    - **名册消息必带 `<team-note kind="announcement-only">`**（文案 `i18n.team_context.roster_announcement_note`，嵌在该 `<team-event>` 内部，见不变量 28）：否则成员看到"有人加入"就会礼节性寒暄，白烧一轮 LLM + 一轮邮箱投递并连锁触发对方。

13b. **fork 继承目标的身份块带 `<identity-conversion>` 子块声明当前身份（[[F_80]]）**：fork 源身份块留在继承历史里无法删除（动它即破坏前缀 KV），转换语义只能追加。`_on_teammate_created` 在 fork 上下文非空且 `is_empty()` 为假时把源名写进 `ctx.fork_source`（缺省 = leader 自己的名字），随 spawn payload 跨进程序列化，经 `TeamPolicyInput.fork_source` → `TeamPolicyRail` → `TeamContextTracker` 透传。目标首条身份投递时，`render_team_context_with_identity` 在 `<identity>` 内嵌 `<identity-conversion>`（正文由 `build_identity_conversion` 渲染，声明当前身份并明示更早身份块的私有约定/工作区不再适用），使模型区分"继承来的旧身份"与"当前身份"。**只追加、不改继承段**——KV 命中不变。普通 spawn（`fork_source` 为空）不渲染该子块；`enable_fork=False` 团队整条路径保持原状（不变量 13 的逐字一致约束）。

13a. **被覆盖的快照类输入整条剔除，只剔 teammate 的（[[F_71]]）**：`TeamPolicyRail.on_user_message` 在折入团队状态**之前**先给这批输入做减法——同一批里出现多条同 kind 的快照事件时，只留最后一条。四条硬约束：
    - **只有全量幂等快照才算快照**：`inbound_render.SNAPSHOT_EVENT_KINDS` 当前只有 `task-board`。增量（`roster-change`）与按主体分片的事件（`stale-claim` 带 `task_id`）丢掉早的那条就是丢信息，**不得**加进这个集合。
    - **按整条 entry 丢弃，不解析正文**：一条 entry 就是一次 `deliver_input` 的全部内容，`snapshot_kind_of` 用开闭标签判定"这条除了快照什么都没有"，带 note 或与别的内容同处一条的一律不动。正则抠 XML 是被明确拒绝的方案。
    - **只对非 LEADER 生效**：teammate 的板子是可认领工作队列，只有当前那份可执行；leader 的板子是全团队未完成工作，它读的正是相邻两份之间的差异（哪个任务出现、哪个动了）来决定重规划还是收尾，压掉就等于删掉它要看的信号。
    - **只作用于尚未写入的这一批**，历史一个字都不改——与不变量 13 的"只插入，不删除、不重写"同源。

14. **探针语义**：`team_info` 用 `get_team_updated_at()`，名册用 `get_members_max_updated_at()`（状态变化不 bump，只有名册变动才 bump）；`identity` 恒定、无探针，靠基线里的一次性标志。**`team_info` 在团队行不存在时（leader 跑 `build_team` 之前，探针读 0）什么都不发**——工作区路径是 rail 构造参数、恒有值，不加这道闸就会渲染出一个「没有团队的团队信息」块，而真正完整的那条紧随其后，等于把同一件事说两遍、第一遍还是错的。名册同理只在有 peer 时才发：`list_members` 排除调用者本人，故 `build_team` 只写了 leader 自己那行时名册为空，不产出消息。探针推进但渲染为空时，基线在 `pending_text` 内部直接推进并落盘——没有东西会投递失败，不推进只会让每次调用重复读 DB。新增状态通道必须提供单调递增的探针；缺少探针的内容不应走这条路径。`MtimeSectionCache`（`prompts/section_cache.py`）作为通用原语保留，当前团队侧无使用者。

### Rail 注入契约

15. **Rail 通过 DeepAgent 的 rail registry 注入，不直接修改 `SystemPromptBuilder`**：`TeamPolicyRail` 在 `init(agent)` 里捕获 `agent.system_prompt_builder` 引用，于 `before_model_call` 写入 section；在 `uninit(agent)` 里按名移除。**禁止**绕过 rail 把 section 直接 `add_section` 到 *DeepAgent 的共享 builder* 上。**例外（外部 CLI 成员）**：非 DeepAgent 的外部 CLI 成员没有共享 builder，由 `sections.build_team_member_system_prompt(...)` 把同一批静态 section 装进一个**一次性 `SystemPromptBuilder`** 渲染成独立字符串(见不变量 18a / [[F_25_external-cli-hardening-and-gemini]])——这不违反本条，因为它不触碰任何 DeepAgent 的共享 builder，且**只装 team section、排除其它 rail**。
    - 18a. **静态 section 的单一真相源是 `sections.build_team_static_sections(...)`**：`TeamPolicyRail._build_static_sections`（非 leader 分支）、`build_team_member_system_prompt`（外部 CLI）与 `build_leader_policy_disclosure`（leader 的 `build_team` 披露，[[F_76]]）三条路径都委托它构建 role / HITT 契约（gate `hitt_enabled`）/ bridge 自契约（仅 BRIDGE_AGENT）/ workflow / dispatch / lifecycle / extra——**全部静态且成员间一致**，两条路径拿到一致的静态 section。成员**公开 `desc` 不进自己的 prompt**（只进他人的名册消息）；成员**私有 `prompt` 只发给自己**（identity 正文的 `## 私有工作约定` 子节）。团队状态（`team_info` / 名册）**不在其中**——它不是 section，由 `TeamContextTracker` 投递进对话（见不变量 13）。inbound_tags 说明 section **无条件**构造（见 [[F_51]] / [[F_70]]），它同时解释 `<team-context>` 与两种名册 `<team-event>`。**唯一按路径分化的是 `include_member_specific`**：外部 CLI 传 True，把 per-member 的 `team_identity` section 内联进静态 prompt（独立进程的一次性快照，无共享前缀可保护、启动期也没有对话可写）；进程内成员走默认 False、由 tracker 投进历史（见不变量 6a）。
16. **Mount order load-bearing**：`TeamHarness.build` 必须先挂 `TeamToolRail` 并 eager `init`，再挂 `TeamPolicyRail`。原因：policy 输出引用 ability 快照，能力必须先就位。Rail 顺序的修改必须同步检视 mount path。
17. **`uninit` 必须把自己写入 builder 的 section 全部清掉**：`TeamPolicyRail.uninit` 删除 `_static_sections` 里的每个 section（HITT 契约 / bridge 自契约都在其中；leader 则是 `team_bootstrap` / `team_extra` 两个）。团队状态写在成员自己的对话历史里，那是它的历史、不由 rail 清理。rail 卸载后 builder 不得残留团队 section。
18. **`team_backend is None` 时状态通道退化**：单测可只关心 static 内容；缺 backend 时 `team_info` / 名册两条通道整体跳过，只剩恒定的 identity 通道（它不需要 backend）。

> 不变量 19 / 20 曾描述 `FirstIterationGate`，该 rail 已随单 supervisor 模型删除
> （`agent_teams/rails/` 下已无此文件）。编号留空不复用，避免打乱后续引用。

### `TeamToolApprovalRail`

21. **审批是中断驱动 + 消息驱动的复合协议**：teammate 端挂 rail，触发时 (a) 通过 `TeamMessageManager.send_message` 把审批请求送给 leader，(b) 调 `self.interrupt(InterruptRequest(...))` 阻塞当前工具调用。leader 通过 `approve_tool` 工具回填 `ConfirmPayload`，rail 在 resume 时根据 payload 决定 approve / reject。
22. **`auto_confirm_config` 是 user input 通道，不持久化**：每轮构造一份；`_get_auto_confirm_key` 从 `tool_call` 派生 key。同一 key 的后续审批请求若命中 config 直接 `approve()`，无需消息。
23. **未配置 `approval_required_tools` 不挂 rail**：`agent_configurator` 仅在 teammate + `agent_spec.approval_required_tools` 非空时构造该 rail。leader 与 human_agent 不挂。
24. **消息发送失败 = 直接 reject**：`send_message` 返回 falsy 时 rail `reject(tool_result="Failed to send approval request to leader")`，**不重试**——避免对 messager 的重试压力反向放大故障。

### `TeamPermissionRail`

25. **继承 `PermissionInterruptRail`**：复用完整 `PermissionEngine` 三级判定（ALLOW/DENY/ASK + auto_confirm）。`enable_permissions=True` 时替代 `TeamToolApprovalRail`。
26. **`_persist_allow_always() → False`**：leader 审批 session-scoped，override 父类的磁盘写盘方法直接返回 `False`，不写 teammate 本地 YAML。
27. **`parse_confirm_payload()` 自动设置 `decided_by="leader"`**：返回 `TeamPermissionConfirmResponse`（`PermissionConfirmResponse` + `decided_by`），`decided_by` 仅用于内部审计，不暴露给 LLM。

### 入站 XML 标签结构

28. **`<team-note>` 嵌在它所修饰的块内部，从不平级（[[F_72]]）**：`inbound_render` 渲染出的
    `<team-inbound>` / `<team-event>` / `<team-context>` 是彼此平级的顶层块，`<team-note>`
    **只作为前两者的最后一个子元素存在**，不单独出现、也不跟在被修饰块后面当兄弟。归属由树
    结构给出，不靠语序推断——成员一次唤醒常拿到多条排队输入拼在一起，平级的 note 到前一个块
    与到后一个块的距离一样近，`reply-hint` 认错块就是「不该回的回了」。三个渲染函数共用
    `_render_block(tag, attrs, body, note="")`，无 note 时拼进空串，**不存在「有没有 note」
    的分支**。`inbound_tags.md`（cn/en）必须与此逐字一致：正文边界写作「除 `<team-note>`
    子元素外」，并明确「看嵌在哪个标签里，不要按前后位置猜」。

### 输入批次配额

29. **一批排队输入喂给一次模型调用的量，由 rail 在消费点现场决定（[[F_78]]）**。两条队列各有
    一个收窄点，性质不同、手段也不同：
    - **follow-up 批次靠剔除**：整批经 `ON_USER_MESSAGE` 交给 rail，`TeamPolicyRail._drop_superseded`
      把被后来者覆盖的任务看板整条丢掉（见不变量 13 / [[F_71]]）。可行的前提是看板是**全量幂等
      快照**——丢掉旧的不损失信息。
    - **steering 批次靠限量**：队列里是信箱消息，每条各说各的、一条都不能丢，所以只能少拿。
      `AgentCallbackEvent.BEFORE_STEERING_DRAIN` 在**每次 drain 之前**触发（队列为空则不触发），
      带 `SteeringDrainInputs(pending, limit)`；rail 写 `limit`，多个 rail 按 priority 串行、
      各自看到前一个留下的值。`TeamPolicyRail` 对**非 leader** 成员写 `steer_batch_size`（默认 2）。
    - **必须在 drain 之前决定**：全取之后再把多余的 `push_steering` 回塞队尾会乱序——rail 链里有
      真实 await，期间投进来的新消息会排在回塞消息之前。在 drain 之前定量，多余的消息从没离开
      过队列，FIFO 顺序天然成立。
    - **队列非空时至少取 1 条**（`drain_steering` 内部 `max(1, limit)`）：消费必须推进，否则
      `has_pending_steering()` 恒真会让 loop 空转到 `max_iterations` 耗尽。`steer_batch_size`
      另有 spec 侧 `> 0` 校验。
    - **不需要额外的续跑机制**：inner loop 本来就在 `has_pending_steering()` 为真时继续迭代，
      剩下的消息由后续模型调用取走。
    - **leader 不限量**，与它不参与看板剔除同源：它读的是快照之间的差异来决定重规划还是收尾。
30. **新增 `AgentCallbackEvent` 成员必须同时做路由决策**：`DeepAgent` 有外层与内层 ReActAgent
    两个 callback-manager 命名空间，`_register_rail_selective` 按 `_BRIDGE_EVENTS` /
    `_OUTER_ONLY_EVENTS` / `_DEEP_EVENTS` 分流，**漏了就静默落到外层、rail 永不触发**。内层
    ReAct loop 触发的事件（`BEFORE_MODEL_CALL` / `ON_USER_MESSAGE` /
    `BEFORE_STEERING_DRAIN` …）一律进 `_BRIDGE_EVENTS`。
    `tests/unit_tests/harness/test_deep_agent_rail_event_routing.py` 强制这一条。

## 接口契约

### `prompts/loader.py`

```python
def load_template(name: str, language: str = "cn") -> PromptTemplate:
    """Load <prompts_dir>/<language>/<name>.md, terminal-cached by (name, language)."""
    ...

def load_shared_template(name: str) -> PromptTemplate:
    """Load <prompts_dir>/<name>.md, terminal-cached by name."""
    ...
```

- `name` 不带扩展名；`language` 取 `"cn"` / `"en"`，未来扩展只需新增子目录。
- 返回 `core.foundation.prompt.PromptTemplate`，`.content` 为原始 markdown，`.format(...)` 渲染 `{{placeholder}}`。
- 文件不存在直接抛 `FileNotFoundError`（`Path.read_text` 默认行为），不做兜底。

### `prompts/sections.py`

`build_team_role_section` 按角色 `load_template` 出 `leader_policy`（LEADER）/ `human_agent_policy`（HUMAN_AGENT，且不渲染执行模式行）/ `teammate_policy`（其它角色）塞进 role section。`team_mode`（`{"default","predefined","hybrid"}` → `leader_workflow*.md`）与 `lifecycle`（`{"temporary","persistent"}` → `lifecycle_*.md`）的映射由 `build_team_workflow_section` / `build_team_lifecycle_section` 承担（`sections.py` 自己的 `_WORKFLOW_TEMPLATES`），非法值走 `"default"` / `lifecycle_temporary`。


每个 builder 返回 `PromptSection | None`，`None` 表示该角色下不应出现该 section。

```python
class TeamSectionName:
    IDENTITY = "team_identity"  # P:10 — own member_name + private prompt (external CLI only)
    ROLE = "team_role"        # P:11
    HITT = "team_hitt"        # P:12 — static collaboration contract (builder)
    BRIDGE = "team_bridge"    # P:12 — bridge avatar self-contract (BRIDGE_AGENT only)
    WORKFLOW = "team_workflow"   # P:13
    LIFECYCLE = "team_lifecycle" # P:14
    EXTRA = "team_extra"      # P:17
    INBOUND_TAGS = "team_inbound_tags"  # P:18

def build_team_identity_section(   # external CLI only; a thin PromptSection wrapper
    *,
    member_name: str | None,
    display_name: str | None = None,
    member_workspace_path: str | None = None,
    member_prompt: str | None = None,   # rendered as a '## private working agreement' subsection
    language: str = "cn",
) -> PromptSection | None: ...    # None when no field is set

def build_team_role_section(
    *,
    role: TeamRole,
    teammate_mode: str = "build_mode",
    language: str = "cn",
) -> PromptSection: ...           # no member_name: identity lives in its own section

def build_team_workflow_section(
    *,
    role: TeamRole,
    team_mode: str = "default",
    language: str = "cn",
) -> PromptSection | None: ...    # None when role != LEADER

def build_team_lifecycle_section(
    *,
    role: TeamRole,
    lifecycle: str,
    language: str = "cn",
) -> PromptSection | None: ...    # None when role != LEADER

def build_team_extra_section(
    *,
    base_prompt: str | None,
    language: str = "cn",
) -> PromptSection | None: ...    # None when base_prompt is empty/whitespace

# Single source of truth for the static section set (role/hitt/bridge/workflow/
# lifecycle/extra + the inbound-tag notice, always). TeamPolicyRail
# ._build_static_sections delegates here and leaves include_member_specific at
# its default False (the tracker delivers it into the conversation instead);
# build_team_member_system_prompt (external CLI members) renders these into a
# standalone string via a throwaway SystemPromptBuilder, excluding other rails,
# and sets include_member_specific=True. Note: the member's public `desc` is NOT
# rendered here — it belongs only in peers' roster message.
def build_team_static_sections(
    *,
    role: TeamRole,
    member_prompt: str = "",
    member_name: str | None,
    lifecycle: str = "temporary",
    teammate_mode: str = "build_mode",
    team_mode: str = "default",
    base_prompt: str | None = None,
    language: str = "cn",
    hitt_enabled: bool = False,
    expose_human_agents_to_teammates: bool = False,
    include_member_specific: bool = False,   # external CLI only; in-process gets it in its conversation
) -> list[PromptSection]: ...      # non-None sections, unsorted

def build_team_member_system_prompt(  # same kwargs as build_team_static_sections
    *,
    role: TeamRole,
    member_prompt: str = "",
    member_name: str | None,
    # ... lifecycle / teammate_mode / team_mode / base_prompt / language /
    # hitt_enabled / expose_human_agents_to_teammates
) -> str: ...                      # priority-ordered, "\n\n"-joined; "" if empty

# HITT is a single static contract section (F_52): rules only, gated on
# hitt_enabled. Human members are tagged [human] in the roster message
# instead of an inline / separate roster section.
def build_team_hitt_section(
    *,
    role: TeamRole,
    hitt_enabled: bool = False,
    language: str = "cn",
    self_member_name: str | None = None,   # injected as {{self_line}} for HUMAN_AGENT only
    expose_human_agents_to_teammates: bool = False,
) -> PromptSection | None: ...    # None when hitt_enabled False / role has no HITT

# Bridge is a self-contract for the bridge avatar only (F_52): peers see bridge
# members as ordinary roster entries, so LEADER/TEAMMATE get no section.
def build_team_bridge_section(
    *,
    role: TeamRole,
    language: str = "cn",
    self_member_name: str | None = None,   # injected as {{self_line}}
) -> PromptSection | None: ...    # None unless role == BRIDGE_AGENT
```

- `language` 未在 `_LABELS` 中时回退到 `"cn"`，**不抛异常**（`sections.py` 与 `messages.py` 各持一份标签表，互不重叠：section 标题归前者，消息正文标题归后者）。

### `prompts/messages.py`

团队状态的消息正文 + 名册 diff。纯函数，不碰投递。

```python
def labels_for(language: str) -> dict[str, str]: ...

@dataclass(frozen=True, slots=True)
class RosterDelta:
    joined: list[dict[str, str]]
    left: list[dict[str, str]]
    changed: list[dict[str, str]]
    def is_empty(self) -> bool: ...

def diff_roster(
    old: list[dict[str, str]] | None,
    new: list[dict[str, str]] | None,
) -> RosterDelta: ...      # keyed by member_name; tracks display_name/desc/role only

def format_member_line(
    member: dict[str, str],
    *,
    mark_humans: bool = False,
    prefix: str | None = None,   # '[joined]' / '[left]' / '[updated]' marker
) -> str: ...

def build_identity_text(*, member_name, display_name=None, member_workspace_path=None,
                        member_prompt=None, language="cn",
                        fork_capable=False) -> str | None
def build_identity_conversion(*, source, member_name, language="cn") -> str
def build_team_info_text(*, team_info, team_workspace_path=None,
                         team_outputs_dir=None, language="cn") -> str | None
def build_roster_snapshot_text(*, members, mark_humans=False, language="cn") -> str | None
def build_roster_delta_text(*, delta, mark_humans=False, language="cn") -> str | None
```

- `team_info` 字段仅识别 `team_name` / `display_name` / `desc`。多余 key 静默丢弃。
- 名册元素至少含 `member_name` / `display_name`；`desc` / `role` 可选。**自身排除由
  `TeamBackend.list_members` 负责**（它已经剔除调用者），渲染层不再做二次排除。
- `diff_roster` 只跟踪 `display_name` / `desc` / `role`——运行时状态一直在变，不是名册成员关系。
- `build_identity_text(fork_capable=True)` 在正文顶部加身份转换能力声明；默认 `False` 输出与
  改造前**逐字相同**。`build_identity_conversion` 渲染 `<identity-conversion>` 的正文（源名 +
  当前名 + 私有约定/工作区不再适用），由 `TeamContextTracker` 在 `fork_source` 非空时传入
  `render_team_context_with_identity`（见不变量 13 / 13b）。

### `team_context.py`

```python
TEAM_CONTEXT_STATE_KEY = "team_prompt_context"
ROSTER_EVENT_KIND = "roster"
ROSTER_CHANGE_EVENT_KIND = "roster-change"
ROSTER_NOTE_KIND = "announcement-only"

class TeamContextTracker:
    def __init__(
        self,
        *,
        team_backend: TeamBackend | None,
        member_name: str | None,
        role: TeamRole,
        display_name: str = "",              # fallback only; the DB row wins
        member_workspace_path: str | None = None,
        member_prompt: str = "",
        team_workspace_path: str | None = None,
        team_outputs_dir: str | None = None,
        expose_human_agents_to_teammates: bool = False,
        language: str = "cn",
        fork_source: str | None = None,   # fork 源名；None = 普通 spawn
    ) -> None: ...

    async def pending_text(self, session) -> str | None:
        """Render what is unsent; advances nothing (see invariant 13)."""

    async def commit(self, session) -> None:
        """Persist the baseline once the text was actually delivered."""
```

- `session` 是**成员自己的 child `AgentSession`**；`None` 关闭整个 tracker。
- 两个调用方：`TeamPolicyRail._sync_team_context`（进程内）与
  `CliRuntimeBase.send` / `announce_team_context`（外部 CLI）。
- 不持锁；不可跨协程并发调用（见不变量 13）。

### `prompts/section_cache.py`

```python
class MtimeSectionCache:
    def __init__(
        self,
        probe: Callable[[], Awaitable[int]],
        fetch_and_build: Callable[[], Awaitable[PromptSection | None]],
    ) -> None: ...

    async def refresh(self) -> PromptSection | None:
        """Cheap probe + lazy rebuild; returns last cached section."""

    def invalidate(self) -> None:
        """Force next refresh to refetch regardless of probe."""
```

- `probe` 必须返回单调递增整数；返回相同值视为无变化。
- `fetch_and_build` 可返回 `None`（数据为空时）；`None` 也会被缓存，下次 probe 不变时直接复用。
- 不持有锁；外部并发调用 `refresh()` 由调用方串行化（实际由 `before_model_call` 单线程保证）。

### `rails/team_policy_rail.py`

```python
class TeamPolicyRail(DeepAgentRail):
    priority = 12

    def __init__(
        self,
        *,
        role: TeamRole,
        member_prompt: str = "",
        member_name: str | None = None,
        lifecycle: str = "temporary",
        teammate_mode: str = "build_mode",
        language: str = "cn",
        team_mode: str = "default",
        base_prompt: str | None = None,
        team_workspace_path: str | None = None,
        team_outputs_dir: str | None = None,
        team_backend: TeamBackend | None = None,
        expose_human_agents_to_teammates: bool = False,
    ) -> None: ...

    def init(self, agent: Any) -> None: ...
    def uninit(self, agent: Any) -> None: ...
    async def on_user_message(self, ctx: AgentCallbackContext) -> None: ...
    async def before_model_call(self, ctx: AgentCallbackContext) -> None: ...


def prepend_to_content(content: Any, text: str) -> Any: ...
    # str -> "text\n\ncontent"; list -> merged into a leading str block, or
    # inserted as a new one when the first block is structured.
```

- `__init__` 中一次性 build **全部**团队静态 section（role / HITT 契约 [gate `team_backend.hitt_enabled()`] / bridge 自契约 [仅 BRIDGE_AGENT] / workflow / dispatch / lifecycle / extra + inbound 说明），并构造 `TeamContextTracker`。HITT 契约 gate 用 sync 的 `hitt_enabled()`（而非 live 名册），所以 HITT 一开即 present、无需等 spawn 人类。
- 该 rail 有两个写入点，见不变量 13：`on_user_message` 把 tracker 的待发文本拼进正在被消费的那条输入；`before_model_call` 先把所有 static section 写回 builder，再把**仍然**待发的内容作为一条独立 user message **追加**到尾部（tool-loop 中途出现、无输入可搭车的情况）。两者都在投递成功后 `commit`；`ctx.context` / `ctx.session` / `ctx.inputs.message` 缺席时相应跳过。**rail 不持有任何位置状态**。
- `uninit` 删 `_static_sections` 里每个 section（HITT 契约 / bridge 自契约都在其中）；写进对话的团队状态是成员自己的历史，不由 rail 清理。

### `rails/first_iteration_gate.py`

```python
class FirstIterationGate(AgentRail):
    async def wait(self) -> None: ...
    @property
    def is_ready(self) -> bool: ...
    async def before_task_iteration(self, ctx: AgentCallbackContext) -> None: ...
    def reset(self) -> None: ...
```

- `before_task_iteration` 是 `core.single_agent.rail.base.AgentRail` 的钩子；agent 进 task loop 时被调用。
- `wait()` 不超时；caller 自行 `asyncio.wait_for` 包外层。
- `reset()` 只清状态，不取消已 `await wait()` 的协程；正常路径是先 `reset()` 再触发新一轮，等待者会被本轮的下一次 `set()` 唤醒。

### `rails/confirm_payload.py`

```python
class TeamConfirmPayload(ConfirmPayload):
    decided_by: str | None = None
        # Records who made the approval decision (e.g. "leader").
        # Not exposed to the LLM — set by TeamPermissionRail.parse_confirm_payload.

class TeamPermissionConfirmResponse(PermissionConfirmResponse):
    decided_by: str | None = None
        # Same as TeamConfirmPayload but for the dataclass-based confirm path.
```

- Both classes extend harness base classes with a ``decided_by`` field for
  internal audit tracking. Exported from ``agent_teams/rails/__init__.py``.

### `rails/tool_approval_rail.py`

```python
class TeamToolApprovalRail(ConfirmInterruptRail):
    def __init__(
        self,
        team_name: str,
        member_name: str,
        db: TeamDatabase,
        messager: Messager,
        leader_member_name: str,
        tool_names: Iterable[str] | None = None,
    ) -> None: ...

    async def resolve_interrupt(
        self,
        ctx: AgentCallbackContext,
        tool_call: ToolCall | None,
        user_input: Any | None,
        auto_confirm_config: dict | None = None,
    ) -> InterruptDecision: ...
```

- `tool_names` 限定该 rail 拦截的工具集合；`None` 表示全部（继承自 `ConfirmInterruptRail`）。
- `resolve_interrupt` 两阶段：
  - `user_input is None`：第一次进入 → 命中 auto_confirm 则 `approve()`；否则 `send_message` + `interrupt(...)`。
  - `user_input` 非空：解析为 `ConfirmPayload` → `approved=True` `approve()`，否则 `reject(tool_result=feedback)`。
- 解析失败重新 `interrupt(...)`，**不丢错误成 approve**。

### `rails/team_permission_rail.py`

```python
class TeamApprovalOrchestrator:
    def __init__(
        self,
        message_manager: TeamMessageManager,
        leader_member_name: str,
    ) -> None: ...

    async def handle_approval_request(
        self,
        request: PermissionConfirmationRequest,
    ) -> PermissionConfirmationResult: ...
        # Sends approval request via message_manager (protocol="plain"),
        # returns "interrupt" to let the rail suspend the teammate.

class TeamPermissionRail(PermissionInterruptRail):
    def _persist_allow_always(self, normalized_name: str, tool_args: dict) -> bool: ...  # always False

    @staticmethod
    def parse_confirm_payload(
        user_input: Any,
    ) -> Optional[TeamPermissionConfirmResponse]: ...
        # Mirrors PermissionInterruptRail.parse_confirm_payload but returns
        # TeamPermissionConfirmResponse with decided_by="leader".
```

- `TeamApprovalOrchestrator` 实现 `RequestPermissionConfirmationHook`，注入到 `ToolPermissionHost.request_permission_confirmation`。
- `handle_approval_request` 用 `message_manager.send_message(content=..., protocol="plain")` 发送审批请求给 leader，返回 `"interrupt"`。
- leader `approve_tool` 写 `protocol="json"` DB message 作为 fallback delivery；`MessageHandler._try_parse_approval_payload` 识别并 `resume_interrupt`。
- `TeamPermissionRail` 的 `parse_confirm_payload` 对所有分支自动注入 `decided_by="leader"`。

## 数据结构

### `PromptSection`（消费的 core 类型）

| 字段 | 类型 | 含义 |
|---|---|---|
| `name` | `str` | section 唯一名，团队侧来自 `TeamSectionName` 常量 |
| `content` | `dict[str, str]` | language → 渲染好的正文；当前所有团队 section 在构造时只填一种语言 |
| `priority` | `int` | 拼接顺序，团队 section 取 10–18 |

### `MtimeSectionCache`（通用原语，团队侧当前无使用者）

| 字段 | 类型 | 生命周期 |
|---|---|---|
| `_probe` | `Callable[[], Awaitable[int]]` | 构造时注入，缓存生命周期内不变 |
| `_fetch_and_build` | `Callable[[], Awaitable[PromptSection \| None]]` | 同上 |
| `_cached_section` | `PromptSection \| None` | 跨 `refresh` 持有，`invalidate` 清空 |
| `_cached_mtime` | `int` | 最后一次成功 fetch 时的 probe 值；初值 `0` |
| `_initialized` | `bool` | 首次调用必 miss 的标志，`invalidate` 复位 |

### `TeamPolicyRail` 状态字段

| 字段 | 类型 | 含义 |
|---|---|---|
| `_language` | `str` | 整个 rail 渲染语言；与 `SystemPromptBuilder.language` 应保持一致 |
| `_member_name` | `str \| None` | 日志与 tracker 身份 |
| `_static_sections` | `list[PromptSection]` | 构造期产出的不变内容（已剔除 `None`），含 inbound 说明 section |
| `_tracker` | `TeamContextTracker` | 团队状态的待发判定 + 基线读写 |
| `system_prompt_builder` | `SystemPromptBuilder \| None` | `init` 时绑定，`uninit` 时解绑 |

### `TeamContextTracker` 持久化基线（成员 child AgentSession，key `team_prompt_context`）

| 字段 | 类型 | 含义 |
|---|---|---|
| `identity_emitted` | `bool` | 身份段是否已投递（恒定内容，一次性）|
| `team_info_mtime` | `int` | 最后一次投递对应的 `get_team_updated_at()` 值 |
| `roster_mtime` | `int` | 最后一次投递对应的 `get_members_max_updated_at()` 值 |
| `roster` | `list[dict[str, str]]` | 最后一次告知该成员的 peer 名册；增量 diff 的 old 侧。**key 不存在**表示还没发过快照，与"存了一个空列表"语义不同 |

### `FirstIterationGate` 状态字段

| 字段 | 类型 | 含义 |
|---|---|---|
| `_event` | `asyncio.Event` | 单次开锁原语；`reset()` 调 `clear()` |

### `TeamToolApprovalRail` 状态字段

| 字段 | 类型 | 含义 |
|---|---|---|
| `team_name` / `member_name` / `leader_member_name` | `str` | 消息路由所需的团队 + 成员标识 |
| `message_manager` | `TeamMessageManager` | 包 `db + messager` 的发送器；rail 自身不直接持有底层句柄 |
| `tool_names`（继承） | `Iterable[str] \| None` | 拦截范围 |

### Rail 装配状态机（来自 `TeamHarness`）

`agent_configurator` 决定每条 rail 是否构造，`TeamHarness.build` 决定挂载顺序：

```
              role=LEADER     role=TEAMMATE   role=HUMAN_AGENT
TeamToolRail        ✓               ✓               ✓
TeamPolicyRail      ✓               ✓               ✓
FirstIterationGate  ✓               ✓               ✗
TeamWorkspaceRail   conditional on workspace_manager
TeamToolApprovalRail ✗  conditional ✓ when team-coordinated
                        and approval_required_tools non-empty
                        and enable_permissions=False
                                                    ✗
TeamPermissionRail  ✗  conditional ✓ when team-coordinated
                        and enable_permissions=True
                                                    ✗
```

当 `enable_permissions=True` 时 `TeamPermissionRail` 替代 `TeamToolApprovalRail`；两者互斥，不同时挂。

## 与其它 spec 的关系

- **S_03 schema**：`TeamRole` 枚举、`TeamAgentSpec.lifecycle / team_mode / teammate_mode / approval_required_tools` 字段定义在 schema 层，本 spec 的 builder / rail 仅消费这些字段。
- **S_05 agent / TeamHarness**：rail 的实际挂载点（`TeamHarness.build`）、`agent_configurator` 决定挂哪些 rail 的逻辑由 agent spec 负责；本 spec 只规定 rail 各自的契约。
- **S_07 tools**：`TeamToolRail` 与团队工具集合属于 tools spec；本 spec 仅指出 mount order（tool rail 必须先于 policy rail eager init）。`TeamToolApprovalRail` 调 `approve_tool` 工具的契约由 tools spec 定义。
- **S_10 team_workspace**：`TeamWorkspaceRail` 与本 spec 平级，但本 spec 的 `team_info` 消息正文携带 `team_workspace_path` / `team_outputs_dir`——workspace 子系统对 prompt 的可见面只通过这两个参数（`team_outputs_dir` 仅无 project_dir 成员注入，见 [[F_89]] / [[S_26]]）。
- **S_11 i18n（如有）**：`prompts/cn/` `prompts/en/` 与 `agent_teams/i18n.py` 的边界由本 spec 的不变量 10 落地；新增语言要求 `prompts/<lang>/*.md` 全套对齐 + `_LABELS` / `_I18N_LABELS` 增加映射。
- **core S_x prompts**：`PromptSection` / `SystemPromptBuilder` / `PromptTemplate` 的契约属于 core；本 spec 假定它们的行为不变（priority 升序拼接 / `add_section` 同名覆盖 / `{{placeholder}}` 渲染）。
