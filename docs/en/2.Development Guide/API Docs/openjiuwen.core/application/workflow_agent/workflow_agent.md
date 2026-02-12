# openjiuwen.core.application.workflow_agent.workflow_agent

## class openjiuwen.core.application.workflow_agent.workflow_agent.WorkflowAgent

```
class openjiuwen.core.application.workflow_agent.workflow_agent.WorkflowAgent(agent_config: WorkflowAgentConfig)
```

Agent based on multi-workflow controller, implementing execution of predefined workflows.

### async invoke(inputs: Dict, session: Session = None) -> Dict

Synchronously (non-streaming) execute workflow and return complete result.

**Parameters**:

* **inputs** (Dict): Input data, typically containing `query`, `conversation_id`, etc.
* **session** (Session, optional): Session context; if not provided, one will be created automatically.

**Returns**:

* **Dict**: Workflow execution result.

### async stream(inputs: Dict, session: Session = None) -> AsyncIterator[Any]

Stream execution of workflow, producing output chunks in real time.

**Parameters**:

* **inputs** (Dict): Input data.
* **session** (Session, optional): Session context; if not provided, one will be created automatically.

**Returns**:

* **AsyncIterator[Any]**: Streaming output iterator.

# openjiuwen.core.single_agent.legacy.config

## class openjiuwen.core.single_agent.legacy.config.WorkflowAgentConfig

Configuration model for Workflow Agent.

- **controller_type** (ControllerType): Controller type, default `WorkflowController`.
- **start_workflow** (WorkflowSchema): Starting workflow definition, default empty `WorkflowSchema()`.
- **end_workflow** (WorkflowSchema): Ending workflow definition, default empty `WorkflowSchema()`.
- **global_variables** (List[dict]): Global variable list, default empty list.
- **global_params** (Dict[str, Any]): Global parameter dictionary, default empty dictionary.
- **constrain** (ConstrainConfig): Behavior constraint configuration, default `ConstrainConfig()`.
- **default_response** (DefaultResponse): Default response configuration, default constructed.
