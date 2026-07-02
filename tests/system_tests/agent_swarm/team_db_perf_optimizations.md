# Team DB 性能优化清单

本清单是基于 `agent_team_tools_db_stress_e2e.py` 压测（20 人团队并发、消息主导画像）
识别出的 SQLite 数据库层优化点汇总。进度：**B1 / C4 / C1 / C2 / D1 已落地**，其余为待办分析项。
消息路径见 A/B/C 三节；**task 相关操作专项见 D 节**（读路径 N+1、写路径重复全表扫描、
task 表索引）。

- 证据来源：`tests/system_tests/agent_swarm/agent_team_tools_db_stress_e2e.py`
- 涉及代码：`openjiuwen/agent_teams/tools/database/`（`engine.py` / `message_dao.py` /
  `member_dao.py` / `models.py`）
- 验证方法：每项改动都用上面的压测 harness 做 A/B，对比 `create_message` / `read_inbox`
  / `mark_read` 的 avg·p95·p99 与总 throughput；把 `STRESS_TEAM_SIZE` 拉到 40/60 更能压出
  连接池欠配。

## 压测基线现象（20×20，消息主导，errors=0）

| 现象 | 数值 | 说明 |
|---|---|---|
| 消息表 INSERT 总量 | ~3700 次 | send_message 2400 + multicast 400×3 行 + broadcast 100 —— 压力压倒性在消息写 |
| `send_multicast` | avg 306ms / p99 2088ms / max 2502ms | 最贵单项，3 收件人大消息扇出 |
| `broadcast` | p99 1026ms | 写尖刺 |
| `mark_read` | avg 120ms / p99 1062ms / max 1489ms | N 次 SELECT+UPDATE 往返 |
| `member_status`/`member_exec` | 各 400 次，max 2537ms | SELECT+UPDATE 两次往返 |
| `read_inbox` | avg ~5ms | 快，但被写者通过共享池传染尾延迟 |
| `view_task` | bounded 后 avg 116ms | 任务池有界后已非瓶颈 |

三条正交优化主线：**索引减写放大** · **算法减往返/复杂度** · **连接池减读排队**。

### 关键洞察：写延迟 ≈ 写锁排队税（非各操作自身开销）

最新一版数据（20×20，有界任务池，errors=0）里，最简单的单行写 `member_status`（PK SELECT +
改一列，实际工作 <1ms）avg **69ms**，与 `send_message`（73ms）/ `task_update`（75ms）/
`claim_task`（69ms）几乎相同——一个只改一列的写不可能干了 69ms。**这 ~70ms 绝大部分是排队，不是干活**：
20 个 worker 的所有写都挤过同一把进程级 `asyncio.Lock`，任一时刻约 19 个在等，每个写都交这笔均匀的税。

- 判据：纯读（不持写锁）全在 3.5~9.8ms；所有写（消息/成员/任务）全在 45~210ms、聚在 ~70ms。
- 推论 1：**单独微优化某个写操作的代码收益极小**——省掉一次 5ms 冗余 read，对 70ms 排队几乎无感。
- 推论 2：写侧吞吐天花板 = 写总数 × 每次 commit 一次 fsync，全部串行。本轮 ~5343 次写 × ~3ms ≈ 16s，
  占 25.8s wall 的 ~62%。只有三条能动：**减写数量** / **缩短临界区（队列排空更快，惠及所有写者）** /
  **降每次 commit 的 fsync（WAL checkpoint = C3）**。
- 推论 3：task 写慢 ≠ task 代码差，是"排在 3700 次消息写后面"。task 写在**等消息写**。因此 task 写的唯一
  task-侧实招是**缩短临界区**（见 D5），其余靠全局 B/C 项。

---

## A. 索引 / 表结构 / 冗余

消息表（per-session 动态表）当前有 5 个二级索引：`team_name` / `to_member_name` /
`timestamp` / `broadcast` / `is_read`。每次 INSERT 额外写 5 棵 B-tree。

- [ ] **A1. 删除动态表上的 `team_name` 索引（冗余，纯写负担）** — 高收益 / 低风险
  - 现象：message/task/dependency 三张动态表**本身按 session 物理分表**，表内 `team_name`
    基数≈1（多 team-per-session 也仅 2~5），单列索引永不被优化器选中，却每次 INSERT 都维护。
  - 改动：`models.py` 中 `TeamTaskBase` / `TeamMessageBase` / `TeamTaskDependencyBase` 的
    `team_name` 去掉 `index=True`。
  - 义务：schema 变更 → 补 `test_database.py` migration/字段用例 + 同步 `docs/specs`。

- [ ] **A2. 用复合索引替换弱单列布尔索引** — 高收益 / 中风险
  - 现象：`broadcast` / `is_read` 单列（2 值）选择性≈50%，常被 SQLite 跳过；`get_messages` 的
    `to_member_name=? AND is_read=? ORDER BY timestamp` 想要的是复合。
  - 改动（消息表）：删 `to_member_name` / `timestamp` / `broadcast` / `is_read` 四个单列索引，
    加两个复合索引（经 `__table_args__`）：
    - `(to_member_name, is_read, timestamp)` — 覆盖 `get_messages`（等值+等值+有序，免 filesort）。
    - `(broadcast, timestamp)` — 覆盖 `get_broadcast_messages` 与 `has_unread` 广播分支。
  - 净效果：5 个弱单列 → 2 个强复合，每次 INSERT 少写 ~3 棵 B-tree（≈ -40% 写放大），读还更快。
  - 注意：`to_member_name = ?` 已隐含排除广播（广播行 `to_member_name IS NULL`），
    `get_messages` 里的 `broadcast.is_(False)` 过滤与之部分重复。
  - 义务：同 A1（迁移 + spec 文档）。

- [ ] **A3. 复查 task 表索引** — 中收益 / 低优先
  - task 表有 `team_name` / `status` / `assignee` / `updated_at` 四个二级索引；`team_name` 同 A1 属
    死索引。任务写现在低频（有界池），优先级低于消息表，但顺手可清。

---

## B. 算法 / 查询往返

- [x] **B1. `mark_read` 批量标记：N 次往返 → 1 条 UPDATE** — ✅ 已落地（零迁移风险）
  - 现象：`_mark_read_in_session` 对每条直连消息**先按 PK SELECT、再改 `is_read`**；批量排空
    N 条 = N 次 SELECT + N 次脏更新。
  - 改动（`message_dao.mark_messages_read`）：一次 `SELECT ... WHERE message_id IN (:ids)` 取全部行
    → 拆 direct/broadcast → 直连一条 `UPDATE ... SET is_read=1 WHERE message_id IN (:direct_ids)`；
    新增 `_eligible_direct_ids` 把逐条成员校验合并成一次 roster 查询；广播仍走原水位线 helper（少，
    manager 已折叠成最新一条）。无 schema 变更，单测 155 条全过。
  - **A/B（20×20，2 轮 before / 2 轮 after）**：`mark_read` avg 58~70ms → 37~48ms（~-35%）；
    p99 234~356ms → 85~161ms（**~-55%**）；max 362~535ms → 193~215ms（**~-55% 尾延迟**）；
    总 throughput +10~15%。尾延迟收益最大——排空大收件箱的 N 次往返最坏情况被消除。

- [ ] **B2. `has_unread_messages` 广播分支下推为 SQL EXISTS** — 中收益 / 中风险
  - 现象：当前把全部广播 + 全部成员 + 全部水位线拉进内存做 O(members × broadcasts) 双重循环。
    压测因广播少而 avg 3.8ms，但广播积累后会退化。
  - 改动：改成一条 `EXISTS`（存在某成员的水位线未覆盖某条非自己发送的广播），或先按成员维度
    聚合。保持 `is_read` 语义（consumer-less 成员写时即已读）。

- [ ] **B3. `update_member_status` / `update_member_execution_status`：SELECT+UPDATE → 单条 CAS** — 低收益 / 低优先
  - 现象：校验在 Python 侧，故先读后写，两次往返（各 400 次，PK 读较快）。
  - 改动：仿 `try_transition_member_status`，用单条 `UPDATE ... WHERE status IN (合法前态)` 的 CAS，
    省一次往返。代价：非法转移不再打详细日志（可保留 rowcount=0 的告警）。

---

## C. 连接池 / PRAGMA / WAL

当前配置（`engine.py`，文件型 SQLite）：
`pool_size=5, max_overflow=0, pool_timeout=10`；每连接 `cache_size=-65536(64MB)` /
`mmap_size=256MB` / `synchronous=NORMAL` / `temp_store=MEMORY`；首连接 `journal_mode=WAL`。
设计前提：**写由应用层 `asyncio.Lock` 串行化（单一逻辑写者）**，读不持锁靠 WAL 并发。

- [x] **C1. 读写分离池：解开"读连接可用性被写者持有时长绑架"** — ✅ 已落地（结构改动，无 schema 迁移）
  - 现象：写事务**同时占写锁 + 一个池连接**，整个事务不放；multicast avg 306ms/p99 2.5s 期间，
    20 个读者只剩 4 个连接可用 → 读尾延迟被写延迟通过共享池反向传染。
  - 改动：`initialize_engine` 返回 `SqlEngines`——文件型 SQLite 拆成**写 engine（QueuePool，
    `write_pool_size=2`）+ 读 engine（QueuePool，`read_pool_size=8`）**，同一文件；`:memory:`/PG/MySQL
    读引擎别名写引擎（不拆）。`DbSessions(write_sf, read_sf)` 分绑；`TeamDatabase.engine`/`session_local`
    仍是写引擎（DDL 用），`read_engine`/`read_session_local` 是读池。
  - **A/B（team=40，2 轮 stash 前/后）结论：延迟中性**——本 harness 读操作本就 ~4ms，池排队不成瓶颈，
    `read_inbox` p99 与 `view_task` p95 双方均在噪声带内（如 view_task p95 602/420→448/417）。
    读写分离的延迟收益只在"读又多又慢 + 写占连接久"时显现，本负载读太便宜。**确定收益是结构隔离
    （写尖刺不再传染读连接可用性）+ 为 C2 的差异化缓存/内存优化提供载体**。

- [x] **C2. `pool_size` 与 `cache_size` 的内存耦合：多连接 + 小每连接缓存** — ✅ 已落地
  - 现象：`cache_size=-65536` 是**每连接 64MB**（SQLite 页缓存非共享），旧单池 5×64MB=320MB。
  - 改动：写连接大缓存（`write_cache_size_kb=65536`=64MB，利 checkpoint），读连接小缓存
    （`read_cache_size_kb=8192`=8MB）。**峰值连接缓存：写 2×64 + 读 8×8 = 192MB，较旧 320MB
    降 ~40%，且读并行度从 5 提到 8**。共享 OS page cache + `mmap_size=256MB` 仍提供跨连接缓存。
  - 单测 `test_reader_connection_uses_smaller_cache` / `test_pool_and_cache_knobs_are_honored` 守住。

- [ ] **C3. WAL checkpoint 停顿：写尖刺的主因** — 中收益 / 中风险
  - 现象：写已串行，单笔却偶发秒级尖刺（multicast p99 2.5s、broadcast p99 1s、member max 2.5s），
    典型是 WAL auto-checkpoint 卡 commit：8KB 大消息每条 ~2-3 页，3700 次插入让 WAL 快速涨过
    `wal_autocheckpoint` 默认阈值（1000 页）。
  - 改动：调大 `wal_autocheckpoint` 降频，或把 checkpoint 挪出写路径（后台定时
    `PRAGMA wal_checkpoint(PASSIVE)`）；写连接调大 `cache_size` 反利 checkpoint（更多脏页在内存）。

- [x] **C4. 把连接池 / PRAGMA 旋钮暴露到 `DatabaseConfig`** — ✅ 已落地（纯 additive）
  - 改动：`DatabaseConfig` 增 `read_pool_size` / `write_pool_size` / `read_cache_size_kb` /
    `write_cache_size_kb` / `mmap_size_mb` / `wal_autocheckpoint` 字段，默认值保持原行为
    （写缓存 64MB、mmap 256MB、autocheckpoint 1000），向后兼容。`wal_autocheckpoint` 也已接入
    PRAGMA（C3 的旋钮就绪，C3 的 checkpoint 挪出写路径尚未做）。

### 保持现状、别乱动

- `max_overflow=0` + 短 `pool_timeout`：故意让池耗尽**暴露**连接泄漏而非掩盖——保留哲学
  （读写分离后读池可适当放宽）。
- 不加 `pool_pre_ping` / `pool_recycle`：本地文件连接不会像网络 socket 静默死掉，加了纯浪费。
- `check_same_thread=False`、`:memory:` 用 `StaticPool`：正确。

---

## D. Task 相关操作专项

消息是当前主负载，但**任务操作藏着最离谱的单点开销**：原始压测画像（每轮建任务、任务板无界膨胀）
下 `view_task` avg **2.6s** / max **14.5s** / 557 次 >1s——远超任何消息操作。改写用例把任务池收成有界
后 view_task 降到 avg 116ms，**但那是把负载画像改小掩盖了问题，根因没动**。根因不是行扫描，是查询次数
爆炸（N+1）。本节独立于负载画像，任务多时必须做。

**最新一版数据（有界池，~30 条任务）实锤**：`view_task` 仍 avg **96ms** / p95 350ms，是其他纯读
（`read_inbox` 4.6ms / `has_unread` 3.5ms）的 **~20 倍**。有界池只有 ~30 条任务，但每次 view_task 仍
~31 次查询（1 list + 30 次 `get_dependencies`），96ms/31 ≈ 3ms/查 = 一次读连接 checkout。**N+1 的 N 是
"每次调用的任务数"，与历史膨胀无关——这就是 D1。** 至于 task 的**写**（claim/create/complete/reset/update
均 ~70ms），那不是 task 特有的慢，是"写锁排队税"（见上文关键洞察）：它们和 `member_status`(69ms) 一样慢，
在等 3700 次消息写。task 侧唯一能缩短临界区的实招见 D5。

- [x] **D1. `list_tasks_with_deps` 的 N+1 → 固定 2 次查询** — ✅ 已落地（无 schema 变更）
  - 现象：`view_task`（list / claimable）走 `task_manager.list_tasks_with_deps`：
    ```python
    tasks = await self.list_tasks(status=status)          # 1 次查询，返回 N 条
    for task in tasks:
        deps = await self.get_dependencies(task.task_id)  # 每条任务再查 1 次
    ```
    `get_dependencies → task_dao.get_task_dependencies` 每次独立 `read()` 一个池连接 →
    **N+1 次查询 / N+1 次连接 checkout**。板子 400 条时单次 view_task = 401 次查询；20 个 worker
    并发、每轮 list+claimable 两次 → 峰值上万次查询抢读池连接。**这才是 2.6s/14.5s 的真凶。**
  - 改动（纯读路径，依赖表已带 `team_name` 列+索引）：
    - `task_dao` 新增 `get_team_dependencies(team_name)`：一次 `SELECT ... WHERE team_name=?` 捞全团队依赖边。
    - `list_tasks_with_deps` 改为 **2 次查询 + 内存分组**：查全部任务（查询 1）+ 查全部依赖（查询 2），
      用 `dict[task_id -> list[unresolved_dep]]`（`defaultdict`）一次遍历分组，再拼 `TaskSummary`。
  - 净效果：401 次查询 → **2 次**，连接 checkout 401 → 2；并发下 view_task 尾延迟从秒级掉到毫秒级。
    即便任务板有界到 ~20 条（N+1=21 次）仍是 ~10 倍收益，**与负载画像无关**。
  - 落地：`task_dao` 新增 `get_team_dependencies(team_name)`（一次查全团队依赖边）；`list_tasks_with_deps`
    改为"查全部任务 + 查全部依赖 + `defaultdict` 内存分组"。回归 `test_list_tasks_with_deps_avoids_n_plus_1`
    spy 断言 `get_team_dependencies` 调 1 次、`get_task_dependencies` 调 0 次，且分组正确；`test_task_manager.py`
    61 条全过。
  - **A/B（默认有界池，20×20，各 2 轮 before/after）**：`view_task` avg 70~72ms → **4.7~5.2ms（~14×）**；
    p95 209~253ms → **9.7~10ms（~24×）**；p99 318~657ms → **12~13ms（~30~50×）**；max 653~731ms → 17~131ms。
    view_task 已降到与其他纯读（`read_inbox` 4.6ms / `has_unread` 3.5ms）同一档。**顺带总 throughput
    431~456 → 578~629 calls/s（~+35%）、wall 16s → 11~12s**——N+1 的读连接 checkout 洪流消失，全局都受益。

- [ ] **D2. 写路径 `_maybe_publish_task_list_drained` 每次终态写都全表扫描** — 中收益 / 低风险
  - 现象：`complete` / `reset` / `cancel` 三处（task_manager:759 / 1041 / 1078）在每次终态转移后都调
    `_maybe_publish_task_list_drained()`，其内部 `await self.list_tasks()` **全表读一遍**判断是否全部终态。
    任务churn 下 = 每次写后跟一次 O(N) 读；压测里 claim/complete/reset 合计每轮都触发。
  - 改动：把"是否全部终态"下推为一条聚合查询——`SELECT 1 WHERE EXISTS(status NOT IN terminal) LIMIT 1`
    的反面，或 `COUNT(*) FILTER (status NOT IN terminal)`，避免把全部行拉进内存。或由 DAO 的终态写直接
    回传"剩余非终态计数"，省掉独立那次扫描。

- [ ] **D3. `get_task_detail` / `get_tasks_depending_on` 的次级 N+1** — 低收益 / 低优先
  - 现象：`view_task`（get，单任务、低频）→ `get_task_detail` → `get_tasks_depending_on`，后者
    （task_dao:699-715）先查依赖边、再**对每条边循环单查任务**，又是 N+1。
  - 改动：同 D1 思路，`WHERE task_id IN (:ids)` 一次批量取 + 内存拼装。因是单任务低频路径，优先级低。

- [ ] **D4. task 表索引：删 `team_name` 死索引 + 按查询形状加复合索引** — 中收益 / 中风险
  - 现象（细化 A3）：task 表（per-session 动态表）有 `team_name` / `status` / `assignee` / `updated_at`
    四个二级索引。`team_name` 同 A1 属死索引（表内基数≈1）。真实查询形状：
    - claimable：`status = 'pending' ORDER BY ...` → `status` 单列够用（低基数但 pending 子集通常小）。
    - `get_tasks_by_assignee`：`assignee = ? AND status = ?` → 想要复合 `(assignee, status)`。
    - list：`status = ?` 或全表。
  - 改动：删 `team_name` 索引；`assignee` 单列 → 复合 `(assignee, status)`（覆盖 cancel/reassign 时按
    assignee 找 claimed 任务）。任务写现为低频（有界池），收益低于消息表，但与 A1/A2 同批做迁移最省事。
  - 义务：schema 变更 → 补 `test_database.py` migration/字段用例 + 同步 `docs/specs`。

- [ ] **D5. task 写：把锁内 SELECT 折成单条 CAS，缩短临界区** — 中收益（惠及所有写者）/ 低风险
  - 现象：`claim_task`（task_dao:396-423）在**写锁内**先 `SELECT`（查 assignee + 校验状态转移）再 `UPDATE`，
    把一次读关进了临界区。`task_update` / `complete` / `reset` 同样是"锁内先读后写"。由「关键洞察」，写延迟
    ~70ms 主体是排队；缩短每个写者的锁持有时长 → 队列排空更快 → **所有写者（不止 task）都受益**。
  - 改动：仿 `try_transition_member_status`，用单条 CAS：
    `UPDATE team_task SET status='claimed', assignee=?, updated_at=? WHERE task_id=? AND assignee IS NULL
    AND status IN (合法前态)`，看 `rowcount`。失败（rowcount=0）时再按需单查一次拿具体原因（在锁外）。
    去掉锁内 SELECT，临界区从"SELECT+UPDATE+commit"缩到"UPDATE+commit"。
  - 注意：这不改变写总数，只缩短单次锁持有；配合 C3（降 fsync 频次）才是写侧吞吐的主要杠杆。

---

## 落地建议顺序

1. ~~**B1（mark_read 批量 UPDATE）**~~ — ✅ 已落地。
2. ~~**D1（`list_tasks_with_deps` N+1 → 2 查询）**~~ — ✅ 已落地。view_task avg 70ms→5ms、p99→~12ms，
   总 throughput +35%。零迁移、数量级收益，正是"view_task 开销离谱"的根因。
3. ~~**C4 → C1 + C2**~~ — ✅ 已落地（连接池旋钮 + 读写分离 + 小读缓存）。
4. **D2 / D3 / D5**（task 写路径全表扫描、get_task_detail 次级 N+1、claim/update 折 CAS 缩临界区） —
   零迁移，随 D1 一并做；D5 惠及所有写者。
5. **A1 + A2 + D4**（索引减写放大：消息表 + task 表） — 收益大但要迁移 + spec 文档，同批做，放最后。
6. **C3 + B2 + B3**（WAL checkpoint 挪出写路径、has_unread EXISTS、member CAS） — 由「关键洞察」，
   C3 降 fsync 是写侧吞吐的主杠杆，视前几步后的剩余尾延迟按需推进。

每步都跑 harness 前后对比，用数字验证，不猜。D1 的 A/B 要临时恢复"任务膨胀"画像（每轮建任务、
不设有界池）才压得出 view_task 的真实收益。
