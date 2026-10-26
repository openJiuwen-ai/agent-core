# Fork 身份转换：继承上下文后显式声明当前身份

## 元信息
| 项 | 值 |
|---|---|
| 日期 | 2026-08-13 |
| 范围 | `prompts/messages.py`（`build_identity_text` 加 `fork_capable`；新增 `build_identity_conversion`）、`inbound_render.py`（新增 `render_team_context_with_identity`）、`team_context.py`（`TeamContextTracker` 加 `fork_source` + `_fork_capable` 渲染分派）、`schema/team.py`（`TeamRuntimeContext.fork_source`）、`rails/team_policy_rail.py` / `rails/elements.py`（`fork_source` 透传）、`agent/agent_configurator.py`（TEAM_POLICY params）、`agent/team_agent.py`（`_on_teammate_created` 写 `ctx.fork_source`）、`prompts/{cn,en}/inbound_tags.md` |
| 测试基线 | `test_team_messages.py` / `test_inbound_render.py` / `test_team_policy_rail.py` / `test_fork.py` / `test_spawn_payload_contract.py` 新增用例全绿；`tests/unit_tests/agent_teams/` + `core/single_agent/rail/` + `harness/test_deep_agent_rail_event_routing.py` 2694 passed |
| Refs | fork 系列（F_75 / F_76 / list_checkpoints） |

## 背景

fork 继承把源成员（如 `reader`）的对话历史原样注入目标成员（如 `dev-1`）。这份历史里包含源成员的 `<team-context>` 身份块（`你的 member_name: reader`）。目标成员 spawn 后 `TeamContextTracker` 又会投递一份**它自己**的身份块（`你的 member_name: dev-1`）——于是子代上下文里并存两份身份，模型可能会困惑于"我到底是谁"。F_75 系列从未处理这份残留。

**不能直接剥掉源身份块**：身份块在对话里位置靠前，改写/删除它会让其后所有 token 的 KV 前缀 cache 失效，fork 省算力的收益归零。约束因此是：**继承段一个 token 都不改，转换语义只能追加**。

## 决策

### D1：两份文本，两个位置

- **能力声明**（恒定、全团队）：「你是拥有身份转换能力的成员，当前身份以本块及你收到的身份转换通知为准」。写在每个成员自己的身份正文顶部（`build_identity_text(fork_capable=True)`）。源身份块因此自带"身份可变"的预告，被 fork 继承时子代读到的是"当时的状态"而非"永恒的本质"。
- **转换通知**（每次 fork 生成）：「你继承了 reader 的上下文，你的身份现在是 dev-1；更早的身份块是此前身份，其私有约定/工作区不再适用」。渲染为 `<identity-conversion>` 子块，嵌套在**目标自己**的 `<identity>` 内，与"你的 member_name 是 dev-1"同处一条最新消息——权威性最强，且明确作废源身份块里的工作区路径与私有工作约定（这两样光靠"身份转换"四个字盖不住）。

### D2：能力声明只对 `enable_fork=True` 团队渲染

`TeamContextTracker._fork_capable = team_backend.fork_enabled()`（`getattr` 兜底让没有该方法的 fake / 外部 runtime 走 off 路径）。`False` 时 `build_identity_text` 不输出声明行、`pending_text` 走原 `render_team_context`——**输出与改造前逐字一致**，非 fork 团队的前缀 KV 与模型输入零变化。

### D3：XML 嵌套结构只转义最内层

`<team-context>` 正文原本整体 `html.escape`。新增 `render_team_context_with_identity` 在**结构层**拼内壳（`<identity>` / `<identity-conversion>` 标签不转义），只对最内层正文转义——否则嵌套标签会被转义成 `&lt;identity&gt;`，模型读到一堆实体符号。转换段正文引用旧身份块时用纯文本「更早的「成员身份」块」，不用字面 `<identity>`。

### D4：`fork_source` 走 `TeamRuntimeContext` 跨进程透传

`_on_teammate_created` 在 fork 上下文非空（且 `is_empty()` 为假，即真的继承了消息）时把源名写进 `ctx.fork_source`（缺省 = leader 自己的名字）。它随 `build_spawn_config` 的 `ctx.model_dump` 序列化、`from_spawn_payload` 的 `model_validate` 还原，天然覆盖 inprocess / subprocess 两条 spawn 路径。装配链：`ctx.fork_source` → `agent_configurator` TEAM_POLICY params → `TeamPolicyInput.fork_source` → `TeamPolicyRail` → `TeamContextTracker`。

## 拒绝的方案

- **fork 时剥掉 / 替换源身份块**：身份块位置靠前，动一个 token，其后整个继承段的前缀 cache 全部失效 → fork 白做。**不采纳**。
- **转换通知追加为继承历史尾部的独立消息**：它是普通历史，会被后续对话埋没，权威性随时间衰减——而"当前身份"恰恰是最不该衰减的信息。改为并进目标自己的身份块（D1）。
- **能力声明放静态系统提示词 section**：F_76 规定 leader 系统提示词只留 `team_bootstrap` + `team_extra`，加 section 要么破坏该设计、要么给 leader 单独走披露通道——两套装配道。身份块内（A）绕开约束，且声明随身份块被 fork 继承，语义内聚。

## 验证

- `test_team_messages.py`：`build_identity_text` 默认输出与改造前逐字相同；`fork_capable=True` 加声明行且位于名字之前；`build_identity_conversion` cn/en 含源名/当前名/不再适用。
- `test_inbound_render.py`：`render_team_context_with_identity` 嵌套结构、仅最内层转义（结构标签不被转义）、无 conversion 时不输出该子块、info 仅在存在时出现。
- `test_team_policy_rail.py`：`enable_fork=False` 身份输出不含 `<identity>` 与能力声明（字节级回归护栏）；`enable_fork=True` 普通 spawn 有 `<identity>` + 能力声明、无转换段；`fork_source` 透传后含 `<identity-conversion>`。
- `test_fork.py`：`_on_teammate_created` 缺省 `fork_source`=leader 名、命名的源正确透传、无 fork（fork_info 为 None）不写 `fork_source`。
- `test_spawn_payload_contract.py`：`fork_source` 序列化/还原 round-trip，默认 `None`。
- 全量 `tests/unit_tests/agent_teams/` + `core/single_agent/rail/` + 相关 harness 测试 2694 passed。

## 已知遗留

- **外部 CLI 成员的身份 section**（`build_team_identity_section`）不带能力声明与转换段：外部 CLI 不参与 fork（F_75 明确不做），无此需求。若未来支持，需在 CLI spawn 路径补 `fork_source` 注入。
- **能力声明是恒定文本但随成员各渲染一份**：未放进全队共享的静态前缀，跨成员 token 不共享。可忽略——声明是短句，且身份块本就 per-member。
