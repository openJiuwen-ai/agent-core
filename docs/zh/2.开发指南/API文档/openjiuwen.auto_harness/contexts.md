# openjiuwen.auto_harness.contexts

执行上下文模块，为 Auto Harness 流水线和阶段提供运行时环境。包含任务运行时依赖封装、分层执行上下文基类，以及会话级和任务级上下文实现。

子模块：
- `execution`：运行时上下文定义

---

## openjiuwen.auto_harness.contexts.execution.task_key

```python
def task_key(task: OptimizationTask) -> str
```

返回任务的制品作用域键。使用任务的 `topic` 字段，若为空则回退到 `"task"`。

**参数**：
* **task**(`OptimizationTask`)：优化任务对象。

**返回**：任务作用域的字符串键。

---

## class openjiuwen.auto_harness.contexts.execution.TaskRuntime

```python
@dataclass
class TaskRuntime:
    """Prepared task-scoped execution dependencies."""
```

预准备的任务级执行依赖集合。包含任务执行所需的相关经验、工作树路径、安全 rail、预存脏文件列表，以及各阶段 Agent 实例。

**字段**：
* **related**(`list[Experience]`)：与当前任务相关的经验记录列表。
* **wt_path**(`str`)：工作树路径。
* **edit_safety_rail**(`Any`)：编辑安全 rail 实例。
* **preexisting_dirty_files**(`list[str]`)：任务开始前已存在的脏文件列表。
* **task_agent**(`Any`)：任务实现 Agent 实例。
* **commit_agent**(`Any`)：提交阶段 Agent 实例。
* **task_session**(`Any`)：任务会话实例，默认 `None`。
* **fix_agent**(`Any`)：修复 Agent 实例，默认 `None`。

---

## class openjiuwen.auto_harness.contexts.execution.BaseExecutionContext

```python
@dataclass
class BaseExecutionContext:
    """Shared execution context surface."""
```

共享的执行上下文基类。提供制品读写、进度消息构建和阶段结果输出等通用能力，所有上下文均通过 `orchestrator` 引用访问全局制品存储。

**字段**：
* **orchestrator**(`AutoHarnessOrchestrator`)：Auto Harness 编排器引用。

### task_id

```python
@property
def task_id(self) -> str
```

当前上下文的任务标识。基类实现返回空字符串，子类可覆盖。

**返回**：任务标识字符串。

---

### get_artifact(name: str, default: Any = None) -> Any

获取制品值，委托给编排器的制品存储。

**参数**：
* **name**(`str`)：制品名称。
* **default**(`Any`)：未找到时的默认值，默认 `None`。

**返回**：制品值，或 `default`。

---

### require_artifact(name: str) -> Any

获取制品值，若不存在则抛出异常。

**参数**：
* **name**(`str`)：制品名称。

**返回**：制品值。

**异常**：`KeyError` — 制品不存在时抛出。

---

### put_artifact(name: str, value: Any) -> None

存储单个制品。

**参数**：
* **name**(`str`)：制品名称。
* **value**(`Any`)：制品值。

---

### put_artifacts(artifacts: dict[str, Any]) -> None

批量存储制品。

**参数**：
* **artifacts**(`dict[str, Any]`)：制品名称到值的映射。

---

### message(text: str, stage: str = '') -> OutputSchema

```python
@staticmethod
def message(text: str, *, stage: str = '') -> OutputSchema
```

构建进度消息 `OutputSchema`。

**参数**：
* **text**(`str`)：消息文本内容。
* **stage**(`str`)：所属阶段名称，默认为空。

**返回**：类型为 `"message"` 的 `OutputSchema` 实例。

---

### stage_result_output(stage: str, status: str, error: str = '', messages: list[str] | None = None, metrics: dict[str, Any] | None = None) -> OutputSchema

```python
@staticmethod
def stage_result_output(
    *,
    stage: str,
    status: str,
    error: str = '',
    messages: list[str] | None = None,
    metrics: dict[str, Any] | None = None,
) -> OutputSchema
```

构建阶段结果 `OutputSchema`。

**参数**：
* **stage**(`str`)：阶段名称。
* **status**(`str`)：阶段状态（如 `"success"` / `"failed"`）。
* **error**(`str`)：错误信息，默认为空。
* **messages**(`list[str] | None`)：附加消息列表，默认 `None`。
* **metrics**(`dict[str, Any] | None`)：指标数据，默认 `None`。

**返回**：类型为 `"stage_result"` 的 `OutputSchema` 实例。

---

## class openjiuwen.auto_harness.contexts.execution.SessionContext

```python
@dataclass
class SessionContext(BaseExecutionContext):
    """Runtime context passed into session pipelines and stages."""
```

传入会话级流水线和阶段的运行时上下文。继承 `BaseExecutionContext` 的全部能力，`task_id` 返回空字符串（会话级无任务作用域）。

---

## class openjiuwen.auto_harness.contexts.execution.TaskContext

```python
@dataclass
class TaskContext(SessionContext):
    """Runtime context passed into task pipelines and stages."""
```

传入任务级流水线和阶段的运行时上下文。继承 `SessionContext`，额外持有当前任务和运行时依赖，`task_id` 通过 `task_key(task)` 计算。

**字段**：
* **task**(`OptimizationTask`)：当前优化任务。
* **runtime**(`TaskRuntime`)：预准备的任务运行时依赖。

### task_id

```python
@property
def task_id(self) -> str
```

返回当前任务的制品作用域键，等价于 `task_key(self.task)`。

**返回**：任务标识字符串。
