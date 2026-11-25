---
id: B_20261125_1037_review-followups-on-issue-115-concurrency-fix
title: issue #115 第二、三轮检视遗留并发洞的修复
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
- tests/unit_tests/agent_teams/test_db_sessions_watchdog.py
designs: []
design_note: 91ad1d944 把锁的边界从「全写锁」收窄到「DDL 锁 + CAS + 行锁」时漏掉的入口；本修复把同一套并发模型补齐到所有剩余读改写路径，不引入新设计
---

# issue #115 第二、三轮检视遗留并发洞的修复记录

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

## 遗留与后续

- 三条修复的并发正确性依赖 PG/MySQL 的 `READ COMMITTED` 行为：A 提交后 B 才能看
  到 A 的写入。若后端配置成 `READ UNCOMMITTED`（脏读）或更弱的隔离级别，行为不
  保证——但项目 DB 配置默认 `READ COMMITTED`，不在本次范围。
- 未在真实 PostgreSQL / MySQL 后端按屏障构造跑过端到端复现。需 swarm 侧用
  PG/MySQL 后端复测，确认下游不再卡 BLOCKED、options 不再丢更新、组织表迁移不再
  报 duplicate column、`update_task` revision 不再丢增、`add_graph` 不再漏检环、
  `reset_task` / `approve_plan_task` 不再被并发 `cancel_task` 复活、广播水位不再
  回退。
- 排查报告确认的「无风险」入口（纯 INSERT / 纯 CAS / 已加 FOR UPDATE / 只读）未
  在本记录逐一列出，详见排查报告。
