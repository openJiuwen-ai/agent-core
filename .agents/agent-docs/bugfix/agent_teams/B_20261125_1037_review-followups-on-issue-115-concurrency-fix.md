---
id: B_20261125_1037_review-followups-on-issue-115-concurrency-fix
title: issue #115 第二至五轮检视遗留并发洞的修复
type: B
author: DaiYuxin511
author_email: 455472400@qq.com
modules:
- openjiuwen/agent_teams
files:
- openjiuwen/agent_teams/tools/database/task_dao.py
- openjiuwen/agent_teams/tools/database/member_dao.py
- openjiuwen/agent_teams/tools/database/message_dao.py
- openjiuwen/agent_teams/tools/database/engine.py
- openjiuwen/agent_teams/organization/db.py
- openjiuwen/agent_teams/tools/AGENTS.md
- tests/unit_tests/agent_teams/test_database.py
- tests/unit_tests/agent_teams/test_db_sessions_watchdog.py
- tests/unit_tests/agent_teams/organization/test_org_db.py
designs: []
design_note: 91ad1d944 把锁的边界从「全写锁」收窄到「DDL 锁 + CAS + 行锁」时漏掉的入口；本修复把同一套并发模型补齐到所有剩余读改写路径，不引入新设计
---

# issue #115 第二至五轮检视遗留并发洞的修复记录

## 现象与影响

91ad1d944（`fix(swarm): restore DDL mutex + CAS-guard non-CAS DAO writes under
PG/MySQL`）把 PostgreSQL / MySQL 上的进程级写锁去掉、改为「DDL 锁 + CAS + 行锁」
三件套。第二、三轮代码检视发现这套模型在多个入口上没收齐，并伴随契约文本失真与
一处死引用。

### 第二轮检视（三条，commit 53f1d5e38 已修）

1. **下游任务卡在 BLOCKED**：MySQL `READ COMMITTED` 下两个上游并发完成，下游 C
   （同时依赖 A 与 B）的 `BLOCKED` 状态无法被任一上游翻成 `PENDING`。
2. **`options` JSON 丢更新**：`update_member_builtin_model` /
   `update_member_served_model` / `promote_member_fallback_model` 三个 options 写入
   入口仍是普通 SELECT-then-overwrite，与已加 `FOR UPDATE` 的
   `update_member_worktree` 不一致。
3. **`_ensure_org_schema` 重复 ALTER 报错**：组织表迁移入口未取 `ddl_lock()`，PG/MySQL
   跳过写锁后两个首次访问可同时通过 `_ORG_SCHEMA_READY` 复查、同时执行
   `ALTER TABLE ... ADD COLUMN`，第二次命中「duplicate column」失败。

### 第三轮检视（两条，本次修复）

4. **PG/MySQL 仍跳过进程级写锁，未覆盖的跨语句读改写路径失去串行化**
   （审查意见 #1，P2）：第二轮的 `ddl_lock` 只闭合 DDL-vs-DDL，仍有两类读改写路径
   未覆盖：
   - `update_task` 的 revision 自增（SELECT task → Python 改 revision → commit）
   - `add_graph` / `mutate_dependency_graph` 的环校验（SELECT existing edges →
     Python 算 adjacency → INSERT new edges）
   未修改的契约文本（`tools/AGENTS.md` 的 `DbSessions` 段落）仍以「`write()` 持有
   一把进程级 `asyncio.Lock`」为前提，与 91ad1d944 后 PG/MySQL 跳过写锁的实际行为
   不符。

   系统排查发现**另外 4 个同构入口**也未被覆盖（审查意见未点名，但不修会来下一轮）：
   - `reset_task`（SELECT task → Python 改 status/assignee/summary/note → commit）：
     与并发 `cancel_task` CAS 之间会丢更新，已取消的任务被复活。
   - `approve_plan_task`（SELECT task → Python 改 status → commit）：同构。
   - `_mark_read_in_session` 广播分支（SELECT read_status → Python 改 read_at →
     commit）：水位回退，已读的广播被重新标记未读。
   - `mark_messages_read` 批量路径共用 `_mark_read_in_session`，同构。

5. **`test_db_sessions_watchdog.py:18` 死引用**（审查意见 #2，P3）：三个用例改用
   实例属性（`sessions._watchdog_seconds` / `sessions._write_lock`）后，
   `from ... import engine as engine_module` 成为死引用，触发 F401 lint。

## 根因与修复决策

### 第二轮（已在 53f1d5e38 修复，此处仅记录要点）

- **#1**：`_refresh_status_in_session` 候选任务 SELECT 加 `with_for_update()`。
- **#2**：3 个 member options 写入口 SELECT 加 `with_for_update()`。
- **#3**：`_ensure_org_schema` 的 `_ddl_once` 用 `sessions.ddl_lock()` 外层包裹
  `sessions.write()`，docstring 同步更新。

### 第三轮（本次修复）

#### #4 未覆盖的读改写路径

修复策略：所有「SELECT 同表行 → Python 改属性 → commit」的入口统一加 `with_for_update()`，
与第二轮 `update_member_worktree` / `_refresh_status_in_session` 同模式。SQLite 方言
剥离 `FOR UPDATE`（进程级写锁兜底），PG/MySQL 靠行锁串行化读改写。

| 入口 | 文件:行 | 修法 |
|---|---|---|
| `update_task`（revision 自增） | task_dao.py:1419 | task SELECT 加 `.with_for_update()` |
| `_check_cycle_and_compute_new_edges`（环校验） | task_dao.py:413 | existing edges SELECT 加 `.with_for_update()` |
| `reset_task` | task_dao.py:1052 | task SELECT 加 `.with_for_update()` |
| `approve_plan_task` | task_dao.py:1291 | task SELECT 加 `.with_for_update()` |
| `_mark_read_in_session` 广播分支 | message_dao.py:635 | read_status SELECT 加 `.with_for_update()` |

`_check_cycle_and_compute_new_edges` 的 `FOR UPDATE` 会锁住该 team 的所有
`task_dependency` 行——`add_graph` 不是高频操作，锁全队边集的代价可接受；两个并发
`add_graph` 串行化后，第二个读到的边集已包含第一个提交的新边，环校验在 union 集上
运行，不会漏检。

不改 CAS 的原因：`update_task` / `reset_task` / `approve_plan_task` 的 Python 改写
涉及多列（title/content/revision/change_notes/assignee/summary/note/...），CAS UPDATE
需要把所有列写进 `values()`，且 `reset_task` 的 `observed_revision = task.revision`
需要读当前 revision——SQL 列引用列在 SQLAlchemy `values()` 里不直观。`FOR UPDATE`
与第二轮已建立的修复模式一致，统一性优先。CAS 风格保留给纯状态翻转
（`update_task_status` / `delete_task` / `cancel_task` / `delete_team`）与无前置 SELECT
的原子写（`start_task` / `claim_task` / `reassign_task` / `observe_revision` 等）。

#### 契约文本同步

`tools/AGENTS.md` 的 `DbSessions` 段落（原 47-58 行）改写：
- 「`write()` 持有一把进程级 `asyncio.Lock`」→「SQLite 上 `write()` 持有进程级
  `asyncio.Lock`；PG/MySQL 跳过进程级写锁，原生写并发才是写仲裁者」。
- 新增「DDL 锁 + CAS + 行锁」三件套的完整说明，列出每类入口的代表方法。
- 新增「新增读改写入口前先归类属于哪一类（DDL / CAS / FOR UPDATE）」的防错指引。
- 删除「DROP 路径暂未收编，见 F_83 已知遗留」（91ad1d944 已收编 DROP 路径）。
- 组织表 DDL 改为「在 `write()` + `ddl_lock()` 内执行」。

#### #5 死引用

`test_db_sessions_watchdog.py:18` 删除 `from openjiuwen.agent_teams.tools.database
import engine as engine_module`。三个用例已改用 `sessions._watchdog_seconds` /
`sessions._write_lock` 实例属性，`engine_module` 不再被引用。

## 考虑过但未采用

- **退回 PG/MySQL 进程级写锁**：会重新引入 issue #115 的 30s 看门狗堆积问题
  （91ad1d944 的修复动机）。审查意见 #1 也说「改动方向正确」，方向不退。
- **`reset_task` / `approve_plan_task` 改 CAS**：这两个是状态翻转，CAS 可行；但
  `reset_task` 还要清空 `summary` / `note` / `resume_pending` / `observed_revision`
  等多列，CAS UPDATE 需要把所有列写进 `values()`，且 `observed_revision = revision`
  需要读当前 revision。`FOR UPDATE` 与第二轮已建立的修复模式一致，统一性优先。
- **`_check_cycle_and_compute_new_edges` 用 team 级 advisory lock**：PG/MySQL 有
  `pg_advisory_xact_lock` / `GET_LOCK`，但 SQLite 没有，需要分后端分支。`FOR UPDATE`
  锁全队边集在三后端统一生效，且 `add_graph` 低频，代价可接受。
- **`mark_message_read` 调用方已串行**：`MessageHandler` 按成员逐个串行 flush，单成员
  并发概率低；但严格按「PG/MySQL 跳过写锁后所有读改写入口必须显式串行化」的契约，
  `FOR UPDATE` 是正确的，不依赖调用方的串行假设。

## 回归证据与适用范围

候选：`fix/issue-115-enterprise`（基于 53f1d5e38）。

```bash
# 死引用删除 + org schema（覆盖 #3 #5）
.venv/bin/python -m pytest tests/unit_tests/agent_teams/test_db_sessions_watchdog.py \
    tests/unit_tests/agent_teams/organization/test_org_db.py -x -q
# → 19 passed

# database + task_manager + message_manager（覆盖 #1 #2 #4）
.venv/bin/python -m pytest tests/unit_tests/agent_teams/test_database.py \
    tests/unit_tests/agent_teams/test_task_manager.py \
    tests/unit_tests/agent_teams/test_message_manager.py -x -q
# → 255 passed

# 广域回归
.venv/bin/python -m pytest tests/unit_tests/agent_teams/ -x -q \
    -k "member or task or database or message or team"
# → 4601 passed, 36 skipped, 2 xfailed
```

`FOR UPDATE` 在 SQLite 上被剥离，所以 SQLite 单测无法直接验证 PG/MySQL 行锁行为；
真实并发场景需在 PG/MySQL 后端按检视意见描述的屏障构造复现。

## 防错模式与资料同步

模式：91ad1d944 引入「DDL 锁 + CAS + 行锁」三件套后，凡是对同一行/同一 JSON 的
读改写入口都必须采用一致的并发控制——DDL 走 `ddl_lock()`，状态翻转走 CAS
`WHERE status=...`，整 JSON / 整行 read-modify-write 走 `SELECT ... FOR UPDATE`。
新增读改写入口前先归类属于哪一类，套用对应模式，不要默认走普通
SELECT-then-UPDATE。已把这条写进 `tools/AGENTS.md` 的 `DbSessions` 段落。

### 第四轮检视追加：engine.py 四处 DDL helper docstring

第四轮检视指出 engine.py 四处 DDL helper 的 docstring 仍声称「runs inside
`DbSessions.write()` so ... serialises with every DAO write」——这条理由在
91ad1d944 之后对 PG/MySQL 已不成立（`write()` 跳过进程级写锁）。本次修复把四处
docstring（`create_cur_session_tables` / `drop_cur_session_tables` /
`cleanup_all_runtime_state` / `drop_session_tables_by_id`）统一改为「On SQLite
the per-write lock serialises ... with every DAO write; on PostgreSQL / MySQL
`write()` skips the process-level lock, so DDL-vs-DAO-write is guarded by the
DB's own table-level DDL lock ... `ddl_lock()` serialises ... with every other
DDL helper on all backends」。与 `organization/db.py` 的 `_ensure_org_schema`
docstring（53f1d5e38 已更新）措辞一致。

### 第五轮检视：环校验锁修正、跨进程 ALTER 幂等、测试覆盖

第五轮检视发现两个真实 bug 与一个测试覆盖缺失：

1. **P2 #1 — `_check_cycle_and_compute_new_edges` 的 dep FOR UPDATE 在空边集上不生效**：
   `SELECT ... FOR UPDATE` 只锁返回的行。若 team 没有任何现有边（空边集），SELECT 返回
   零行、零锁。两个并发 `mutate_dependency_graph` 都不取锁，各自通过环校验，合在一起
   可能成环。即使有现有边，两个加不相交端点新边的调用也可能各自通过但合并成环（经
   现有边桥接）。

   **修法**：移除 `_check_cycle_and_compute_new_edges` 的 dep FOR UPDATE，改为在
   `mutate_dependency_graph` 开头锁 **team 行**（`SELECT Team WHERE team_name=? FOR UPDATE`）。
   team 行锁串行化同 team 的所有图变更，不依赖边集大小。team 行是单行短锁，不与
   `_terminate_task_in_session`（不锁 team_info）冲突。

2. **P3 #4 — dep FOR UPDATE 与 `_terminate_task_in_session` 的锁序相反导致死锁**：
   `mutate_dependency_graph`（dep→task）与 `_terminate_task_in_session`（task→dep）锁序
   相反。A 持全部 dep 锁等 task T，B 持 task T 锁等 dep 锁 → PG/MySQL 检测死锁并 abort
   一笔事务。移除 dep FOR UPDATE、改用 team 行锁后，`mutate_dependency_graph` 只锁
   team 行 + task 行（refresh），不锁 dep 行，锁序与 `_terminate_task_in_session` 一致。

3. **P2 #3 — `ddl_lock` 是 `asyncio.Lock`（进程级），多进程下 org 迁移仍可能报
   duplicate column**：`_ddl_once` 的 `_ORG_SCHEMA_READY` 复查 + `ddl_lock` 都只在进程内
   有效。两个进程同时首次访问同一 PG/MySQL 库时，各自通过复查、各自执行
   `ALTER TABLE ADD COLUMN`，第二个进程命中「duplicate column」失败。

   **修法**：新增 `_add_column_idempotent` 辅助函数，包裹 `ALTER TABLE ADD COLUMN` 并
   捕获 `OperationalError`，对「duplicate column」/「already exists」类错误视为成功
   （列已存在，正是所需状态）。三处 ALTER 调用（`_ensure_org_task_columns` 循环 /
   `_ensure_org_summary_execution_columns` / `ensure_org_static_tables` 的
   `unclaimed_task_policy_json`）全部改用此函数。

4. **P3 #5 — client/server 写锁分支与 ddl_lock 无测试覆盖**：新增 3 个测试：
   `test_sqlite_write_holds_process_lock`（SQLite 上 `write()` 持进程锁）、
   `test_client_server_write_skips_process_lock`（PG/MySQL 上 `write()` 跳过进程锁）、
   `test_ddl_lock_serialises_concurrent_acquirers`（`ddl_lock()` 串行化并发获取者）。

### 第六轮检视：`_add_column_idempotent` 异常类错误 + 事务状态污染

第六轮检视发现第五轮新增的 `_add_column_idempotent` 有两个真实 bug：

1. **异常类错误**：`except OperationalError` 只捕获 SQLite 的重复列错误。PG 的重复列
   （SQLSTATE 42701）在 SQLAlchemy 层是 `ProgrammingError`（与 `OperationalError` 平行，
   都继承自 `DBAPIError`），旧代码根本捕获不到，异常照旧抛出——跨进程 duplicate column
   在 PG/MySQL 上仍然失败。

2. **事务状态污染**：即使改对异常类，PG 在语句失败后会把整个事务置为 aborted 状态，
   `db.py:141` 的 `await session.commit()` 随后抛 `InFailedSQLTransaction`（非 transient，
   `retry_on_transient` 不重试），`_ensure_org_schema` 依旧失败。

   修法：用 `with sync_conn.begin_nested():`（SAVEPOINT）包住 ALTER，捕获
   `DBAPIError`（覆盖 `OperationalError` + `ProgrammingError`）。失败时回滚到 savepoint，
   外层事务保持 clean、可提交。**MySQL 路径单独处理**：MySQL 的 DDL 隐式提交会销毁
   savepoint，`begin_nested` 退出时的 `ROLLBACK TO SAVEPOINT` 会抛二次 `DBAPIError`
   （ERROR 1305 "SAVEPOINT does not exist"），掩盖原 "Duplicate column name" 错误——
   因此 MySQL 跳过 `begin_nested`、直接执行 ALTER，吞掉 duplicate-column 错误后用
   `inspect.get_columns` 兜底验证列确实存在（隐式提交使 post-ALTER 状态成为 ground truth）；
   若列仍缺失则 re-raise（不一致状态）。

   补 2 个迁移幂等单测：
   - `test_ensure_org_static_tables_is_idempotent_when_columns_already_exist`：先完整迁移，
     再在显式事务里调 `ensure_org_static_tables`，断言 commit 成功。
   - `test_add_column_idempotent_swallows_duplicate_column`：直接测 helper——预加列后调
     `_add_column_idempotent`，断言不抛异常且事务可继续写入。

## 遗留与后续

- 修复的并发正确性依赖 PG/MySQL 的 `READ COMMITTED` 行为：A 提交后 B 才能看
  到 A 的写入。`engine.py` 的 `create_async_engine` 已为 PG/MySQL 显式设
  `isolation_level="READ COMMITTED"`——MySQL 默认 `REPEATABLE READ` 会在事务首个
  一致性读建立 read view，使 `_refresh_status_in_session` 的 deps count 查询看不到
  并发上游已提交的 dep 解析（FOR UPDATE 行锁拿到后 count 仍用旧 view → 下游卡
  BLOCKED）。显式设 RC 后每条语句取新快照，FOR UPDATE 串行化才真正生效。
- 未在真实 PostgreSQL / MySQL 后端按屏障构造跑过端到端复现。需 swarm 侧用
  PG/MySQL 后端复测，确认下游不再卡 BLOCKED、options 不再丢更新、组织表迁移不再
  报 duplicate column、`update_task` revision 不再丢增、`add_graph` 不再漏检环、
  `reset_task` / `approve_plan_task` 不再被并发 `cancel_task` 复活、广播水位不再
  回退、`mutate_dependency_graph` 与 `complete_task` 不再死锁、跨进程 org 迁移
  不再报 duplicate column。
- 排查报告确认的「无风险」入口（纯 INSERT / 纯 CAS / 已加 FOR UPDATE / 只读）未
  在本记录逐一列出，详见排查报告。
- **P2 #2**（PG/MySQL 跳过进程写锁的「未闭合」指控）：91ad1d944 的核心改动就是
  跳过进程写锁以解决 issue #115 的吞吐问题。经五轮系统排查，所有 read-modify-write
  入口已覆盖（FOR UPDATE / CAS / ddl_lock / team 行锁）。`ddl_lock` 的进程级局限
  通过 `_add_column_idempotent` 跨进程兜底：SQLite/PG 走 SAVEPOINT + DBAPIError，
  MySQL 跳过 savepoint（DDL 隐式提交销毁之）改为 ALTER + `inspect` 兜底验证。
  方向不退——退回进程写锁会重新引入 issue #115 的 30s 看门狗堆积。

### 第七轮检视：加边与上游并发完成竞态（`_apply_new_edges` 的 `resolved` 决策用过期读）

第七轮检视点名 `_apply_new_edges` 的 `resolved` 决策：

> 当给 B 添加对 A 的依赖时，这里可能先读到 A 仍在执行。若 A 随后完成并提交，
> `complete_task(A)` 扫描依赖时还看不到这条边。加边事务接着按之前读到的状态写入
> `resolved=False`，使 B 进入 BLOCKED。两次操作都返回成功，但 A 已完成，B 无法
> 通过这次完成事件解锁。

**根因**：第五轮把环校验的 dep `FOR UPDATE` 换成 team_info 行锁后，闭合了「两个并发
加边各自通过环校验、合并成环」的竞态，但 `_apply_new_edges` 决定 `resolved` 用的
仍是 `_load_endpoints_and_validate` 的**普通 SELECT 快照**（`endpoint_tasks[dep_id].status`）。
team_info 行锁不与 `_terminate_task_in_session`（不锁 team_info）串行化，故加边与上游
完成可交错：加边读到 A 仍在执行 → A 完成提交（其 dep 扫描看不到新边，不解锁 B）→
加边按过期读写入 `resolved=False` → B 永久 BLOCKED（本应解锁它的完成事件已发完）。

**修法**：`_apply_new_edges` 在插边前对每条新边的上游端点取
`select(task_id, status).where(...).with_for_update()` 的**标量行锁**，用现读的
`status` 决定 `resolved`。两点关键：

1. **标量列投影，不是整行 FOR UPDATE**：上游行已被 `_load_endpoints_and_validate`
   载入本 session 的 identity map，整行 `select(Model).with_for_update()` 会回退
   identity map 里缓存对象的**过期属性**而非现读 DB（session 内 FOR UPDATE 不刷新
   已加载对象的属性）；标量 `select(task_id, status)` 绕过 identity map，读 live
   committed 值（PG/MySQL `READ COMMITTED` 下每条语句取新快照，FOR UPDATE 读看到
   并发完成提交）。这条 identity-map 行为经实验确认（同 session 内整行 FOR UPDATE
   回退缓存属性，`populate_existing=True` 在 aiosqlite 路径下也未刷新；标量列投影
   稳定读到 in-transaction / committed 的现值）。
2. **锁序与 `_terminate_task_in_session` 一致**：本路径锁上游 task（FOR UPDATE）→
   插 dep 行 → 刷新下游 task（FOR UPDATE），即 task→dep→task；terminator 锁 task
   （CAS）→ dep 行（UPDATE）→ 下游 task（refresh FOR UPDATE），同序。不重引第五轮
   移除的 dep→task 反向死锁。team_info 行锁不被 terminator 触碰，不增冲突。

**回归测试** `test_add_edge_to_upstream_completed_after_validation_is_born_resolved`
（`tests/unit_tests/agent_teams/test_database.py`）：在单事务内复刻交错——验证读 A=
in_progress → 在同事务内把 A 翻成 completed（`synchronize_session=False` 使验证时
ORM 对象保持过期，忠实模拟「另一 session 已提交、本 session 的 identity map 尚未
得知」）→ 调 `_apply_new_edges`。修前断言失败（`resolved=False`、B 卡 BLOCKED），
修后通过（`resolved=True`、B 保持 PENDING）。SQLite 上 `FOR UPDATE` 被剥离，故测
的是「插边用现读上游状态、不用过期 `endpoint_tasks` 快照」这一不变量，而非 PG/MySQL
行锁行为本身——后者仍需 swarm 侧 PG/MySQL 端到端复测。

**契约同步**：`tools/AGENTS.md` 第 (4) 条追加 `_apply_new_edges` 上游标量行锁的
说明（标量投影绕 identity map 的理由 + 锁序与 terminator 一致、不重引第五轮死锁）。

### 第八轮检视：`create_all` / `CREATE INDEX` 跨进程并发未幂等

第八轮检视指出 `ensure_org_static_tables` 只对 `ADD COLUMN`（`_add_column_idempotent`）
做了跨进程幂等，同函数的 `SQLModel.metadata.create_all`（建 org 静态表）与
`Index.create`（建 `org_task` 两个索引）仍走 `checkfirst=True` 的 inspect-then-create
模式——与第五轮修 `ADD COLUMN` 前完全同构的 TOCTOU 竞态。

**根因**：`checkfirst=True` 对每个表 / 索引先 `inspect`（不存在）再 `CREATE`，两步
之间有窗口。PG/MySQL 跳过进程级写锁后，两个进程的 `_ddl_once` 可同时通过
`_ORG_SCHEMA_READY` 复查（进程级 `WeakSet`，跨进程不共享）、同时进入
`ensure_org_static_tables`。进程 A 先提交创建了全部表，进程 B 的 `checkfirst` 在
A 提交前已读到「不存在」、随后 `CREATE TABLE` 撞 PG 的表级 DDL 锁等待 A，A 提交后
B 的 `CREATE` 报 `already exists` → PG 事务进入 aborted 状态 → 后续
`_add_column_idempotent` 全部 `InFailedSQLTransaction`。

**修法**：新增 `_run_ddl_idempotent(sync_conn, ddl, verify)` 辅助，把
`_add_column_idempotent` 的 SAVEPOINT + DBAPIError catch 模式泛化到任意 DDL callable。
SQLite / PG 走 `begin_nested` 包住 DDL，吞 `already exists` / `duplicate` 错误后
savepoint 回滚、外层事务恢复 clean；MySQL 跳过 savepoint（DDL 隐式提交销毁之），
改为裸执行 + 吞错误后调 `verify` callable 复查 DDL 目标确实存在（隐式提交使
post-DDL 状态成为 ground truth）。`ensure_org_static_tables` 的 `create_all` 与
两个 `index.create` 全部改用此辅助。

**为什么 `create_all` 的 savepoint 回滚安全**：`create_all` 在并发进程里是原子的
（要么全部建、要么全部不建），不会出现「表 1、2 建了、表 3 没建」的半态。savepoint
回滚只会发生在「某个表已存在（并发进程建的）」时，此时并发进程已提交、全部表都
可见，回滚本进程建的那几张等价于回退到「全部表已由对方建好」的状态，后续的
`_ensure_org_task_columns` 等 inspect-only 迁移自然短路。

**回归测试**（`tests/unit_tests/agent_teams/organization/test_org_db.py`）：
- `test_run_ddl_idempotent_swallows_already_exists_on_savepoint_path`：预建表后
  调 `_run_ddl_idempotent`（DDL 直接 `CREATE TABLE` 同名，不走 checkfirst），
  断言不抛异常且事务可继续写入（INSERT 成功）。
- `test_run_ddl_idempotent_mysql_skips_savepoint_and_rechecks`：fake MySQL 连接
  驱动 MySQL 分支——断言不调 `begin_nested`、DDL 执行一次、`verify` 被调。
- `test_run_ddl_idempotent_mysql_reraises_when_verify_fails`：`verify` 返回 False
  （不一致态）时 helper 必须 re-raise。

**契约同步**：`tools/AGENTS.md` 第 (1) 条 DDL 锁清单追加 `_run_ddl_idempotent` 对
`create_all` / `CREATE INDEX` 的覆盖说明。
