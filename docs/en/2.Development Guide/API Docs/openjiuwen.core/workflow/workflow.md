# openjiuwen.core.workflow

## class WorkflowOutput

Data class for the output result of workflow execution `invoke`.

* **result** (Union(list[[WorkflowChunk](./workflow.md#class-workflowchunk)], dict)): Output result data. The type of `result` differs depending on the execution state.
* **state** ([WorkflowExecutionState](./workflow.md#enum-workflowexecutionstate)): State of the current execution result.

> **Note**
>
> * When state is `WorkflowExecutionState.INPUT_REQUIRED`, `result` is of type `list[WorkflowChunk]`, where each item is information about an interruption node;
> * When state is `WorkflowExecutionState.COMPLETED`, `result` is of type `dict`, which is the final batch output result of the workflow.

## class WorkflowChunk

Data class for data frames returned by workflow execution `stream`, of type [CustomSchema](), [TraceSchema](), [OutputSchema]().

## enum WorkflowExecutionState

Enumeration class for the execution state of workflow execution `invoke`.

* **COMPLETED**: Execution completed state.
* **INPUT_REQUIRED**: Interruption state, waiting for user interaction input.


## class WorkflowCard
Workflow card data class, inheriting from `BaseCard`, containing basic information and parameter definitions of the workflow.

* **id** (str, optional): Unique identifier of the workflow, default is a random UUID.
* **name** (str, optional): Workflow name, default is `""`.
* **description** (str, optional): Workflow description, default is `""`.
* **version** (str, optional): Version of the workflow, default is `""`.
* **input_params** (Dict[str, Any] | Type[BaseModel], optional): Input parameter schema of the workflow. Can be a JSON Schema dictionary or Pydantic model class. Defaults to empty dictionary, indicating no input parameter schema, accepting any input.

## class Workflow
```python
Workflow(card: WorkflowCard = None, **kwargs)
```

The `Workflow` class is the core class for defining and executing asynchronous computation graphs, serving as a stateful execution engine responsible for managing and running computation graphs defined by nodes and edges. When creating a `Workflow`, you must first define the workflow and then add nodes and edges to ensure ordered execution of computation logic.

**Parameters:**

* **card** ([WorkflowCard](./workflow.md), optional): Workflow card for defining basic information and input definitions of the workflow, default is `None`.
* **\*\*kwargs**: Workflow environment variable configuration, currently supports:
  * **workflow_max_nesting_depth** (int): Used to control the maximum nesting depth of workflows. Default is 5, value range (0, 10].

**Example:**

```python
>>> from openjiuwen.core.workflow import Workflow, WorkflowCard
>>> 
>>> # Create a workflow card containing workflow ID, name, and version information
>>> workflowCard = WorkflowCard(name="workflow_name", id="workflow_id", version="version-1")
>>> workflow = Workflow(workflow_config=workflowCard)
```

### set_start_comp
```python
def set_start_comp(self,
            start_comp_id: str,
            component: ComponentComposable,
            inputs_schema: dict | Transformer = None,
            outputs_schema: dict | Transformer = None
    )->Self
```

Define the start component of the workflow and data transformation rules, which is the entry point for workflow execution.

**Parameters:**

* **start_comp_id** (str): Unique identifier of the start component, used to reference this component in the workflow.
* **component** (ComponentComposable): Workflow component instance to be added.
* **inputs_schema** (dict|Transformer, optional): Structure definition of start component input parameters. Key names should match component input parameter names, and values should be corresponding input parameter types. Default value: `None`.
* **outputs_schema** (dict|Transformer, optional): Structure definition of start component output results, used to define output data format specifications for subsequent component parsing. Default value: `None`.


**Example:**

```python
>>> from openjiuwen.core.workflow.start_comp import Start
>>> 
>>> # input example: {"question": "What is Agent?", "user_id": 123}
>>> workflow.set_start_comp("start", Start(), inputs_schema={"query": "${inputs.question}"})
```


### set_end_comp
```python
def set_end_comp(self,
            end_comp_id: str,
            component: ComponentComposable,
            inputs_schema: dict | Transformer = None,
            outputs_schema: dict | Transformer = None,
            stream_inputs_schema: dict | Transformer = None,
            stream_outputs_schema: dict | Transformer = None,
            response_mode: str = None
    ) -> Self:
```

Define the end component of the workflow, which is the final node of workflow execution, responsible for processing final results, passing data outside the graph, and terminating the workflow.

**Parameters:**

* **end_comp_id** (str): Unique identifier of the end component, used to reference this component in the workflow.
* **component** (ComponentComposable): Workflow component instance to be added.
* **inputs_schema** (dict|Transformer, optional): Structure definition of end component input parameters. Key names should match component input parameter names, and values should be corresponding input parameter types. Default value: `None`.
* **outputs_schema** (dict|Transformer, optional): Structure definition of end component output results, used to define the format of the final return result of the workflow. Default value: `None`.
* **stream_inputs_schema** (dict|Transformer, optional): Structure definition of end component input parameters. Only applicable to components that support streaming data input, same function as `inputs_schema`; default value: `None`.
* **stream_outputs_schema** (dict|Transformer, optional): Structure definition of end component output results. Only applicable to components that support streaming data output, same function as `outputs_schema`. Default value: `None`.
* **response_mode** (str, optional): Specify the ComponentAbility supported by the end component. Currently supports only two modes, default value: `None`.
  
  * If set to `'streaming'`, it means using the `ComponentAbility.STREAM` or `ComponentAbility.TRANSFORM` capability of the end component for output.
  * If not set or set to a value other than `streaming`, it means using the `ComponentAbility.INVOKE` capability of the end component for output.

> **Note**
>
> * When the connection between the component and upstream components is a streaming edge, you can configure stream_inputs_schema as needed.

**Example:**

```python
>>> from openjiuwen.core.workflow import End
>>> # Create End component (specify output template)
>>> end = End({"responseTemplate": "Result:{{final_output}}"})
>>> 
>>> # Register End component to workflow (batch output mode)
>>> workflow.set_end_comp("end", end, inputs_schema={"final_output": "${llm.query}"})
```

### add_workflow_comp
```python
def add_workflow_comp(
            self,
            comp_id: str,
            workflow_comp: ComponentComposable,
            *,
            wait_for_all: bool = None,
            inputs_schema: dict | Transformer = None,
            outputs_schema: dict | Transformer = None,
            stream_inputs_schema: dict | Transformer = None,
            stream_outputs_schema: dict | Transformer = None,
            comp_ability: list[ComponentAbility] = None
    ) -> Self
```

Workflow component addition interface, used to add a specific executable component or sub-workflow component to the current workflow instance, and configure the component's input/output rules, data flow transformation logic, and capability attributes.

**Parameters:**

* **comp_id** (str): Unique identifier of the component, used to reference this component in the workflow.
* **workflow_comp** (ComponentComposable): Workflow component instance to be added.
* **wait_for_all** (bool, optional): Whether to wait for all upstream dependent components to complete execution before executing this component. `True` means waiting for all upstream dependent components to complete execution, `False` means not waiting. Default value: `False`.
* **inputs_schema** (dict|Transformer, optional): Structure definition of component input parameters. Key names should match component input parameter names, and values should be corresponding input parameter types. Default value: `None`.
* **outputs_schema** (dict|Transformer, optional): Structure definition of component output results, used to define output data format specifications for subsequent component parsing. Default value: `None`.
* **stream_inputs_schema** (dict|Transformer, optional): Structure definition of component input parameters. Only applicable to components that support streaming data input, same function as `inputs_schema`; default value: `None`.
* **stream_outputs_schema** (dict|Transformer, optional): Structure definition of component output results. Only applicable to components that support streaming data output, same function as `outputs_schema`. Default value: `None`.
* **comp_ability** (list[ComponentAbility)], optional): Specify the operations supported by the component. When a component has both streaming and batch connections upstream and downstream, the component cannot determine which capabilities to use among transform, invoke, collect, stream, so this value needs to be specified. Default value: `None`, meaning only using invoke capability.

> **Note**
>
> * When the connection between the component and upstream components is a streaming edge, you can configure stream_inputs_schema as needed; when the connection between the component and downstream components is a streaming edge, you can configure stream_outputs_schema as needed.



**Example:**

```python
>>> import os
>>> from datetime import datetime
>>> 
>>> from openjiuwen.core.foundation.llm import ModelClientConfig, ModelRequestConfig
>>> from openjiuwen.core.workflow import LLMComponent, LLMCompConfig, Workflow
>>> 
>>> API_BASE = os.getenv("API_BASE", "mock://api.openai.com/v1")
>>> API_KEY = os.getenv("API_KEY", "sk-fake")
>>> MODEL_NAME = os.getenv("MODEL_NAME", "")
>>> MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "")
>>> os.environ.setdefault("LLM_SSL_VERIFY", "false")  # Disable SSL verification only for local debugging, must enable in production
>>> SYSTEM_PROMPT_TEMPLATE = "You are an AI assistant for query rewriting. Today's date is {}."
>>> 
>>> fake_model_client_config = ModelClientConfig(
...         client_provider="OpenAI",
...         api_key="sk-fake",
...         api_base="https://api.openai.com/v1",
...         timeout=30,
...         max_retries=3,
...         verify_ssl=False
...     )
>>> fake_model_config = ModelRequestConfig(
...         model="fake-model",
...         temperature=0.7,
...         top_p=0.9
...     )
>>> 
>>> def build_current_date():
...      current_datetime = datetime.now()
...      return current_datetime.strftime("%Y-%m-%d")
... 
>>> def _create_llm_component() -> LLMComponent:
...      """Create LLM component, only for extracting structured fields (location/date)."""
...      config = LLMCompConfig(
...             model_client_config=fake_model_client_config,
...             model_config=fake_model_config,
...             template_content=[{"role": "user", "content": "Hello {query}"}],
...             response_format={"type": "text"},
...             output_config={"result": {
...                 "type": "string",
...                 "required": True,
...             }},
...         )
...      return LLMComponent(config)
... 
>>> llm = _create_llm_component()
>>> workflow = Workflow()
>>> 
>>> # Register LLM component to workflow
>>> workflow.add_workflow_comp("llm", llm, inputs_schema={"query": "${start.query}"})
```

### add_connection
```python
def add_connection(self, src_comp_id: str | list[str], target_comp_id: str) -> Self
```

Workflow component connection interface, used to establish dependency relationships between source and target components, defining the direction of data flow and control flow.

**Parameters:**

* **src_comp_id** (str | list[str]): Identifier of the source component, representing the starting point of the connection. When a string is passed, it represents a single starting point; when a list of strings is passed, it represents multiple starting points executing in parallel, all pointing to the same target component.
* **target_comp_id** (str): Unique identifier of the target component, representing the end point of the connection.

**Example:**

```python
>>> # Add normal connection: from "start" component to "llm"
>>> workflow.add_connection("start", "llm")
>>> 
>>> # Add normal connection: "llm1", "llm2", "llm3" execute in parallel and point to "end"
>>> workflow.add_connection(["llm", "llm2", "llm3"], "end")
```

### add_conditional_connection
```python
add_conditional_connection(self, src_comp_id: str, router: Router) -> Self
```

Workflow component conditional connection interface, establishing conditional dependencies from a specified source component to multiple possible target components. Control flow and data flow direction will be dynamically determined based on rules in the router.

**Parameters:**

* **src_comp_id** (str): Unique identifier of the source component.
* **router** ([Router](../graph/graph.md#type-alias-router)): "Router" that determines the next step. It is a regular Python function.

**Example:**

```python
>>> # Get the "query" field value from the output of the "start" component from Context, decide the next component to execute based on the size of query
>>> def router(session):
...     num = session.get_global_state("start.query")
...     if num == 0:
...         return "llm"
...     elif num == 1:
...         return "abc"
...     else:
...         return "end"
... 
>>> # Add conditional connection start -> llm/abc/end
>>> workflow.add_conditional_connection("start", router=router)
```

### add_stream_connection
```python
add_stream_connection(self,  src_comp_id: str,  target_comp_id: str) -> Self
```

Workflow component streaming connection interface, used to establish streaming connections between source and target components, supporting real-time data stream processing and transmission.

**Parameters:**

* **src_comp_id** (str): Unique identifier of the source component, producer component ID that outputs streaming messages. The corresponding component needs to implement the stream interface or transform interface to produce real-time data streams.
* **target_comp_id** (str): Unique identifier of the target component, consumer component ID that accepts streaming input messages. The corresponding component needs to implement the transform or collect interface to receive and process real-time data streams.

**Example:**

```python
>>> # When registering end component, specify streaming connection input definition stream_inputs_schema
>>> workflow.set_end_comp("end_stream", end, stream_inputs_schema={"data": "${llm.output}"})
>>> 
>>> # Add streaming connection: from "llm" to "end", where the LLM component by default implements [stream interface or transform interface], and the end component by default implements [stream interface or transform interface]
>>> workflow.add_stream_connection("llm", "end_stream")
>>> # Note: llm node example see add_workflow_comp() interface above
```

### invoke

```python
async def invoke(
            self,
            inputs: Input,
            session: Session,
            context: ModelContext = None,
            **kwargs
    ) -> WorkflowOutput
```

Workflow execution method that processes complete batch data at once. It receives a complete set of data as input, processes it through the workflow, and returns the complete processing result at once.

**Parameters:**

* **inputs** (Input): Input data of the workflow, serving as initial parameters for execution.
* **session** (Session): Workflow runtime environment, providing execution context and state management.
* **context** (ModelContext, optional): Context engine object for storing user dialogue information. Default value: `None`, meaning context engine functionality is not enabled.
* **kwargs**: Optional parameters, currently supports:
    * **is_sub** (bool): Used to specify whether it is a nested sub-workflow execution, default is `False`, meaning non-sub-workflow execution.
    * **skip_inputs_validate** (bool): Used to specify whether to skip input parameter validation based on `input_params` specified in the card. Default is `False`, meaning validation is required.
**Returns:**

**WorkflowOutput**, execution result of the workflow, which may be normal output or interactive output.

**Example:**

```python
>>> import asyncio
>>> 
>>> from openjiuwen.core.workflow import create_workflow_session

>>> result = asyncio.run(workflow.invoke("Check the weather in Shanghai", create_workflow_session()))
>>> print(f"{result}")
{"state": "COMPLETED", "result": "Result:Check the weather in Shanghai on 2025-08-22"}
```

### stream

```python
async def stream(
            self,
            inputs: Input,
            session: Session,
            context: ModelContext = None,
            stream_modes: list[StreamMode] = None,
            **kwargs
    ) -> AsyncIterator[WorkflowChunk]
```

An async generator method. After executing the workflow through stream, it returns an async generator that progressively produces various chunks throughout the process in a streaming manner; supports multiple stream modes and real-time data transmission, allowing users to see progress immediately and continuously receive supplementary content until the process ends.

**Parameters:**

* **inputs** (Input): Input data of the workflow, serving as initial parameters for execution.
* **session** (Session): Workflow runtime environment, providing execution context and state management.
* **context** (ModelContext, optional): Context engine object for storing user dialogue information. Default value: `None`, meaning context engine functionality is not enabled.
* **stream_modes** (list[StreamMode])], optional): List of stream modes, specifying the behavior and output content of streaming processing. Default value: `None`, same as `TRACE` logic.
* **kwargs**: Optional parameters, currently supports:
    * **is_sub** (bool): Used to specify whether it is a nested sub-workflow execution, default is `False`, meaning non-sub-workflow execution.
    * **skip_inputs_validate** (bool): Used to specify whether to skip input parameter validation based on `input_params` specified in the card. Default is `False`, meaning validation is required.
  
**Returns:**

**AsyncIterator(WorkflowChunk)**, async iterator that progressively produces data chunks from workflow processing.

**Example:**

openJiuwen provides **three streaming output methods**, providing customized output capabilities for streaming information. Since all streaming messages share an output pipeline within the workflow, distinguishing output modes can improve the extensibility of streaming messages.

* Example 1: When stream_modes is `​BaseStreamMode.OUTPUT`, only standard streaming data defined by the framework is output, and the data is of type `OutputSchema`.
  
  ```python
  >>> from openjiuwen.core.workflow import create_workflow_session
  >>> from openjiuwen.core.stream.base import BaseStreamMode, OutputSchema
  >>> async for chunk in workflow.stream({"query": "Check the weather in Shanghai"}, create_workflow_session(), stream_modes=[BaseStreamMode.OUTPUT]):
  ...    print(chunk)
  >>> OutputSchema(type = 'end node stream', index = 0, payload = {'response': 'Result:'}),
  >>> OutputSchema(type = 'end node stream', index = 1, payload ={ 'response': 'Check the weather in Shanghai on 2025-08-22' })
  ```

* Example 2: When stream_modes is `​BaseStreamMode.TRACE`, only debug streaming data defined by the framework is output, and the data is of type `TraceSchema`.
  
  ```python

  >>> import asyncio
  >>> import datetime
  >>> 
  >>> from openjiuwen.core.context_engine import ModelContext
  >>> from openjiuwen.core.session.stream import TraceSchema, BaseStreamMode
  >>> from openjiuwen.core.workflow import create_workflow_session, WorkflowComponent, Input, Output, Workflow, Start, End, \
  ...   LoopGroup, LoopComponent, SubWorkflowComponent
  >>> # 1. Basic workflow
  >>> # Custom component `CustomComponent`, returns input directly as output
  >>> # Define a custom component
  >>> class CustomComponent(WorkflowComponent):
  ...     async def invoke(self, inputs: Input, session: Session, context: Context) -> Output:
  ...         return inputs
  ... 
  >>> # Build basic workflow
  >>> # start -> a -> end
  >>> workflow = Workflow()
  >>> # Set start component
  >>> workflow.set_start_comp("start", Start(), inputs_schema={"num": "${num}"})
  >>> # Set custom component a
  >>> workflow.add_workflow_comp("a", CustomComponent(), inputs_schema={"a_num": "${start.num}"})
  >>> # Set end component
  >>> workflow.set_end_comp("end", End(conf={"responseTemplate": "hello:{{result}}"}),
  ...                   inputs_schema={"result": "${a.a_num}"})
  >>> # Connect components: start -> a -> end
  >>> workflow.add_connection("start", "a")
  >>> workflow.add_connection("a", "end")
  >>> async def run_workflow_base():
  ...     async for chunk in workflow.stream({"num": 1}, session=create_workflow_session(), stream_modes=[BaseStreamMode.TRACE]):
  ...         print(f"stream chunk: {chunk}")

  >>> asyncio.run(run_workflow_base())
  
   >>> # The debug information for component `a` is as follows. You can see that two frames of information are obtained, including one start event frame and one end event frame. Among them, `traceId` is used to identify this debug event. The start event contains the `startTime` value, and `status` corresponds to `"start"`. The end event refreshes the `endTime` value, and `status` corresponds to `"end"`. `parentInvokeId` is the `invokeId` of the previously run component `start`.
   >>> # Debug start event frame for component a
   TraceSchema(type = 'tracer_workflow', payload = {
       'traceId': '86b76988-5549-482b-8401-444b2621641e',
       'startTime': datetime.datetime(2025, 8, 22, 14, 39, 9, 678133),
       'endTime': None,
       'inputs': {
           'a_num': 1,
       },
       'outputs': None,
       'error': None,
       'invokeId': 'a',
       'parentInvokeId': 'start',
       'executionId': '86b76988-5549-482b-8401-444b2621641e',
       'onInvokeData': None,
       'componentId': '',
       'componentName': '',
       'componentType': 'a',
       'loopNodeId': None,
       'loopIndex': None,
       'status': 'start',
       'parentNodeId': ''
   })
   >>> # Debug end event frame for component a
   TraceSchema(type = 'tracer_workflow', payload = {
       'traceId': '86b76988-5549-482b-8401-444b2621641e',
       'startTime': datetime.datetime(2025, 8, 22, 14, 39, 9, 678133),
       'endTime': datetime.datetime(2025, 8, 22, 14, 39, 9, 679167),
       'inputs': {
           'a_num': 1,
       },
       'outputs': {
           'a_num': 1,
       },
       'error': None,
       'invokeId': 'a',
       'parentInvokeId': 'start',
       'executionId': '86b76988-5549-482b-8401-444b2621641e',
       'onInvokeData': None,
       'componentId': '',
       'componentName': '',
       'componentType': 'a',
       'loopNodeId': None,
       'loopIndex': None,
       'status': 'finish',
       'parentNodeId': ''
   })
  
  >>> # 2. Loop workflow
  >>> # Use custom component `CustomComponent` to build a workflow containing loop component, start component, and end component. The loop component uses array loop mode, where the loop body has three serial custom components `a`, `b`, `c`, each outputting the array element of each loop iteration:
  >>> # Custom component returns the value of the "value" field in the component input, assigned to the output variable
  >>> class CustomComponent(WorkflowComponent):
  ...     async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
  ...         return {"output": inputs["value"]}
  ... 
  >>> # Create LoopGroup
  >>> loop_group = LoopGroup()
  >>> 
  >>> # Add 3 workflow components to LoopGroup (using array loop as example)
  >>> loop_group.add_workflow_comp("a", CustomComponent(), inputs_schema={"value": "${loop.item}"})
  >>> loop_group.add_workflow_comp("b", CustomComponent(), inputs_schema={"value": "${loop.item}"})
  >>> loop_group.add_workflow_comp("c", CustomComponent(), inputs_schema={"value": "${loop.item}"})
  >>> 
  >>> # Specify component "a" as loop start, component "c" as loop end
  >>> loop_group.start_nodes(["a"])
  >>> loop_group.end_nodes(["c"])
  >>> 
  >>> # Connect 3 components in LoopGroup, a->b->c
  >>> loop_group.add_connection("a", "b")
  >>> loop_group.add_connection("b", "c")
  >>> 
  >>> # Create LoopComponent
  >>> # First parameter is loop body (LoopGroup instance), second parameter defines the output mode of the loop component
  >>> loop_component = LoopComponent(
  ...   loop_group, 
  ...   {"output": {"a": "${a.output}", "b": "${b.output}", "c": "${c.output}"}}
  ... )
  ... 
  >>> workflow = Workflow()
  >>> 
  >>> # Add start and end components, end component input references loop component output
  >>> workflow.set_start_comp("start", Start(), inputs_schema={"query": "${user_input}"})
  >>> workflow.set_end_comp("end", End(), inputs_schema={"user_var": "${loop.output}"})
  >>> 
  >>> # Add loop component, input array is the query output of the start component
  >>> workflow.add_workflow_comp("loop", loop_component,
  ...                         inputs_schema={"loop_type": "array",
  ...                                      "loop_array": {"item": "${start.query}"}
  ...                                        })
  ... 
  >>> # Serial connection of components: start->loop->end
  >>> workflow.add_connection("start", "loop")
  >>> workflow.add_connection("loop", "end")
  >>> 
  >>> # Execute workflow through `flow.stream` method and specify `stream_modes=[BaseStreamMode.TRACE]` to get streaming debug information of the loop workflow:
  >>> async def run_workflow_loop():
  >>>     async for chunk in workflow.stream({"user_input": [1, 2, 3]}, session=create_workflow_session(), stream_modes=[BaseStreamMode.TRACE]):
  ...         print(f"stream chunk: {chunk}")
  >>> asyncio.run(run_workflow_loop())
   >>> # The debug information for component `a` located in the loop body is as follows. You can see that two frames of information are obtained, including one start event frame and one end event frame. Among them, `loopIndex` indicates that the component is in loop component `loop`, with a value of 2 (counting from 0), indicating that it is currently in the 3rd loop execution state. At the same time, the `invokeId` of component `a` in the loop body is `loop.a`, and its `parentInvokeId` is `loop.c`, indicating that the previously executed component was component `c` in the loop body from the previous loop.
   >>> # Component a's 3rd run in loop component, debug start frame
   TraceSchema(type = 'tracer_workflow', payload = {
       'traceId': 'dad9cd8c-ac77-484f-90ae-20b263276e3f',
       'startTime': datetime.datetime(2025, 8, 25, 19, 31, 48, 361042),
       'endTime': None,
       'inputs': {
           'value': 3
       },
       'outputs': None,
       'error': None,
       'invokeId': 'loop.a',
       'parentInvokeId': 'loop.c',
       'executionId': 'dad9cd8c-ac77-484f-90ae-20b263276e3f',
       'onInvokeData': None,
       'componentId': '',
       'componentName': '',
       'componentType': 'loop.c',
       'loopNodeId': 'loop',
       'loopIndex': 2,
       'status': 'start',
       'parentNodeId': ''
     })
   # Component a's 3rd run in loop component, debug end frame
   TraceSchema(type = 'tracer_workflow', payload = {
       'traceId': 'dad9cd8c-ac77-484f-90ae-20b263276e3f',
       'startTime': datetime.datetime(2025, 8, 25, 19, 31, 48, 361042),
       'endTime': datetime.datetime(2025, 8, 25, 19, 31, 48, 361042),
       'inputs': {
           'value': 3
       },
       'outputs': {
           'output': 3
       },
       'error': None,
       'invokeId': 'loop.a',
       'parentInvokeId': 'loop.c',
       'executionId': 'dad9cd8c-ac77-484f-90ae-20b263276e3f',
       'onInvokeData': None,
       'componentId': '',
       'componentName': '',
       'componentType': 'loop.a',
       'loopNodeId': 'loop',
       'loopIndex': 2,
       'status': 'finish',
       'parentNodeId': ''
     })
   ...
  
  >>> # 3. Nested workflow
  >>> # Build a sub-workflow containing start component, end component, and the previously custom `CustomComponent`. The sub-workflow output equals the input when calling the workflow:
  >>> # Sub-workflow: sub_start -> sub_a -> sub_end
  >>> sub_workflow = Workflow()
  >>> # Sub-workflow start component is sub_start
  >>> sub_workflow.set_start_comp("sub_start", Start(),
  ...                             inputs_schema={
  ...                                 "num": "${a_num}"})
  >>> # Sub-workflow component sub_a
  >>> sub_workflow.add_workflow_comp("sub_a", CustomComponent(),
  ...                                inputs_schema={
  ...                                    "a_num": "${sub_start.num}"})
  >>> # Sub-workflow end component sub_end
  >>> sub_workflow.set_end_comp("sub_end",
  ...                           End(),
  ...                           inputs_schema={
  ...                               "result": "${sub_a.a_num}"
  ...                           })
  >>> # Connect sub-workflow components: sub_start -> sub_a -> sub_end
  >>> sub_workflow.add_connection("sub_start", "sub_a")
  >>> sub_workflow.add_connection("sub_a", "sub_end")
  >>> 
  >>> # Build nested workflow, containing start component, end component, and using sub-workflow as main workflow component "a". Main workflow output also equals the input when calling the main workflow:
  >>> # workflow: start -> a(sub-workflow) -> end
  >>> # Sub-workflow: sub_start -> sub_a -> sub_end
  >>> main_workflow = Workflow()
  >>> # Set workflow start component as start
  >>> main_workflow.set_start_comp("start", Start(),
  ...                              inputs_schema={
  ...                                  "num": "${num}"})
  >>> # Define workflow component a
  >>> main_workflow.add_workflow_comp("a", SubWorkflowComponent(sub_workflow),
  ...                                 inputs_schema={
  ...                                     "a_num": "${start.num}"})
  >>> # Set workflow end component as end
  >>> # Sub-workflow End component output {"output": {"result": ...}}, so main workflow references ${a.result}
  >>> main_workflow.set_end_comp("end",
  ...                            End(conf={"responseTemplate": "hello:{{result}}"}),
  ...                            inputs_schema={
  ...                                "result": "${a.result}"
  ...                            })
  >>> # Connect workflow components: start -> a -> end
  >>> main_workflow.add_connection("start", "a")
  >>> main_workflow.add_connection("a", "end")
  >>> 
  >>> # Execute workflow through `flow.stream` method and specify `stream_modes=[BaseStreamMode.TRACE]` to get streaming debug information of the nested workflow:
  >>> async def run_workflow_sub():
  >>>     async for chunk in main_workflow.stream({"num": 1}, session=create_workflow_session(), stream_modes=[BaseStreamMode.TRACE]):
  ...         print(f"stream chunk: {chunk}")
  >>> asyncio.run(run_workflow_sub())
  >>> # The debug information for the `sub_a` component in the sub-workflow is as follows. You can see that two frames of information are obtained, including one start event frame and one end event frame. Among them, the `parentNodeId` field of this component records the `invokeId` of the sub-workflow it belongs to, which is `"a"`. This field is mainly used for nested sub-workflow scenarios to clarify the hierarchical relationship and ownership structure of components. At the same time, the `sub_a` component is in the sub-workflow component `a`, so its `invokeId` is `"a.sub_a"`, used to identify the invocation of this component in the entire workflow.
  >>> # Debug start frame for sub_a component in sub-workflow
  TraceSchema(type = 'tracer_workflow', payload = {
      'traceId': '416c45dd-12f1-427e-943f-e16c2171c69d',
      'startTime': datetime.datetime(2025, 8, 22, 17, 38, 13, 535141),
      'endTime': None,
      'inputs': {
        'a_num': 1
      },
      'outputs': None,
      'error': None,
      'invokeId': 'a.sub_a',
      'parentInvokeId': 'a.sub_start',
      'executionId': '416c45dd-12f1-427e-943f-e16c2171c69d',
      'onInvokeData': None,
      'componentId': '',
      'componentName': '',
      'componentType': 'a.sub_a',
      'loopNodeId': None,
      'loopIndex': None,
      'status': 'start',
      'parentNodeId': 'a'
  }), 
  >>> # Debug end frame for sub_a component in sub-workflow
  TraceSchema(type = 'tracer_workflow', payload = {
      'traceId': '416c45dd-12f1-427e-943f-e16c2171c69d',
      'startTime': datetime.datetime(2025, 8, 22, 17, 38, 13, 535141),
      'endTime': datetime.datetime(2025, 8, 22, 17, 38, 13, 535141),
      'inputs': {
        'a_num': 1
      },
      'outputs': {
        'a_num': 1,
      },
      'error': None,
      'invokeId': 'a.sub_a',
      'parentInvokeId': 'a.sub_start',
      'executionId': '416c45dd-12f1-427e-943f-e16c2171c69d',
      'onInvokeData': None,
      'componentId': '',
      'componentName': '',
      'componentType': 'a.sub_a',
      'loopNodeId': None,
      'loopIndex': None,
      'status': 'finish',
      'parentNodeId': 'a'
  })
  ```

* Example 3: When stream_modes is `BaseStreamMode.CUSTOM​`, only user-defined streaming data is output, and the data is of type `CustomSchema`.
  
  ```python
  >>> class TestLLMExecutable(LLMExecutable):
  ...     async def prepare_model_inputs(self, input_):
  ...         return await super()._prepare_model_inputs(input_)
  ...
  >>> class LLMComponent(WorkflowComponent):
  ...     async def invoke(self, inputs: Input, session: BaseSession, context: ModelContext) -> Output:
  ...         exe = TestLLMExecutable(LLMCompConfig(
  ...             model_client_config=fake_model_client_config,
  ...             model_config=fake_model_config,
  ...             template_content=[{"role": "user", "content": "Hello {query}"}],
  ...             response_format={"type": "text"},
  ...             output_config={"result": {
  ...                 "type": "string",
  ...                 "required": True,
  ...             }},
  ...         ))
  ...         model_inputs = await exe.prepare_model_inputs(fake_input(userFields=dict(query="pytest")))
  ...         llm_response = await llm.ainvoke(model_inputs)
  ...         response = llm_response.content
  ...
  ...         await session.write_custom_stream(**dict(custom_output=response))
  ...
  ...         # 2. Call workflow streaming output
  ...         async for chunk in workflow.stream({"query": "Check the weather in Shanghai"}, session=create_workflow_session(),
  ...                                           stream_modes=[BaseStreamMode.CUSTOM]):
  ...             print(chunk)
  ...
  >>> def fake_input():
  ...     return lambda **kw: {USER_FIELDS: kw}
  ```


### draw

```python
def draw(
        self,
        title: str = "",
        output_format: str = "mermaid",  # "mermaid", "png", "svg"
        expand_subgraph: int | bool = False,
        enable_animation: bool = False,  # only works for "mermaid" format
        **kwargs
    ) -> str | bytes
```
Generate a visualization diagram of the workflow, supporting export in three formats: Mermaid syntax text, PNG static image, and SVG vector image. Unified encapsulation of workflow structure visualization capabilities, suitable for workflow debugging, documentation generation, and visualization display scenarios.

**Parameters:**

* **title** (str, optional): Visualization diagram title, displayed at the top of the diagram after rendering. Default value: "".
* **output_format** (str, optional): Specify output format, only supports `"mermaid"`/`"png"`/`"svg"` three types. Default value: `"mermaid"`.
* **expand_subgraph** (int | bool, optional): Sub-workflow expansion configuration. Boolean value controls full expansion / no expansion, non-negative integer controls subgraph expansion depth. Default value: `False`.
* **enable_animation** (bool, optional): Mermaid format animation switch, only effective for Mermaid syntax. After enabling, streaming edges can display dynamic effects. Default value: `False`.
* **kwargs** (dict, optional): Additional rendering configuration items for passing custom parameters to the underlying renderer.

**Returns:**
* **str**: When output_format="mermaid", returns a standard Mermaid flowchart syntax string;
* **bytes**: When output_format="png" or output_format="svg", returns binary data stream of the corresponding format image.

**Example:**

```python
>>> from openjiuwen.core.workflow import End
>>> from openjiuwen.core.workflow import Start
>>> from openjiuwen.core.context_engine.base import ModelContext
>>> from openjiuwen.core.graph.executable import Output
>>> from openjiuwen.core.workflow.components import Session
>>> from openjiuwen.core.workflow import Input, Workflow, WorkflowComponent
>>> 
>>> 
>>> # Custom component
>>> class Node1(WorkflowComponent):
...     def __init__(self):
...         super().__init__()
... 
...     async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
...         return {}
... 
>>> 
>>> # Set environment variable to enable workflow visualization functionality
>>> import os
>>> os.environ["WORKFLOW_DRAWABLE"] = "true"
>>> 
>>> # Build workflow
>>> flow = Workflow()
>>> flow.set_start_comp("start", Start())
>>> flow.add_workflow_comp("a", Node1())
>>> flow.set_end_comp("end", End())
>>> flow.add_connection("start", "a")
>>> flow.add_connection("a", "end")
>>> 
>>> # Print workflow's mermaid script
>>> print(flow.draw(title="simple workflow",output_format="mermaid"))
...
---
title: simple workflow
---
flowchart TB
	node_1("start")
	node_2["a"]
	node_3("end")
	node_1 --> node_2
	node_2 --> node_3

```

## class openjiuwen.core.workflow.Session

```python
class openjiuwen.core.workflow.Session(parent: "AgentSession" = None, session_id: str = None, envs: dict[str, Any] = None)
```

The core runtime session for workflow execution. This class provides session management for workflow execution scenarios.

**Parameters:**

- **parent**(AgentSession, optional): The session of the parent Agent associated with this workflow execution. Default: `None`.
- **session_id**(str, optional): The unique identifier of the session. Default: `None`. If not provided, a UUID will be automatically generated.
- **envs**(dict[str, Any], optional): Environment variables used during workflow execution. Default: `None`.

**Example:**

```python
>>> from openjiuwen.core.workflow import Session
>>> 
>>> session = Session(session_id="123")
>>> 
```

### get_callback_manager

```python
get_callback_manager(self) -> CallbackManager
```

Returns the callback manager for the current workflow execution.

**Returns:**

**CallbackManager**: The callback manager associated with the current workflow execution.

**Example:**

```python
>>> from openjiuwen.core.workflow import Session
>>> 
>>> session = Session()
>>> 
>>> callback_manager = session.get_callback_manager()
```

### get_session_id

```python
get_session_id(self) -> str
```

Returns the unique session identifier for the current workflow execution.

**Returns:**

**str**: The unique session identifier of the current workflow.

**Example:**

```python
>>> from openjiuwen.core.workflow import Session
>>> 
>>> session = Session(session_id="123")
>>> 
>>> print(f"session id is: {session.get_session_id()}")
session id is: 123
```

### get_envs

```python
get_envs(self)
```

Retrieves the environment variables configured for the current workflow execution.

**Returns:**

**dict[str, Any]**: The environment variables configured for the current workflow execution.

**Example:**

```python
>>> from openjiuwen.core.workflow import Session
>>> from openjiuwen.core.session import WORKFLOW_EXECUTE_TIMEOUT
>>>
>>> session = Session(envs={WORKFLOW_EXECUTE_TIMEOUT: 100})
>>> 
>>> print(f"envs is: {session.get_envs()}")
envs is: {'_execute_timeout': 100}
```

### get_parent

```python
get_parent(self)
```

Returns the Agent session associated with the current workflow execution.

**Returns:**

**AgentSession**: The Agent session to which the current workflow execution belongs.

**Example:**

```python
>>> from openjiuwen.core.workflow import Session
>>> from openjiuwen.core.single_agent import Session as AgentSession
>>> 
>>> session = Session(parent=AgentSession())
>>> 
>>> agent_session = session.get_parent()
```

### set_workflow_card

```python
set_workflow_card(self, card)
```

Sets the workflow card information for the current session.

**Parameters:**

- **card**(WorkflowCard): The workflow card information.

**Example:**

```python
>>> from openjiuwen.core.workflow import Session, WorkflowCard
>>> 
>>> session = Session()
>>> session.set_workflow_card(WorkflowCard(id="workflow_id"))
```

### get_workflow_card

```python
get_workflow_card(self)
```

Retrieves the workflow card information associated with the current session.

**Returns:**

**WorkflowCard**: The workflow card information of the current session.

**Example:**

```python
>>> from openjiuwen.core.workflow import Session, WorkflowCard
>>> 
>>> session = Session()
>>> session.set_workflow_card(WorkflowCard(id="workflow_id"))
>>> print(session.get_workflow_card())
id='workflow_id' name='' description='' version='' input_params=None
```

## function create_workflow_session

```python
create_workflow_session(parent: "AgentSession" = None, session_id: str = None, envs: dict[str, Any] = None) -> Session
```

Creates a session for workflow execution.

**Parameters:**

- **parent**(AgentSession, optional): The session of the parent Agent. Default: `None`.
- **session_id**(str, optional): The unique identifier of the session. Default: `None`. If not provided, a UUID will be automatically generated.
- **envs**(dict[str, Any], optional): Environment variables used during workflow execution. Default: `None`.

**Returns:**

**Session**: The session required for workflow execution.

**Example:**

```python
>>> from openjiuwen.core.workflow import create_workflow_session
>>> 
>>> session = create_workflow_session(session_id="123")
>>> 
>>> print(f"session id is: {session.get_session_id()}")
session id is: 123
```