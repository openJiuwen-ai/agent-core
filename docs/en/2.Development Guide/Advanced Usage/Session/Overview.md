`Session` is the core execution engine and management framework for Agents and Workflows, providing an isolated and stable runtime environment for task execution. By abstracting the complexity of underlying infrastructure, `Session` achieves unified scheduling and governance of the entire task lifecycle.

The core advantages of `Session` include:

* **Stateful session management**: Maintains complete runtime and intermediate state during task execution, supports transactional operations and checkpoint resumption, ensuring consistency and reliability of complex tasks.
* **Structured message streaming**: Natively supports real-time streaming of text and structured intermediate results (such as reasoning steps, tool call events), providing an immersive interactive experience for frontends.
* **Controlled execution flow**: Provides standardized mechanisms for interruption, pause, rollback, and continuation of execution, giving developers and systems unprecedented fine-grained control over long-running tasks.
* **Deep observability**: Automatically tracks and records end-to-end telemetry data from reasoning chains, tool calls to API requests, providing the only trusted source for performance profiling, cost auditing, and root cause analysis.

## Basic Functions

* **State Management**: `Session` is responsible for managing execution state data. During Agent or Workflow execution, data read and write operations can be performed through interfaces provided by `Session`.
* **Debugging Capability**: `Session` has a built-in end-to-end debugging module that provides real-time, hierarchical runtime insights for Workflow execution. This module transparently presents the system's internal state to developers through automated fine-grained event collection and open extension interfaces.
* **Streaming Output**: `Session` has a built-in high-performance structured streaming output module that implements the functionality of streaming data in real time to the outside of workflows or Agents, providing efficient and real-time data processing and transmission capabilities for workflow and Agent execution processes.
* **Interruption and Recovery**: `Session` has built-in interruption and recovery capabilities. When execution needs to be resumed, the system automatically loads saved snapshot data to achieve seamless continuation. During execution, if an interruption or exception occurs, `Session` immediately takes the following measures: first, saves a snapshot of the current state; second, notifies the user in real time through streaming messages.
