openJiuwen supports defining input and output formats for components. When adding a component to a workflow, you can configure the component's input and output format requirements, as well as format the component's input and output, ensuring standardized data transmission within the workflow.

openJiuwen provides the following two data formatting methods:

- Configure `schema`: Use a dict to specify the keys and values for component inputs and outputs. When adding a component to a workflow, you can configure `inputs_schema/stream_inputs_schema` as needed based on the component's capabilities and its connection to upstream components; similarly, you can configure `output_schema/stream_output_schema` based on the component's capabilities and its connection to downstream components. This is suitable for scenarios where the data structure is relatively simple and clearly defined.
- Configure `transformer`: Define transformer functions to access the output results of executed nodes in the context and convert them into the current component's input or reconstruct its output. This is suitable for scenarios requiring more complex data processing, such as data validation, field concatenation, structure restructuring, and logic judgment.

    When adding a component to a workflow, you can specify input and output data formats based on the component's capabilities and its connection methods with upstream and downstream components:
    - Connecting with upstream components: Configure inputs_transformer, specifying the batch input data format.
    - Connecting with downstream components: Configure outputs_transformer, specifying the batch output data format.


# Defining Component Input/Output Formats via Schema Configuration

schema is a structural description of component inputs and outputs:

- Input:
    - If connected to a preceding component via a standard connection, configure `inputs_schema`.
    - If connected to a preceding component via a streaming connection, configure `stream_inputs_schema`.
    - If connected to preceding components via both standard and streaming connections, you can configure both `inputs_schema` and `stream_inputs_schema`.
- Output:
    - If connected to a succeeding component via a standard connection, configure `output_schema`.
    - If connected to a succeeding component via a streaming connection, configure `stream_output_schema`.
    - If connected to succeeding components via both standard and streaming connections, you can configure both `output_schema` and `stream_output_schema`.

## Configuring Input Schema

- Taking a standard connection between a Start component and an End component as an example, the End component can reference the `query` parameter of the Start component using the format `${start.query}`:
  
  ```python
  from openjiuwen.core.workflow import End, Workflow

  workflow = Workflow()
  workflow.set_end_comp("end", End(), inputs_schema={"query": "${start.query}"})
  workflow.add_connection("start", "end")
  ```
- If connected to a preceding component via a streaming connection, taking a streaming connection between an LLM component and an End component as an example:
  
  ```python
  from openjiuwen.core.workflow import End, Workflow
  
  workflow = Workflow()
  # When registering the end component, specify the stream connection input definition stream_inputs_schema
  workflow.set_end_comp("end_stream", End(), stream_inputs_schema={"data": "${llm.output}"})
  # Add stream connection: from "llm" to "end", where the LLM component implements the `stream` interface by default, and the end component implements the `transform` and `collect` interfaces by default
  workflow.add_stream_connection("llm", "end_stream")
  ```
- If connected to preceding components via both standard and streaming connections, taking an End component as an example, where it has a standard connection with the Start component and a streaming connection with the LLM component. Its standard connection input schema:
  
  ```python
  end_inputs_schema = {
      "query": "${inputs.userInput}"
  }
  ```
  
  Streaming connection input schema:
  
  ```python
  end_stream_inputs_schema = {
      "llm_output": "${llm.output}"
  }
  ```
  
  After defining the `inputs_schema`, add the End component to the workflow. The End component's batch input `query` references the `userInput` from the user input, and the stream input `llm_output` references the `output` from the LLM component:
  
  ```python
  from openjiuwen.core.workflow import End, Workflow
  
  workflow = Workflow()
  workflow.set_end_comp("end", End(), inputs_schema=end_inputs_schema,stream_inputs_schema=end_stream_inputs_schema)
  workflow.add_connection("start", "end")
  workflow.add_stream_connection("llm", "end")
  ```

## Configuring Output Schema

- If connected to a succeeding component via a standard connection, the return value `invoke_value` of component a's invoke method is formatted as `invoke_output` via outputs_schema. The succeeding component b retrieves component a's return value via `${a.invoke_output}`:
  
  ```python
  from openjiuwen.core.workflow import WorkflowComponent, Workflow
  from openjiuwen.core.workflow.components import Session
  from openjiuwen.core.context_engine import ModelContext
  from openjiuwen.core.workflow.components.component import Input, Output
  from openjiuwen.core.workflow.workflow_config import ComponentAbility

  class InvokeCompNode(WorkflowComponent):
      def __init__(self, node_id: str = ''):
          super().__init__()
          self.node_id = node_id

      async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
          if inputs and "value" in inputs:
              return {"value": inputs["value"] * 2}  # The actual field name returned by the component is "value"
          return {"value": 0}  # The actual field name returned by the component is "value"

  workflow = Workflow()
  # Register source component a that implements the invoke capability, format its returned "value" field as "invoke_output"
  workflow.add_workflow_comp("a", InvokeCompNode("a"), inputs_schema={"value": "${start.a}"},
                            outputs_schema={"invoke_output": "${value}"},  # "${value}" references the actual "value" field returned by component a
                            comp_ability=[ComponentAbility.INVOKE])
  workflow.add_workflow_comp("b", InvokeCompNode("b"), inputs_schema={"value": "${a.invoke_output}"},
                            comp_ability=[ComponentAbility.INVOKE])
  workflow.add_connection("a", "b")
  ```
- If connected to a succeeding component via a streaming connection, the return value `stream_value` of component a's stream method is formatted as `stream_output` via stream_outputs_schema. The succeeding component b retrieves component a's return value via `${a.stream_output}`:
  
  ```python
  from typing import AsyncIterator

  from openjiuwen.core.common.logging import logger
  from openjiuwen.core.workflow import WorkflowComponent, Workflow
  from openjiuwen.core.workflow.components import Session
  from openjiuwen.core.context_engine import ModelContext
  from openjiuwen.core.workflow import Input, Output
  from openjiuwen.core.workflow.workflow_config import ComponentAbility

  class StreamCompNode(WorkflowComponent):
      def __init__(self, node_id: str = ''):
          super().__init__()
          self.node_id = node_id

      async def stream(self, inputs: Input, session: Session, context: ModelContext) -> AsyncIterator[Output]:
          if inputs and "value" in inputs:
              # Generate two streaming data frames
              for i in range(1, 3):
                  yield {"value": inputs["value"] * i}  # The actual field name returned by the component is "value"

  # Component that implements transform capability, processes stream data frame by frame
  class TransformCompNode(WorkflowComponent):
      def __init__(self, node_id: str = ''):
          super().__init__()
          self.node_id = node_id

      async def transform(self, inputs: Input, session: Session, context: ModelContext) -> AsyncIterator[Output]:
          try:
              value_generator = inputs.get("value")
              async for value in value_generator:
                  logger.debug(f"===TransformCompNode[{self.node_id}], processed input: {value}")
                  yield {"value": value + 100}
          except Exception as e:
              logger.error(f"===TransformCompNode[{self.node_id}], error in transform: {e}")
              raise

  workflow = Workflow()
  # Register source component a that implements the stream capability, format its returned "value" field as "stream_output"
  workflow.add_workflow_comp("a", StreamCompNode("a"), inputs_schema={"value": "${start.a}"},
                            stream_outputs_schema={"stream_output": "${value}"},  # "${value}" references the actual "value" field returned by component a
                            comp_ability=[ComponentAbility.STREAM], wait_for_all=True)
  workflow.add_workflow_comp("b", TransformCompNode("b"), stream_inputs_schema={"value": "${a.stream_output}"},
                            comp_ability=[ComponentAbility.TRANSFORM], wait_for_all=True)
  workflow.add_stream_connection("a", "b")
  ```
- If connected to succeeding components via both standard and streaming connections, the return value `stream_value` of component a's stream method is formatted as `stream_output` via stream_outputs_schema. Succeeding component b retrieves component a's return value via `${a.stream_output}`, and succeeding component c retrieves component a's return value via `${a.invoke_output}`:
  
  ```python
  from typing import AsyncIterator

  from openjiuwen.core.common.logging import logger
  from openjiuwen.core.workflow import WorkflowComponent, Workflow
  from openjiuwen.core.workflow.components import Session
  from openjiuwen.core.context_engine import ModelContext
  from openjiuwen.core.workflow import Input, Output
  from openjiuwen.core.workflow.workflow_config import ComponentAbility

  # Component that implements invoke capability, used for batch processing
  class InvokeCompNode(WorkflowComponent):
      def __init__(self, node_id: str = ''):
          super().__init__()
          self.node_id = node_id

      async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
          if inputs and "value" in inputs:
              # Simple implementation: double the input value
              return {"value": inputs["value"] * 2}
          return {"value": 0}


  # Component that implements stream capability, converts batch input to streaming output
  class StreamCompNode(WorkflowComponent):
      def __init__(self, node_id: str = ''):
          super().__init__()
          self.node_id = node_id

      async def stream(self, inputs: Input, session: Session, context: ModelContext) -> AsyncIterator[Output]:
          if inputs and "value" in inputs:
              # Generate two streaming data frames
              for i in range(1, 3):
                  yield {"value": inputs["value"] * i}  # The actual field name returned by the component is "value"


  # Component that implements transform capability, processes stream data frame by frame
  class TransformCompNode(WorkflowComponent):
      def __init__(self, node_id: str = ''):
          super().__init__()
          self.node_id = node_id

      async def transform(self, inputs: Input, session: Session, context: ModelContext) -> AsyncIterator[Output]:
          try:
              value_generator = inputs.get("value")
              async for value in value_generator:
                  yield {"value": value + 100}
          except Exception as e:
              logger.error(f"===TransformCompNode[{self.node_id}], error in transform: {e}")
              raise

  workflow = Workflow()
  workflow.add_workflow_comp("a", StreamCompNode("a"), inputs_schema={"value": "${start.a}"},
                            outputs_schema={"invoke_output": "${value}"},  # "${value}" references the actual "value" field returned by component a
                            stream_outputs_schema={"stream_output": "${value}"},  # "${value}" references the actual "value" field returned by component a
                            comp_ability=[ComponentAbility.STREAM, ComponentAbility.INVOKE], wait_for_all=True)
  workflow.add_workflow_comp("b", TransformCompNode("b"), stream_inputs_schema={"value": "${a.stream_output}"},
                            comp_ability=[ComponentAbility.TRANSFORM], wait_for_all=True)
  workflow.add_workflow_comp("c", InvokeCompNode("c"), inputs_schema={"value": "${a.invoke_output}"},
                            comp_ability=[ComponentAbility.INVOKE])
  workflow.add_stream_connection("a", "b")
  workflow.add_connection("a", "c")
  ```

# Defining Component Input/Output Formats via Transformer Configuration

transformer is an interface used for custom data formatting. When adding a component to a workflow, you can specify the input data format via `inputs_transformer` and the output data format via `outputs_transformer`, used for processing batch data. transformer supports adding custom return fields and executing more complex data processing operations (such as data validation, field concatenation, structure restructuring, logic judgment, etc.), thereby flexibly meeting various data processing needs. The following primarily introduces the differences from `schema` using `inputs_transformer` and `outputs_transformer` as examples.

## Configuring Input Transformer

`inputs_transformer` customizes the structure and format of the component's input data. The interface input covers the output data of all components preceding the current component in the workflow, and the interface output is the formatted input data. Users can use `inputs_transformer` to generate the component's input, supporting complex data processing operations such as data validation, field concatenation, structure restructuring, and logic judgment.

The `inputs_transformer` for an LLM component is defined as follows (if the input is empty, a default input is set):

```python
from openjiuwen.core.session.state import ReadableStateLike

def llm_inputs_transformer(state: ReadableStateLike):
    query = state.get("start.query")
    if query:
        return {"query": query}
    return {"query": "Default Input"}  # Set default input
```

Add the LLM component to the workflow, set the component ID to `llm`, and specify its `inputs_transformer`:

```python
  from openjiuwen.core.workflow import LLMComponent, Workflow

  workflow = Workflow()
  workflow.add_workflow_comp("llm", LLMComponent(),
                           inputs_transformer=llm_inputs_transformer)
  ```

When executing the workflow, if the Start component output is `{"query": "Book a hotel in Beijing"}`, the LLM component's input references the `query` field of the Start component, therefore the LLM component's final input is `{"query": "Book a hotel in Beijing"}`; if the Start component has no query field, then the LLM's final input is `{"query": "Default Input"}`.

## Configuring Output Transformer

`outputs_transformer` customizes the structure and format of the component's output data. The interface input is all outputs of the current component, and the interface output is the formatted output data. Users can use `outputs_transformer` to format the component's output, supporting complex data processing operations such as data conversion, field extraction, structure restructuring, and result filtering.

The `outputs_transformer` for an LLM component is defined as follows (if the output is empty, a default output is set):

```python
def llm_outputs_transformer(results: dict):
    output = results.get("output", None)
    if output:
        return {"result": output}
    return {"result": "Default Output"}
```

Add the LLM component to the workflow, set the component ID to `llm`, and specify its `inputs_transformer` and `outputs_transformer`:

```python
  from openjiuwen.core.workflow import LLMComponent, Workflow
  from openjiuwen.core.session.state import ReadableStateLike

  def llm_inputs_transformer(state: ReadableStateLike):
      query = state.get("start.query")
      if query:
          return {"query": query}
      return {"query": "Default Input"}

  def llm_outputs_transformer(results: dict):
      output = results.get("output", None)
      if output:
          return {"result": output}
      return {"result": "Default Output"}

  workflow = Workflow()
  workflow.add_workflow_comp("llm", LLMComponent(),
                           inputs_transformer=llm_inputs_transformer,
                           outputs_transformer=llm_outputs_transformer)
  ```

If the LLM component's output is `{"output": "Beijing Hotel"}`, then the formatted output result is `{"result" : "Beijing Hotel"}`; if the LLM component's output is `{"output": None}`, then the formatted output result is `{"result" : "Default Output"}`.