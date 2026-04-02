# schema.task

任务状态、任务项和任务计划的 Pydantic 模型定义，用于 DeepAgent 外层任务循环。

---

## class TaskStatus

```python
class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
```

单个任务项的生命周期状态枚举。

| 值 | 说明 |
|---|---|
| `PENDING` | 待执行 |
| `IN_PROGRESS` | 执行中 |
| `COMPLETED` | 已完成 |
| `FAILED` | 已失败 |

---

## class TaskItem

```python
class TaskItem(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    title: str = ""
    description: str = ""
    status: TaskStatus = TaskStatus.PENDING
    depends_on: List[str] = Field(default_factory=list)
    result_summary: Optional[str] = None
```

`TaskPlan` 中的单个可执行任务。

**属性**:

| 属性 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `id` | `str` | 自动生成 8 字符 UUID | 唯一任务标识符 |
| `title` | `str` | `""` | 简短的祈使式标题 |
| `description` | `str` | `""` | 详细描述 |
| `status` | `TaskStatus` | `PENDING` | 当前生命周期状态 |
| `depends_on` | `List[str]` | `[]` | 必须先完成的前置任务 ID 列表 |
| `result_summary` | `Optional[str]` | `None` | 完成或失败后填写的简要总结 |

---

## class TaskPlan

```python
class TaskPlan(BaseModel):
    goal: str = ""
    tasks: List[TaskItem] = Field(default_factory=list)
    current_task_id: Optional[str] = None
```

外层任务循环的结构化任务计划。

**属性**:

| 属性 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `goal` | `str` | `""` | 高层目标描述 |
| `tasks` | `List[TaskItem]` | `[]` | 有序任务项列表 |
| `current_task_id` | `Optional[str]` | `None` | 当前正在执行的任务 ID |

---

### get_task

```python
def get_task(self, task_id: str) -> Optional[TaskItem]
```

按 ID 查找任务。

**参数**:

| 参数 | 类型 | 说明 |
|---|---|---|
| `task_id` | `str` | 任务 ID |

**返回值**: `Optional[TaskItem]` — 匹配的任务，未找到时返回 None。

---

### get_next_task

```python
def get_next_task(self) -> Optional[TaskItem]
```

返回第一个依赖已满足的 `PENDING` 状态任务。

**返回值**: `Optional[TaskItem]` — 下一个可执行的任务，无剩余任务时返回 None。

---

### add_task

```python
def add_task(self, task: TaskItem) -> None
```

追加一个任务项。

**参数**:

| 参数 | 类型 | 说明 |
|---|---|---|
| `task` | `TaskItem` | 要添加的任务 |

---

### mark_in_progress

```python
def mark_in_progress(self, task_id: str) -> None
```

将任务状态设置为 `IN_PROGRESS` 并更新 `current_task_id`。

---

### mark_completed

```python
def mark_completed(self, task_id: str, summary: str = "") -> None
```

将任务状态设置为 `COMPLETED`，记录摘要并清除 `current_task_id`。

---

### mark_failed

```python
def mark_failed(self, task_id: str, reason: str = "") -> None
```

将任务状态设置为 `FAILED`，记录原因并清除 `current_task_id`。

---

### get_progress_summary

```python
def get_progress_summary(self) -> str
```

返回进度摘要字符串（例如 `"3/7 completed"`）。

**返回值**: `str` — 进度描述。

---

### to_markdown

```python
def to_markdown(self) -> str
```

将计划渲染为 Markdown 清单，使用 `[x]`/`[~]`/`[!]`/`[ ]` 标记。

**返回值**: `str` — Markdown 格式的任务清单。

---

### to_dict / from_dict

```python
def to_dict(self) -> Dict[str, Any]
@classmethod
def from_dict(cls, data: Optional[Dict[str, Any]]) -> TaskPlan
```

JSON 友好的序列化与反序列化方法，用于会话持久化。
