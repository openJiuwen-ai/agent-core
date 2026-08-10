# execute_single_component

## Description

Executes a single component and returns the execution result.

## Function Signature

```python
async def execute_single_component(
        component_id: str,
        session: Session,
        executor: ComponentComposable,
        inputs: dict,  # Input data
        inputs_schema: dict = None,  # Input schema
        outputs_schema: dict = None,  # Output schema
        context: ModelContext = None  # Context, optional parameter
) -> Optional[Dict[str, Any]]
```

## Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `component_id` | `str` | Yes | Node ID |
| `session` | `Session` | Yes | Session object |
| `executor` | `ComponentComposable` | Yes | Component executable object |
| `inputs` | `dict` | Yes | Input data |
| `inputs_schema` | `dict` | No | Input schema, used to retrieve input data from global state |
| `outputs_schema` | `dict` | No | Output schema, used to process the component's output data |
| `context` | `ModelContext` | No | Context object, optional parameter |

## Return Value

| Type | Description |
|------|-------------|
| `Optional[Dict[str, Any]]` | Component execution result; returns `None` if there are no results |

## Execution Flow

1. Create a `WorkflowSession`
2. Create a `NodeSession`
3. Create a `Vertex`
4. Initialize the `Vertex`
5. Directly set the `_node_config` attribute, creating a simple configuration object
6. Submit input data to the `NodeSession` state
7. Create a `PregelConfig`
8. Execute the component
9. Commit all state updates
10. Retrieve and return the execution result

## Example Code

### Basic Usage

```python
import asyncio
from openjiuwen.core.workflow import WorkflowComponent, Input, Output
from openjiuwen.core.workflow import execute_single_component
from openjiuwen.core.session import Session
from openjiuwen.core.context_engine import ModelContext

class CustomComponent(WorkflowComponent):
    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        # Process input data
        result = {"processed_data": inputs.get("data", "") + " processed"}
        return result

async def run_single_component():
    # Create component instance
    component = CustomComponent()
    
    # Create session object
    from openjiuwen.core.workflow import create_workflow_session
    session = create_workflow_session()
    
    # Prepare input data
    inputs = {"data": "test"}
    
    # Execute single component
    result = await execute_single_component(
        component_id="custom_node",
        session=session,
        executor=component,
        inputs=inputs,
        inputs_schema={"data": "${data}"},
        outputs_schema={"result": "${processed_data}"}
    )
    
    print(f"Execution result: {result}")

if __name__ == "__main__":
    asyncio.run(run_single_component())
```

### Using LLMComponent

```python
import asyncio
from openjiuwen.core.workflow.components.llm.llm_comp import LLMComponent
from openjiuwen.core.workflow import execute_single_component
from openjiuwen.core.session import Session
from openjiuwen.core.context_engine import ModelContext

async def run_llm_component():
    # Create LLM component configuration
    from openjiuwen.core.workflow.components.llm.llm_comp import LLMCompConfig
    llm_config = LLMCompConfig(
        model="gpt-3.5-turbo",
        system_prompt="You are a helpful assistant",
        max_tokens=100
    )
    
    # Create LLM component instance
    llm_component = LLMComponent(llm_config)
    
    # Create session object
    from openjiuwen.core.workflow import create_workflow_session
    session = create_workflow_session()
    
    # Prepare input data
    inputs = {"prompt": "Please introduce artificial intelligence"}
    
    # Execute single component
    result = await execute_single_component(
        component_id="llm_node",
        session=session,
        executor=llm_component,
        inputs=inputs,
        inputs_schema={"prompt": "${prompt}"}
    )
    
    print(f"LLM response: {result}")

if __name__ == "__main__":
    asyncio.run(run_llm_component())
```

## Notes

1. This function creates temporary `WorkflowSession` and `NodeSession` instances to execute the component, without affecting the original session state.

2. If `inputs_schema` is not provided, the passed `inputs` are used directly as the component's input.

3. If `outputs_schema` is not provided, the component's raw output is returned directly.

4. When `context` is `None`, the function can still execute normally.

5. This function is suitable for scenarios where a specific component needs to be tested or executed independently, without depending on a complete workflow definition.
