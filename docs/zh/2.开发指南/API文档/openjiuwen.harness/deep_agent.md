# deep_agent

## class openjiuwen.harness.DeepAgent

```python
class DeepAgent(BaseAgent):
    def __init__(self, card: AgentCard): ...
```

高层智能体，委托内部 `ReActAgent` 执行。支持单轮调用和外层任务循环两种模式。Rails 在首次 `invoke()` 时延迟异步注册。

**属性**:

| 属性 | 类型 | 说明 |
|---|---|---|
| `deep_config` | `DeepAgentConfig` | 当前运行时配置 |
| `react_agent` | `Optional[ReActAgent]` | 内部 ReActAgent 实例 |
| `loop_coordinator` | `Optional[LoopCoordinator]` | 外层任务循环的循环协调器 |
| `loop_controller` | `Optional[TaskLoopController]` | 外层任务循环的循环控制器 |
| `event_queue` | `Optional[EventQueue]` | 当前任务循环的事件队列 |
| `event_handler` | `Optional[TaskLoopEventHandler]` | 当前任务循环的事件处理器 |
| `is_initialized` | `bool` | 延迟 Rail 初始化是否完成 |
| `is_invoke_active` | `bool` | 当前是否有活跃的 invoke 调用 |
| `is_auto_invoke_scheduled` | `bool` | 是否已调度自动 invoke |
| `system_prompt_builder` | `Optional[SystemPromptBuilder]` | 系统提示词构建器 |

---

### configure

```python
def configure(self, config: DeepAgentConfig) -> DeepAgent
```

应用配置并重建内部 ReActAgent。首次调用执行初始设置，后续调用执行热重载。

**参数**:

| 参数 | 类型 | 说明 |
|---|---|---|
| `config` | `DeepAgentConfig` | 运行时配置对象 |

**返回值**: `DeepAgent` — 当前实例（支持链式调用）。

---

### invoke

```python
async def invoke(
    self,
    inputs: Any,
    session: Optional[Session] = None,
) -> Dict[str, Any]
```

执行 DeepAgent，支持单轮模式或任务循环模式。

**参数**:

| 参数 | 类型 | 说明 |
|---|---|---|
| `inputs` | `Any` | 用户输入，支持 `str`、`dict`（含 `query` 键）或 `InteractiveInput` |
| `session` | `Optional[Session]` | 会话实例。任务循环模式下必填 |

**返回值**: `Dict[str, Any]` — 执行结果字典，包含 `output`、`result_type` 等键。

---

### stream

```python
async def stream(
    self,
    inputs: Any,
    session: Optional[Session] = None,
    stream_modes: Optional[List[StreamMode]] = None,
) -> AsyncIterator[Any]
```

流式执行 DeepAgent，支持单轮模式或任务循环模式。

**参数**:

| 参数 | 类型 | 说明 |
|---|---|---|
| `inputs` | `Any` | 用户输入，格式同 `invoke` |
| `session` | `Optional[Session]` | 会话实例。任务循环模式下必填 |
| `stream_modes` | `Optional[List[StreamMode]]` | 流模式过滤器 |

**返回值**: `AsyncIterator[Any]` — `OutputSchema` 数据块（`llm_reasoning` / `llm_output` / `answer`）。

---

### follow_up

```python
async def follow_up(
    self,
    msg: str,
    task_id: Optional[str] = None,
    session: Optional[Session] = None,
) -> None
```

向 `FOLLOW_UP` 主题发布 `FollowUpEvent`。事件与 `INPUT` 并发处理，不阻塞当前迭代。处理器将消息推入 `LoopQueues.follow_up`，外层循环在每次迭代后排空。

**参数**:

| 参数 | 类型 | 说明 |
|---|---|---|
| `msg` | `str` | 后续消息文本 |
| `task_id` | `Optional[str]` | 关联的任务 ID |
| `session` | `Optional[Session]` | 当前会话 |

---

### steer

```python
async def steer(
    self,
    msg: str,
    session: Optional[Session] = None,
) -> None
```

向 `TASK_INTERACTION` 主题发布 `TaskInteractionEvent`。事件与 `INPUT` 并发处理，处理器将消息推入 `LoopQueues.steering`，执行器在下次内部 invoke 前排空。

**参数**:

| 参数 | 类型 | 说明 |
|---|---|---|
| `msg` | `str` | 引导指令文本 |
| `session` | `Optional[Session]` | 当前会话 |

---

### abort

```python
async def abort(
    self,
    session: Optional[Session] = None,
) -> None
```

请求立即中止任务循环。在协调器上设置中止标志并调用事件处理器的 `on_abort()` 方法。

**参数**:

| 参数 | 类型 | 说明 |
|---|---|---|
| `session` | `Optional[Session]` | 当前会话（未使用） |

---

### add_rail

```python
def add_rail(self, rail: AgentRail) -> DeepAgent
```

同步将 Rail 加入待注册队列。添加 `TaskCompletionRail` 时会替换先前排队的同类实例。

**参数**:

| 参数 | 类型 | 说明 |
|---|---|---|
| `rail` | `AgentRail` | 要注册的 Rail 实例 |

**返回值**: `DeepAgent` — 当前实例（支持链式调用）。

---

### register_rail

```python
async def register_rail(self, rail: AgentRail) -> DeepAgent
```

异步注册 Rail，执行选择性路由。

**参数**:

| 参数 | 类型 | 说明 |
|---|---|---|
| `rail` | `AgentRail` | 要注册的 Rail 实例 |

**返回值**: `DeepAgent` — 当前实例。

---

### unregister_rail

```python
async def unregister_rail(self, rail: AgentRail) -> DeepAgent
```

从待注册队列、外层和内层智能体中注销 Rail。

**参数**:

| 参数 | 类型 | 说明 |
|---|---|---|
| `rail` | `AgentRail` | 要注销的 Rail 实例 |

**返回值**: `DeepAgent` — 当前实例。

---

### load_state

```python
def load_state(self, session: Session) -> DeepAgentState
```

从会话加载 DeepAgent 运行时状态。优先返回缓存的运行时对象；否则从持久化会话状态加载并预热缓存。

**参数**:

| 参数 | 类型 | 说明 |
|---|---|---|
| `session` | `Session` | 当前会话 |

**返回值**: `DeepAgentState` — 当前状态（永不为 None）。

---

### save_state

```python
def save_state(
    self,
    session: Session,
    state: Optional[DeepAgentState] = None,
) -> None
```

将 DeepAgent 状态持久化到会话。提供 `state` 时使用新快照；否则使用缓存状态。

**参数**:

| 参数 | 类型 | 说明 |
|---|---|---|
| `session` | `Session` | 当前会话 |
| `state` | `Optional[DeepAgentState]` | 要保存的状态；为 None 时使用缓存 |

---

### clear_state

```python
def clear_state(
    self,
    session: Session,
    clear_persisted: bool = False,
) -> None
```

清除会话上的 DeepAgent 运行时缓存。

**参数**:

| 参数 | 类型 | 说明 |
|---|---|---|
| `session` | `Session` | 当前会话 |
| `clear_persisted` | `bool` | 为 True 时同时清除持久化快照 |

---

### create_subagent

```python
def create_subagent(
    self,
    subagent_type: str,
    subsession_id: str,
) -> DeepAgent
```

创建子智能体实例。由 `TaskTool` 和 `SessionSpawnExecutor` 共享。

**参数**:

| 参数 | 类型 | 说明 |
|---|---|---|
| `subagent_type` | `str` | 子智能体类型（如 `"general-purpose"`） |
| `subsession_id` | `str` | 子智能体的会话 ID |

**返回值**: `DeepAgent` — 已配置的 DeepAgent 实例。

---

### init_workspace

```python
async def init_workspace(self) -> None
```

使用目录结构和默认内容初始化工作区。`root_path` 在 `configure()` 中预先计算，此处仅创建目录结构。
