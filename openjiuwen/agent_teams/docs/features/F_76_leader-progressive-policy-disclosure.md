# Leader 协同准则的渐进式披露

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-08-08 |
| 状态 | **已实现** |
| 范围 | `prompts/`（新增 `leader_bootstrap.md` cn/en、`build_leader_bootstrap_section` / `build_leader_policy_disclosure`、`leader_policy.md` 去槽、`leader_swarmflow.md` 收尾句改写）、`rails/team_policy_rail.py`（leader 分支）、`tools/tool_team.py`（`BuildTeamTool` 承载披露）、`tools/tool_factory.py` + `rails/team_tool_rail.py` + `rails/elements.py` + `agent/agent_configurator.py`（`team_mode` 接线）、`tools/locales/descs/*/build_team.md`（返回内容说明）、`core/context_engine/processor/compressor/`（压缩后重注入） |
| 测试基线 | `python -m pytest tests/unit_tests --override-ini="addopts="` → **14442 passed, 308 skipped, 3 xfailed** |
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

1. **cold recovery 的 leader 拿不到准则**。团队已存在、leader 经 `recover_team()` 重启时不会再调 `build_team`，历史里若已无那条 tool result（跨 session 重建），准则就缺失。用户已知悉并接受（选项二的代价），未来若要补，最小改法是 recovery 路径主动补投一条披露消息。
2. **`leader_policy.md` 的「任务状态流转」一节仍同时描述两种模式**的状态转换（`in_progress` 那段既讲自主认领也讲调度框架）。本次改动刻意不动模板正文（要求是"内容与之前完全一致"），所以这处模式混杂被搬进了 disclosure 而非被消除。真要彻底隔离，需要把该节按 dispatch_mode 拆成两份模板。
3. **external CLI leader 未覆盖**。`build_team_member_system_prompt` 仍渲染全量静态 section；当前 external CLI 成员都是 teammate，暂不构成问题。
