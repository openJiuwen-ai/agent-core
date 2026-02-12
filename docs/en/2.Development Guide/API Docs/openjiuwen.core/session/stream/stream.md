# openjiuwen.core.session.stream

## class openjiuwen.core.session.stream.BaseStreamMode

```python
class openjiuwen.core.stream.BaseStreamMode
```

Setting different `BaseStreamMode` types in the workflow's `stream` method can control the data format and content of streaming output.

- **OUTPUT**: Standard streaming output data format, output is `OutputSchema` type.
- **TRACE**: Debug information streaming output data format, output is `TraceSchema` type.
- **CUSTOM**: Custom streaming output data format, output is `CustomSchema` type.

## class openjiuwen.core.session.stream.CustomSchema

```python
class openjiuwen.core.session.stream.CustomSchema
```

Data class for user-defined streaming output data format.

- **kwargs** (dict, optional): User-defined streaming data.

**Example:**

Based on [BaseStreamMode.CUSTOM](#class-openjiuwencoresessionstreamcustomschema) example, the output obtained:

```python
CustomSchema(custom_output = 'Check the weather in Shanghai on 2025-08-22')
```

## class openjiuwen.core.session.stream.OutputSchema

```python
class openjiuwen.core.session.stream.OutputSchema
```

Data class for openJiuwen standard streaming output data format.

- **type** (str): Type of streaming data. Currently supports two built-in streaming message types:
  - `'INTERACTION'`: Indicates that the streaming message is an interruption streaming message for interruption interaction
  - `'end node stream'`: Indicates that the streaming message is a streaming message from the [End](../../workflow/components/flow/end_comp.md) component.
- **index** (int): Index of streaming data.
- **payload** (Any): Data content of streaming data.

**Example:**

Based on [​BaseStreamMode.OUTPUT](#class-openjiuwencoresessionstreamoutputschema) example, the output obtained:

```python
from openjiuwen.core.session.stream import OutputSchema

OutputSchema(type = 'end node stream', index = 0, payload = {'answer': 'Result:'}),
OutputSchema(type = 'end node stream', index = 1, payload ={ 'answer': 'Check the weather in Shanghai on 2025-08-22' })
```

## class openjiuwen.core.session.stream.StreamMode

```python
class openjiuwen.core.session.stream.StreamMode
```

Base class for openJiuwen [streaming output data format](#class-openjiuwencoresessionstreambasestreammode).

**Example:**

Print the `OUTPUT` format of the subclass [BaseStreamMode](#class-openjiuwencoresessionstreambasestreammode) of this class.

```python
from openjiuwen.core.session.stream import BaseStreamMode

print(BaseStreamMode.OUTPUT)
```

Output is as follows:
```python
StreamMode(mode=output, desc=Standard stream data defined by the framework, options={})
```

## class openjiuwen.core.session.stream.StreamSchemas

```python
class openjiuwen.core.session.stream.StreamSchemas
```

Collection class for openjiuwen streaming data formats, including [OutputSchema](#class-openjiuwencoresessionstreamoutputschema), [CustomSchema](#class-openjiuwencoresessionstreamcustomschema), [TraceSchema](#class-openjiuwencoresessionstreamtraceschema).

## class openjiuwen.core.session.stream.TraceSchema

```python
class openjiuwen.core.session.stream.TraceSchema
```

Data class for debug information streaming output data format.

- **type** (str): Type of streaming data. Currently supports two built-in debug information types:
  
  - `'tracer_workflow'`: Indicates workflow component debugging information.
  - `'tracer_agent'`: Indicates agent debugging information, including LLM and plugin calls.
- **payload** (Any): Debug information data, with built-in format definitions, including key fields such as unique identifier, start/end time, input/output, error details, and component status.
  
  - Basic debug fields:

    | Field                      | Type         | Description                                                                 |
    |---------------------------|--------------|----------------------------------------------------------------------|
    | traceId                   | str          | Debug id                                                               |
    | startTime                 | datetime     | Start time, e.g., `datetime.datetime(2025, 8, 22, 14, 39, 9, 678133)` represents August 22, 2025 at 2:39:09 PM with 678133 microseconds |
    | endTime                   | datetime     | End time, e.g., `datetime.datetime(2025, 8, 22, 14, 40, 9, 678133)` represents August 22, 2025 at 2:40:09 PM with 678133 microseconds |
    | inputs                    | dict         | Component input, e.g., `{'a_num': 1}`, which are the input parameters required by the component                         |
    | outputs                   | dict         | Component output, e.g., `{'result': 2}`, which are the outputs corresponding to component parameters                   |
    | error                     | dict         | Component exception, e.g., `{"error_code": -1, "message": TypeError}`, containing error code and exception information |
    | invokeId                  | str          | Event id                                                               |
    | parentInvokeId            | str          | Parent event id, which is the event id of the previously executed component                                 |
    | executionId               | str          | Execution id, equal to debug id                                                   |
    | onInvokeData              | List[dict]   | Used to record runtime information during component execution                                   |
    | componentId               | str          | Component id, generally empty if no special value is passed                                         |
    | componentName             | str          | Component name, generally empty if no special value is passed                                         |
    | componentType             | str          | Component type, e.g., when using `workflow.add_workflow_comp("a", CustomComponent())` to add a component to a workflow, "a" is the componentType of that component                                     |
    | status                    | str          | Component status, including "start", "running", "finish", "error", representing component start, component running, component finish, and component exception respectively |
  
  - Loop component fields:
    | Field             | Type   | Description                                           |
    |------------------|--------|------------------------------------------------|
    | loopNodeId       | str    | When the component is in a loop component, this is the invokeId of the loop component it belongs to |
    | loopIndex        | int    | Loop count, starting from 1                          |
  
  - Nested sub-workflow fields:
    | Field             | Type   | Description                                                                 |
    |------------------|--------|----------------------------------------------------------------------|
    | parentNodeId     | str    | If the component is in a sub-workflow, this is the invokeId of the sub-workflow it belongs to, otherwise it is "" |
  
**Example:**

- Example 1: Based on [BaseStreamMode.TRACE](#class-openjiuwencoresessionstreambasestreammode) basic workflow example, the output obtained.
  
  ```python
  import datetime
  from openjiuwen.core.session.stream import TraceSchema
  # The debug information for component `a` is as follows. You can see that two frames of information are obtained, including one start event frame and one end event frame. Among them, `traceId` is used to identify this debug event. The start event contains the `startTime` value, and `status` corresponds to `"start"`. The end event refreshes the `endTime` value, and `status` corresponds to `"end"`. `parentInvokeId` is the `invokeId` of the previously run component `start`.
  # Debug start event frame for component a
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
  # Debug end event frame for component a
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
  ```

- Example 2: Based on [BaseStreamMode.TRACE](#class-openjiuwencoresessionstreambasestreammode) loop workflow example, the output obtained.
  
  ```python
  import datetime
  from openjiuwen.core.session.stream import TraceSchema
  # The debug information for component `a` located in the loop body is as follows. You can see that two frames of information are obtained, including one start event frame and one end event frame. Among them, `loopIndex` indicates that the component is in loop component `loop`, with a value of 3, indicating that it is currently in the 3rd loop execution state. At the same time, the `invokeId` of component `a` in the loop body is `loop.a`, and its `parentInvokeId` is `loop.c`, indicating that the previously executed component was component `c` in the loop body from the previous loop.
  # ...
  # Component a's 3rd run in loop component, debug start frame
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
      'loopIndex': 3,
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
      'loopIndex': 3,
      'status': 'finish',
      'parentNodeId': ''
    })
  ...
  ```

- Example 3: Based on [BaseStreamMode.TRACE](#class-openjiuwencoresessionstreambasestreammode) nested workflow, the output obtained.
  
  ```python
  import datetime
  from openjiuwen.core.session.stream import TraceSchema
  # The debug information for the `sub_a` component in the sub-workflow is as follows. You can see that two frames of information are obtained, including one start event frame and one end event frame. Among them, the `parentNodeId` field of this component records the `invokeId` of the sub-workflow it belongs to, which is `"a"`. This field is mainly used for nested sub-workflow scenarios to clarify the hierarchical relationship and ownership structure of components. At the same time, the `sub_a` component is in the sub-workflow component `a`, so its `invokeId` is `"a.sub_a"`, used to identify the invocation of this component in the entire workflow.
  # Debug start frame for sub_a component in sub-workflow
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
  # Debug end frame for sub_a component in sub-workflow
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
  ...
  ```
