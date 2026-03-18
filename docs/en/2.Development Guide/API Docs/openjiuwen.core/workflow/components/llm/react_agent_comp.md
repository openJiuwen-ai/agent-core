# ReAct Agent Workflow Component

The ReAct Agent Workflow Component brings the power of ReAct (Reasoning + Acting) agents into the workflow system. It allows you to incorporate complex reasoning and tool usage within your workflow orchestrations.

## Features

- Full ReAct agent functionality within workflow contexts
- Support for all workflow execution patterns (invoke, stream, collect, transform)
- Tool execution capabilities
- Context management and memory persistence
- Configurable iteration limits

## Usage

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