---
id: B_20261031_1134_org-ddl-bypasses-write-lock
title: 组织表 DDL 绕过写锁导致团队激活写池耗尽
type: B
author: alan_cheng
author_email: chengshuo5@huawei.com
modules:
- openjiuwen/agent_teams
files:
- openjiuwen/agent_teams/organization/db.py
- openjiuwen/agent_teams/tools/database/__init__.py
- openjiuwen/agent_teams/tools/database/engine.py
- openjiuwen/agent_teams/spawn/shared_resources.py
- tests/unit_tests/agent_teams/organization/test_org_db.py
- tests/unit_tests/agent_teams/test_database_concurrency.py
- tests/unit_tests/agent_teams/test_shared_resources.py
designs: []
design_note: 普通缺陷修复，沿用 F_83（DDL 走写锁）与 tools/AGENTS.md「数据库并发」的既有契约，无独立设计
---

# 组织表 DDL 绕过写锁导致团队激活写池耗尽修复记录

## 现象与影响

swarm 前端开着多个 team session（其中一个 teammate 正在持续流式输出写库），对一个专家团 session
发起对话时 leader 激活失败，前端收到 `chat.error`：

```
sqlalchemy.exc.TimeoutError: QueuePool limit of size 2 overflow 0 reached, connection timed out, timeout 10.00
```

调用链：`team_runner._stream_leader_lifecycle` → `TeamRuntimeManager.activate` →
`ensure_team_binding` → `OrgTaskManager.find_organization_ids_for_team` →
`OrgDbContext.initialize` → `engine.begin()`。同一窗口内 swarm 的 `team.list` 对历史 session 调用
`load_persisted_organization` 也在同一位置报相同错误。单个 swarm 进程日志中短时间出现 8 次。

影响：只要开启了组织（organization）且同一 `team.db` 上存在并发写入，leader 激活和 `team.list`
都可能失败；前端轮询越密、并发 session 越多越容易触发。

## 关联设计与当前契约

`tools/AGENTS.md`「数据库并发」与 F_83 规定：SQLite 只允许一个写者，同一 `TeamDatabase` 的所有写
（包括 bind-time DDL）都必须经同一个 `DbSessions.write()` 的进程级写锁串行；经 `engine.begin()`
直连会绕过锁，并发时占满 2 连接的写池。组织表（`org_*`）与团队表在同一个 `TeamDatabase`
（同一个文件、同一个 engine）中，原本也应受这条契约约束。

## 根因与修复决策

根因（三点叠加）：

1. `OrgDbContext.initialize` 用裸 `self.db.engine.begin()` 跑 `ensure_org_static_tables`
   （`create_all` + inspect + 可能的 `ALTER`），绕过写锁直接占一个写池连接。
2. `OrgDbContext` 自己 `DbSessions(...)` 新建了 provider。写锁 `_write_lock` 挂在 `DbSessions`
   实例上，于是组织写与团队 DAO 写分属两把锁，彼此不互斥，同抢 2 个写连接；拿到连接的一方在
   SQLite 文件锁上等待（`db_timeout`）时一直占着连接。
3. `OrgTaskManager.find_organization_ids_for_team` 每次调用都新建 `OrgDbContext`，`_initialized`
   缓存失效，每次只读查询都重跑一遍 DDL、占一个写连接。swarm `team.list` 约 2 秒轮询一次，且对
   每个历史 session 都会调用一次。

修复：

- `TeamDatabase` 新增只读属性 `sessions`，暴露其共享的 `DbSessions`。`initialize()` 在最后一步才
  发布它；中途失败时释放刚建好的 engine 再抛出，外部既拿不到半成品 provider，重试也不会泄漏旧的连接池。
- `TeamDatabase.close()` 改为终态：清空 `sessions`，之后 `initialize()` 抛 `AGENT_TEAM_STATE_INVALID`，
  不再重新打开；新增只读属性 `closed`。生产代码里没有 `close()` 的调用方，只有测试会在关闭后重开，已改为
  新建实例。`get_shared_db` 遇到已关闭的缓存实例会换一个新的，避免把死实例一直发给调用方。
- `get_shared_db` 的缓存 key 对文件 SQLite 做 `expanduser().resolve()`：同一个库文件写成 `~/...`、
  相对路径或绝对路径时共用一个 `TeamDatabase`，不会出现两套连接池、两把写锁争同一个文件。`:memory:`
  和 PostgreSQL / MySQL 的连接串保持原样。相对路径按调用 `get_shared_db` 时的工作目录解析，与
  engine 打开文件时的解析方式一致；进程中途切换工作目录不在考虑范围内。
- `OrgDbContext` 不再缓存 provider：`sessions` 属性每次都读 `TeamDatabase.sessions`，库没初始化或已
  关闭就抛 `AGENT_TEAM_STATE_INVALID`；`initialize()` 只做"初始化数据库 + 确保组织表"。因此无论是还被
  引用的旧组织存储，还是 `team.list` 每次新建的 context，都没法把已关闭的库悄悄重新打开（重建 engine 和
  后台 WAL checkpoint 任务）；数据库换了 provider 时也不会继续在旧写锁上写。
- 组织静态表 DDL 移入 `sessions.write()`（取得写锁后再复查一次）。
- `engine.py` 新增 `retry_on_transient`：与 `retry_on_locked` 用同一套退避参数，但额外重试
  `DbSessions.write()` 看门狗的 `TimeoutError`（与 `create_cur_session_tables` 一致），且重试预算
  耗尽时抛出最后一次的原始异常，不返回兜底值。看门狗超时每次已经等满 30 秒，卡死的驱动也很少在几秒内
  恢复，所以看门狗超时最多只重试 1 次（`_WATCHDOG_RETRY_ATTEMPTS = 2`）：最坏约 60 秒报错，若按 3 次
  则约 90 秒；修复前不重试，约 30 秒。可重试的异常集中在模块常量 `_TRANSIENT_DB_ERRORS`。组织 DDL 用它：组织读写离不开这些表，不能像会话建表
  那样降级为告警。`create_cur_session_tables` 原有的手写重试循环也改为调用它，并捕获同一个
  `_TRANSIENT_DB_ERRORS`（失败时仍只记日志、不抛出；看门狗超时从最多 3 次变为最多 2 次），重试逻辑从三份收敛为两份；`retry_on_locked` 返回兜底值、异常范围更窄，语义不同，
  保持不动。
- 写锁不可重入，因此 `_ensure_org_schema` 不能在已打开的 `sessions.write()` 内调用。核查
  `task_pool.py` / `message_service.py`：所有写方法都是先 `await self.initialize()` 再进入
  `_write()`，不存在这种调用路径；只在 docstring 写明约束，不加运行时防护。
- 「已建好」标记按 `DbSessions` 实例记在模块级 `WeakSet` 中，一个数据库只做一次：
  `find_organization_ids_for_team` 每次新建 context 也不会重跑 DDL；`TeamDatabase.close()` 后再
  `initialize()` 会产生新的 provider（新的 `:memory:` 库没有表），标记自然失效、DDL 重跑。

考虑过但未采用：

- 把标记放在 `TeamDatabase` 实例上：需要在 `close()` 里手动重置，且让工具层数据库知道组织层的
  schema，耦合方向反了；按 provider 记标记无需任何清理。
- 只把 `find_organization_ids_for_team` 改为复用缓存的 context：只解决第 3 点，DDL 仍绕过写锁、
  组织写仍是第二把锁，池耗尽依旧可能发生。
- 把组织表 DDL 挪进 `TeamDatabase.initialize`，让它排在写队列最前面：会让工具层的数据库依赖组织层
  的表结构，依赖方向反了。代价是冷启动后第一次组织操作要排在已有写操作后面，每个库只发生一次，可接受。
- 调大 `write_pool_size`：掩盖锁绕过问题，SQLite 仍只能有一个写者，多出的连接只会在文件锁上排队。

## 回归证据与适用范围

候选：`fix/org-db-shared-write-lock`（基于 upstream/develop `0e79fb153`），本地 macOS、Python 3.13，
使用 swarm 的 `.venv`。

- `tests/unit_tests/agent_teams/organization/test_org_db.py` 新增 7 条。断言都挂在 SQL 边界上：用
  `before_cursor_execute` 事件记录写 engine 上下发的建表 / 表结构查询语句，以及执行时
  `DbSessions.write_locked`（新增的只读诊断属性）的值；不 mock 被测模块的函数，也不读私有字段。
  - `test_org_db_context_reuses_team_database_sessions`：组织 context 拿到的就是 `db.sessions`。
  - `test_org_static_ddl_runs_under_team_database_write_lock`：文件 SQLite 新库上，每条
    `CREATE TABLE org_*` 执行时共享写锁都处于持有状态。与运行快慢无关。
  - `test_find_organization_ids_runs_static_ddl_once_per_database`：首次建表后再查询 3 次，不再下发任何
    建表或表结构查询语句。
  - `test_org_static_ddl_runs_for_each_new_database`：新建的数据库实例会重新建组织表。
  - `test_org_context_does_not_reopen_closed_database`：库关闭后，已有 context 的 `initialize()` /
    `sessions`，以及新的 `find_organization_ids_for_team` 都报错，`db.engine` 仍为 `None`。
  - `test_closed_team_database_refuses_reinitialize`：`close()` 后 `sessions` 为 `None`，再
    `initialize()` 报错。
  - `test_failed_initialize_releases_engines_and_hides_sessions`：`SessionFileStore` 构造失败时，
    `sessions` 与两个 engine 都为 `None`；原因消除后重试初始化成功。
- `tests/unit_tests/agent_teams/test_database_concurrency.py` 新增 3 条：`retry_on_transient` 遇到一次
  看门狗超时后重试成功；连续两次看门狗超时即抛出；其它瞬时错误预算耗尽时抛出最后一次的原始异常。
- `tests/unit_tests/agent_teams/test_shared_resources.py` 新增 2 条：已关闭的缓存实例会被换成新实例；
  同一个 SQLite 文件的 `~`、相对、绝对、含 `..` 四种写法拿到同一个实例，另一个文件拿到不同实例。
  后一条在 key 归一化前的代码上失败。
- 对照：把 `organization/db.py` 换回修复前版本，前 3 条组织测试失败；保留共享 provider、只把 DDL 改回
  直连 `engine.begin()`，锁测试单独失败；把 `tools/database/__init__.py` 换回上一提交的版本，关闭与
  初始化失败相关的 3 条失败。
- swarm 侧（venv 已 editable 链接本地 core）：`tests/integration/test_team_organization_history.py`、
  `tests/unit_tests/server/test_group_service.py`、
  `tests/unit_tests/server/extensions/test_organization_package_uninstall.py`、
  `tests/agents/swarm/test_group_chat.py` 共 74 passed。
- `tests/unit_tests/agent_teams` 全量：4111 passed / 36 skipped / 2 xfailed / 1 failed。失败的
  `agent/test_member_activity.py::test_observe_member_status_emits_after_the_debounce` 在不含本次任何
  改动的基线上同样失败（4099 passed / 1 failed），与本修复无关。
- 另：`test_database.py::TestTeamDatabaseInit::test_postgresql_initialize_uses_asyncpg_engine` 在与
  `test_runner_team_runtime.py` 单独组合运行时会失败，旧代码上同样如此，全量运行和单独运行均通过，
  属于已有的测试间隔离问题。

未验证：没有在 swarm 中按原始场景（多 team session 并发 + 前端轮询）端到端复现；需要 swarm 用
`/link-core` 指向本分支后在同样场景下复测，确认日志中不再出现 `QueuePool limit`。

## 防错模式与资料同步

模式：同一 `TeamDatabase` 上的任何写（包括 DDL）都必须经它自己的 `db.sessions.write()`，
不要新建 `DbSessions`，也不要用 `engine.begin()` 直连。已把这条写进 `tools/AGENTS.md`「数据库并发」
的 `DbSessions` 段落，并给出本记录的链接。

## 遗留与后续

- F_83 已知遗留未变：`drop_cur_session_tables` 与 `cleanup_all_runtime_state` 等清理路径仍走
  `engine.begin()`，属于停摆期操作，本期不处理。
- swarm 的 `team.list` 对每个历史 session 都会查一遍组织，读负载仍偏高（现在走读池，不再抢写池）；
  如需进一步优化在 swarm 侧缓存，与本修复无关。
