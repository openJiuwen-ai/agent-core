---
id: B_20261125_1037_review-followups-on-issue-115-concurrency-fix
title: issue #115 检视遗留并发洞的修复
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
- openjiuwen/agent_teams/schema/blueprint.py
- openjiuwen/agent_teams/agent/team_agent.py
designs: []
design_note: 91ad1d944 把锁的边界从「全写锁」收窄到「DDL 锁 + CAS + 行锁」时漏掉的入口；本修复把同一套并发模型补齐到所有剩余读改写路径，不引入新设计
---

# issue #115 检视遗留并发洞的修复记录

## 现象与影响

91ad1d944（`fix(swarm): restore DDL mutex + CAS-guard non-CAS DAO writes under
PG/MySQL`）把 PostgreSQL / MySQL 上的进程级写锁去掉、改为「DDL 锁 + CAS + 行锁」
三件套。检视发现这套模型在多个入口上没收齐，本修复把同一套并发模型补齐到所有剩余
读改写路径。

## 根因与修复决策

### #1 下游任务卡在 BLOCKED（`_refresh_status_in_session` + MySQL READ COMMITTED）

复现条件：MySQL READ COMMITTED。创建任务 A、B、C，让 C 同时依赖 A 和 B。将 A、B
置为 IN_PROGRESS，并发调用 `TaskDao.complete_task()`。用屏障让两笔事务都执行完
`_refresh_status_in_session()` 后再提交。此时每笔事务只能看到自己解决的那条依赖，
都会判断 C 仍有一条未解决依赖，C 卡在 BLOCKED。

**修法**：
- `_refresh_status_in_session` 候选任务 SELECT 加 `with_for_update()`，串行化两个
  上游的 dependent refresh。
- `engine.py` 显式为 PG/MySQL 引擎设 `isolation_level="READ COMMITTED"`。MySQL
  默认 `REPEATABLE READ` 会在事务首个一致性读建立 read view，使 deps count 查询
  在 FOR UPDATE 行锁拿到后仍读旧 view，看不到并发上游已提交的 dep 解析 → 下游卡
  BLOCKED。READ COMMITTED 让每条语句取新快照，FOR UPDATE 串行化才真正生效。

### #2 `_ensure_org_schema` 重复 ALTER 报错

组织表迁移入口未取 `ddl_lock()`，PG/MySQL 跳过写锁后两个首次访问可同时通过
`_ORG_SCHEMA_READY` 复查、同时执行 `ALTER TABLE ... ADD COLUMN`，第二次命中
「duplicate column」失败。

**修法**：`_ensure_org_schema` 的 `_ddl_once` 用 `sessions.ddl_lock()` 外层包裹
`sessions.write()`，docstring 同步更新。

### #3 options JSON 丢更新

`update_member_worktree` 已加 `FOR UPDATE`，但同款的 3 个 options 写入口
（`update_member_builtin_model` / `update_member_served_model` /
`promote_member_fallback_model`）仍是普通 SELECT-then-overwrite，与
`update_member_worktree` 不一致。两个并发写会丢更新。

**修法**：3 个 options 写入口 SELECT 加 `with_for_update()`，与
`update_member_worktree` / `_refresh_status_in_session` 同模式。SQLite 方言剥离
`FOR UPDATE`（进程级写锁兜底），PG/MySQL 靠行锁串行化读改写。

### #4 新增依赖与上游完成并发时下游卡住（`_apply_new_edges` 的 `resolved` 决策用过期读）

当给 B 添加对 A 的依赖时，`_apply_new_edges` 可能先读到 A 仍在执行。若 A 随后完成
并提交，`complete_task(A)` 扫描依赖时还看不到这条边。加边事务接着按之前读到的状态
写入 `resolved=False`，使 B 进入 BLOCKED。两次操作都返回成功，但 A 已完成，B 无法
通过这次完成事件解锁。

**修法**：`_apply_new_edges` 在插边前对每条新边的上游端点取
`select(task_id, status).where(...).with_for_update()` 的**标量行锁**，用现读的
`status` 决定 `resolved`。标量列投影绕过 identity map（整行 FOR UPDATE 会回退缓存
对象的过期属性而非现读 DB）。锁序与 `_terminate_task_in_session` 一致
（task→dep→task），不重引反向死锁。

### #5 `member_model` 回写判据与冷恢复重新物化判据不同源

`AgentConfigurator` 的回写判据用已展开的 `ctx.team_spec.model_pool`（`build()` 时
`model_router` / `model_intelli_router` 展开进 `team_spec.model_pool`），但冷恢复
路径 `team_agent.py` 检查未展开的 `runtime_spec.model_pool`（对 router /
intelli_router 团队为空），跳过 `context.member_model` 重新物化 —— 让 stale client
跨冷恢复存活。

**修法**：提取 `TeamAgentSpec.effective_model_pool()` 方法（返回展开后的
`(pool, strategy)`），`build()` 与 `recover_from_session` 都改用此 helper，两条路径
共享同一展开源。

### #6 `router` 策略冷恢复重新物化 `member_model` 时丢失模型名 hint

#5 的 `effective_model_pool()` 修复让 `router` 团队第一次进入冷恢复重新物化分支，但
现有代码只处理 `intelli_router` / `by_model_name`，`router` 落到
`allocator.allocate(None)` 返回 pool[0]，丢弃 checkpoint 的 model_name hint。

**修法**：把 `router` 与 `by_model_name` 同等处理（两者都是按名路由）：用
`checkpoint_name` 作 `allocate()` 的 hint，回写 `spec.leader.model_name`。

### #7 广播已读水位的 CAS UPDATE 在 `read_at IS NULL` 时静默不生效

CAS UPDATE 的 WHERE 子句 `read_at == current_read_at` 在 `current_read_at` 为
`None` 时生成 SQL `read_at = NULL`，而 `NULL = NULL` 在 SQL 中结果是 `NULL`（不是
`True`），WHERE 匹配 0 行、UPDATE 不生效，但函数仍返回 `True`。`read_at` 列是
`nullable=True` + `default=None`。

**修法**：`current_read_at` 为 `None` 时用 `read_at.is_(None)`（SQL `IS NULL`）；
CAS UPDATE 后检查 `result.rowcount`，0 行时返回 `False`。

## 考虑过但未采用

- **退回 PG/MySQL 进程级写锁**：会重新引入 issue #115 的 30s 看门狗堆积问题
  （91ad1d944 的修复动机）。方向不退。
- **`reset_task` / `approve_plan_task` 改 CAS**：这两个是状态翻转，CAS 可行；但
  `reset_task` 还要清空多列，CAS UPDATE 需要把所有列写进 `values()`。`FOR UPDATE`
  与已建立的修复模式一致，统一性优先。

## 回归证据与适用范围

候选：`fix/issue-115-enterprise`。

```bash
# database + task_manager + message_manager（覆盖 #1 #3 #4 #7）
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
