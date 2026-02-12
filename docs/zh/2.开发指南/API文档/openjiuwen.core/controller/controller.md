# openjiuwen.core.controller

## class openjiuwen.core.controller.schema.FileDataFrame

文件数据帧，用于在事件、任务输入/输出中传输文件。

- **type** (Literal["file"])：数据帧类型，固定为`file`。
- **name** (str)：文件名。
- **mimeType** (str)：MIME类型，例如 `image/png`、`application/pdf`。
- **bytes** (Optional[bytes])：文件二进制内容；与`uri`互斥，二选一。
- **uri** (Optional[str])：文件的URI（本地路径或远端地址）；与`bytes`互斥。

## class openjiuwen.core.controller.schema.dataframe.JsonDataFrame

表示 JSON 格式的数据帧，用于在事件、任务输入/输出中传递结构化数据（如配置、API 响应等）。

- **type** (Literal["json"])：数据帧类型，固定为`json`。
- **data** (Dict[str, Any])：JSON字典内容。

## class openjiuwen.core.controller.schema.dataframe.TextDataFrame

表示文本类型的数据帧，用于在事件、任务输入/输出的流式或批量场景中传递纯文本内容（如用户输入、提示词片段、日志片段等）。

- **type** (Literal["text"])：数据帧类型，固定为`text`。
- **text** (str)：文本内容。

## class openjiuwen.core.controller.schema.DataFrame

数据帧联合类型，涵盖`TextDataFrame`、`FileDataFrame`、`JsonDataFrame`。

## class openjiuwen.core.controller.schema.Event

事件基类，描述控制器处理的通用事件。

- **event_type** (EventType)：事件类型。
- **event_id** (str)：事件唯一标识，默认生成UUID。
- **metadata** (Optional[Dict[str, Any]])：事件元数据。

## class openjiuwen.core.controller.schema.EventType

事件类型枚举，定义了控制器处理的四类事件标识。

- **INPUT**：用户输入事件。
- **TASK_INTERACTION**：任务执行过程中的交互事件。
- **TASK_COMPLETION**：任务完成事件。
- **TASK_FAILED**：任务失败事件。

## class openjiuwen.core.controller.schema.InputEvent

用户输入事件，作为控制器主要入口。

- **event_type**(EventType)：固定为`EventType.INPUT`。
- **input_data** (List[DataFrame])：输入载荷列表，支持Text/File/JSON数据帧。

## class openjiuwen.core.controller.schema.TaskInteractionEvent

任务执行中需要用户交互时产生的事件。

- **event_type**(EventType)：固定为`EventType.TASK_INTERACTION`。
- **interaction** (List[DataFrame])：交互内容数据帧列表。
- **task** (Optional[Task])：关联任务对象。

## class openjiuwen.core.controller.schema.TaskCompletionEvent

任务完成事件，携带输出结果。

- **event_type**(EventType)：固定为`EventType.TASK_COMPLETION`。
- **task_result** (List[DataFrame])：任务输出数据帧列表。
- **task** (Optional[Task])：关联任务对象。

## class openjiuwen.core.controller.schema.TaskFailedEvent

任务失败事件，携带错误信息。

- **event_type**(EventType)：固定为`EventType.TASK_FAILED`。
- **error_message** (Optional[str])：错误描述。
- **task** (Optional[Task])：关联任务对象。

## class openjiuwen.core.controller.schema.ControllerOutputPayload

控制器输出载荷。

- **type** (Literal[EventType.TASK_COMPLETION, EventType.TASK_INTERACTION, EventType.TASK_FAILED,
                    TASK_PROCESSING, ALL_TASKS_PROCESSED])：输出类型。
- **data** (List[DataFrame])：输出数据帧列表。
- **metadata** (Optional[Dict[str, Any]])：附加元数据。

## class openjiuwen.core.controller.schema.ControllerOutputChunk

流式输出块。

- **index** (int)：输出序号。
- **type** (str)：固定为`"controller_output"`。
- **payload** (ControllerOutputPayload)：输出载荷。
- **last_chunk** (bool)：是否为最后一个块。

## class openjiuwen.core.controller.schema.ControllerOutput

批量输出结果。

- **type** (Literal[EventType.TASK_COMPLETION, EventType.TASK_INTERACTION, EventType.TASK_FAILED, TASK_PROCESSING])：输出类型。
- **data** (List[ControllerOutputChunk] | Dict)：输出内容，支持块列表或字典。
- **input_event_id** (Optional[str])：关联的输入事件ID。

## class openjiuwen.core.controller.schema.IntentType

意图类型枚举，表示不同的意图类别。

- **CREATE_TASK**：创建新任务（若有正在执行的任务会先中断）。
- **PAUSE_TASK**：暂停当前执行的任务。
- **RESUME_TASK**：恢复之前暂停的任务（状态从paused变为submitted）。
- **CONTINUE_TASK**：基于已完成任务继续执行，新的任务依赖完成任务的上下文。
- **SUPPLEMENT_TASK**：为需要用户补充信息的任务提供补充后继续执行。
- **CANCEL_TASK**：取消当前执行的任务。
- **MODIFY_TASK**：修改正在执行任务的参数/配置，修改后重新提交执行。
- **SWITCH_TASK**：中断当前任务并执行新的任务。
- **UNKNOWN_TASK**：未识别的意图，需要向用户澄清。

## class openjiuwen.core.controller.schema.Intent

单个意图对象，描述“用户想做什么”，并包含执行该意图所需的上下文信息。

- **intent_type** (IntentType)：意图类型。
- **event** (Event)：关联的输入事件。
- **target_task_id** (Optional[str])：目标任务ID。
- **target_task_description** (Optional[str])：任务描述（CREATE_TASK/SWITCH_TASK必需）。
- **depend_task_id** (List[str])：依赖任务 ID 列表（CONTINUE_TASK必需）。
- **supplementary_info** (Optional[str])：补充信息（SUPPLEMENT_TASK必需）。
- **modification_details** (Optional[str])：修改内容（MODIFY_TASK必需）。
- **confidence** (float)：置信度，必须在0.0–1.0之间，默认值1.0。
- **metadata** (Optional[Dict[str, Any]])：附加元数据，默认空字典。
- **clarification_prompt** (Optional[str])：澄清提示（UNKNOWN_TASK必需）。

## class openjiuwen.core.controller.schema.TaskStatus

任务状态枚举：
- **SUBMITTED**：已提交，等待执行。
- **WORKING**：执行中。
- **INPUT_REQUIRED**：需要用户输入/补充信息。
- **COMPLETED**：已完成。
- **FAILED**：执行失败。
- **PAUSED**：已暂停，可恢复。
- **CANCELED**：已取消。
- **WAITING**：等待中（如依赖任务未完成）。
- **UNKNOWN**：未知状态。

## class openjiuwen.core.controller.schema.Task

任务数据模型，描述任务的标识、状态及输入输出。

- **session_id** (str)：所属会话ID，不能为空。
- **task_id** (str)：任务ID，不能为空，且不能与`parent_task_id`相同。
- **task_type** (str)：任务类型，用于选择执行器，不能为空。
- **description** (Optional[str])：任务描述。
- **priority** (int)：优先级，数值越小优先级越高。取值范围大于等于零。
- **inputs** (Optional[List[Event]])：关联的输入事件列表。
- **outputs** (List[ControllerOutputChunk])：流式输出块列表。
- **status** (TaskStatus)：任务状态。若为FAILED必须提供非空`error_message`；若为INPUT_REQUIRED必须提供`input_required_fields`。
- **parent_task_id** (Optional[str])：父任务ID。不能等于`task_id`。
- **context_id** (Optional[str])：上下文关联ID。
- **input_required_fields** (Optional[Union[Dict[str, Any], BaseModel]])：若状态为`input-required`时所需字段定义。
- **error_message** (Optional[str])：失败时的错误信息。
- **metadata** (Optional[Dict[str, Any]])：附加元数据。
- **extensions** (Optional[Dict[str, Any]])：扩展字段。

## class openjiuwen.core.controller.modules.EventHandlerInput

事件处理器的入参包装。

- **event** (Event)：事件对象。
- **session** (Session)：会话对象。
- **model_config**：`{"arbitrary_types_allowed": True}`，允许保存非Pydantic类型（如Session）。

## class openjiuwen.core.controller.modules.EventHandler

```
class openjiuwen.core.controller.modules.EventHandler()
```

抽象事件处理器，定义控制器对四类事件的处理接口。依赖项（config、context_engine、ability_manager、task_manager、task_scheduler）由控制器通过属性注入，无需在构造函数中传参。

### abstractmethod async handle_input(inputs: EventHandlerInput) -> Optional[Dict]

处理用户输入事件。

**参数**：

* **inputs**(EventHandlerInput)：包装了`event`与`session`的入参。

**返回**：

`Optional[Dict]`，具体由子类实现。

### abstractmethod async handle_task_interaction(inputs: EventHandlerInput) -> Optional[Dict]

处理任务交互事件（TaskInteractionEvent）。

**参数**：

* **inputs**(EventHandlerInput)：事件与会话。

**返回**：

`Optional[Dict]`，具体由子类实现。

### abstractmethod async handle_task_completion(inputs: EventHandlerInput) -> Optional[Dict]

处理任务完成事件（TaskCompletionEvent）。

**参数**：

* **inputs**(EventHandlerInput)：事件与会话。

**返回**：

`Optional[Dict]`，具体由子类实现。

### abstractmethod async handle_task_failed(inputs: EventHandlerInput) -> Optional[Dict]

处理任务失败事件（TaskFailedEvent）。

**参数**：

* **inputs**(EventHandlerInput)：事件与会话。

**返回**：

`Optional[Dict]`，具体由子类实现。

## class openjiuwen.core.controller.modules.EventQueue

```
class openjiuwen.core.controller.modules.EventQueue(config: ControllerConfig)
```

内存消息队列封装，用于控制器的事件发布与订阅。

**参数**：

* **config**(ControllerConfig)：控制器配置，决定事件队列大小（`event_queue_size`）与超时时间（`event_timeout`）。默认值：无（必须显式传入）。

### set_event_handler(event_handler: EventHandler) -> None

注册事件处理器，订阅前必须调用。

**参数：**

* **event_handler**(EventHandler)：事件处理器实例。

### start() -> None

启动底层消息队列的后台消费任务。

### async stop() -> None

停止底层消息队列的后台消费任务。

### async subscribe(agent_id: str, session_id: str) -> tuple[dict[str, str], dict[str, str]]

为指定agent+session订阅四类事件：INPUT、TASK_INTERACTION、TASK_COMPLETION、TASK_FAILED。

**参数：**

* **agent_id**(str)：Agent标识。
* **session_id**(str)：会话标识。

**返回：**

**tuple[dict[str, str], dict[str, str]]**，依次为订阅实例映射、topic映射；topic格式为`{agent_id}_{session_id}_{event_type}`。

### async unsubscribe(agent_id: str, session_id: str) -> dict

退订上述四类事件。

**参数：**

* **agent_id**(str)：Agent标识。
* **session_id**(str)：会话标识。

**返回：**

**dict**，退订的topic映射。

### async publish_event(agent_id: str, session: Session, event: Event) -> None

将事件发布到队列并阻塞等待处理完成，保证事件顺序。

**参数：**

* **agent_id**(str)：Agent标识。
* **session**(Session)：会话对象，用于获取`session_id`。
* **event**(Event)：待发布事件。

**异常：**

* 事件处理失败时会抛出`AGENT_CONTROLLER_EVENT_HANDLER_ERROR`或`AGENT_CONTROLLER_EVENT_QUEUE_ERROR`封装的九问异常。

### async unsubscribe_all() -> None

退订所有topic，通常在控制器关闭时调用。

## class openjiuwen.core.controller.modules.TaskManager

```
class openjiuwen.core.controller.modules.TaskManager(config: ControllerConfig)
```

任务管理器，负责任务的存储、索引与状态管理（CRUD、优先级、父子关系等），并支持状态持久化/恢复。

**参数：**

* **config**(ControllerConfig)：控制器配置，初始化任务管理策略。默认值：无（必须显式传入）。

### async get_state() -> TaskManagerState

获取当前任务管理器的可序列化状态。

**返回：**

**TaskManagerState**，包含任务、优先级索引、父子关系、根任务集合等。

### async load_state(state: TaskManagerState) -> None

加载任务管理状态。

**参数：**

* **state**(TaskManagerState)：待恢复的状态对象。


### async clear_state() -> None

清空任务与索引。


### async add_task(task: Task | List[Task]) -> None

新增任务（可批量）。

**参数：**

* **task**(Task | List[Task])：单个任务或任务列表。

### async get_task(task_filter: TaskFilter) -> List[Task]

按过滤条件查询任务。

**参数：**

* **task_filter**(TaskFilter)：过滤条件，需至少指定 `task_id`/`session_id`/`user_id`/`priority`/`status` 之一或 `is_root=True`。

**返回：**

**List[Task]**，匹配的任务列表。

### async update_task(task: Task) -> None

更新任务。

**参数：**

* **task**(Task)：要更新的任务对象。


### async remove_task(task_filter: TaskFilter) -> List[Task]

按过滤条件删除任务。

**参数：**

* **task_filter**(TaskFilter)：删除条件。

**返回：**

**List[Task]**，被删除的任务列表。

### async pop_task(task_filter: TaskFilter) -> Task | None

按过滤条件弹出一个任务。

**参数：**

* **task_filter**(TaskFilter)：筛选条件。

**返回：**

Task | None，匹配并移除的任务。


## class openjiuwen.core.controller.modules.TaskManagerState

`TaskManager` 的可序列化状态对象。

- **tasks** (Dict[str, Task])：任务快照。
- **priority_index** (Dict[int, List[str]])：按优先级索引的任务 ID 列表。
- **parent_to_children** (Dict[str, Set[str]])：父子任务映射。
- **children_to_parent** (Dict[str, str])：子父任务映射。
- **root_tasks** (Set[str])：根任务集合。

## class openjiuwen.core.controller.modules.TaskFilter

任务过滤条件模型。

- **task_id** (Optional[str | List[str]])：按任务 ID 或 ID 列表过滤。
- **session_id** (Optional[str])：按会话 ID 过滤。
- **user_id** (Optional[str])：按用户 ID 过滤。
- **priority** (Optional[int | Literal["highest"]])：按优先级过滤，可指定 `"highest"`。
- **status** (Optional[TaskStatus])：按任务状态过滤。
- **with_children** (bool)：是否包含子任务。
- **is_root** (bool)：是否仅查询根任务。

## class openjiuwen.core.controller.modules.TaskExecutor(dependencies: TaskExecutorDependencies)

抽象任务执行器，不同 `task_type` 的执行器需继承并实现。依赖通过 `TaskExecutorDependencies` 注入（包含 config、ability_manager、context_engine、task_manager、event_queue）。

**参数：**

* **dependencies**(TaskExecutorDependencies)：执行器依赖集合，必须显式传入。

### abstractmethod async execute(task_id: str, session: Session) -> AsyncIterator[ControllerOutputChunk]

执行任务，产出流式输出块。

**参数：**

* **task_id**(str)：任务ID。
* **session**(Session)：会话对象。

**返回：**

**AsyncIterator[ControllerOutputChunk]**，执行过程中产生的输出流。

### abstractmethod async can_pause(task_id: str, session: Session) -> tuple[bool, str]

检查任务是否可暂停。

**参数：**

* **task_id**(str)：任务ID。
* **session**(Session)：会话对象。

**返回：**

**tuple[bool, str]**，是否可暂停及不可暂停原因。

### abstractmethod async pause(task_id: str, session: Session) -> bool

暂停任务。

**参数：**

* **task_id**(str)：任务ID。
* **session**(Session)：会话对象。

**返回：**

**bool**，是否暂停成功。

### abstractmethod async can_cancel(task_id: str, session: Session) -> tuple[bool, str]

检查任务是否可取消。

**参数：**

* **task_id**(str)：任务ID。
* **session**(Session)：会话对象。

**返回：**

**tuple[bool, str]**，是否可取消及不可取消原因。

### abstractmethod async cancel(task_id: str, session: Session) -> bool

取消任务。

**参数：**

* **task_id**(str)：任务ID。
* **session**(Session)：会话对象。

**返回：**

**bool**，是否取消成功。

## class openjiuwen.core.controller.modules.TaskExecutorRegistry

```
class openjiuwen.core.controller.modules.TaskExecutorRegistry()
```

任务执行器注册表，按 `task_type` 管理执行器的构建函数。

### add_task_executor(task_type: str, builder: Callable[[TaskExecutorDependencies], TaskExecutor]) -> None

注册执行器构建函数。

**参数：**

* **task_type**(str)：任务类型标识。
* **builder**(Callable)：接收`TaskExecutorDependencies`并返回`TaskExecutor`实例的工厂函数。


### remove_task_executor(task_type: str) -> None

移除指定任务类型的执行器构建函数（若不存在则忽略）。

**参数：**

* **task_type**(str)：任务类型标识。


### get_task_executor(task_type: str, dependencies: TaskExecutorDependencies) -> TaskExecutor

按任务类型构建并返回执行器实例，若未注册对应类型会抛出异常。

**参数：**

* **task_type**(str)：任务类型标识。
* **dependencies**(TaskExecutorDependencies)：执行器依赖集合。

**返回：**

**TaskExecutor**，对应任务类型的执行器实例。

## class openjiuwen.core.controller.modules.TaskScheduler

```
class openjiuwen.core.controller.modules.TaskScheduler(config: ControllerConfig,
                                                                      task_manager: TaskManager,
                                                                      context_engine: ContextEngine,
                                                                      ability_manager: AbilityManager,
                                                                      event_queue: EventQueue,
                                                                      card: AgentCard)
```

任务调度器，负责调度/执行/暂停/取消任务，并流式产出执行结果。

**参数：**

* **config**(ControllerConfig)：控制器配置。默认值：无（必须显式传入）。
* **task_manager**(TaskManager)：任务管理器实例。
* **context_engine**(ContextEngine)：上下文引擎。
* **ability_manager**(AbilityManager)：能力管理器（工具/工作流等）。
* **event_queue**(EventQueue)：事件队列，用于发布任务相关事件。
* **card**(AgentCard)：Agent 卡片信息，用于事件发布标识。

### async start() -> None

启动后台调度循环`schedule`，开始周期性扫描`SUBMITTED`任务。

### async stop() -> None

停止调度器：取消未完成任务、终止调度循环并等待清理。

### async schedule() -> None

调度主循环：按`schedule_interval`拉取`SUBMITTED`任务；在`max_concurrent_tasks`限制内为每个任务创建异步执行包装`_execute_task_wrapper`；无任务时短暂休眠；停止时等待全部运行任务完成。

### async execute_task(task_id: str, session: Session) -> None

执行单个任务主流程：获取任务→创建执行器→置状态`WORKING`→迭代`execute_ability`流式输出→根据`payload.type`更新状态并发布事件。

**参数：**

* **task_id**(str)：任务ID。
* **session**(Session)：会话对象。

**异常：**

* `AGENT_CONTROLLER_TASK_EXECUTION_ERROR`:任务不存在或执行器缺失。

### async pause_task(task_id: str) -> bool

若任务运行中则调用执行器`can_pause`/`pause`并取消对应`asyncio.Task`，状态更新为`PAUSED`。

**参数：**

* **task_id**(str)：任务ID。

**返回：**

**bool**，是否暂停成功。

### async cancel_task(task_id: str) -> bool

与`pause_task`类似，调用执行器`can_cancel`/`cancel`后取消协程并将状态置为`CANCELED`。

**参数：**

* **task_id**(str)：任务ID。

**返回：**

**bool**，是否取消成功。


## class openjiuwen.core.controller.modules.IntentRecognizer

```
class openjiuwen.core.controller.modules.IntentRecognizer(config: ControllerConfig,
                                                                            task_manager: TaskManager,
                                                                            ability_manager: AbilityManager,
                                                                            context_engine: ContextEngine)
```

意图识别器，负责从输入事件中识别用户意图并返回 `Intent` 列表。

**参数：**

* **config**(ControllerConfig)：控制器配置，需显式传入。
* **task_manager**(TaskManager)：任务管理器。
* **ability_manager**(AbilityManager)：能力管理器。
* **context_engine**(ContextEngine)：上下文引擎。

### async recognize(event: Event, session: Session) -> List[Intent]

识别输入意图。

**参数：**

* **event**(Event)：输入事件，仅支持单条`InputEvent`，且`input_data` 需为单个`TextDataFrame`；包含文件/JSON或多条文本会抛错。
* **session**(Session)：会话对象。

**返回：**

**List[Intent]**，识别出的意图列表（基于`intent_llm_id`模型和`intent_confidence_threshold`,通过`IntentToolkits`解析）。

### async _prepare_user_message(self, query)
 基于当前任务列表和用户原始输入组装提示词，供意图识别 LLM 使用。

**参数：**

* **query**(str)：用户原始输入文本。

**返回：**

**UserMessage**：可直接送入 LLM 的用户消息。


## class openjiuwen.core.controller.modules.EventHandlerWithIntentRecognition

```
class openjiuwen.core.controller.modules.EventHandlerWithIntentRecognition()
```

带意图识别的事件处理器封装，先识别`Intent`再分派业务处理。依赖（config、context_engine、task_manager、ability_manager、task_scheduler、IntentRecognizer）由控制器注入。

### handle_input(inputs: EventHandlerInput) -> Any

识别输入意图并按类型分派到创建/暂停/恢复/续接/补充/取消/修改/未知任务的处理函数。

**参数：**

* **inputs**(EventHandlerInput)：事件与会话。

**返回：**

**Any**，聚合各处理协程的执行结果。

### handle_task_interaction(inputs: EventHandlerInput) -> Any

校验事件类型为 `TaskInteractionEvent`，将交互内容写入会话流。

**参数：**

* **inputs**(EventHandlerInput)：事件与会话。

**返回：**

**Any**，写流结果。

### handle_task_completion(inputs: EventHandlerInput) -> Any

校验事件类型为 `TaskCompletionEvent`，将结果写入会话流。

**参数：**

* **inputs**(EventHandlerInput)：事件与会话。

**返回：**

**Any**，写流结果。

### handle_task_failed(inputs: EventHandlerInput) -> Any

校验事件类型为 `TaskFailedEvent`，将错误信息写入会话流。

**参数：**

* **inputs**(EventHandlerInput)：事件与会话。

**返回：**

**Any**，写流结果。

## class openjiuwen.core.controller.ControllerConfig


控制器配置，覆盖调度、任务、事件队列与意图识别参数。

- **max_concurrent_tasks** (int)：并发任务上限，默认值为5，0表示无上限。
- **schedule_interval** (float)：调度扫描间隔（秒），默认值为1.0。
- **task_timeout** (Optional[float])：任务超时（秒，下限600），默认值为None。
- **default_task_priority** (int)：默认任务优先级，默认值为1。
- **enable_task_persistence** (bool)：是否启用任务状态持久化，默认值为False。
- **event_queue_size** (Optional[int])：事件队列大小，默认值为10000。
- **event_timeout** (Optional[float])：事件处理超时（秒），默认值为300。
- **enable_intent_recognition** (bool)：是否启用意图识别，默认值为False。
- **intent_llm_id** (str)：意图识别LLM ID。
- **intent_confidence_threshold** (float)：意图置信度阈值，默认值为0.7。
- **intent_type_list** (List[str])：支持的意图类型列表。

## class openjiuwen.core.controller.base.Controller

```
class openjiuwen.core.controller.base.Controller()
```

控制器主体，负责事件处理与任务生命周期管理，是 ControllerAgent 的核心组件。依赖（AgentCard、ControllerConfig、AbilityManager、ContextEngine、EventHandler 等）通过 `init` 与属性注入完成。

### set_event_handler(event_handler: EventHandler) -> None

绑定事件处理器，并将 config/context_engine/task_scheduler/task_manager/ability_manager 注入。

**参数：**

* **event_handler**(EventHandler)：事件处理器实例。

### add_task_executor(task_type: str, task_executor_builder: Callable[[TaskExecutorDependencies], TaskExecutor]) -> Controller

注册指定任务类型的执行器构建器，返回 self 便于链式调用。

**参数：**

* **task_type**(str)：任务类型标识。
* **task_executor_builder**(Callable[[TaskExecutorDependencies], TaskExecutor])：执行器构建函数。

**返回：**

* **Controller**：当前控制器实例（链式调用）。

### remove_task_executor(task_type: str) -> None

移除指定任务类型的执行器。

**参数：**

* **task_type**(str)：任务类型标识。

### async start() -> None

启动调度器与事件队列，负责事件订阅循环的开启。

### async stop() -> None

停止调度器与事件队列，负责事件订阅循环的关闭。

### async invoke(inputs: InputEvent, session: Session, **kwargs) -> ControllerOutput

批量执行入口：内部调用 `stream` 收集 `BaseStreamMode.OUTPUT` 块并聚合为 `ControllerOutput` 返回。

**参数：**

* **inputs**(InputEvent)：用户输入事件。
* **session**(Session)：会话对象。
* **kwargs**：其他可选参数。

**返回：**

* **ControllerOutput**：聚合后的控制器输出。

### async stream(inputs: InputEvent, session: Session, stream_modes: Optional[List[StreamMode]]=None, **kwargs) -> AsyncIterator[ControllerOutputChunk]

流式执行入口：恢复/保存 TaskManager 状态，注册会话，订阅事件，发布输入事件，按需逐块产出 `ControllerOutputChunk`；完成后退订但保留控制器状态便于复用。

**参数：**

* **inputs**(InputEvent)：用户输入事件。
* **session**(Session)：会话对象。
* **stream_modes**(List[StreamMode],可选)：期望的流式模式，默认 `BaseStreamMode.OUTPUT`。
* **kwargs**：其他可选参数。

**返回：**

* **AsyncIterator[ControllerOutputChunk]**：控制器流式输出块迭代器。