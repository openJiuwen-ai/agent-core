# Leader 协同准则的渐进式披露

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-08-08 |
| 状态 | **已实现** |
| 范围 | `prompts/`（新增 `leader_bootstrap.md` + `task_state_autonomous.md` / `task_state_scheduled.md` cn/en、`build_leader_bootstrap_section` / `build_leader_policy_disclosure` / `build_team_task_state_section`、`leader_policy.md` 去槽 + 摘除状态机一节、`leader_swarmflow.md` 收尾句改写）、`rails/team_policy_rail.py`（leader 分支）、`tools/tool_team.py`（`BuildTeamTool` 承载披露 + `enable_task_verification` 的 dispatch 门控）、`tools/tool_factory.py` + `rails/team_tool_rail.py` + `rails/elements.py` + `agent/agent_configurator.py`（`team_mode` 接线）、`tools/locales/descs/*/build_team.md`（返回内容说明 + `{{build_team_verify_gate}}` 槽）、`tools/locales/descs/*/fragments/build_team_verify_gate.md`（新增）、`tools/locales/{cn,en}.py`（参数文案纠错）、`tools/locales/__init__.py`（节级槽 omit 后的间距归一化）、`core/context_engine/processor/compressor/`（压缩后重注入） |
| 测试基线 | `python -m pytest tests/unit_tests --override-ini="addopts="` → **14448 passed, 308 skipped, 3 xfailed**；补上 `build_team` 的 dispatch 门控后（2026-08-10，rebase 到 upstream/develop 之上）→ **14595 passed, 308 skipped, 3 xfailed** |
| Refs | #984 |
| 关系 | 直接动因是 F_62 / F_73 把调度模式的验证层做厚之后暴露的问题。复用 F_57 的"工具形态在构造期分化"思路，把它从工具 schema 扩展到提示词投递。与 F_70（团队状态走对话历史）同源：都在回答"这段内容该在什么时刻、以什么身份到达成员" |

## 背景

F_73（reviewer 角色拆分与弹性验证）落地后，一个问题变得无法回避：**系统提示词里无法隔离不同协同模式的干扰**。

leader 的系统提示词是一次性拼死的：role policy、workflow、dispatch、lifecycle、HITT 契约、inbound tags 全部在 `TeamPolicyRail.__init__` 组装完成。其中 dispatch 一节按 `dispatch_mode` 选模板，看起来已经隔离了，但实际没有：

1. **三条协同路径的文案互相污染**。自主模式、调度模式、swarmflow 各有一套行为约定，而它们共处一份提示词。`leader_swarmflow.md` 结尾不得不写一句"本节之后的所有内容描述的都是 build_team 路径"——用散文给提示词划边界，这本身就是设计失败的信号。
2. **调度模式的 reviewer 机制越做越厚**（F_62 的投票、F_73 的三类 reviewer + 打分表 + 返工轮数），这些文案对自主模式团队是纯噪音，但它们和 leader_policy 的「任务状态流转」一节相互引用，拆不干净。
3. **构造期还不知道该讲哪一套**。`enable_hitt` / `enable_task_verification` 的最终生效值由 `build_team` 调用决定（spec 天花板 × leader 选择），而 HITT 契约在 rail 构造时就按 spec 的 `hitt_enabled` 拼好了——**讲的是能力上限，不是实际能力**。

## 核心洞察

三条：

1. **`build_team` 是团队协同的唯一入口，也是所有模式变量落定的那一刻。** 在它返回之前，leader 不需要任何协同准则（它唯一该做的就是建队）；在它返回之后，每个变量都已确定。这个时间点天然就是披露边界——不是我们设计出来的，是数据流本来的形状。
2. **提示词的投递时刻是可以设计的，不是只能"全都塞进系统提示词"。** 团队状态已经在 F_70 走了对话历史这条路；协同准则同理可以走工具返回值。系统提示词只该装"在任何时刻都成立"的内容。
3. **一份内容只该有一个消费者。** 分流规则（`leader_swarmflow.md`）原本填进 `leader_policy` 的 `{{collaboration_mechanism}}` 槽，现在只填进 bootstrap；`leader_policy.md` 的占位符**直接删掉**而不是留着填空串——留着就会有人再往里塞东西。

## 设计

### 1. leader 的系统提示词只剩两个 section

| section | 内容 | 为什么留在前缀 |
|---|---|---|
| `team_bootstrap`（P:11，新增） | 身份定位 + `{{collaboration_mechanism}}` 槽（swarmflow 分流规则，gate 同工具）+ 「先调 build_team，完整准则随它返回」 | 这是**建队之前**唯一成立的内容 |
| `team_extra`（P:17，不变） | 用户经 `TeamAgentSpec.base_prompt` 给的自定义指令 | **不是团队协同准则**，是调用方对这个 leader 的指令，且往往正是"该建什么团队"的依据，必须建队前生效 |

`swarmflow_enabled=False` 时槽收敛为空串，bootstrap 退化成一句 build_team 起手指令（266 字符）——沿用 prompts/AGENTS.md 编辑规则 2「能力关掉时讲这个能力的文案必须跟着消失」。

### 2. 其余全部由 `build_team` 的 ToolResult 披露

`build_leader_policy_disclosure(...)` 复用**原来那套 section 拼接**（`build_team_static_sections` + `SystemPromptBuilder`，priority 排序 + `\n\n` 拼接），内容与改动前逐字一致，只是装配产物从"系统提示词"变成"工具返回文本"。

排除三项，各有理由：

- `team_identity`：per-member，归 `TeamContextTracker` 走对话历史（F_70 不变量 13）
- `team_extra`：见上，留在前缀
- `team_bootstrap`：已在前缀，路都选完了再重复一遍路标是噪音

**HITT 契约的 gate 因此变准了**：`map_result` 读的是 `output.data["enable_hitt"]`，即 `build_team` 解析后的**实际生效值**，而非 spec 天花板。这是本次改动顺带修掉的一处旧偏差。

### 2a. 「任务状态流转」按 dispatch_mode 拆成两份模板

披露只解决"什么时候讲"，不解决"讲的内容本身混了两种模式"。`leader_policy.md` 的「任务状态流转」
一节就是后者的典型：同一节并列描述 `pending → in_progress` 的两种驱动（自主认领 / 调度框架），
并无条件地教 leader 用 `create_task(reviewer=[...])` 配验证闸。

**这是一次真实回归**。该节原本是模式感知的：

```diff
- 用 `update_task(reviewer=[...])` 指派；在 `create_task` schema 暴露 `reviewer` 的调度形态里，
- 也可以创建时直接设置。验证者须是真实成员且不能是 assignee 本人。
+ 用 `create_task(reviewer=[...])` 或 `update_task(reviewer=[...])` 指派（不能是 assignee 本人）
```

改动它的那次提交（`1208ed1d`，scheduled-only 的临时 reviewer harness）在 PR 描述里写的是
"回退模式无关文件中的 reviewer 特定描述，**避免污染 autonomous dispatch 模式**"——实际做反了：
把模式感知措辞抹平成了无条件措辞，正好污染了 autonomous。它同时删掉的"验证者须是真实成员"
对 scheduled 是正确的（scheduler 会为非成员名建临时 harness），对 autonomous 则不是。

于是拆成 `task_state_autonomous.md` / `task_state_scheduled.md`，由
`build_team_task_state_section`（P:16，LEADER only）按 `dispatch_mode` 挑版。

**autonomous 版对验证闸一字不提**——连"本模式没有验证闸、不要试图绕出一个"这样的警告也不写。
最初的草稿写了那句警告，是错的：**点名一个不存在的能力，恰恰是让模型去够它的原因**。这与
`spawn_teammate` 关掉 fork 时那整节散文直接消失、而不是变成"本团队不支持 fork"是同一条规则
（`prompts/AGENTS.md` 编辑规则 2）。autonomous 模板因此就是一份自洽的状态机：状态集里没有
`in_review`，转换表里没有验证边，正文不出现 `reviewer` / `验证` 任何字样，只在末尾给出这个模式
真正可用的质量把关方式（content 写验收标准 + leader 亲自审阅）。

门控的实质理由仍然成立：`TaskCreateTool` 不暴露 `reviewer`；即便用 `update_task` 硬配，
`TeamScheduler` 只在 `dispatch_mode == "scheduled"` 的 leader 上构造，没有任何东西会去唤起
那些验证者，任务会永久停在 `in_review` 并占死该成员唯一的活跃任务名额（`in_review` 属于活跃态）。
但这个理由属于设计文档和代码注释，不属于喂给 leader 的提示词。

`test_autonomous_never_mentions_the_verify_gate` 是这条的回归闸：双语断言 `reviewer` /
`in_review` / `verify_task` / `验证` 四个词在 autonomous 模板里一个都不出现。

### 2a-2. `build_team(enable_task_verification=...)` 走同一道门

同一道门还漏了一个入口：`build_team` 的 `enable_task_verification`。这个开关能开关的东西**只有**
verify 闸，而闸只在 scheduled 下存在——三个消费点（`create_task` 的 scheduled 形态剥 reviewer、
`update_task` 剥 reviewer、`TeamScheduler._reconcile_reviews` 整段短路）在 autonomous 下**一个都
不可达**。所以在 autonomous 下把它摆给模型，是让模型去调一个既开不了也关不了的开关。

门控与 `update_task` 逐字同形：属性只在 `dispatch_mode == "scheduled"` 进 schema，
`{{build_team_verify_gate}}` 那节散文用同一个信号一起消失，`invoke` 对偷传报错拒掉而非静默剥离。

**scheduled 下必须回带实际生效值**（`data["enable_task_verification"]` + `map_result` 里的
`task_verification=`）。理由是这个开关的天花板语义与 `enable_hitt` / `enable_bridge` **不同**：后两者
撞天花板会 `raise_error`，它是静默收窄——

```python
effective = spec_ceiling and (arg if arg is not None else True)
```

spec 配 `False` 时，leader 传 `True` 得到的是 `False`，且没有任何报错。回带生效值是 leader 唯一
能发现"我要的验证闸没拿到"的通道；不回带，它就会围绕一个不存在的闸去给任务配 reviewer，而那些
reviewer 会在 `create_task` / `update_task` 里被静默剥掉。**这条静默收窄是 F_62 既有的、文档化的
契约**（描述里明写"用户配置 false → 无论传什么都不生效"），这次不改它，只把生效值补回结果里。

顺带修掉参数文案的一处**反向错误**：`build_team.enable_task_verification` 原文写"reviewer 分配不受
此开关影响——无论开关值如何，你都应为关键交付任务指派 reviewer"，而代码在开关关闭时**恰恰会剥掉
reviewer**（`tool_task.py` 两处），同一工具的 `_desc` 段落也写的是"reviewer 会被忽略"。三处互相矛盾，
以代码为准改文案。

### 2a-3. capability 槽的间距：omit 掉正文中间的 `##` 节

`build_team_verify_gate` 是第一个**位于正文中间的 `##` 级 capability 槽**（`fork_usage` 在文末靠
`strip()` 兜住，`update_task_verify_gate` 是 bullet 不是节）。模板按既有约定在节级槽两侧各留一个
空行，于是 omit 时槽塌成空串会留下**两个连续空行**——一个"这里本来有东西"的洞。模型读的是这段
raw markdown，洞正是让它去问"是不是少了什么能力"的诱因，与 2a 里"不点名不存在的能力"同一条规则。

修在 `_render_desc` 一处：插值后把 3+ 连续换行折回 2 个，与既有的 `strip()`（管首尾槽）是同一个
意图的两半。全仓 desc / fragment 无一处故意使用连续空行，所以这个归一化没有副作用。顺带把
`update_task.md` 里槽前缺的那个空行补上——之前不能补正是因为补了会在 omit 时留双空行，现在安全了。

### 2b. `build_team` 对已存在的团队幂等：接管而不是失败

披露挂在 `build_team` 上，于是**没有那次调用的 leader** 什么也拿不到。到底哪种运行会落进这一格，
取决于**对话历史在不在**，而这由 dispatch 的 `team_in_session` 一维决定（`runtime/dispatch.py`）：

| 分支 | `team_in_session` | child session id | leader 的对话历史 | 准则从哪来 |
|---|---|---|---|---|
| `COLD_RECOVER` | True（同一个 session 继续跑） | 同 id | **恢复了** | 历史里那条 build_team tool result（`team_policy` 重注入保证它不会被压缩掉） |
| `NEW_TEAM_IN_SESSION` | False（新 session 接手已有团队） | 新 id | **空的** | **无处可来** —— 这一格才是问题所在 |

child agent session **共享 team session id**（`harness/team_harness.py` 的 `_make_child_session`），
所以历史随 session 走：同 session 恢复就还在，换 session 就没了。**真正缺准则的是
`NEW_TEAM_IN_SESSION`**——它的 leader 从一段空历史起跑，面对的却是一个成员齐备、任务可能过半的团队。
而此时 `create_team` 撞主键 `IntegrityError → return False`，`build_team` 随即 `raise RuntimeError`，
leader 只剩 bootstrap：知道"要建团队"，对手上这个团队一无所知。

**cold recovery 不是"不需要"，是明令禁止**。它的历史里已经有准则，而那条 tool result 由
`team_policy` 重注入保证不会被压缩掉——所以再调一次 `build_team` 永远不会带来任何新信息，只会白烧
一轮。`BuildTeamTool.invoke` 因此在这种情况下**直接拒绝**，而不是幂等地服务它：

```python
if await self.team.rejects_rebuild():   # _history_restored and 团队行仍在
    return ToolOutput(success=False, error="...do not call build_team again...")
```

信号在 `TeamAgent.recover_from_session` 里置位（`backend.mark_history_restored()`）——那个方法**就是**
冷恢复入口，开头强制要求 session 里有该团队的 bucket，语义与 `COLD_RECOVER` 完全重合。

**两个条件缺一不可**。只看"是不是冷恢复"会误伤一种真实情况：恢复出来的 leader 若在本轮中途被
`CoordinationKernel.start` 判定为"上次清理没做完"（全部 teammate 都是 SHUTDOWN）而执行了
`clean_team`，团队行就没了——这时它**确实需要**重新建队。所以拒绝条件加上"团队行仍在"，
`test_recovered_leader_may_rebuild_a_disbanded_team` 守住这一条。

**解法是让这条路走得通，而不是给它修一条旁路**：`build_team` 开头查团队行，已存在则走
`_reattach_team` 接管并照常返回准则。接管路径写零行、注册零人——名册与成员配置都是既成事实，
teammate 由 `recover_team` 从那份名册重新拉起，与本次调用无关。

三条约束：

- **本次调用的能力参数不适用**。团队建成时就配好了，成员已经按那套跑，任务也可能已被它塑形
  （verify 闸上的 reviewer）。所以生效的 verification flag 从行里读回，而不是重算；leader 从返回值
  得知自己实际拿到什么——与 create 路径上"天花板收窄"的告知方式一致。
- **`on_team_built` 照常触发**。*这个 session* 的 checkpoint 必须记下团队行存在，否则下一次 run 会在
  与数据库不符的状态上做 dispatch 决策。
- **不发 `TeamCreated` 事件**。什么都没被创建；成员收到它只会对一个自己早就在其中的团队做出反应。

返回值的首行区分两者（`Team created:` / `Existing team taken over:`），接管时另附一句"成员已在名册上、
先看清现状、不要重复创建"。准则正文两条路径逐字相同——**差异只在这一行，不说 leader 就看不见**。

**拒绝的方案：`<team-context kind="collaboration-policy">` 兜底通道。** 曾经实现并提交过：给
`TeamContextTracker` 加一条 leader 专用 channel，把准则补投进对话，靠三道闸
（基线一次性标志 / `backend.policy_disclosed()` 进程内内存态 / 团队行探针）避免重复。它能工作，但
代价是**同一份内容有两条投递路径**，各自有各自的时机、各自的去重逻辑，还要给 `<team-context>` 引入
一个"这块是指令不是事实"的 `kind` 例外并在 `inbound_tags.md` 里解释它。让 `build_team` 幂等之后，
这些全部消失：一条路径，无闸，无例外。

### 3. 只有 leader 走这条路

teammate / human_agent / bridge 保持原逻辑不变：它们的协同约定在 spawn 时就已固定，也没有 `build_team` 调用可以挂载披露。`TeamPolicyRail._build_static_sections` 用一个 `role == LEADER` 分支分流，其余角色走原来的 `build_team_static_sections`。

### 4. 压缩与恢复：`build_team` 那条 ToolResult 会被重注入

把契约从"每轮重建的系统提示词"搬到"一条历史消息"，代价是它会被 full compaction 摘要掉——而摘要一段行为契约就等于毁掉它（"leader 被告知了如何交接工作"不是 leader 能执行的东西）。

复用既有扩展点 `FullCompactStateReinjector`（skills / task_status / plan_mode 已在用），新注册一个 `team_policy` builder：扫描被压缩的消息，找到 `build_team` 的 tool result 原样重注入，**不截断**（返回 `list[UserMessage]` 绕开 `state_snapshot_max_chars`，与 skill builder 同法——半份准则等于被静默删掉了后半段）。已在 `messages_to_keep` 里幸存时不重复注入；非团队会话直接返回空。

core 层按工具名 `build_team` 匹配，不 import `agent_teams`——与既有 `TEAM_TOOL_CALL_NAMES` 的做法一致，依赖方向不变。

## 拒绝的方案

- **build_team 之后把 section 注回系统提示词**（rail 记住"已建队"）。更抗压缩、cold recovery 也能重建，但它让系统提示词前缀在一轮中途改变——`TeamPolicyRail` 每轮重建、前缀 KV cache 依赖逐字稳定，中途长出一大段等于把那一刻之后的 cache 全作废。且它把"渐进披露"退化成"延迟拼接"，`build_team` 的返回值不再是真相源。**已与用户确认，选纯 tool result + 压缩保护。**
- **给 compaction 加通用 pin 消息机制**。为一个场景造一套新框架，而 `FullCompactStateReinjector` 已经就是这个扩展点。
- **把准则写进 session state 再由 builder 读**。要么 core 依赖 agent_teams，要么 tool 需要拿到 session 句柄（`tool.invoke` 无稳定 session kwargs）。从 transcript 里挖 tool result 无需任何新接线。
- **`leader_policy.md` 保留 `{{collaboration_mechanism}}` 槽填空串**。留着的槽早晚被再次填上，两个消费者就会重新长出来。

## 已知遗留

1. **接管路径依赖 leader 真的会去调 `build_team`**。这由 bootstrap 提示词保证（"无论团队是否已经存在
   都调这一次"），不是由代码强制的——没有任何东西拦住一个 leader 跳过它直接 `create_task`。同 session
   的 cold recovery 里它本来也不该再调（准则还在历史里，压缩掉则由 `team_policy` 重注入复原），所以
   这条只在"新 session 接手同一团队"时真正吃紧。要更硬的保障，得在 leader 的第一个非 `build_team`
   团队工具调用上加一道闸，代价是每个工具都要认识这条规则。
2. **`create_task` 的 autonomous 形态没有对称的偷传拒绝**。它靠"schema 里就没有 `reviewer` 字段"挡住
   宿主 LLM，但 `TaskCreateTool.invoke` 不像 `UpdateTaskTool` 那样显式拒绝偷传的 reviewer——它是直接
   忽略（构造 `TaskGraphSpec` 时压根不读那个键）。忽略在这里是安全的（不会写进库），所以没有卡死风险，
   只是 MCP 客户端偷传时不会得到"这个模式没有验证闸"的反馈。要补的话是在 `TaskCreateTool.invoke`
   加一条与 `UpdateTaskTool` 同形的 guard。
3. **external CLI leader 未覆盖**。`build_team_member_system_prompt` 仍渲染全量静态 section；当前 external CLI 成员都是 teammate，暂不构成问题。
