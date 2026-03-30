# ReAct Agent Workflow Component

The ReAct Agent Workflow Component brings the power of ReAct (Reasoning + Acting) agents into the workflow system. It allows you to incorporate complex reasoning and tool usage within your workflow orchestrations.

## Features

- Full ReAct agent functionality within workflow contexts
- Support for all workflow execution patterns (invoke, stream, collect, transform)
- Tool execution capabilities
- Context management and memory persistence
- Configurable iteration limits

## Usage

### Basic Usage

```python
from openjiuwen.core.workflow import ReActAgentComp, ReActAgentCompConfig
from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ModelRequestConfig

# Create configuration
config = ReActAgentCompConfig(
    model_client_config=ModelClientConfig(
        client_provider="OpenAI",
        api_key="your-api-key",
        api_base="https://api.openai.com/v1"
    ),
    model_config_obj=ModelRequestConfig(model_name="gpt-3.5-turbo"),
    max_iterations=10
)

# Create component
react_component = ReActAgentComp(config=config)

# Use in workflow
# ...
```

### Usage with Tool Calling

ReActAgentComp supports tool calling functionality, allowing the agent to invoke external tools during execution to complete tasks.

```python
from openjiuwen.core.workflow import Workflow, Start, End, create_workflow_session
from openjiuwen.core.workflow.components.llm.react import ReActAgentComp, ReActAgentCompConfig
from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ModelRequestConfig
from openjiuwen.core.foundation.tool import LocalFunction, ToolCard
from openjiuwen.core.runner import Runner

# 1. Create a tool
add_tool = LocalFunction(
    card=ToolCard(
        name="add",
        description="Addition operation, calculates the sum of two numbers",
        input_params={
            "type": "object",
            "properties": {
                "a": {"description": "First addend", "type": "number"},
                "b": {"description": "Second addend", "type": "number"},
            },
            "required": ["a", "b"],
        },
    ),
    func=lambda a, b: a + b,
)

# 2. Register tool to Runner.resource_mgr (must be done before creating component)
Runner.resource_mgr.add_tool(add_tool)

# 3. Create a workflow
flow = Workflow()

# Create components
start_component = Start()
end_component = End({"responseTemplate": "{{output}}"})

# Create ReActAgentComp configuration
config = ReActAgentCompConfig(
    model_client_config=ModelClientConfig(
        client_provider="OpenAI",
        api_key="your-api-key",
        api_base="https://api.openai.com/v1",
        verify_ssl=False,
    ),
    model_config_obj=ModelRequestConfig(model_name="gpt-3.5-turbo"),
    max_iterations=5,
)
react_component = ReActAgentComp(config=config)

# 4. Add tool to Agent's ability list (critical step)
# Add tool via the public ability_manager property of executable
react_component.executable.ability_manager.add(add_tool.card)

# 5. Set up the workflow connections
flow.set_start_comp("s", start_component, inputs_schema={"query": "${query}"})
flow.set_end_comp("e", end_component, inputs_schema={"output": "${react.output}"})
flow.add_workflow_comp("react", react_component, inputs_schema={"query": "${s.query}"})

# Add connections: start -> react -> end
flow.add_connection("s", "react")
flow.add_connection("react", "e")

# 6. Create session context
context = create_workflow_session()

# 7. Invoke the workflow, requesting tool-based calculation
result = await flow.invoke(
    inputs={"query": "Use add tool to calculate 123 + 456"},
    session=context
)

# 8. Verify the result
print(f"Calculation result: {result.result['response']}")
# Output: Calculation result: 123 + 456 = 579
```

**Key Points:**

1. **Tool Registration Order**: Must call `Runner.resource_mgr.add_tool()` to register the tool before creating the component
2. **Add Ability**: Add tool card to Agent's ability list via `react_component.executable._react_agent.ability_manager.add()`
3. **Caching Mechanism**: `react_component.executable` caches the executable instance, ensuring tools are registered to the correct instance
4. **Tool Calling Flow**:
   - LLM receives request and decides to call a tool
   - Get tool instance via `Runner.resource_mgr.get_tool()`
   - Execute tool and get result
   - LLM generates final response based on the result

## Configuration

The component accepts all the same configuration options as the ReActAgent, plus workflow-specific options.

## Execution Patterns

The ReActAgentComp supports all four workflow execution patterns:

- **Invoke**: Execute the ReAct loop synchronously with batch input/output
- **Stream**: Execute the ReAct loop with streaming output
- **Collect**: Execute the ReAct loop with streaming input aggregated to batch output
- **Transform**: Execute the ReAct loop with streaming input/output

## Integration with Workflows

The component can be seamlessly integrated into workflow graphs:

```python
from openjiuwen.core.workflow import Workflow, Start, End

# Create a workflow
flow = Workflow()

# Create components
start_component = Start()
end_component = End({"responseTemplate": "{{output}}"})
react_component = ReActAgentComp(config=config)  # Your configured component

# Set up the workflow connections
flow.set_start_comp("s", start_component, inputs_schema={"query": "${query}"})
flow.set_end_comp("e", end_component, inputs_schema={"output": "${react.output}"})
flow.add_workflow_comp("react", react_component, inputs_schema={"query": "${s.query}"})

# Add connections: start -> react -> end
flow.add_connection("s", "react")
flow.add_connection("react", "e")

# Create session context and invoke the workflow
context = create_workflow_session()
result = await flow.invoke(inputs={"query": "What is the weather today?"}, session=context)
```