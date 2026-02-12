The orchestration and capability invocation of components within a workflow depend on the types of edges connected to their ends. The workflow automatically determines whether to run a component in `invoke`, `stream`, `transform`, or `collect` mode based on whether the component's inputs and outputs are stream edges or batch edges. Furthermore, in workflow orchestration, the method of data referencing between components directly influences the data transmission path and dependencies within the workflow. The following sections detail component connection methods and data transmission mechanisms.

# Component Connection Methods

In a workflow, the connection method of a component and its inherent capabilities mutually influence and constrain each other. The connection method dictates which specific capability of the component the workflow will invoke. Conversely, the capabilities supported by the component itself limit how it can be connected. The specific streaming I/O connection rules corresponding to different capabilities are as follows:

- When Component A implements the `invoke` capability, Component A uses `add_connection` to connect with predecessor and successor components.
- When Component A implements the `stream` capability, Component A uses `add_connection` to connect with predecessor components and `add_stream_connection` to connect with successor components.
- When Component A implements the `transform` capability, Component A uses `add_stream_connection` to connect with both predecessor and successor components.
- When Component A implements the `collect` capability, Component A uses `add_stream_connection` to connect with predecessor components and `add_connection` to connect with successor components.

The following section introduces three custom components: `StreamCompNode`, `TransformCompNode`, and `CollectCompNode`. Based on the different capabilities implemented by these components, distinct connection methods are selected to achieve streaming orchestration.

`StreamCompNode` implements the `stream` method (corresponding to `ComponentAbility.STREAM`), converting batch input into streaming output. Specifically, it receives a single input value (e.g., `{"value": 1}`) and generates two streaming data frames (`{"value": 1 * 1}` and `{"value": 1 * 2}`). `StreamCompNode` uses `add_connection` to connect with predecessors and `add_stream_connection` to connect with successors. The example code is as follows:

```python
import asyncio
from typing import AsyncIterator

from openjiuwen.core.common.logging import logger
from openjiuwen.core.workflow import WorkflowComponent
from openjiuwen.core.workflow.components.component import Input, Output
from openjiuwen.core.workflow.components import Session
from openjiuwen.core.context_engine import ModelContext

class StreamCompNode(WorkflowComponent):
    def __init__(self, node_id: str = ''):
        super().__init__()
        self.node_id = node_id

    async def stream(self, inputs: Input, session: Session, context: ModelContext) -> AsyncIterator[Output]:
        logger.debug(f"===StreamCompNode[{self.node_id}], input: {inputs}")
        if inputs is None:
            yield 1
        else:
            for i in range(1, 3):
                yield {"value": i * inputs["value"]}
```

`TransformCompNode` implements the `transform` method (corresponding to `ComponentAbility.TRANSFORM`), processing stream data frame by frame. Specifically, it receives streaming input (e.g., `{"value":1}`, `{"value":2}`) and passes through each frame (logic for format conversion can be added in practice). `TransformCompNode` uses `add_stream_connection` to connect with both predecessors and successors. The example code is as follows:

```python
import asyncio
from typing import AsyncIterator
from openjiuwen.core.common.logging import logger
from openjiuwen.core.workflow import WorkflowComponent
from openjiuwen.core.workflow.components.component import Input, Output
from openjiuwen.core.workflow.components import Session
from openjiuwen.core.context_engine import ModelContext

class TransformCompNode(WorkflowComponent):
    def __init__(self, node_id: str = ''):
        super().__init__()
        self.node_id = node_id

    # inputs is a generator dictionary
    async def transform(self, inputs: Input, session: Session, context: Context) -> AsyncIterator[
        Output]:
        logger.debug(f"===TransformCompNode[{self.node_id}], input stream started")
        try:
            # Assume key is value, get corresponding generator
            value_generator = inputs.get("value")
            async for value in value_generator:
                logger.debug(f"===TransformCompNode[{self.node_id}], processed input: {value}")
                yield {"value": value}
        except Exception as e:
            logger.error(f"===TransformCompNode[{self.node_id}], critical error in transform: {e}")
            raise  # Re-raise critical exceptions (e.g., stream interruption)
```

`CollectCompNode` implements the `collect` method (corresponding to `ComponentAbility.COLLECT`), aggregating stream input into batch output. Specifically, it receives input stream data `[{"value":1}, {"value":2}]`, aggregates the calculation 1+2, and outputs the batch result `{"value":3}`. `CollectCompNode` uses `add_stream_connection` to connect with predecessors and `add_connection` to connect with successors. The example code is as follows:

```python
import asyncio
from typing import AsyncIterator
from openjiuwen.core.common.logging import logger
from openjiuwen.core.workflow import WorkflowComponent
from openjiuwen.core.workflow.components.component import Input, Output
from openjiuwen.core.workflow.components import Session
from openjiuwen.core.context_engine import ModelContext

class CollectCompNode(WorkflowComponent):
    def __init__(self, node_id: str = ''):
        super().__init__()
        self.node_id = node_id

    # inputs is a generator dictionary
    async def collect(self, inputs: Input, session: Session, context: Context) -> Output:
        logger.info(f"===CollectCompNode[{self.node_id}], input stream started")
        result = 0
        try:
            # Assume key is value, get corresponding generator
            value_generator = inputs.get("value")
            async for value in value_generator:
                if value is None:
                    logger.warning(f"===CollectCompNode[{self.node_id}], missing 'value' in input: {value}")
                    continue
                result += value.get("value", 0) if isinstance(value, dict) else value
                logger.info(f"===CollectCompNode[{self.node_id}], processed input: {value}")
            return {"value": result}
        except Exception as e:
            logger.error(f"===CollectCompNode[{self.node_id}], critical error in collect: {e}")
            raise  # Re-raise critical exceptions, e.g., stream interruption
```

Construct the workflow, set the start and end components, and add the three components above to the workflow:

```python
from openjiuwen.core.workflow import Workflow
from openjiuwen.core.workflow import Start, End

flow = Workflow()
flow.set_start_comp("start", Start({}), inputs_schema={"a": "${a}"})
# a: throw 2 frames: {value: 1}, {value: 2}
flow.add_workflow_comp("a", StreamCompNode("a"), inputs_schema={"value": "${start.a}"})
# b: transform 2 frames to c
flow.add_workflow_comp("b", TransformCompNode("b"), stream_inputs_schema={"value": "${a.value}"})
# c: value = sum(value of frames)
flow.add_workflow_comp("c", CollectCompNode("c"), stream_inputs_schema={"value": "${b.value}"})
flow.set_end_comp("end", End({}), inputs_schema={"result": "${c.value}"})
```

Connect the components; streaming components are connected using `add_stream_connection`:

```python
flow.add_connection("start", "a")
flow.add_stream_connection("a", "b")
flow.add_stream_connection("b", "c")
flow.add_connection("c", "end")
```

Execute the workflow:

```python
import asyncio
from openjiuwen.core.workflow import create_workflow_session
result = asyncio.run(flow.invoke({"a": 1}, create_workflow_session()))
print(f"{result}")
```

Execution result:

```python
result={'output': {'result': 3}} state=<WorkflowExecutionState.COMPLETED: 'COMPLETED'>
```

# Data Transmission Methods Between Components

During the workflow construction process, data transmission between components can be achieved via reference expressions. Reference expressions can cite variables, input, or output values but do not support the addition of logic.

## Referencing Inputs in Successor Components

Assuming the input is `{"inputs": "test"}`, a successor component can reference the input via `${inputs}`.

```python
from openjiuwen.core.workflow import End
from openjiuwen.core.workflow import Workflow
from openjiuwen.core.workflow import Start

workflow = Workflow()
workflow.set_start_comp("start", Start(), inputs_schema={"query": "${inputs}"})
```

Assuming the input is `{"inputs": {"a": 1}}`, a successor component can reference the input via `${inputs.a}`.

```python
workflow.set_start_comp("start", Start(), inputs_schema={"query": "${inputs.a}"})
```

## Successor Components Referencing Predecessor Components

The output of a predecessor component can be referenced via `${<predecessor_component_id>.<output_value_id>}`. Assuming the predecessor component's output is `{"query": "result"}` and its ID added to the workflow is `start`, the successor component can reference it via `${start.query}`.

```python
workflow.set_end_comp("end", End(), inputs_schema={"query": "${start.query}"})
```

Below is a complete runnable example demonstrating how to use reference expressions for data transmission between components:

```python
import asyncio
from openjiuwen.core.workflow import Start, End, create_workflow_session
from openjiuwen.core.workflow import Workflow

# Create workflow
workflow = Workflow()

# Set Start component, demonstrating referencing simple input and nested input
# ${inputs} in inputs_schema references the entire input object
# ${inputs.message} references the message field in the input object
workflow.set_start_comp(
    "start", 
    Start(), 
    inputs_schema={
        "query": "${inputs.message}",  # Reference nested input
        "metadata": "${inputs}"         # Reference entire input
    }
)

# Set End component, demonstrating referencing predecessor component's output
# ${start.query} references the query output of the component with id "start"
# ${start.metadata} references the metadata output of the component with id "start"
workflow.set_end_comp(
    "end", 
    End({"responseTemplate": "Processing result: {{result}}, Metadata: {{meta}}"}),
    inputs_schema={
        "result": "${start.query}",      # Reference predecessor component's output
        "meta": "${start.metadata}"      # Reference predecessor component's output
    }
)

# Connect components
workflow.add_connection("start", "end")

# Execute workflow
inputs = {
    "inputs": {
        "message": "Hello, openJiuwen!"
    }
}

result = asyncio.run(workflow.invoke(inputs=inputs, session=create_workflow_session()))
print(f"Execution result: {result.result}")
print(f"Execution state: {result.state}")
```

**Expected Output:**

```python
Execution result: {'response': "Processing result: Hello, openJiuwen!, Metadata: {'message': 'Hello, openJiuwen!'}"}
Execution state: WorkflowExecutionState.COMPLETED
```
