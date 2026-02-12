# openjiuwen.core.application.workflow_agent.workflow_agent

## class openjiuwen.core.application.workflow_agent.workflow_agent.WorkflowAgent

```
class openjiuwen.core.application.workflow_agent.workflow_agent.WorkflowAgent(agent_config: WorkflowAgentConfig)
```

基于多工作流控制器的 Agent，实现预定义工作流的执行。

### async invoke(inputs: Dict, session: Session = None) -> Dict

同步（非流式）执行工作流并返回完整结果。

**参数：**

* **inputs**(Dict)：输入数据，通常包含 `query`、`conversation_id` 等。
* **session**(Session, 可选)：会话上下文；不传则自动创建。

**返回：**

* **Dict**：工作流执行结果。

### async stream(inputs: Dict, session: Session = None) -> AsyncIterator[Any]

流式执行工作流，实时产出输出块。

**参数：**

* **inputs**(Dict)：输入数据。
* **session**(Session, 可选)：会话上下文；不传则自动创建。

**返回：**

* **AsyncIterator[Any]**：流式输出迭代器。

# openjiuwen.core.single_agent.legacy.config

## class openjiuwen.core.single_agent.legacy.config.WorkflowAgentConfig

工作流 Agent 的配置模型。

- **controller_type**(ControllerType)：控制器类型，默认`WorkflowController`。
- **start_workflow**(WorkflowSchema)：起始工作流定义，默认空`WorkflowSchema()`。
- **end_workflow**(WorkflowSchema)：结束工作流定义，默认空`WorkflowSchema()`。
- **global_variables**(List[dict])：全局变量列表，默认空列表。
- **global_params**(Dict[str, Any])：全局参数字典，默认空字典。
- **constrain**(ConstrainConfig)：行为约束配置，默认`ConstrainConfig()`。
- **default_response**(DefaultResponse)：默认回复配置，默认构造。
