# S_10 SubAgent 运行时

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | `openjiuwen/harness/subagent_runtime/`（18 文件） |
| 最近一次修订日期 | 2026-08-23 |
| 关联 feature | N/A |

## 范围 / 边界

本规约定义 harness 的异步子代理运行时：spawn / wait / control / status / snapshot /
persistence。`subagent_runtime/` 18 文件承载 `enable_subagent_runtime=True` 时的
子代理生命周期；`S_18` 是预设子代理构建（同步 / 复用），`S_05` 是子代理工具（工具面）。

具体覆盖：

- `subagent_runtime/control.py`：`SubagentControl`（spawn / wait / send_input / resume /
  close / cancel_all / hydrate / flush / snapshot）。
- `subagent_runtime/models.py`：`SubagentStatusKind` / `SubagentStatus` / `SubagentMetadata` /
  `SpawnResult` / `WaitResult` / `ResumeResult` / `SubagentRecord` / `SubagentTurn` /
  `SubagentActivity` / `SubagentMessage` / `SubagentSnapshot` / `UserInputOp` / `ShutdownOp`。
- `subagent_runtime/registry.py`：`SubagentRegistry` + `SpawnReservation`（容量预留）。
- `subagent_runtime/config.py`：`SubagentRuntimeConfig`。
- `subagent_runtime/status.py`：`StatusChannel` / `StatusReceiver`。
- `subagent_runtime/persistence.py`：落盘 / 恢复。
- `subagent_runtime/{activity,transcript}.py`：`ActivityProjector` / `TranscriptEmitter` +
  快照 / 分页投影（`DEFAULT_SNAPSHOT_PAGE_SIZE`）。
- `subagent_runtime/session_manager.py`：`SubagentSessionManager`。
- `subagent_runtime/ids.py`：`build_subagent_id` / `new_task_id`。
- `subagent_runtime/errors.py`：`build_subagent_runtime_error` /
  `raise_subagent_capacity_invalid` / `raise_subagent_not_found`。
- `subagent_runtime/__init__.py` 的导出面（`__all__`）。

不在本规约范围内：
- 子代理工具（`SubagentSpawnTool` 等）—— `S_05`。
- 预设子代理（browser/code/research/verification）—— `S_18`。
- `SubagentRail` / `SessionRail` —— `S_04`。
- KVC 亲和（`kv_cache_subagent_lifecycle.py`）—— `S_16`。

## 不变量

1. **状态机唯一**：`SubagentStatusKind` 枚举七态：`PENDING_INIT` / `RUNNING` / `COMPLETED` /
   `INTERRUPTED` / `ERRORED` / `CLOSED` / `NOT_FOUND`。任何子代理状态查询落到这七态。
2. **`SubagentControl` 是控制面唯一入口**：`spawn`（携带 `subagent_type` + `query`，可选
   `subagent_id` / `display_name` / `role` / `browser_capabilities`）→ `SpawnResult`；
   `wait(subagent_ids, timeout_ms=WAIT_TIMEOUT_MS_DEFAULT)` → `WaitResult`，超时钳制在
   `WAIT_TIMEOUT_MS_MIN` / `WAIT_TIMEOUT_MS_MAX`。
3. **容量预留**：spawn 前 `SubagentRegistry.reserve_slot()` 拿 `SpawnReservation`
   （`commit(metadata)` / `rollback()`）；超容量 spawn → `raise_subagent_capacity_invalid`。
   槽位按 `SubagentRuntimeConfig` 限制；LRU 淘汰候选 `lru_candidates()`。
4. **状态订阅**：`subscribe_status(subagent_id) -> StatusReceiver` 经 `StatusChannel` 推送；
   `emit_status_update` 是状态变更发布唯一路径。
5. **持久化与恢复**：`hydrate()` 冷启动从磁盘恢复已登记子代理；`flush()` 落盘；
   `snapshot(...)` 产出 `SubagentSnapshot`（分页 `DEFAULT_SNAPSHOT_PAGE_SIZE`）。
   `_merge_turns` / `_merge_activities` / `_merge_snapshot_items` 负责增量合并。
6. **ID 体系唯一**：`build_subagent_id` / `new_task_id`（`subagent_runtime/ids.py`）
   产生子代理 id 与任务 id；`_build_metadata_from_record` 恢复元数据。
7. **错误语义唯一**：子代理不存在 → `raise_subagent_not_found`；容量不足 →
   `raise_subagent_capacity_invalid`；`build_subagent_runtime_error` 是错误构造唯一入口。
8. **子代理生命周期挂钩 DeepAgent 会话**：`DeepAgent.abort` 调
   `_release_session_subagent_controls`；`_cancel_session_deep_tasks` 收尾
   （`S_02` 不变量 11）。`cancel_all(reason="parent_ended")` 是父会话结束时的批量清理。
9. **输出投影两路**：`ActivityProjector`（活动事件：reasoning / boundary / tool）与
   `TranscriptEmitter` / `TranscriptProjector`（turn 转录）；`resolve_presentation` 把它们
   折成宿主可渲染形态。`SUBAGENT_*_EVENT_TYPE` 常量是事件类型契约。

## 接口契约

```python
class SubagentControl:
    async def spawn(self, subagent_type: str, query: str, *,
                    subagent_id: str | None = None,
                    display_name: str | None = None,
                    role: str | None = None,
                    browser_capabilities: list[str] | None = None) -> SpawnResult
    async def wait(self, subagent_ids: list[str],
                   timeout_ms: int = WAIT_TIMEOUT_MS_DEFAULT) -> WaitResult
    def get_status(self, subagent_id: str) -> SubagentStatus
    def subscribe_status(self, subagent_id: str) -> StatusReceiver
    def list_live(self) -> list[SubagentMetadata]
    def capacity(self) -> dict[str, int]
    async def send_input(self, subagent_id: str, ...) -> None
    async def resume(self, subagent_id: str) -> ResumeResult
    async def close(self, subagent_id: str, reason: str = "manual") -> SubagentStatus
    async def cancel_all(self, reason: str = "parent_ended") -> list[str]
    def hydrate(self) -> None
    def flush(self) -> None
    def snapshot(self, ...) -> SubagentSnapshot

class SubagentRegistry:
    def reserve_slot(self) -> SpawnReservation
    def register(self, metadata: SubagentMetadata) -> None
    def release(self, subagent_id: str) -> None
    def touch(self, subagent_id: str) -> None
    def find_metadata(self, subagent_id: str) -> SubagentMetadata | None
    def list_live(self) -> list[SubagentMetadata]
    def lru_candidates(self) -> list[str]

class SubagentStatusKind(str, Enum):
    PENDING_INIT = "pending_init"
    RUNNING = "running"
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    ERRORED = "errored"
    CLOSED = "closed"
    NOT_FOUND = "not_found"
```

错误 / 返回语义：

- `spawn` 超容量 → 抛 `SubagentCapacityInvalid`（`raise_subagent_capacity_invalid`）。
- `wait` 对已关闭 / 不存在的子代理 → `WaitResult` 带对应状态（不抛）。
- `get_status` 未知 id → `SubagentStatus(NOT_FOUND)`。
- `close` 返回关闭后的 `SubagentStatus`；幂等。
- `send_input` 目标非运行中 → 抛（`UserInputOp` 校验）。

## 数据结构

### SubagentRecord / SubagentSnapshot 生命周期

| 对象 | 写入 | 读取 | 说明 |
|---|---|---|---|
| `SubagentRecord` | spawn / flush / close(finalize) | hydrate / snapshot | 持久化主记录 |
| `SubagentTurn` | 每轮完成 | transcript 投影 | 增量合并 |
| `SubagentActivity` | 活动事件 | activity 投影 | 增量合并 |
| `SubagentSnapshot` | snapshot() | 恢复 / 分页 | 分页上限 `DEFAULT_SNAPSHOT_PAGE_SIZE` |

### 状态迁移

| 迁移 | 触发 |
|---|---|
| `PENDING_INIT → RUNNING` | spawn 完成初始化 |
| `RUNNING → COMPLETED` | 正常完成 |
| `RUNNING → INTERRUPTED` | 中断 / `send_input` 打断 |
| `* → ERRORED` | 执行异常 |
| `* → CLOSED` | `close` / `cancel_all` |
| `* → NOT_FOUND` | 查询未知 id |

## 与其它 spec 的关系

- 子代理工具消费本运行时 —— `S_05`；`DeepAgent.abort` 收尾 —— `S_02`。
- 预设子代理（`subagents/`）被 manifest 构建器（`S_12`）注册、被
  `SubagentRail`（`S_04`）挂载。
- 活动/转录投影喂给宿主 UI —— `S_02` 输出流（`S_15` CLI 消费）。
- KVC 子代理生命周期适配 —— `S_16`。
- 与 `agent_teams` 的 `F_44`（worker-not-teammate-no-db）同属子代理运行时思想，但实现
  独立（harness 侧不自带 DB，状态走 record 文件）。
