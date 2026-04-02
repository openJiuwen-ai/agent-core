# schema.state

## class DeepAgentState

```python
@dataclass
class DeepAgentState:
    iteration: int = 0
    task_plan: Optional[TaskPlan] = None
    stop_condition_state: Optional[Dict[str, Any]] = None
    pending_follow_ups: List[str] = field(default_factory=list)
```

每次调用的可变状态。对象在 `invoke`/`stream` 请求运行期间存活于 `ctx.session` 上。可序列化子集可检查点到会话状态。

**属性**:

| 属性 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `iteration` | `int` | `0` | 当前迭代计数 |
| `task_plan` | `Optional[TaskPlan]` | `None` | 当前任务计划 |
| `stop_condition_state` | `Optional[Dict[str, Any]]` | `None` | 停止条件评估器的持久化状态 |
| `pending_follow_ups` | `List[str]` | `[]` | 待处理的后续消息列表 |

---

### to_session_dict

```python
def to_session_dict(self) -> Dict[str, Any]
```

转换为 JSON 友好的字典，用于会话持久化。

**返回值**: `Dict[str, Any]` — 包含 `iteration`、`task_plan`、`stop_condition_state`、`pending_follow_ups` 键的字典。

---

### from_session_dict

```python
@classmethod
def from_session_dict(
    cls,
    data: Optional[Dict[str, Any]],
) -> DeepAgentState
```

从会话快照构建状态。

**参数**:

| 参数 | 类型 | 说明 |
|---|---|---|
| `data` | `Optional[Dict[str, Any]]` | 先前导出的快照字典 |

**返回值**: `DeepAgentState` — 恢复的状态实例。
