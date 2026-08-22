# Session 动态表 DDL 收编进 DbSessions 写锁

bind-time 的 per-session 动态表 DDL 从 `engine.begin()` 直连改为经 `DbSessions.write()` 执行——修复并发 bind 时写池耗尽导致的 `QueuePool limit of size 2` 崩溃。本文记录决策与拒绝的方案。

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-08-19 |
| 范围 | `openjiuwen/agent_teams/tools/database/{__init__,engine,member_dao}.py`、`tests/unit_tests/agent_teams/test_database_concurrency.py` |
| 测试基线 | `test_database_concurrency.py` 全绿（新增 `test_session_ddl_runs_under_write_lock` 回归守卫）；`test_db_sessions_watchdog.py` 4 用例全绿 |
| Refs | #1561 |

## 背景

per-session 动态表（task / dependency / message / read_status / review_vote）在 `bind_session` 时创建，DDL 由 `engine.create_cur_session_tables(engine)` 经 `engine.begin()` 直接连写引擎执行——**完全绕过** `DbSessions` 的进程级写锁。

写池是 SQLite 单写者模型的产物：`write_pool_size` 默认 2，写锁保证同一时刻只有一个 writer 持连接干活。DDL 绕过锁意味着它可以和 DAO 写**并行**占连接。

## 根因

两个 teammate 并发 bind（多成员 spawn 的常见形态）：

1. 两个 bind 各自 `engine.begin()` 从写池各 checkout 一条连接——**池已空**。
2. 两条 DDL 都 park 在 SQLite 文件锁上（一条等另一条）。
3. 期间任何 DAO 写（如 `update_member_status`）checkout 写连接 → 池空 → `pool_timeout` 到点 → `QueuePool limit of size 2` 崩溃。

这是同池双占的死锁形态：DDL 占着连接等文件锁，写者等连接。锁本身没坏——坏在 DDL 根本不参与锁。

## 决策

1. **DDL 走写锁**：`TeamDatabase` 持有 `_sessions`（与四个 DAO 同实例的 `DbSessions`）；`create_cur_session_tables(sessions: DbSessions)` 签名从 `AsyncEngine` 改为 `DbSessions`，表创建在 `sessions.write()` 内执行、结束 `commit`。DDL 与一切 DAO 写同锁串行——任何时刻只有一条 DDL 或一次写占连接，池 2 恒够用。
2. **状态写热路径重试**：`MemberDao.update_member_status` 包 `retry_on_locked`（`on_locked_result=False`）——它坐落在每次 kernel.start / spawn 流转的热路径上，瞬态 SQLite 文件锁等待或暂时性池紧张应重试而非崩掉成员。
3. **回归守卫**：新增 `test_session_ddl_runs_under_write_lock`，用 spy 断言 DDL 走 `DbSessions.write()` 路由而非 `engine.begin()`——零真实等待。

## 拒绝的方案

- **加大写池 / 调大 `pool_timeout`**：治标。池耗尽只是被推迟，并发 bind 数一多照样触发；且写池大小与 SQLite 单写者语义绑定，调大徒增闲置连接。
- **DDL 单独一把锁（如 `asyncio.Lock` 包 DDL）**：引入第二层锁而不解决「DDL 与 DAO 写共享写池」的占用问题——两把锁互不约束，DDL 仍可与写并行占满池。
- **bind 前强制串行（复用 `_init_lock`）**：`_init_lock` 只保证 init 互斥，覆盖不了 init 之后任意时刻的 DDL 与写并发；语义错位。

## 验证

- `test_database_concurrency.py`：新增回归用例全过（DDL 路由断言）。
- `test_db_sessions_watchdog.py`：4 用例全过（含锁等待不计时语义）。
- 行为不变量保持：DDL 幂等（`checkfirst=True`），`bind_session` 重复调用无副作用（S_04 I-5）。

## 已知遗留

- `drop_cur_session_tables` 仍走 `engine.begin()`（session 释放路径）。DROP 同样绕过写锁，理论上可与并发写争池——但释放发生在团队停摆期，且现网未观察到问题；如需对称可一并收编，本期不做。
