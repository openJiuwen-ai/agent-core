# Tool Variants + Templated Descriptions（dispatch_mode 进入工具装配）

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-07-08 |
| 范围 | `tools/locales/`（loader + descs + fragments）、`tools/tool_task.py`、`tools/tool_message.py`、`tools/tool_permissions.py`、`tools/tool_factory.py`、`tools/team.py`（`resolve_leader_member_name`）、`schema/task.py`、`schema/team.py`、`schema/blueprint.py`、`tools/task_manager.py`、`tools/database/task_dao.py`、`rails/elements.py`、`rails/team_tool_rail.py`、`agent/agent_configurator.py`、`external/descriptor.py`、`external/client.py`、`external/cli_agent/spawn.py` |
| 测试基线 | `pytest tests/unit_tests/agent_teams` → 改动前 1746 passed / 16 skipped；改动后 **1796 passed / 16 skipped**（新增 50：`tools/test_tool_variants.py` 25、`tools/test_locales_variants.py` 25） |
| Refs | `#751` |

## 背景

`TeamAgentSpec.dispatch_mode`（`autonomous` / `scheduled`）此前是**纯提示词开关**：只流向
`TEAM_POLICY` rail 去挑 `dispatch_{mode}_{role}.md` 模板，从不进入 `TEAM_TOOL` rail、
`create_team_tools` 或任何权限集合。于是提示词在对 LLM 撒谎：

- `dispatch_scheduled_teammate.md` 说「你没有 `claim_task`，用 `member_complete_task` 完成」
  —— 实际 teammate 照样有 `claim_task`，且 `member_complete_task` 只在 `HUMAN_AGENT_TOOLS` 里。
- `dispatch_scheduled_leader.md` 说「`create_task` 必须指定 assignee」——`TaskGraphSpec` 根本
  没有 `assignee` 字段，`add_graph` 硬编码 `assignee=None`。

同时工具层缺两个能力：(1) 工具描述无法按场景动态装配（44 个 desc md 里一个 `{{placeholder}}`
都没有，`t()` 的 kwargs 分支是死代码）；(2) `all_tools` 是 `name → 单实例` 的 dict，装配链
只有集合减法，**无法替换同名工具的形态**。

## 决策

### 1. 形态选择放在 `all_tools` 的构造期

`all_tools` 只能做减法——但那是对**已构造好的 dict** 而言。它每次调用都重新构造，把形态选择
放在构造那一刻，减法链一行不改，下游（权限集、`exclude_tools`、prompts、MCP）零感知。每个
工具实例仍是「schema 扁平、`invoke` 直线、零分支」——分支被前移到装配器（S_08 不变量 18）。

三个正交维度各自独立表达：注册与否走**集合减法**，同名不同形态走**构造期查表**，
同形态不同文案走**不同 desc_key**。

形态表是模块级字面量而非注册表：形态是闭集（2 dispatch × 3 role），缺失组合抛 `KeyError`。

### 2. `send_message` 的 role 维度由类吸收，不进描述

`send_message` 的形态是 `(dispatch, leader|member)` 二维：scheduled 下 leader 仍要广播 / 多播 /
`_auto_start_members`，只有成员侧收敛成 `ReportToLeaderTool`。

**这一条是设计中途被推翻的部分。** 最初方案让 `make_translator(lang, variant=dispatch_mode)`
按 variant 挑描述片段。但 leader 与成员共享 `ToolCard.name`（都是 `send_message`）、共享
同一份 `send_message.md`，而 translator 只绑 dispatch、不绑 role —— scheduled 的 leader 会
拿到「只能发给 leader/user」的描述，**与其实际行为完全相反**。

改为：**骨架文件名 = `desc_key`（每形态一份），形态类自己选 key**；`{{slot}}` 只引用与形态
无关的共享片段。副作用是 `make_translator` 签名一行不改，`tiny_agent` / `swarmflow` 等调用点
零影响，i18n 层也不必知道 `dispatch_mode` 是什么。

**收件人本身也角色化，不放具体名字。** `ReportToLeaderTool` 的 `to` enum 是
`["leader", "user"]` —— `"leader"` 是**角色占位符**，工具在 `_dispatch` 里翻译成真实
leader member_name 再投递。理由：schema 不该泄漏会变的 leader 身份、不该逼工具在**构造期**
就知道 leader 是谁（最初版本读 `TeamBackend.leader_member_name`，为空即 `ValueError`）、
LLM 看到的应是角色而非 `team_leader` 这种实例名。名字解析推迟到 invoke，缺 leader 名时
只有 `to="leader"` 软失败，autonomous 完全不受影响。

**leader 名从 DB 查，不从装配链传。** 解析走 `TeamBackend.resolve_leader_member_name()`：
leader 名是 team 的持久属性，`build_team` 已经把它写进 `team_info` DB 行（single source of
truth）。与其在 spec → configurator → descriptor → client → backend 一路复制（复制字段正是
「external 没传导致空串」那类洞的温床），不如让需要它的 backend 直接查 DB 一次并缓存
（leader 一个 team 内不变）。leader 侧 backend 构造时 `member_name if is_leader` 天然有值、
走快路径不查库；member 侧字段为空、查 `team_info` 行。

### 3. `assignee` 与任务图同一事务落库

scheduled 的 `create_task` 里 `assignee` 必填，随 `TaskGraphSpec` → `NewTaskSpec` 进同一次
`mutate_dependency_graph`。`initial_status` 由依赖决定，使得 refresh pass 一行不改：

```
assignee 非空 且 无 depends_on  →  CLAIMED   # refresh 只重写 PENDING/BLOCKED，不碰它
其余                            →  PENDING   # 有依赖的被 refresh 自然翻成 BLOCKED，携带 assignee
```

`CLAIMED` 虽在 `TASK_DEPENDENCY_REJECT_STATUSES` 里，但那只对 **edge source（依赖方）** 生效，
而预指派成 `CLAIMED` 的任务必定没有 `depends_on`、从不作为依赖方——安全。

### 4. 描述模板化：槽由 loader 枚举并强制填满

`descs/<lang>/<desc_key>.md` 声明 `{{slot}}`，由 `descs/<lang>/fragments/<slot>.md` 填充。
loader 用正则从模板自身枚举槽、逐个加载片段、渲染后做残留守卫（`"{{" in rendered → ValueError`）。

这是对一个已知陷阱的正面回答：`PromptAssembler.prompt_assemble`（`assembler.py:107-117`）
对缺失的 key **回填 `{{key}}` 字面量**而不报错，会把占位符直接喂给 LLM，与 S_08 不变量 6
（缺失即报错）直接冲突。我们**从不**把不完整的 kwargs 交给 `prompt_assemble`。

### 5. 外部成员链路

外部 CLI 成员是唯一 prompt 与 tools 分两条链装配的角色。`TeamSpec` 加 `dispatch_mode` /
`teammate_mode`，`TeamJoinDescriptor` 加 `dispatch_mode` / `teammate_mode`，
`ExternalTeamClient.connect` 透传给 `create_team_tools`，使外部成员的工具集与它 spawn
时的系统提示词对齐。`mcp/server.py` / `skill/cli.py` **零改动**——
它们只透 `card.input_params` 与 `str(await tool.invoke(...))`，形态换了自动跟着换。这恰好
验证了形态选择放在 `create_team_tools` 是对的层。

**leader 名不进 descriptor**（见决策 2）。它是 team 的持久属性、DB 里已有，external member 的
`TeamBackend` 直接查 `team_info` 行解析，不需要 spawn 侧填、descriptor 传、client 转发这一路
复制。这也让「external 没传 leader 名」不再是洞——根本没有这个字段可漏。

## 拒绝的方案

- **`make_translator(lang, variant=...)` 按 variant 挑片段**：拒绝。见决策 2 —— `send_message`
  的形态是 `(dispatch, role)` 二维，而 translator 只能绑一维，scheduled 的 leader 会拿到与
  行为相反的描述。变化维度是**形态**，不是 dispatch_mode。
- **建图后逐个 `assign()`，BLOCKED 的记为 `deferred` 并在 `map_result` 里回执**：拒绝。
  `add_graph` 是一次原子事务，在它后面缀一串非原子的 `assign()` 等于给 `create_task` 引入
  不可回滚的部分成功态，用工具层复杂度掩盖 runtime 缺口。`assignee` 是任务的**属性**，不是
  建图之后的补丁操作。
- **工具边界禁止 `assignee` 与 `depends_on` 共存**：拒绝。那会砍掉 scheduled 最核心的用法
  （一次规划带依赖的 DAG 并派活），逼 leader 二次派活。
- **`ReportToLeaderTool` 删掉 `to` 字段（或收成单一 const）**：拒绝。会物理堵死 teammate 回复
  `user` 的通道（`user` 是伪成员，绕过 roster 校验），而那是它面向人类调用者的唯一出口。
- **保留自由 `to`、在 `invoke` 里拒绝非 leader**：拒绝。违反「schema 即契约」，LLM 会反复
  尝试发给同伴、反复吃错误，浪费 token 且污染上下文。
- **形态注册表 + 插件**：拒绝。形态是闭集，字面量表更可读、可 grep、能被类型检查器看穿。
  F_26 的四个 spawn 工具就是硬写在 `all_tools` 里的。
- **`scheduled × plan_mode` 构造期报错**：由用户决策保留 `submit_plan`，故未加 validator。
  ⚠️ 该组合当前是死锁，见「已知遗留」。

## 验证基线

```
uv run python -m pytest tests/unit_tests/agent_teams -q      # 1796 passed, 16 skipped
```

（注意 `uv run pytest` 会抓到系统 python3.9，必须用 `python -m pytest`。）

关键用例：

- `test_tool_variants.py::test_scheduled_member_swaps_claim_for_complete` —— 注册差分。
- `test_tool_variants.py::test_variants_keep_card_identity` —— `card.id` / `name` 不随形态漂移。
- `test_tool_variants.py::test_send_message_variant_narrows_to_enum` —— scheduled 成员 `to` 是
  `enum ["leader", "user"]`（角色词，不含真实 leader 名）且无 `anyOf`；leader 仍是 `anyOf`；
  `content` 描述逐字复用。
- `test_tool_variants.py::test_report_to_leader_resolves_leader_from_db` —— backend 没被传 leader
  名，`resolve_leader_member_name()` 从 `team_info` 行查到，`to="leader"` 投递到真实名。
- `test_tool_variants.py::test_report_to_leader_soft_fails_when_leader_unresolvable` —— team 行缺失时
  仍能装配，`to="leader"` 在 invoke 时软失败（非构造期 ValueError）。
- `test_tool_variants.py::test_report_to_leader_rejects_peers_at_invoke` —— 发真实 leader 名被拒、
  发 `"leader"` 角色词才投递到真实名；双层执法（MCP 绕过
  schema 时 `invoke` 仍拦）。
- `test_tool_variants.py::test_scheduled_create_task_lands_assignee_atomically` —— `t2 depends_on t1`
  且两者带 assignee → 一次 `add_graph` 后 `t1 = CLAIMED/dev-1`、`t2 = BLOCKED/dev-2`（assignee
  已落库，不是 `None`）。
- `test_tool_variants.py::test_every_toolset_assembles` —— 笛卡尔冒烟（lang × dispatch × role）。
  **已做负向验证**：临时删掉 `en/fragments/artifact_handoff_policy.md` → 6 个 en 用例立即失败。
- `test_locales_variants.py` —— 槽枚举、共享片段逐字复用、缺片段 `FileNotFoundError`（带期望
  路径）、缺 key `KeyError`、运行时错误串的 `format_map` 路径未被破坏。

## 已知遗留（归 runtime）

1. **`BLOCKED + assignee` → `CLAIMED`**：依赖解除时的翻转与 `TaskClaimedEvent` 补发。
   `_refresh_status_in_session`（`database/task_dao.py:110-119`）已有 `BLOCKED → PENDING`
   的翻转，加一个 `assignee is not None` 分支即可。工具层已经把 assignee 落到位。
2. **成员启动路径**：`_auto_start_members`（`tool_message.py`）只在 leader 调 `send_message`
   时触发。scheduled 提示词禁止广播 → **当前没有任何代码会启动成员**。调度器必须提供独立
   启动路径。
3. ⚠️ **`scheduled × plan_mode` 是死锁**（用户选择保留 `submit_plan`，工具层不拦）。
   `assign()` 直推 `CLAIMED` 绕过 `claim()` 里的 PLAN_MODE gate（`task_manager.py:454`），
   而 `complete()` 要求 PLAN_MODE 成员只能完成 `PLAN_APPROVED` 任务（`task_manager.py:531`）。
   成员没有任何工具能把 `CLAIMED` 推到 `PLAN_APPROVED`。runtime 必须补一个成员侧的
   `CLAIMED → PLAN_SUBMITTED → PLAN_APPROVED` 入口，否则该组合下任务永远无法完成。

**本次不碰、仅记录的既有死代码**：`qualify_ids` 整条链（`team_tool_rail.py:92` 赋值后从不读，
`agent_configurator.py` 还在算）；`SHARED_TOOLS` 里的 `workspace_meta`（不在 `all_tools`，由
`TeamToolRail.init` 单独 append）；`ListMembersTool`（从未进 `all_tools`）。
