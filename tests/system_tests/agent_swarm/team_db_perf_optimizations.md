# Team DB 性能优化清单

本清单是基于 `agent_team_tools_db_stress_e2e.py` 压测（20 人团队并发、消息主导画像）
识别出的 SQLite 数据库层优化点汇总。进度：**B1 / C4 / C1 / C2 已落地**，其余为待办分析项。

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

## 落地建议顺序

1. **B1（mark_read 批量 UPDATE）** — 零迁移风险，立刻能量化收益。
2. **C4 → C1 + C2**（连接池旋钮暴露 + 读写分离 + 小读缓存） — 结构收益最大、无 schema 迁移。
3. **A1 + A2**（索引减写放大） — 收益大但要迁移 + spec 文档，放最后。
4. C3 / B2 / B3 视前三步后的剩余尾延迟按需推进。

每步都跑 harness 前后对比，用数字验证，不猜。
