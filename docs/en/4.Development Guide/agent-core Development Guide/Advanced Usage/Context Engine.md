# Context Engine

The Context Engine (`ContextEngine`) is a unified component in openJiuwen for **model input context** construction and reuse. It is a **member at the Agent level**, created and held by the Agent during initialization, working around `Session` and `ModelContext`, and is responsible for:

- Historical conversation messages (short-term memory, window cropping)
- Token statistics and window control
- Scenarios where multiple "context IDs" coexist (e.g., multiple subtasks running simultaneously in one session)

## Core Concepts

- **ContextEngine**:
  - Entry class: `openjiuwen.core.context_engine.ContextEngine`.
  - As a **member of the Agent**, created during Agent initialization.
  - Responsible for managing multiple `ModelContext` instances by `session_id + context_id`.
  - Provides unified capabilities: `create_context / get_context / clear_context / save_contexts`.
- **ModelContext**:
  - Represents a specific "conversation context" in a session.
  - Responsible for maintaining message lists, constructing windows (`get_context_window()`), etc.
- **ContextWindow**:
  - Represents a snapshot of a "context window" about to be sent to the model, i.e., the return result of `get_context_window()`.
  - Encapsulates three types of content: system_messages (system instructions), context_messages (conversation history), tools (tool definitions).

## Agent Using Context

ContextEngine is a member of the Agent, created during Agent initialization. The Agent manages context through `context_engine` in `invoke`, with a typical flow as follows:

### Typical Call Flow

1. **Agent Initialization**: Create and hold a `context_engine` instance (e.g., `ContextEngine(ContextEngineConfig())`).
2. **invoke Start**: Call `context_engine.create_context(session=agent_session)` to establish the context environment for the current session.
3. **invoke Execution**: Delegate the request to Controller (e.g., ReActController, WorkflowController), which uses context internally as needed.
4. **invoke End**: Call `context_engine.save_contexts(session)` to persist context, and call `clear_context` at the appropriate time to release resources.

### Code Example: Using Context in Agent

```python
# During Agent initialization (using BaseAgent / ReActAgent as examples)
from openjiuwen.core.context_engine import ContextEngine, ContextEngineConfig

class MyAgent(BaseAgent):
    def __init__(self, config):
        super().__init__(config)
        # ContextEngine is created by base class or subclass during initialization
        self.context_engine = ContextEngine(config=ContextEngineConfig(
            default_window_message_num=100,
        ))

# In Agent invoke (illustration)
async def invoke(self, inputs: Dict, session: Session = None) -> Dict:
    agent_session = session or await self._session.pre_run(session_id=...)
    # 1. Establish context environment
    await self.context_engine.create_context(session=agent_session)
    try:
        # 2. Delegate to Controller for execution
        result = await self.controller.invoke(inputs, agent_session)
        # 3. Persist context
        await self.context_engine.save_contexts(agent_session)
        return result
    finally:
        await agent_session.post_run()
```

**Key Points:**

- ContextEngine is created and held by the Agent, and it is not recommended to create a global instance separately in workflow components or externally.
- `create_context` and `save_contexts` are called uniformly at the Agent's `invoke` entry point to ensure session isolation and lifecycle consistency.

## Workflow Using Context

Workflow execution occurs **inside the Agent's invoke**, scheduled by Controller (e.g., WorkflowController, LLMController). The `ModelContext` used by Workflow is created by **the Agent's context_engine** and passed through `Runner.run_workflow`, rather than being created by Workflow components themselves.

### Typical Call Flow

1. The Agent's `invoke` delegates the request to Controller.
2. When Controller needs to execute Workflow, it creates ModelContext through **the Agent's context_engine**:
   - `context = await self._context_engine.create_context(context_id=workflow_id, session=session)`
3. Pass `context` to `Runner.run_workflow`:
   - `await Runner.run_workflow(workflow, inputs=..., session=workflow_session, context=context)`
4. Runner passes `context` to `workflow.invoke(..., context=context)`, and Workflow internal components can use this context.

### Code Example: Creating context in Controller and Passing Through

```python
# In the invoke logic of WorkflowController / LLMController and other Controllers
from openjiuwen.core.runner import Runner

# When executing Workflow, create context through the Agent's context_engine and pass it to Runner
result = await Runner.run_workflow(
    workflow,
    inputs=task.input.arguments,
    session=workflow_session,
    context=await self._context_engine.create_context(
        context_id=workflow_id,
        session=session,
    ),
)
```

### Code Example: Receiving and Using context in Workflow Components

```python
# Workflow components receive context from invoke parameters, no need to create it themselves
from openjiuwen.core.workflow import WorkflowComponent
from openjiuwen.core.context_engine import ModelContext
from openjiuwen.core.graph.executable import Input, Output
from openjiuwen.core.foundation.llm import UserMessage


class ChatNode(WorkflowComponent):
    async def invoke(self, inputs: Input, session, context: ModelContext) -> Output:
        user_query = inputs.get("query", "")

        # 1. context is passed through by Agent, use directly
        await context.add_messages(UserMessage(content=user_query))

        # 2. Construct window for calling LLM
        window = await context.get_context_window()
        messages = window.get_messages()

        # 3. Call Model to complete reasoning (omitted here)
        # llm_response = await model.ainvoke(messages=messages, ...)

        return {"answer": "..."}
```

**Key Points:**

- The context used by Workflow is created by the Agent's `context_engine.create_context` and passed through `Runner.run_workflow`.
- The `invoke` signature of Workflow components should include the `context: ModelContext` parameter, which is injected by the framework. **Components should not call `context_engine.create_context` themselves**.

## Cleanup and Lifecycle Management

In long-running services, lifecycle management of context needs to be considered:

- Use `clear_context(session_id=...)`: Clear all contexts under a session (e.g., when the user actively ends the session).
- Use `clear_context(session_id=..., context_id=...)`: Only clear a specific sub-conversation.
- Before Runner / service shutdown, you can choose to call `save_contexts` + `clear_context` to persist necessary information and then release memory.

Example:

```python
# When user ends session
context_engine.clear_context(session_id="s1")

# Only end a specific sub-conversation
context_engine.clear_context(session_id="s1", context_id="chat")
```

## Summary

- **ContextEngine is a member at the Agent level**, created and held by the Agent during initialization.
- **Agent Using Context**: Call `create_context`, `save_contexts` in `invoke`, and delegate to Controller for execution.
- **Workflow Using Context**: Inside the Agent's invoke, Controller creates context through the Agent's `context_engine.create_context`, passes it to `Runner.run_workflow`, and Workflow components receive context from parameters. **They should not create it themselves**.
- Complete multi-session isolation, multi-context ID management, and Token-level window control through `create_context / get_context / save_contexts / clear_context`.
