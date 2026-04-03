# schema.loop_event

外层任务循环的事件类型和数据定义。

---

## class DeepLoopEventType

```python
class DeepLoopEventType(str, Enum):
    FOLLOWUP = "followup"
    STEER = "steer"
    ABORT = "abort"
```

外层任务循环消费的事件类型枚举。

| 值 | 默认优先级 | 说明 |
|---|---|---|
| `FOLLOWUP` | `10` | 后续消息事件 |
| `STEER` | `1` | 引导指令事件 |
| `ABORT` | `0` | 中止事件（最高优先级） |

---

## class DeepLoopEvent

```python
@dataclass(order=True)
class DeepLoopEvent:
    priority: int
    seq: int
    created_at: float = field(default_factory=time.monotonic, compare=False)
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()), compare=False)
    event_type: DeepLoopEventType = field(default=DeepLoopEventType.FOLLOWUP, compare=False)
    content: str = field(default="", compare=False)
    task_id: Optional[str] = field(default=None, compare=False)
    metadata: Dict[str, Any] = field(default_factory=dict, compare=False)
```

排队的外层循环事件。前两个字段作为 `PriorityQueue` 的排序键。

**属性**:

| 属性 | 类型 | 说明 |
|---|---|---|
| `priority` | `int` | 优先级值，越低优先级越高 |
| `seq` | `int` | 同优先级内的 FIFO 序号 |
| `created_at` | `float` | 创建时间（单调时钟） |
| `event_id` | `str` | 唯一事件 ID |
| `event_type` | `DeepLoopEventType` | 事件类型 |
| `content` | `str` | 事件内容文本 |
| `task_id` | `Optional[str]` | 关联的任务 ID |
| `metadata` | `Dict[str, Any]` | 附加元数据 |

---

## function default_event_priority

```python
def default_event_priority(event_type: DeepLoopEventType) -> int
```

返回指定事件类型的默认队列优先级。

**参数**:

| 参数 | 类型 | 说明 |
|---|---|---|
| `event_type` | `DeepLoopEventType` | 事件类型 |

**返回值**: `int` — 默认优先级值。

---

## function create_loop_event

```python
def create_loop_event(
    *,
    seq: int,
    event_type: DeepLoopEventType,
    content: str,
    task_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    priority: Optional[int] = None,
) -> DeepLoopEvent
```

构建 `DeepLoopEvent`，省略 `priority` 时使用默认优先级。

**参数**:

| 参数 | 类型 | 说明 |
|---|---|---|
| `seq` | `int` | FIFO 序号 |
| `event_type` | `DeepLoopEventType` | 事件类型 |
| `content` | `str` | 事件内容 |
| `task_id` | `Optional[str]` | 关联的任务 ID |
| `metadata` | `Optional[Dict[str, Any]]` | 附加元数据 |
| `priority` | `Optional[int]` | 自定义优先级，为 None 时使用默认值 |

**返回值**: `DeepLoopEvent` — 构建的事件实例。
