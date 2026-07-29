# spawn 只在首启注入初始消息，recover/resume 不再自动 send

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-06-05 |
| 范围 | `agent_teams/spawn/inprocess_spawn.py`、`agent_teams/agent/payload.py`、`agent_teams/agent/team_agent.py`、`agent_teams/agent/spawn_manager.py`、`agent_teams/prompts/sections.py` |
| 测试基线 | `pytest -m level0 tests/unit_tests/agent_teams/` → 681 passed / 2 skipped |
| Refs | #751 |

## 背景

teammate 启动链路存在一个"占位符 hack"导致的设计缺陷，使得 recover / resume /
restart 会重复触发成员的首轮 LLM：

1. **占位符 fabricate**：`inprocess_spawn.py` 与 `payload.py`（子进程 wire 格式）在
   `initial_message` 为空时填一句 `"Join the team and wait for your first
   assignment."`，让 `inputs["query"]` **永远非空**。
2. **无条件首轮 send**：`TeamAgent.invoke` / `stream` 在非 leader-routing 分支上
   **无条件** `enqueue_user_input` → `USER_INPUT` 内部事件 →
   `AgentLifecycleHandler.on_user_input` → `deliver_input` → `harness.send(...)`，
   即便内容只是占位符也会跑一个 LLM 首轮。
3. **恢复路径 replay prompt**：`SpawnManager.restart_teammate` 在每条容错路径
   （`recover_team` / `on_teammate_unhealthy` / 切 session 的
   `restart_for_session_switch`）都读出持久化在 DB 的 `teammate.prompt` 当
   `initial_message` 再 send 一遍。`teammate.prompt` 是「首次启动任务指令」，DB
   永不清空——于是每次恢复都把首轮重复触发。

期望行为：SpawnManager 只在**首次启动**向 team agent 注入初始消息；
recover/resume/restart 等流程**不**自动 send 触发底层 harness send；
**只有真的有消息时才 send**。

### 关键洞察（消除特殊情况）

占位符是 hack。删掉它后，首轮 send 由「query 是否非空」单点门控：

- teammate 的总线订阅、`READY` 状态、协调循环都在
  `CoordinationKernel.start()` 完成，**不依赖**首轮 send；
- `enqueue_initial_mailbox_poll` 触发的 `POLL_MAILBOX` 走
  `MessageHandler._process_unread_messages`，其中 `if not new_messages: break`
  ——**收件箱为空就不 send**。

所以一个无消息的成员（首启无 prompt / restart / recover 都属此类）只是「起来、
订阅、空等」，有消息时由 mailbox poll 自然投递。首启带 prompt → query 非空 →
照常 send。两种情况落到同一条代码路径，无 `role` / `is_restart` 分支。

## 决策

1. **删除占位符 fabricate**（`inprocess_spawn.py` / `payload.py`）：空 `initial_message`
   产出空 query（`""`）。`payload.py` 保留 `"query"` 键以维持子进程 wire schema 不变。
2. **首轮 send 门控**（`team_agent.py` 的 `invoke` / `stream`）：
   - `raw_query` 取值改为 `(inputs.get("query") or "")`，正确处理
     `{"query": None}`（`.get` 默认值对「键存在但值 None」不生效）。
   - 非 leader-routing 分支：`if raw_query: enqueue_user_input(inputs)`；
     `enqueue_initial_mailbox_poll` 保持无条件——它是「只有有消息才 send」
     的兑现点。
   - 该门控对 leader 同样成立：leader 冷启动 query 必非空（用户任务）→ 照常 send；
     leader resume/recover 透传空 inputs 时跳过无意义的空首轮（严格更优）。
3. **恢复路径不 replay prompt**（`spawn_manager.py` 的 `restart_teammate`）：
   不再 `get_member` 读 `teammate.prompt`，`initial_message` 恒为 `None`。成员存在性
   已由 `build_context_from_db` 校验。
4. **清理过时系统提示词**（`sections.py` HITT 段，cn + en）：删掉教 LLM「首启只收到
   『Join the team and wait...』占位消息就静默」的指引——占位符已不存在，该规则不可达
   且会误导。"无控制者指令则静默"由前面的 bullet 已覆盖。

## 拒绝的方案

- **保留占位符 + 仅在 invoke 判断占位符字符串跳过**：用魔法字符串做反向识别是更脏的
  hack，且子进程 wire 仍传无意义内容。直接消除占位符才是根因解。
- **在 spawn 层加 `is_restart` 标志区分首启/重启**：引入新的分支与状态。实际上
  "有无消息" 已完整表达意图——首启无 prompt 与 restart 都该静默，无需区分。
- **把门控限定为非 leader 角色**：会重新引入 role 特殊分支。门控对 leader 同样正确
  （冷启动必非空，resume 空首轮本就该跳过），故保持 role-agnostic。
- **external CLI 成员同步纳入**：`external_cli_spawn.py` 总是构造一条精心设计的 join
  prompt（CLI 二进制启动所需，注释说明流式 CLI 会把 "wait" 当真而卡住）。其纯
  mailbox 驱动启动未经实测，有卡住/早退风险，**本次不纳入**，保持原状单独评估。
  `restart_teammate` 传 `None` 后 CLI restart 由 `external_cli_spawn` 内部 join prompt
  自驱（仍会 send），CLI 行为不被破坏。

`HarnessProtocol` / `MemberRuntime` 表面（[[S_18_harness-interaction-contract]]）未变
——`send` 语义不变，改动只在 `TeamAgent.invoke` 的调用侧门控，故 S_18 无需修订。

## 验证

- 更新契约测试 `test_spawn_payload_contract.py`：无 `initial_message` 时
  `payload["query"] == ""`（原断言占位符字符串）。
- 新增 `test_team_agent_coordination.py`：空 query / `{"query": None}` 不
  `enqueue_user_input`、仍 `enqueue_initial_mailbox_poll`；非空 query 照常
  send；stream 路径同此门控。
- 新增 `test_spawn_manager_restart.py`：`restart_teammate` 以 `initial_message=None`
  re-spawn，且不读 `teammate.prompt`。
- 回归：`pytest -m level0 tests/unit_tests/agent_teams/` → 681 passed / 2 skipped。

## 已知遗留

- external CLI 成员的 join prompt 仍是「首启 + 重启都 send」。后续应评估其纯
  mailbox 驱动启动是否可行，若可行则统一到同一「无消息不 send」模型。
