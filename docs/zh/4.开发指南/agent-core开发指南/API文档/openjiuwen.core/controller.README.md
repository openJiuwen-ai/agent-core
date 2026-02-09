## openjiuwen.core.controller

`openjiuwen.core.controller` 模块提供了 **Agent 控制器及其相关的意图/任务/事件建模** 能力，当前主要通过 `legacy` 子模块对外兼容一套旧版控制器 API。

> **说明**：在 0.1.4 中，推荐优先使用 `openjiuwen.core.application.llm_agent.LLMController`、`openjiuwen.core.application.workflow_agent.WorkflowController` 等高层控制器；`openjiuwen.core.controller` 作为旧版控制器能力入口，仍然会被部分文档和代码引用（例如 `LLMController` 文档中对任务模型/事件模型的说明）。

### 对外导出的核心类型

对应源码入口：`openjiuwen.core.controller.__init__`，内部从 `legacy` 子模块导出以下几类对象：

- **控制器基类**：
  - `BaseController`：控制器抽象基类，负责接收事件、调度任务执行，是老版控制器实现（如 ReAct 风格控制器）的基础。

- **意图与任务相关类型**：
  - `IntentDetectionController`：基于意图识别的控制器实现，用于从对话/输入中识别用户意图并派发任务。
  - `IntentType`：意图类型枚举。
  - `Intent`：单个意图对象，描述“用户想做什么”。
  - `TaskQueue`：任务队列，用于管理待执行任务。
  - `Task`：任务对象，描述一次需要执行的动作（包含输入、状态等）。
  - `TaskInput`：任务输入结构。
  - `TaskStatus`：任务状态枚举（如 PENDING/RUNNING/COMPLETED/FAILED）。
  - `TaskResult`：任务执行结果。

- **推理与规划相关类型**：
  - `IntentDetector`：意图检测器，实现“从输入中识别意图”的逻辑。
  - `Planner`：规划器，实现“根据意图规划后续任务/步骤”的逻辑。

- **事件模型相关类型**：
  - `Event`：控制器事件（如用户输入事件、系统回调事件等）。
  - `EventType`：事件类型枚举。
  - `EventPriority`：事件优先级。
  - `EventSource`：事件来源。
  - `EventContent`：事件内容模型。
  - `EventContext`：事件上下文信息。
  - `SourceType`：来源类型。

- **配置相关类型**：
  - `IntentDetectionConfig`：意图识别配置。
  - `PlannerConfig`：规划器配置。
  - `ProactiveIdentifierConfig`：主动识别相关配置。
  - `ReflectorConfig`：反思/自我纠错相关配置。
  - `ReasonerConfig`：综合推理配置。

### 使用关系与推荐方式

- 对于 **新开发的业务 Agent**：
  - 推荐使用 `LLMController` / `WorkflowController` 这类封装好的控制器，它们在内部会使用 Runner、Session、ContextEngine、Memory 等新能力。
  - 若需要理解其中用到的“意图/任务/事件”等基础概念，可以参考本模块导出的这些类型。

- 对于 **旧代码迁移或阅读**：
  - 如果看到代码中直接使用 `IntentDetectionController`、`TaskQueue`、`Event` 等类型，对应的定义均来自 `openjiuwen.core.controller.legacy`，并通过当前模块统一导出。

本模块不再新增新的控制器实现，而是作为旧控制器体系的统一入口和类型定义集合，便于向后兼容和文档引用。需要构建新控制器时，请优先参考 `openjiuwen.core.application.*` 下的高层控制器实现文档。

