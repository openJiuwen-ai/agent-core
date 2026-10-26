# Checkpoint 持久化

## 元信息
| 项 | 值 |
|---|---|
| 日期 | 2026-08-10 |
| 范围 | `runtime/metadata.py`（`TEAM_CHECKPOINTS_KEY` + `read_team_checkpoints` / `merge_team_checkpoints`），`agent/team_agent.py`（`set_checkpoint` leader 侧 merge + `_setup_infra` 接 leader `set_store_checkpoint_fn` + `recover_from_session` 恢复），`spawn/inprocess_spawn.py`（teammate 的 store 回调改路由到 leader），`agent/recovery_manager.py`（`persist_leader_config` 保留 checkpoints） |
| 测试基线 | `test_metadata.py` 20 passed；`test_fork.py` 50 passed（持久化 +3，归档后 fork 捕获修复 +12）；`test_runner_team_runtime.py` 57 passed（+3 新增）；全量 `tests/unit_tests/agent_teams/` 待跑 |
| Refs | F_75 |

## 背景

F_75 引入命名 checkpoint（`checkpoint("code-ready")` → `_named_checkpoints[name] = len(messages)`），但快照只存在进程内存的 `TeamAgent._named_checkpoints` dict 里：进程重启后冷恢复（`recover_from_session`）重建的 leader 带空 dict，`fork="<checkpoint>"` 全部回退为全量注入，上下文继承能力随进程丢失。

生产环境已配置 sqlite persistence checkpointer（`CheckpointerConfig(type="persistence", conf={"db_type": "sqlite", "db_path": ".../agent/.checkpoint/checkpoint"})`，由产品层 `ensure_persistent_checkpointer` 注入），session 的 per-team namespace（`spec` / `context` / `model_allocator_state` / `db_state` / `pending_resume`）本就跨进程重启存活——checkpoint 应搭同一条便车，不引入新后端。

## 决策

### D1 存储位置：session per-team namespace

`state["teams"][team_name]["checkpoints"] = {name: message_count}`，与 `spec`/`context`/`db_state` 同 bucket、同 blob、同一刻落盘。读写一律经 `runtime/metadata.py` 新增的 `read_team_checkpoints` / `merge_team_checkpoints`（沿用 I-6"写入只走 namespace 入口"约束）。

- `merge_team_checkpoints` 写**整份 dict 快照**（不是单 entry），保证 bucket 恒等于 `_named_checkpoints` 当前全量。
- `read_team_checkpoints` 只保留 `k=str, v=int` 的合法项——脏 blob 里的非 int 计数不能炸掉冷恢复。

### D2 落盘策略：写入时 merge、run cycle `post_run` 统一落盘（方案 A）

打快照时只改 session 内存态，不显式 `flush_checkpoint()`；持久化发生在 run cycle 收尾的 `post_run`（`team_runner.py:291` → `post_agent_team_execute` → `team_store.save`）。

审计结论支撑：`teams[team_name]` 下所有"普通运行时字段"（`model_allocator_state` / `lifecycle` / `pending_resume`）全部是"只改内存 + post_run 统一落盘"；仅有的两个即时 flush（`db_state`、激活期 `spec/context`）都有**跨资源顺序要求**（`cleaned` 必须先于 team DB 行删除落盘；manifest 必须早于 `runtime_ready` 落盘），checkpoint 无此要求。故 `set_checkpoint` 全程保持 sync，无 async 级联，`store_checkpoint` / `CheckpointTool` 零改动。

### D3 写入所有权：leader-only merge

session 只有一个持久化副本，由 **leader** 写入。teammate 的 `checkpoint()` 经 `inprocess_spawn` 的 `set_store_checkpoint_fn` 回调路由到 `team_agent.set_checkpoint`（leader）——dict 按引用共享（`set_checkpoints_from` = 引用赋值），teammate 即时可见，leader 是唯一镜像进 session 的写入者。避免 teammate 各自 `AgentTeamSession` 的独立 State 对象在 `post_run` 时用旧快照覆盖 leader 更新。

### D4 恢复与防覆盖

- `recover_from_session`：读 bucket 的 `checkpoints` → `set_checkpoints_from(...)`，冷恢复后 `_on_teammate_created` 的命名 fork 解析恢复可用；后续 inprocess teammate 经 `share_checkpoints_with` 拿到恢复后的 dict。
- `persist_leader_config` 是全量 `write_team_namespace`，payload 里用 `read_team_checkpoints(...) or {}` 保留现有值（与它保留 `db_state` 同模式），防止覆盖抹掉先前轮次的快照。

### D5 顺带修复 F_75 缺口：leader 自身 checkpoint 工具路由

F_75 只给 inprocess teammate 的 backend 设过 `set_store_checkpoint_fn`；leader 自己的 `checkpoint()` 落在 `TeamBackend._checkpoints` 兜底 dict，fork 解析读的 `_named_checkpoints` 永远看不到。`_setup_infra` 给 leader backend 接 `set_store_checkpoint_fn(self.set_checkpoint)`，使 leader 打快照也进 `_named_checkpoints` + session。与 D2/D3 同一处接线，零新增机制。

## 拒绝的方案

- **方案 B：每次打快照显式 `flush_checkpoint()`**。比其它运行时字段更激进持久化、不对称；`db_state` 之外的即时 flush 没有先例；改动面大（`store_checkpoint`/工具/接线/存量测试 async 级联）；只对"round 中途进程被硬杀"场景多一层保护，而正常重启方案 A 已解决。
- **团队 DB 方案（新表 `team_checkpoint` + DAO）**。checkpoint 是 per-team 语义标记、绑 session 生命周期，不应进成员/任务/消息的行式运营 DB；F_75 已明确预留 session state 路径。
- **teammate 各自持久化**。多 `AgentTeamSession` 独立 State，`post_run` 互相覆盖风险，收敛到 leader-only（D3）。

## 验证

- `test_metadata.py`（+4）：`merge_team_checkpoints` 创建/整体替换 bucket 子键、不碰其它子键；`read_team_checkpoints` 缺失返回 None / 过滤非 int 值。
- `test_fork.py`（+3，`TestTeamAgentCheckpointPersistence`）：leader `set_checkpoint` 把整份 dict 镜像进 session；unbound session 优雅降级；teammate 只改内存 dict、不碰 session。
- `test_runner_team_runtime.py`（+3）：`recover_from_session` 从 bucket 还原 `_named_checkpoints`；`checkpoints_survive_reload`（`InMemoryCheckpointer` 隔离，pre_run → merge → flush → 新 session 恢复，模拟重启）；`persist_leader_config` 全量覆盖时保留 checkpoints。
- 存量 `test_store_checkpoint_*` 两用例不改（store 保持 sync）。
- 归档后追加的 **fork 捕获修复**（按用户指示未单独归档，测试并入 `test_fork.py`，+12）：`ForkContext.from_agent` 持久化回退 `_read_persisted_messages` + 消息归一化 `_normalize_messages`；截断边界闭合（截在 assistant 工具调用后时把紧邻 ToolMessage 整段带过，消除注入后的悬空调用，防产品 rail 合成 `[工具执行被中断]`）；`_on_teammate_created` fork 捕获失败优雅降级（warning + 无继承 spawn，不阻断成员启动）。

## 已知遗留

- **subprocess 模式 teammate 的 checkpoint**：不经 `inprocess_spawn`，无 `share_checkpoints_with` / `set_store_checkpoint_fn` 接线，快照仍落进程本地 `_named_checkpoints`（不共享、不持久化）——与 F_75 的"subprocess spawn fork 未接线"同源，不在本次范围。
- **持久性继承 checkpointer 后端**：生产走 sqlite persistence，跨重启（fork 正常）；仅默认 `in_memory` 后端下 session 状态（含 checkpoints）随重启全丢。
