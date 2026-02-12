## openjiuwen.core.controller

The `openjiuwen.core.controller` module provides **Agent controllers and related intent/task/event modeling** capabilities. Currently, it mainly provides compatibility with a legacy controller API through the `legacy` submodule.

> **Note**: In version 0.1.4, it is recommended to prioritize using high-level controllers such as `openjiuwen.core.application.llm_agent.LLMController` and `openjiuwen.core.application.workflow_agent.WorkflowController`; `openjiuwen.core.controller` serves as the entry point for legacy controller capabilities and is still referenced by some documentation and code (e.g., task model/event model descriptions in `LLMController` documentation).

### Core Types Exported

Corresponding source entry: `openjiuwen.core.controller.__init__`, internally exports the following types from the `legacy` submodule:

- **Controller Base Classes**:
  - `BaseController`: Controller abstract base class, responsible for receiving events and scheduling task execution, is the foundation for legacy controller implementations (e.g., ReAct-style controllers).

- **Intent and Task Related Types**:
  - `IntentDetectionController`: Intent-based controller implementation, used to identify user intent from dialogue/input and dispatch tasks.
  - `IntentType`: Intent type enumeration.
  - `Intent`: Single intent object, describing "what the user wants to do".
  - `TaskQueue`: Task queue for managing pending tasks.
  - `Task`: Task object, describing an action to be executed (including input, state, etc.).
  - `TaskInput`: Task input structure.
  - `TaskStatus`: Task status enumeration (e.g., PENDING/RUNNING/COMPLETED/FAILED).
  - `TaskResult`: Task execution result.

- **Reasoning and Planning Related Types**:
  - `IntentDetector`: Intent detector, implementing logic for "identifying intent from input".
  - `Planner`: Planner, implementing logic for "planning subsequent tasks/steps based on intent".

- **Event Model Related Types**:
  - `Event`: Controller event (e.g., user input event, system callback event, etc.).
  - `EventType`: Event type enumeration.
  - `EventPriority`: Event priority.
  - `EventSource`: Event source.
  - `EventContent`: Event content model.
  - `EventContext`: Event context information.
  - `SourceType`: Source type.

- **Configuration Related Types**:
  - `IntentDetectionConfig`: Intent detection configuration.
  - `PlannerConfig`: Planner configuration.
  - `ProactiveIdentifierConfig`: Proactive identification related configuration.
  - `ReflectorConfig`: Reflection/self-correction related configuration.
  - `ReasonerConfig`: Comprehensive reasoning configuration.

### Usage Relationships and Recommendations

- For **newly developed business Agents**:
  - It is recommended to use encapsulated controllers such as `LLMController` / `WorkflowController`, which internally use new capabilities like Runner, Session, ContextEngine, Memory, etc.
  - If you need to understand basic concepts such as "intent/task/event" used therein, you can refer to these types exported by this module.

- For **legacy code migration or reading**:
  - If you see code directly using types like `IntentDetectionController`, `TaskQueue`, `Event`, etc., their definitions come from `openjiuwen.core.controller.legacy` and are uniformly exported through the current module.

This module no longer adds new controller implementations, but serves as a unified entry point and type definition collection for the legacy controller system, facilitating backward compatibility and documentation references. When building new controllers, please refer to the high-level controller implementation documentation under `openjiuwen.core.application.*` first.
