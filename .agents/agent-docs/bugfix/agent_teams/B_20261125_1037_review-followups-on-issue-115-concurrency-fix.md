---
id: B_20261125_1037_review-followups-on-issue-115-concurrency-fix
title: issue #115 第二轮检视三条遗留并发洞的修复
type: B
author: DaiYuxin511
author_email: 455472400@qq.com
modules:
- openjiuwen/agent_teams
files:
- openjiuwen/agent_teams/tools/database/task_dao.py
- openjiuwen/agent_teams/tools/database/member_dao.py
- openjiuwen/agent_teams/organization/db.py
designs: []
design_note: 91ad1d944 把锁的边界从「全写锁」收窄到「DDL 锁 + CAS + 行锁」时漏掉的三个入口；本修复把同一套并发模型补齐到剩余的读改写路径，不引入新设计
---

# issue #115 第二轮检视三条遗留并发洞的修复记录

## 现象与影响

91ad1d944（`fix(swarm): restore DDL mutex + CAS-guard non-CAS DAO writes under
PG/MySQL`）把 PostgreSQL / MySQL 上的进程级写锁去掉、改为「DDL 锁 + CAS + 行锁」
三件套。第二轮代码检视发现这套模型在三个入口上没收齐：

1. **下游任务卡在 BLOCKED**：MySQL `READ COMMITTED` 下两个上游并发完成，下游 C
   （同时依赖 A 与 B）的 `BLOCKED` 状态无法被任一上游翻成 `PENDING`。
2. **`options` JSON 丢更新**：`update_member_builtin_model` /
   `update_member_served_model` / `promote_member_fallback_model` 三个 options 写入
   入口仍是普通 SELECT-then-overwrite，与已加 `FOR UPDATE` 的
   `update_member_worktree` 不一致。两个并发写入各读旧 JSON、各自回写整列，会
   互相覆盖对方刚写入的字段。
3. **`_ensure_org_schema` 重复 ALTER 报错**：组织表迁移入口仍只靠 `sessions.write()`
   串行，PG/MySQL 跳过写锁后两个首次访问可同时通过 `_ORG_SCHEMA_READY` 复查、
   同时执行 `ALTER TABLE ... ADD COLUMN`，第二次命中「duplicate column」失败。

三条共同特征：**91ad1d944 把锁的边界从「全写锁」收窄到「DDL 锁 + CAS + 行锁」
时漏掉了三个应该一并改的入口**。本修复把同一套并发模型补齐到剩余路径，不退回
到全写锁，也不引入新机制。

## 根因与修复决策

### 1. `_refresh_status_in_session` 下游 BLOCKED 卡死

`_terminate_task_in_session`（task_dao.py:162-250）流程：CAS 翻终态 →
`UPDATE dependencies SET resolved=True WHERE depends_on_task_id=<本任务>` →
拉 downstream_ids → 调 `_refresh_status_in_session`（task_dao.py:112-159）。

`_refresh_status_in_session` 是「SELECT COUNT(未解决依赖) → 判断要不要 UPDATE」
的读改写模式。READ COMMITTED 下：

- 事务 A 把 A→C 标 `resolved=True`（未提交），自己读 C 的未解决依赖 count=1
  （只剩 B→C），判断 C 仍 BLOCKED，不改。
- 事务 B 同理，自己读 count=1（只剩 A→C），判断 C 仍 BLOCKED，不改。
- 两者提交后 C 实际两条依赖都已 resolved，但行上 status 仍是 BLOCKED。

`_terminate_task_in_session` 的终态 CAS（task_dao.py:187-194）只防终态翻转竞争，
**不覆盖下游刷新的读改写**。

修复：给 `_refresh_status_in_session` 候选任务 SELECT 加 `with_for_update()`
（task_dao.py:139-143）。事务 A 持 C 行锁 → 读 deps（看到 1 未解决）→ 不改 →
释放锁 → 提交；事务 B 此时才拿到 C 锁，**此时 A 已提交，B 在 READ COMMITTED 下
能看到 A 的 `resolved=True`**，读 deps count=0 → 翻 C 为 PENDING。与
`update_member_worktree` 的 `FOR UPDATE` 修复同模式。

`_refresh_status_in_session` 的三个调用点（task_dao.py:249 / 1589 / 1824）都在
`sessions.write()` 块内，FOR UPDATE 安全：写 session 已持连接，FOR UPDATE 在
SQLite 被剥离、在 PG/MySQL 取行锁，行与同事务里后续 UPDATE 是同一行，不存在
锁升级死锁。

### 2. options JSON 丢更新

`member_dao.py` 里 4 个 options 写入入口，只有 `update_member_worktree` 加了
`with_for_update()`（91ad1d944 引入）。其余 3 个仍是普通 SELECT：

| 方法 | 行 | options 改写 |
|---|---|---|
| `update_member_worktree` | 755-795 | `set_member_worktree_options`（已加锁） |
| `update_member_builtin_model` | 797-817 | `set_member_builtin_model` |
| `update_member_served_model` | 819-854 | `append_member_served_model` |
| `promote_member_fallback_model` | 856-878 | `promote_member_fallback_model` |

后 3 个都是「读旧 options JSON → Python 里改 → 写回整列」的 read-modify-write。
READ COMMITTED 下两个并发调用各自读到旧 JSON、各自构造完整 options、各自的
UPDATE 把对方刚写的字段抹掉。例如 `update_member_builtin_model` 与
`update_member_worktree` 并发时，前者写回时把 `member.options` 整列覆盖，会顺手
覆盖掉后者刚写入的 `worktree` 字段。

修复：3 个方法的 SELECT 都补上 `.with_for_update()`，让同一成员的 options 串行化。
`update_member_worktree` 的 docstring（member_dao.py:764-773）已经讲清「SQLite
dialect 自动剥离 FOR UPDATE、靠进程级写锁兜底；PG/MySQL 靠行锁」——这套理由对
另外 3 个方法同样适用，照搬即可。

不补 `update_member_status` / `update_member_execution_status`（行 434 / 556）：
它们是 CAS 风格的状态翻转（已有 `WHERE status=...` guard），不是整 JSON 重写。

### 3. `_ensure_org_schema` 重复 ALTER

`organization/db.py:107-115` 的 `_ddl_once`：

```python
async def _ddl_once() -> None:
    async with sessions.write() as session:          # PG/MySQL 上不互斥
        if sessions in _ORG_SCHEMA_READY:            # 弱集合，无锁
            return
        conn = await session.connection()
        await conn.run_sync(ensure_org_static_tables)
        await session.commit()
    _ORG_SCHEMA_READY.add(sessions)
```

注释「Re-check under the lock」的「the lock」在 91ad1d944 之后名存实亡——PG/MySQL
跳过 `_write_lock`，所以 `sessions.write()` 不再是互斥区。两个首次访问可以同时
进 `_ddl_once`、同时通过 re-check、同时走到 `ensure_org_static_tables`。
`ensure_org_task_columns`（db.py:51-68）里虽然有 `if name not in columns` 检查
（行 59），但 check 与 ALTER 是分两条语句，两条之间没有锁保护。两个事务都看到列
缺失 → 都执行 `ALTER TABLE org_task ADD COLUMN ...` → 第二条命中「Duplicate
column name」失败。`retry_on_transient` 只重试 `locked`/`busy`/池超时类异常，
**不重试 schema 冲突**，会直接抛出，组织表初始化失败。

修复：与 `create_cur_session_tables` / `drop_cur_session_tables` /
`cleanup_all_runtime_state` / `drop_session_tables_by_id` 完全同模式，把
`sessions.write()` 包进 `sessions.ddl_lock()`（db.py:113-123）。事务 A 抢到
ddl_lock → 跑 DDL → 加进 WeakSet → 释放锁、提交；事务 B 这时才进 → re-check
命中 WeakSet → 直接 return。

同步把 docstring 里「The DDL runs inside `sessions.write()` so it serialises
with every DAO write of the same TeamDatabase」（db.py:92-100）改掉，这条理由在
91ad1d944 之后对 PG/MySQL 已经不成立；改为「runs inside `sessions.write()`
**and** `sessions.ddl_lock()` so it serialises with the four DDL / cleanup
helpers」，与 engine.py 里那四个 helper 的 docstring 措辞对齐。

## 考虑过但未采用

- **意见 #1**：给 `complete_task` / `cancel_task` 提交后追加一次
  `_verify_and_fix_blocked_tasks` 兜底。已有这套基础设施（task_dao.py:1809-1826），
  但兜底扫描是「事后纠偏」，不能替代并发互斥本身；`FOR UPDATE` 是把根因堵住。
  保留兜底作为后续可选加固，不在本次修复范围。
- **意见 #1**：把 `_refresh_status_in_session` 改成单条 `UPDATE ... WHERE
  unresolved_count = 0`（SQL 端 CAS，不取行锁）。改造面更大，且与
  `update_member_worktree` 的 `FOR UPDATE` 模式不一致；本次按检视意见
  「让同一 JSON 的所有读改写入口采用一致的并发控制」的精神，统一走
  `FOR UPDATE`。
- **意见 #3**：把 `_ORG_SCHEMA_READY` 的复查改成「持 ddl_lock 后复查 + 加 schema
  exists 检查」。当前实现已足够：持锁后复查 WeakSet，A 跑完 DDL 后 B 直接 return，
  不需要再 SQL 端查一次表存在性。`ensure_org_static_tables` 内部的
  `if name not in columns` 是单事务内的 check-then-alter，持锁后只有一个事务
  能进到这里，不存在 race。

## 回归证据与适用范围

候选：`fix/issue-115-enterprise`（基于 91ad1d944）。

- 现有 SQLite 测试 `tests/unit_tests/agent_teams/organization/test_org_db.py`：
  - `test_org_static_ddl_runs_under_team_database_write_lock`：SQLite 上 `write()`
    仍持进程级写锁，加 `ddl_lock()` 不影响 `write_locked` 在 DDL 期间为 True，断言
    仍通过。
  - `test_org_static_ddl_runs_for_each_new_database` /
    `test_find_organization_ids_runs_static_ddl_once_per_database`：WeakSet 复查
    行为不变（ddl_lock 内复查与原 write_lock 内复查逻辑一致），断言仍通过。
- 未新增显式并发测试：91ad1d944 处理 CAS / FOR UPDATE 改动时也未新增并发用例，
  本次沿用同一模式（review 主导 + 现有测试不回归）。`FOR UPDATE` 在 SQLite 上
  被剥离，所以 SQLite 单测无法直接验证 PG/MySQL 行锁行为；真实并发场景需在
  PG/MySQL 后端按检视意见描述的屏障构造复现。

未验证：未在真实 PostgreSQL / MySQL 后端按屏障构造跑过端到端复现。需要 swarm
侧用 PG/MySQL 后端在同样场景下复测，确认下游不再卡 BLOCKED、options 不再丢更新、
组织表迁移不再报 duplicate column。

## 防错模式与资料同步

模式：91ad1d944 引入「DDL 锁 + CAS + 行锁」三件套后，凡是对同一行/同一 JSON 的
读改写入口都必须采用一致的并发控制——DDL 走 `ddl_lock()`，状态翻转走 CAS
`WHERE status=...`，整 JSON 重写走 `SELECT ... FOR UPDATE`。新增读改写入口前
先归类属于哪一类，套用对应模式，不要默认走普通 SELECT-then-UPDATE。

## 遗留与后续

- 三条修复的并发正确性依赖 PG/MySQL 的 `READ COMMITTED` 行为：A 提交后 B 才能看
  到 A 的写入。若后端配置成 `READ UNCOMMITTED`（脏读）或更弱的隔离级别，行为不
  保证——但项目 DB 配置默认 `READ COMMITTED`，不在本次范围。
- 意见 #1 的可选加固（complete 后追加 `_verify_and_fix_blocked_tasks` 兜底）未做，
  留作后续若发现 FOR UPDATE 在某些极端调度下仍漏的兜底。
