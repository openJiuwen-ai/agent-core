# Human Agent — Persist Role on Member Row

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-05-16 |
| 范围 | `openjiuwen/agent_teams/tools/models.py`、`openjiuwen/agent_teams/tools/database/engine.py`、`openjiuwen/agent_teams/tools/database/member_dao.py`、`openjiuwen/agent_teams/tools/memory_database.py`、`openjiuwen/agent_teams/tools/team.py`、`openjiuwen/agent_teams/agent/spawn_manager.py`、`openjiuwen/agent_teams/agent/recovery_manager.py`、`openjiuwen/agent_teams/agent/team_agent.py`、`tests/unit_tests/agent_teams/test_human_agent_role_restore.py`、`tests/unit_tests/agent_teams/agent/test_human_agent_setup.py`、`tests/unit_tests/agent_teams/interaction/test_human_agent_inbox.py` |
| 测试基线 | `pytest tests/unit_tests/agent_teams/`：1017 通过 / 16 skipped；新增 7 条 e2e 用例锁死 cold-restart 路径 |
| Refs | `#751` |

## 背景

`TeamMember` 表过去没有 `role` 列；HITT 成员身份的唯一真相源是 leader 进程内存里
`TeamBackend._human_agent_names: set[str]`。这个集合的写入只有两条路径：

1. `TeamBackend.__init__` 时从 `spec.predefined_members` 里筛 `role_type == HUMAN_AGENT`
   重建（参见旧 `tools/team.py` 的 `__init__` 段）。
2. `spawn_human_agent` 运行时同步追加。

读取侧（`SpawnManager.build_context_from_db`）则承认这个 hack：

```python
# Role isn't stored on the member row; infer it from the live
# human-agent roster the leader holds. Without this the standard
# spawn path would label every UNSTARTED member as TEAMMATE and
# the human agent would inherit the wrong tool / rail set.
role = TeamRole.HUMAN_AGENT if team_backend.is_human_agent(teammate.member_name) else TeamRole.TEAMMATE
```

这种"内存即真相、DB 不带 role"的耦合在一种场景下必坏：

**leader 进程冷启 + 之前**动态** `spawn_human_agent` 创建过的 human-agent**。
- spec 持久化只带 `predefined_members`，动态 spawn 的 human-agent 不在那里；
- 冷启时新 `TeamBackend` 的 `_human_agent_names` 只能从 `predefined_members` 重建，
  动态那批 100% 丢失；
- 下一次 `build_context_from_db` 走 else 分支落到 `TEAMMATE`；
- 该成员复活后拿到的是 teammate 的工具集 / rails / prompt section —— 跑错的运行时
  画像，行为完全乱套。

predefined 路径下 spec 自身能恢复 role_type，故只暴露 bug 表面在动态 spawn 这一侧。

## 数据结构 / 状态机

| 之前 | 之后 |
|---|---|
| DB row 无 role | `team_member.role: TEXT NOT NULL DEFAULT 'teammate'`（`teammate` / `human_agent` / `leader`） |
| `_human_agent_names` = 真相源 | `_human_agent_names` = sync-lookup cache；DB row 才是真相源 |
| `_human_agent_names` 由 `__init__` 从 `predefined_members` 重建 | `_human_agent_names` 由 `refresh_human_agent_roster()` 从 DB 重 load；构造时为空 |
| `spawn_human_agent` 写 DB 后追加 cache | `spawn_member(role=HUMAN_AGENT)` 写 DB 后追加 cache；`spawn_human_agent` 复用 `spawn_member(role=HUMAN_AGENT)` |
| `build_context_from_db` 用 `is_human_agent` 猜 | `build_context_from_db` 直接读 `TeamRole(row.role)` |

冷启动 / teammate 进程的两条 async 入口在第一次需要 cache 之前都显式 `await
refresh_human_agent_roster()`：

- 冷恢复：`RecoveryManager.recover_team()` 开头；
- teammate 进程：`TeamAgent.from_spawn_payload()` 在 `configure` 之后。

## 决策

### 1. DB 真相源 + 同步 cache 双写

`team_member.role` 是 source of truth；`_human_agent_names` 保留作为同步内存 cache。
所有同步 caller（coordination handlers、rails、prompt sections、tools）继续走 cache，
无需变成 async。写入路径用"写 DB + 同步追加 cache"两步双写。

理由：

- DB 是跨进程持久状态，单一真相源避免「leader 内存 vs DB 状态分叉」类 bug。
- 同步 caller 涉及 7+ 处（含 prompt section、tool factory），全部改 async 是不必要
  的扩散——大多数 caller 在 sync 路径，能查 O(1) 就够了。
- 双写不是"两个真相源"——cache 只是 read-side 性能优化，由 `refresh_human_agent_roster`
  在任意时刻可重建。

### 2. `spawn_member` 加 `role: TeamRole = TeamRole.TEAMMATE` 显式参数

`TeamBackend.spawn_member` 公开签名加 keyword-only `role` 入参，默认 `TEAMMATE`。
`spawn_human_agent` 简化为「调 `spawn_member(role=HUMAN_AGENT)`」+ 失败 logging。

理由：

- role 是 member 行的一等属性，spawn 是写 member 行的唯一入口，必须在这里显式传。
- 默认 `TEAMMATE` 让 27 处现有测试 `db.member.create_member(...)` 不需要 churn——
  DAO 层默认值与 DB schema 默认值一致，单点变更不会漂移。
- 删了 `spawn_human_agent` 内部"再次追加 cache"的旧代码——`spawn_member` 已经写
  cache 了，重复 add 是冗余。

### 3. Migration helper：legacy DB 启动自动 ALTER TABLE

`tools/database/engine.py::_ensure_team_member_role_column` 在 `SQLModel.metadata.create_all`
之后跑一次 inspector + `ALTER TABLE team_member ADD COLUMN role TEXT NOT NULL DEFAULT
'teammate'`。SQLite / PostgreSQL / MySQL 都接受这种形式；默认值同时应用到 legacy 行
的后续读取，无须单独 UPDATE。

理由：

- 项目无 alembic / 正式 migration 框架，`create_all` 不 ALTER 已存在的表，否则升级
  上线立刻爆。
- 默认 `teammate` 对 legacy 行语义无伤——上线前的部署里要么没 human-agent，要么
  已经在 leader 内存里维护；冷启动后这些 legacy human-agent 的 role 误标只发生
  在重启后第一个 round，可接受（且与 bug 修复无关：legacy DB 必然不带动态 spawn 的
  human-agent，因为旧版本根本不持久化任何 role）。

### 4. 字符串字面量 `"teammate"` 而非 `TeamRole.TEAMMATE.value`

`tools/models.py`、`tools/database/engine.py`、`tools/database/member_dao.py`、
`tools/memory_database.py` 的 `role` 默认值都写字面量 `"teammate"`，配注释指明
"keep in sync with `TeamRole.TEAMMATE`"。

理由：`schema/team.py` 已经 import `tools/database` 和 `tools/memory_database`，
反向 import `TeamRole` 会闭合 circular import 触发 `ImportError: cannot import name
'TeamRole' from partially initialized module`。改 schema 层让它停止 import tools
范围太大不在本次 scope；先用字面量解耦，注释固化同步关系。

### 5. cold-restart roster refresh 放在两个 async 入口

- leader：`RecoveryManager.recover_team()` 开头 `await refresh_human_agent_roster()`
- teammate：`TeamAgent.from_spawn_payload()` 在 `configure(...)` 之后

`refresh_human_agent_roster` 自身 graceful：先 `await db.initialize()`（idempotent），
再 `getattr(db, "member", None)`，DAO 不可用就 no-op + debug log。让测试构造的
半成品 backend（不 initialize db）不会因 roster refresh 炸掉。

## 拒绝的方案

### A. 全部同步 caller 改成 async + 每次查 DB

让 `is_human_agent` / `human_agent_names()` 变成 `async`，删 cache，每个调用都查 DB。

代价 & 拒绝：

- 7+ 处同步 caller 横跨 coordination handlers / rails / prompt section build /
  tool factory；prompt section build 链是 sync 的（`MtimeSectionCache` 驱动），把
  它强行 async 化牵涉 `TeamPolicyRail` / `SystemPromptBuilder` 等多层。本次只为
  修一个 role 持久化 bug 不值得这种扩散。
- 每次发消息 / 每次渲染 prompt 都 round-trip DB 是无谓 overhead——roster 的写频率
  极低（仅 spawn / shutdown），自然的 read-cache + write-through 模式才匹配。

### B. 把 `role` 加在 SDK / config 层，不进 DB

让 `spawn_human_agent` 在 leader 进程的 metadata 里另存一份 roster JSON，cold-restart
时反序列化回 cache。

代价 & 拒绝：

- 又造一个真相源（DB row 仍没 role，新增 leader-side roster JSON），违反「数据结构
  优先」哲学：role 是 member 的一等属性，应该跟 member 一起持久化，不是单挂在某
  process 外面。
- teammate 进程拿不到这份 JSON（跨进程），还要单独同步——重新发明缓存协议。
- 走 DB row 没有任何额外的协议负担，DAO 一行 select 全场景搞定。

### C. `_human_agent_names` 改成「lazy first-call 从 DB load」而不是显式 entry point

构造时空集；第一次 `is_human_agent` / `human_agent_names()` 调用时检测未初始化，
触发 sync 异步 fallback load DB。

代价 & 拒绝：

- 同步方法触发 async 加载（`asyncio.run_coroutine_threadsafe` 或 `loop.run_until_complete`）
  跨 event-loop 边界，在 coroutines 之间是反模式且 fragile。
- 显式入口（recover_team / from_spawn_payload）反而清晰、可被测试单独覆盖。
- "lazy initialize" 在多线程 / 并发 first-call 还要加锁，复杂度倒挂。

### D. 撤掉 `predefined_members` 的 backend 构造参数

既然 `_human_agent_names` 不再从 `predefined_members` 重建，干脆把 `predefined_members`
也从 `TeamBackend.__init__` 拿走。

代价 & 拒绝：

- `predefined_members` 在 `build_team` 流程里仍是真正的消费者（驱动 spawn 路径），
  不是死参数。拿走会让 build_team 失去 spec→backend 的引用桥。
- 删冗余 cache 重建逻辑就够了，参数本身有别的合法用途，保留。

## 验证

新增单测 `tests/unit_tests/agent_teams/test_human_agent_role_restore.py`（7 条）：

| 用例 | 覆盖 |
|---|---|
| `test_spawn_member_persists_human_agent_role` | `spawn_member(role=HUMAN_AGENT)` 后 row.role == `"human_agent"` |
| `test_spawn_member_default_role_is_teammate` | 默认参数下 row.role == `"teammate"` |
| `test_dynamic_human_agent_survives_backend_restart` | **bug 直接回归**：dynamic spawn → drop backend → 新 backend `refresh_human_agent_roster()` → `is_human_agent` 命中 |
| `test_predefined_human_agent_survives_backend_restart` | predefined `build_team` 路径同样能 cold-restart |
| `test_build_context_reads_role_from_member_row` | `SpawnManager.build_context_from_db` 直接读 row.role，独立于 cache 状态 |
| `test_build_context_returns_teammate_for_ordinary_member` | 普通 teammate 走相同路径仍是 TEAMMATE |
| `test_legacy_team_member_table_gets_role_column` | 手工建一个无 role 列的 legacy SQLite，过 `initialize_engine` 后表带 role 列、legacy 行 backfill 为 `"teammate"` |

测试 fixture 改动：

- `tests/unit_tests/agent_teams/agent/test_human_agent_setup.py`：原 `test_human_agent_role_inferred_from_backend` 测的是被删的 hack 行为；改名为 `test_human_agent_role_restored_from_member_row`，调用改为显式 `spawn_member(role=TeamRole.HUMAN_AGENT)`，docstring 改写为"persisted on row, not inferred from cache"。
- `tests/unit_tests/agent_teams/interaction/test_human_agent_inbox.py`：fixture `db` 给 HUMAN 行写 `role="human_agent"`；fixture `team_backend` 加 `await backend.refresh_human_agent_roster()`，对齐生产 cold-restart 路径。

回归：

- `tests/unit_tests/agent_teams/`：1017 passed / 16 skipped
- `tests/unit_tests/`（除 agent_teams）：8302 passed / 2 pre-existing failed（`test_execute_javascript_code_success` Node ANSI 颜色码、`test_shell_timeout` flake，main 分支同样失败，与本变更无关）
- `ruff check`：所有改动文件全过
- `ruff format`：所有改动文件全过（`models.py` 触发 1 处整文件 reformat 是 pre-existing layout，未带）

## 已知遗留

1. **`schema/team.py` 反向 import `tools/database` + `tools/memory_database`**：本次为了
   不闭合 circular，role 默认值在四处用字面量 `"teammate"` 配同步注释维护。彻底解
   法是把 schema 层与 tools 层的依赖方向纠正过来（schema 不应反向 import tools），
   涉及 `TeamRuntimeContext.db_config` 的 typing 重构，scope 超过本 bug 范围。
2. **`spec.predefined_members` 仍在 `TeamBackend.__init__` 参数列表里**：本次没拿走
   （`build_team` 仍消费），但 backend 构造期不再读它推导 role。等下一次拆 `build_team`
   时一并审视是否还要这个参数。
3. **migration helper 仅覆盖 `team_member.role`**：写法是单点的"探测+ALTER"，未做成
   通用 schema migration 框架。下一次需要加列时如果列数增多，应考虑抽象一个 mini
   migration table 或上 alembic，而不是手堆 `_ensure_*_column` 函数。
4. **`status=MemberStatus` enum 直接传给 DAO 的 `status: str`**：现存的旧 typing
   不一致（`tools/team.py:268` 把 enum 实例直传 str 参数），SQLModel 当前 coerce
   是 silent；不属于本 bug，但同一模块的代码气味，下次顺手清理。
