# S_11 Goal 与评估

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | `openjiuwen/harness/goal/`（5 文件） |
| 最近一次修订日期 | 2026-08-23 |
| 关联 feature | N/A |

## 范围 / 边界

本规约定义 harness 的 goal 子系统：会话级持久目标的声明式管理、完成评估、stop 策略、
与 task loop 的挂钩。`goal/` 5 文件，状态**独占**在 `GoalManager`；task-loop 生命周期钩子
由 `TaskCompletionRail`（`S_04`）负责，不在本包。

具体覆盖：

- `goal/schema.py`：`GoalStatus` / `GoalAssessmentStatus` / `GoalStopStrategy` /
  `GoalAssessment` / `TokenUsage` / `GoalRecord` / `GoalStopConfig` / `GoalOperationError`。
- `goal/manager.py`：`GoalManager`（get / set / pause / resume / clear / begin_attempt /
  accumulate_usage / apply_assessment / peek / ensure_active_goal_work_locked）。
- `goal/evaluation.py`：`GoalEvaluator`。
- `goal/store.py`：`SessionGoalStore`（session 绑定）。
- `goal/__init__.py` 导出面（`__all__` 11 个符号）。

不在本规约范围内：
- goal 工具（`SubmitGoalReportTool` / `GetCurrentGoalTool`）—— `S_05`。
- `TaskCompletionRail` 的评估钩子实现 —— `S_04`。
- `DeepAgent._load_goal_record_locked` 的 supervisor 消费 —— `S_02`。

## 不变量

1. **goal 状态独占 `GoalManager`**：状态读写、attempt 记账、评估应用全部经
   `GoalManager`；`TaskCompletionRail` 只通过公开方法（`set_goal_manager` /
   `begin_attempt` / `accumulate_usage` / `apply_assessment`）挂钩，不直接改 `GoalRecord`。
2. **持久化经 `SessionGoalStore`**：`load()` / `save()` / `clear()` 以 session 为界；
   `GoalManager.get_store(session_id)` 返回绑定 store；`commit()` 显式落盘。
3. **状态枚举**：`GoalStatus`：`ACTIVE` / `PAUSED` / `COMPLETED` / `BLOCKED`；
   `GoalAssessmentStatus`：`COMPLETE` / `CONTINUE` / `BLOCKED`（评估输出三态）；
   `GoalStopStrategy`：`AGENT_REPORT` / `TRANSCRIPT` / `HYBRID`。
4. **attempt 记账**：`begin_attempt` 使 `attempt_count += 1`（受 `max_attempts` 限制）；
   `accumulate_usage` 累加 `TokenUsage`（input / output / cached_input / total）；
   `apply_assessment` 把 `GoalAssessment` 应用到 record 并更新 `last_stop_reason`。
5. **stop 配置**：`GoalStopConfig`：`strategy`（默认 `HYBRID`）、
   `transcript_window_attempts`（默认 8）、`verification_interval`、`max_attempts`、
   `token_budget`。`GoalStopStrategy.HYBRID` = `AGENT_REPORT` + `TRANSCRIPT` 组合评估。
6. **并发/一致性**：`GoalManager` 持有 record 锁；`_ensure_goal_work_locked` /
   `ensure_active_goal_work_locked` 判定"该不该为当前 goal 干活"；
   `_has_in_flight_goal_attempt` / `_matches_in_flight` 防重复 attempt；
   `_emit_goal_updated_locked` 发状态事件（`InteractionEventType.GOAL_UPDATED`，`S_02`）。
7. **goal 与 task loop**：`TaskCompletionRail` 用 `build_evaluators`（`S_04`）构造
   stop 求值器并喂 `LoopCoordinator.get_completion_promise_evaluator`（`S_03`）；
   goal work 由 `EventManager.push_goal(work) -> bool` 进入 supervisor `RoundWorkItem.goal`
   （`S_02`），重复（同 goal_id+revision）返回 `False`。
8. **错误语义**：goal 操作失败抛 `GoalOperationError`（`RuntimeError` 子类）。

## 接口契约

```python
class GoalStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    BLOCKED = "blocked"

class GoalAssessmentStatus(str, Enum):
    COMPLETE = "complete"
    CONTINUE = "continue"
    BLOCKED = "blocked"

class GoalStopStrategy(str, Enum):
    AGENT_REPORT = "agent_report"
    TRANSCRIPT = "transcript"
    HYBRID = "hybrid"

class GoalManager:
    def get_store(self, session_id: str | None = None) -> GoalStore
    def peek(self) -> Optional[GoalRecord]
    async def get(self) -> Optional[GoalRecord]
    async def set(self, ...) -> GoalRecord
    async def pause(self) -> Optional[GoalRecord]
    async def resume(self) -> Optional[GoalRecord]
    async def clear(self) -> Optional[GoalRecord]
    def ensure_active_goal_work_locked(self) -> bool
    async def begin_attempt(self, ...) -> None
    async def accumulate_usage(self, ...) -> None
    async def apply_assessment(self, ...) -> None

class GoalEvaluator:
    def evaluate(...) -> GoalAssessment

class SessionGoalStore:
    def load(self) -> Optional[GoalRecord]
    def save(self, record: GoalRecord) -> None
    def clear(self) -> None

class GoalStopConfig:
    strategy: GoalStopStrategy = GoalStopStrategy.HYBRID
    transcript_window_attempts: int = 8
    verification_interval: Optional[int] = None
    max_attempts: Optional[int] = None
    token_budget: Optional[int] = None
```

错误 / 返回语义：

- `pause` / `resume` / `clear` 无 active goal → 返回 `None`（不抛）。
- 已 COMPLETED / BLOCKED 的 goal 再 `set` → 语义由 `GoalOperationError` 承载。
- `apply_assessment(COMPLETE)` → status 置 `COMPLETED`；`BLOCKED` → 置 `BLOCKED`；
  `CONTINUE` → 保持 `ACTIVE`（并累计 attempt）。

## 数据结构

### GoalRecord 生命周期

| 字段 | 设置时机 | 清空时机 | 备注 |
|---|---|---|---|
| `goal_id` / `session_id` | set 时 | — | 主键对 |
| `objective` | set 时 | — | 目标文本 |
| `status` | set 时 `ACTIVE` | pause/complete/blocked | 五态见上 |
| `revision` | set / resume 时递增 | — | 目标修订 |
| `attempt_count` | `begin_attempt` | clear | 受 max_attempts |
| `token_usage` | `accumulate_usage` | clear | TokenUsage 四字段 |
| `last_assessment` / `last_stop_reason` | `apply_assessment` | clear | 评估快照 |
| `created_at` / `updated_at` | set / 每次变更 | — | ISO 时间戳 |

### 评估三态到 goal 动作

| `GoalAssessmentStatus` | 动作 |
|---|---|
| `COMPLETE` | 置 `COMPLETED`，`stop_reason="goal_completed"` 类 |
| `CONTINUE` | 保持 `ACTIVE`，推进 attempt |
| `BLOCKED` | 置 `BLOCKED`，`stop_reason` 记录阻塞原因 |

## 与其它 spec 的关系

- goal work 经 `EventManager.push_goal` / `RoundWorkItem.goal` —— `S_02`；goal 事件
  `InteractionEventType.GOAL_UPDATED` —— `S_02`。
- 评估器喂 `LoopCoordinator` —— `S_03`；`TaskCompletionRail` 挂钩 —— `S_04`。
- goal 工具（`SubmitGoalReportTool` / `GetCurrentGoalTool`）—— `S_05`。
- `GoalRecord` 是 pydantic 序列化模型（`model_dump_json` 快照路径由 core / 模型规约锚定）；token
  budget 语义与 `S_05` 的 `ModelUsageRecord` 共享字段命名。
