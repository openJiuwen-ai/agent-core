# execute_single_component

## Function Description

Execute a single component and return the execution result.

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
| `inputs_schema` | `dict` | No | Input schema used to get input data from global state |
| `outputs_schema` | `dict` | No | Output schema used to process component output data |
| `context` | `ModelContext` | No | Context object, optional parameter |

## Return Value

| Type | Description |
|------|-------------|
| `Optional[Dict[str, Any]]` | Component execution result, returns `None` if no result |

## Execution Flow

1. Create `WorkflowSession`
2. Create `NodeSession`
3. Create `Vertex`
4. Initialize `Vertex`
5. Directly set `_node_config` attribute, create simple configuration objects
6. Submit input data to `NodeSession` state
7. Create `PregelConfig`
8. Execute component
9. Commit all state updates
10. Get execution result and return

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
        system_prompt="You are an intelligent assistant",
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

1. This function creates temporary `WorkflowSession` and `NodeSession` to execute the component, which will not affect the state of the original session.

2. If no `inputs_schema` is provided, the passed `inputs` will be directly used as the component's input.

3. If no `outputs_schema` is provided, the component's original output will be directly returned.

4. When `context` is `None`, the function can still execute normally.

5. This function is suitable for scenarios where you need to test or execute a single component independently, without relying on a complete workflow definition.