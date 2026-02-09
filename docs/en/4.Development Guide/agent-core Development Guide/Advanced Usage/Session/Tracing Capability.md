Session provides a tracing module to make diagnostics convenient for users. During Workflow execution, the module automatically collects event information and sends it to users in real time as **streaming messages**, transparently presenting the system's internal states to users. It also supports user-injected custom tracing information. openJiuwen provides the following two tracing capabilities:

* **Basic tracing capability**: Session's tracing in Workflow mode records the start and final completion states of the entire workflow. It also performs more fine-grained event collection throughout the Workflow execution, including:
  
  * **Component execution**: Track the start, end, and execution status of each component.
  * **Complex logic enhancement**: Enhanced collection for complex structures such as **loops and conditional branches**.
  * **Execution exceptions**: Errors thrown during Workflow execution are automatically captured.
* **Custom tracing capability**: Session also provides standardized tracing information collection interfaces, allowing developers to inject and collect **custom tracing information**.

# Basic tracing capability

Refer to the API documentation [openjiuwen.core.session.stream.TraceSchema](../../API Docs/openjiuwen.core/session/stream/stream.md#class-openjiuwencoresessionstreamtraceschema) for the tracing fields.

Below are key fields and output examples for three typical tracing scenarios: basic workflow tracing, loop component tracing, and nested sub-workflow tracing.

## Basic workflow scenario

We use a basic workflow to illustrate the basic tracing information.

A custom component `CustomComponent` returns inputs directly as outputs, making the explanation of tracing information clearer:

```python
from openjiuwen.core.workflow import WorkflowComponent
from openjiuwen.core.context_engine import ModelContext
from openjiuwen.core.workflow import Input, Output
from openjiuwen.core.workflow.components import Session

# Define a custom component
class CustomComponent(WorkflowComponent):
    def __init__(self):
        super().__init__()

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        return inputs
```

Build a basic workflow with the process start -> a -> end. The final output of the workflow equals the inputs at invocation time:

```python
from openjiuwen.core.workflow import Workflow
from openjiuwen.core.workflow import End
from openjiuwen.core.workflow import Start
"""
Build a basic workflow
start -> a -> end
"""
workflow = Workflow()
# Set the start component
workflow.set_start_comp("start", Start(), inputs_schema={"num": "${num}"})
# Set the custom component a
workflow.add_workflow_comp("a", CustomComponent(), inputs_schema={"a_num": "${start.num}"})
# Set the end component
workflow.set_end_comp("end", End(conf={"responseTemplate": "hello:{{result}}"}),
                      inputs_schema={"result": "${a.a_num}"})
# Connect components: start -> a -> end
workflow.add_connection("start", "a")
workflow.add_connection("a", "end")
```

Execute the workflow via `flow.stream` and specify `stream_modes=[BaseStreamMode.TRACE]` to get tracing information in streaming output for the basic workflow:

```python
import asyncio
from openjiuwen.core.workflow import create_workflow_session
from openjiuwen.core.session.stream import BaseStreamMode

async def run_workflow():
    session = create_workflow_session()
    async for chunk in workflow.stream({"num": 1}, session, stream_modes=[BaseStreamMode.TRACE]):
        print(f"stream chunk: {chunk}")


if __name__ == '__main__':
    asyncio.run(run_workflow())
```

Below is the tracing information for component `a`. You get two frames: one start event and one finish event. Here, `traceId` identifies the tracing event.
- The start event contains the special field `startTime`, and `status` is 'start'.
- The finish event contains the special field `endTime`, and `status` is 'finish'.

```python
...
# Trace start event frame for component a
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
# Trace finish event frame for component a
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
...
```

## Loop workflow scenario

We use a loop workflow example to illustrate loop component tracing information.
Again using the custom component `CustomComponent`, we build a workflow that includes a loop component, a start component, and an end component. The loop component uses array loop mode, and inside the loop body there are three chained custom components `a`, `b`, and `c`, each outputting the array element for each iteration:

```python
from openjiuwen.core.workflow import WorkflowComponent
from openjiuwen.core.workflow.components.loop import LoopGroup, LoopComponent
from openjiuwen.core.context_engine import ModelContext
from openjiuwen.core.workflow import Input, Output
from openjiuwen.core.workflow.components import Session
from openjiuwen.core.workflow import Workflow
from openjiuwen.core.workflow import End
from openjiuwen.core.workflow import Start

# Custom component returns the value of the "value" field in the component inputs as "output"
class CustomComponent(WorkflowComponent):
    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        return {"output": inputs["value"]}

# Create LoopGroup
loop_group = LoopGroup()

# Add 3 workflow components to the LoopGroup (array loop as an example)
loop_group.add_workflow_comp("a", CustomComponent(), inputs_schema={"value":"${loop.item}"})
loop_group.add_workflow_comp("b", CustomComponent(), inputs_schema={"value":"${loop.item}"})
loop_group.add_workflow_comp("c", CustomComponent(), inputs_schema={"value":"${loop.item}"})

# Set component "a" as loop start and component "c" as loop end
loop_group.start_nodes(["a"])
loop_group.end_nodes(["c"])

# Connect components within LoopGroup: a->b->c
loop_group.add_connection("a", "b")
loop_group.add_connection("b", "c")

# Create the loop component using array loop. The input array comes from the loop component's input schema
loop_component = LoopComponent(
    loop_group,
    {"output": {"a": "${a.output}", "b": "${b.output}", "c": "${c.output}"}}
)

workflow = Workflow()

# Add start and end components; the end component’s inputs reference the loop component’s outputs
workflow.set_start_comp("start", Start(), inputs_schema={"query": "${array}"})
workflow.set_end_comp("end", End(), inputs_schema={"user_var": "${loop.output}"})

# Add the loop component; its input array is the start component’s query output
workflow.add_workflow_comp("loop", loop_component, inputs_schema={"loop_type": "array", "loop_array": {"item": "${start.query}"}})

# Connect components in series: start->loop->end
workflow.add_connection("start", "loop")
workflow.add_connection("loop", "end")
```

Execute the workflow via `flow.stream` and specify `stream_modes=[BaseStreamMode.TRACE]` to get streaming output tracing information for the loop workflow:

```python
import asyncio
from openjiuwen.core.workflow import create_workflow_session
from openjiuwen.core.session.stream import BaseStreamMode

async def run_workflow():
    session = create_workflow_session()
    async for chunk in workflow.stream({"array": [1, 2, 3]}, session, stream_modes=[BaseStreamMode.TRACE]):
        print(f"stream chunk: {chunk}")

if __name__ == "__main__":
    asyncio.run(run_workflow())
```

Below is the tracing information for component `a` inside the loop body. You get two frames: a start event and a finish event. Here, `loopIndex` indicates this component is inside the loop component `loop`, with a value of 3, which means we are in the 3rd loop iteration. At the same time, the `invokeId` of `a` inside the loop body is `loop.a`, and its `parentInvokeId` is `loop.c`, indicating that the previously executed component was component `c` from the previous iteration.

```python
...
# The 3rd run of component a within the loop component, trace start frame
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
    'componentType': 'loop.a',
    'loopNodeId': 'loop',
    'loopIndex': 3,
    'status': 'start',
    'parentNodeId': ''
})
# The 3rd run of component a within the loop component, trace finish frame
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

## Nested sub-workflow scenario

We use a nested workflow example to illustrate nested sub-workflow tracing information.

First, build a sub-workflow that includes a start component, an end component, and the previously defined `CustomComponent`. The sub-workflow’s output is equal to the input provided when invoking the workflow:

```python
from openjiuwen.core.workflow import WorkflowComponent
from openjiuwen.core.context_engine import ModelContext
from openjiuwen.core.workflow import Input, Output
from openjiuwen.core.workflow.components import Session
from openjiuwen.core.workflow import Workflow
from openjiuwen.core.workflow import End
from openjiuwen.core.workflow import Start

# Custom component returns the value of the "a_num" field in the inputs as "a_num"
class CustomComponent(WorkflowComponent):
    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        return {"a_num": inputs["a_num"]}

# sub-workflow: sub_start -> sub_a -> sub_end
sub_workflow = Workflow()
# Set the sub-workflow start component to sub_start
sub_workflow.set_start_comp("sub_start", Start(),
                            inputs_schema={
                                "num": "${a_num}"})
# The sub-workflow component sub_a
sub_workflow.add_workflow_comp("sub_a", CustomComponent(),
                               inputs_schema={
                                   "a_num": "${sub_start.num}"})
# Set the sub-workflow end component to sub_end
sub_workflow.set_end_comp("sub_end",
                          End(conf={"responseTemplate": "hello:{{result}}"}),
                          inputs_schema={
                              "result": "${sub_a.a_num}"
                          })
# Connect sub-workflow components: sub_start -> sub_a -> sub_end
sub_workflow.add_connection("sub_start", "sub_a")
sub_workflow.add_connection("sub_a", "sub_end")
```

Build the nested workflow that includes a start component, an end component, and uses the sub-workflow as the main workflow’s component "a". The main workflow’s output also equals the input provided when invoking the main workflow:

```python
from openjiuwen.core.workflow.components.flow import SubWorkflowComponent

"""
workflow: start -> a (sub-workflow) -> end
sub-workflow: sub_start -> sub_a -> sub_end
"""
# workflow: start->a (sub-workflow)->end
main_workflow = Workflow()
# Set the main workflow start component to start
main_workflow.set_start_comp("start", Start(),
                             inputs_schema={
                                 "num": "${inputs.num}"})
# Define workflow component a
main_workflow.add_workflow_comp("a", SubWorkflowComponent(sub_workflow),
                                inputs_schema={
                                    "a_num": "${start.num}"})
# Set the main workflow end component to end
main_workflow.set_end_comp("end",
                           End(conf={"responseTemplate": "hello:{{result}}"}),
                           inputs_schema={
                               "result": "${a.a_num}"
                           })
# Connect main workflow components: start -> a -> end
main_workflow.add_connection("start", "a")
main_workflow.add_connection("a", "end")
```

Execute the workflow via `flow.stream` and specify `stream_modes=[BaseStreamMode.TRACE]` to get streaming output tracing information for the nested workflow:

```python
import asyncio
from openjiuwen.core.workflow import create_workflow_session
from openjiuwen.core.session.stream import BaseStreamMode


async def run_workflow():
    session = create_workflow_session()
    async for chunk in main_workflow.stream({"num": 1}, session,
                                            stream_modes=[BaseStreamMode.TRACE]):
        print(f"stream chunk: {chunk}")


if __name__ == "__main__":
    asyncio.run(run_workflow())
```

Below is the tracing information for the `sub_a` component in the sub-workflow. You get two frames: a start event and a finish event. In particular, the `parentNodeId` field records the `invokeId` of the sub-workflow to which it belongs, namely `"a"`. This field is mainly used in nested sub-workflow scenarios to clarify component hierarchy and ownership. Meanwhile, since `sub_a` is inside the sub-workflow component `a`, its `invokeId` is `"a.sub_a"`, which identifies its invocation within the entire workflow.

```python
...
# Trace start frame for sub_a component in the sub-workflow
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
# Trace finish frame for sub_a component in the sub-workflow
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

# Custom tracing capability

To facilitate the debugging of custom components, Session provides interfaces that allow developers to inject custom tracing information into components.

Create a custom component `AddComponent` for addition operations, and call Session's `trace` and `trace_error` capabilities to record custom tracing information.

```python
import time

from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.workflow import WorkflowComponent
from openjiuwen.core.context_engine import ModelContext
from openjiuwen.core.workflow import Input, Output
from openjiuwen.core.workflow.components import Session


class AddComponent(WorkflowComponent):
    def __init__(self):
        super().__init__()


    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        a = inputs.get('a')
        b = inputs.get('b')
        if not a:
            error = JiuWenBaseException(-1, 'a is not exit')
            await session.trace_error(error)
            raise error
        if not b:
            error = JiuWenBaseException(-1, 'b is not exit')
            await session.trace_error(error)
            raise error
        start_time = time.time()
        result = a + b
        cost = time.time() - start_time
        await session.trace({"a": a, "b": b, "cost": cost})
        return {"output": result}

```

Build a simple workflow and add the custom `AddComponent` to the workflow.

```python
from openjiuwen.core.workflow import Workflow
from openjiuwen.core.workflow import End
from openjiuwen.core.workflow import Start

flow = Workflow()
flow.set_start_comp("start", Start())
flow.add_workflow_comp("add", AddComponent(), inputs_schema={"a": "${inputs.a}", "b": "${inputs.b}"})
flow.set_end_comp("end", End(), inputs_schema={"result": "${add.output}"})
flow.add_connection("start", "add")
flow.add_connection("add", "end")
```

Invoke the workflow’s `stream` interface and print the trace information.

```python
import asyncio
from openjiuwen.core.workflow import create_workflow_session
from openjiuwen.core.session.stream import BaseStreamMode

async def run_workflow():
    session = create_workflow_session()
    async for chunk in flow.stream(inputs={"inputs": {"a": 1, "b": 2}}, session,
                                   stream_modes=[BaseStreamMode.TRACE]):
        print(chunk)
        
if __name__ == "__main__":
    asyncio.run(run_workflow())
```

The output is as follows, where the custom trace information is the 5th frame in the stream output.

```python
type='tracer_workflow' payload={'traceId': '9d494056-0a13-43a6-beaf-54af5448f1d0', 'startTime': datetime.datetime(2025, 10, 25, 16, 23, 55, 673436), 'endTime': None, 'inputs': None, 'outputs': None, 'error': None, 'invokeId': 'start', 'parentInvokeId': None, 'executionId': '9d494056-0a13-43a6-beaf-54af5448f1d0', 'onInvokeData': [], 'componentId': '', 'componentName': '', 'componentType': 'start', 'loopNodeId': None, 'loopIndex': None, 'status': 'start', 'parentNodeId': ''}
type='tracer_workflow' payload={'traceId': '9d494056-0a13-43a6-beaf-54af5448f1d0', 'startTime': datetime.datetime(2025, 10, 25, 16, 23, 55, 673436), 'endTime': datetime.datetime(2025, 10, 25, 16, 23, 55, 673763), 'inputs': None, 'outputs': None, 'error': None, 'invokeId': 'start', 'parentInvokeId': None, 'executionId': '9d494056-0a13-43a6-beaf-54af5448f1d0', 'onInvokeData': [], 'componentId': '', 'componentName': '', 'componentType': 'start', 'loopNodeId': None, 'loopIndex': None, 'status': 'finish', 'parentNodeId': ''}
type='tracer_workflow' payload={'traceId': '9d494056-0a13-43a6-beaf-54af5448f1d0', 'startTime': datetime.datetime(2025, 10, 25, 16, 23, 55, 871254), 'endTime': None, 'inputs': {'a': 1, 'b': 2}, 'outputs': None, 'error': None, 'invokeId': 'add', 'parentInvokeId': 'start', 'executionId': '9d494056-0a13-43a6-beaf-54af5448f1d0', 'onInvokeData': [], 'componentId': '', 'componentName': '', 'componentType': 'add', 'loopNodeId': None, 'loopIndex': None, 'status': 'start', 'parentNodeId': ''}
type='tracer_workflow' payload={'traceId': '9d494056-0a13-43a6-beaf-54af5448f1d0', 'startTime': datetime.datetime(2025, 10, 25, 16, 23, 55, 871254), 'endTime': None, 'inputs': {'a': 1, 'b': 2}, 'outputs': None, 'error': None, 'invokeId': 'add', 'parentInvokeId': 'start', 'executionId': '9d494056-0a13-43a6-beaf-54af5448f1d0', 'onInvokeData': [{'a': 1, 'b': 'b', 'cost': 4.76837158203125e-07}], 'componentId': '', 'componentName': '', 'componentType': 'add', 'loopNodeId': None, 'loopIndex': None, 'status': 'running', 'parentNodeId': ''}
type='tracer_workflow' payload={'traceId': '9d494056-0a13-43a6-beaf-54af5448f1d0', 'startTime': datetime.datetime(2025, 10, 25, 16, 23, 55, 871254), 'endTime': datetime.datetime(2025, 10, 25, 16, 23, 55, 871614), 'inputs': {'a': 1, 'b': 2}, 'outputs': {'output': 3}, 'error': None, 'invokeId': 'add', 'parentInvokeId': 'start', 'executionId': '9d494056-0a13-43a6-beaf-54af5448f1d0', 'onInvokeData': [{'a': 1, 'b': 'b', 'cost': 4.76837158203125e-07}], 'componentId': '', 'componentName': '', 'componentType': 'add', 'loopNodeId': None, 'loopIndex': None, 'status': 'finish', 'parentNodeId': ''}
type='tracer_workflow' payload={'traceId': '9d494056-0a13-43a6-beaf-54af5448f1d0', 'startTime': datetime.datetime(2025, 10, 25, 16, 23, 55, 920343), 'endTime': None, 'inputs': {'result': 3}, 'outputs': None, 'error': None, 'invokeId': 'end', 'parentInvokeId': 'add', 'executionId': '9d494056-0a13-43a6-beaf-54af5448f1d0', 'onInvokeData': [], 'componentId': '', 'componentName': '', 'componentType': 'end', 'loopNodeId': None, 'loopIndex': None, 'status': 'start', 'parentNodeId': ''}
type='tracer_workflow' payload={'traceId': '9d494056-0a13-43a6-beaf-54af5448f1d0', 'startTime': datetime.datetime(2025, 10, 25, 16, 23, 55, 920343), 'endTime': datetime.datetime(2025, 10, 25, 16, 23, 55, 920582), 'inputs': {'result': 3}, 'outputs': {'output': {'result': 3}}, 'error': None, 'invokeId': 'end', 'parentInvokeId': 'add', 'executionId': '9d494056-0a13-43a6-beaf-54af5448f1d0', 'onInvokeData': [], 'componentId': '', 'componentName': '', 'componentType': 'end', 'loopNodeId': None, 'loopIndex': None, 'status': 'finish', 'parentNodeId': ''}
type='workflow_final' index=0 payload={'output': {'result': 3}}

```

If you call the workflow’s `stream` interface again, it triggers the error scenario in `AddComponent`.

```python
import asyncio
from openjiuwen.core.workflow import create_workflow_session
from openjiuwen.core.session.stream import BaseStreamMode

async def run_workflow():
    session = create_workflow_session()
    async for chunk in flow.stream(inputs={"inputs": {"b": 2}}, session,
                                   stream_modes=[BaseStreamMode.TRACE]):
        print(chunk)
        
if __name__ == "__main__":
    asyncio.run(run_workflow())
```

The output is as follows, where the penultimate frame is the custom error tracing information.

```python
type='tracer_workflow' payload={'traceId': '2d5474e9-7e25-46b9-b56d-36e47462a2ed', 'startTime': datetime.datetime(2025, 12, 6, 20, 44, 54, 875415), 'endTime': None, 'inputs': {'inputs': {'b': 2}}, 'outputs': None, 'error': None, 'invokeId': 'e1a75cf4e00d404fa545a069225b0bea', 'parentInvokeId': None, 'executionId': '2d5474e9-7e25-46b9-b56d-36e47462a2ed', 'onInvokeData': [], 'componentId': '', 'componentName': '', 'componentType': 'e1a75cf4e00d404fa545a069225b0bea', 'loopNodeId': None, 'loopIndex': None, 'status': 'start', 'parentNodeId': ''}
type='tracer_workflow' payload={'traceId': '2d5474e9-7e25-46b9-b56d-36e47462a2ed', 'startTime': datetime.datetime(2025, 12, 6, 20, 44, 54, 886619), 'endTime': None, 'inputs': None, 'outputs': None, 'error': None, 'invokeId': 'start', 'parentInvokeId': 'e1a75cf4e00d404fa545a069225b0bea', 'executionId': '2d5474e9-7e25-46b9-b56d-36e47462a2ed', 'onInvokeData': [], 'componentId': '', 'componentName': '', 'componentType': 'start', 'loopNodeId': None, 'loopIndex': None, 'status': 'start', 'parentNodeId': ''}
type='tracer_workflow' payload={'traceId': '2d5474e9-7e25-46b9-b56d-36e47462a2ed', 'startTime': datetime.datetime(2025, 12, 6, 20, 44, 54, 886619), 'endTime': datetime.datetime(2025, 12, 6, 20, 44, 54, 886822), 'inputs': None, 'outputs': None, 'error': None, 'invokeId': 'start', 'parentInvokeId': 'e1a75cf4e00d404fa545a069225b0bea', 'executionId': '2d5474e9-7e25-46b9-b56d-36e47462a2ed', 'onInvokeData': [], 'componentId': '', 'componentName': '', 'componentType': 'start', 'loopNodeId': None, 'loopIndex': None, 'status': 'finish', 'parentNodeId': ''}
type='tracer_workflow' payload={'traceId': '2d5474e9-7e25-46b9-b56d-36e47462a2ed', 'startTime': datetime.datetime(2025, 12, 6, 20, 44, 54, 887183), 'endTime': None, 'inputs': {'a': None, 'b': 2}, 'outputs': None, 'error': None, 'invokeId': 'add', 'parentInvokeId': 'start', 'executionId': '2d5474e9-7e25-46b9-b56d-36e47462a2ed', 'onInvokeData': [], 'componentId': '', 'componentName': '', 'componentType': 'add', 'loopNodeId': None, 'loopIndex': None, 'status': 'start', 'parentNodeId': ''}
type='tracer_workflow' payload={'traceId': '2d5474e9-7e25-46b9-b56d-36e47462a2ed', 'startTime': datetime.datetime(2025, 12, 6, 20, 44, 54, 887183), 'endTime': datetime.datetime(2025, 12, 6, 20, 44, 54, 887349), 'inputs': {'a': None, 'b': 2}, 'outputs': None, 'error': {'error_code': -1, 'message': 'a is not exit'}, 'invokeId': 'add', 'parentInvokeId': 'start', 'executionId': '2d5474e9-7e25-46b9-b56d-36e47462a2ed', 'onInvokeData': [], 'componentId': '', 'componentName': '', 'componentType': 'add', 'loopNodeId': None, 'loopIndex': None, 'status': 'error', 'parentNodeId': ''}
type='tracer_workflow' payload={'traceId': '2d5474e9-7e25-46b9-b56d-36e47462a2ed', 'startTime': datetime.datetime(2025, 12, 6, 20, 44, 54, 887183), 'endTime': datetime.datetime(2025, 12, 6, 20, 44, 54, 887519), 'inputs': {'a': None, 'b': 2}, 'outputs': None, 'error': {'error_code': 100005, 'message': 'component [add] encountered an exception while executing ability [invoke], error detail: [-1] a is not exit'}, 'invokeId': 'add', 'parentInvokeId': 'start', 'executionId': '2d5474e9-7e25-46b9-b56d-36e47462a2ed', 'onInvokeData': [], 'componentId': '', 'componentName': '', 'componentType': 'add', 'loopNodeId': None, 'loopIndex': None, 'status': 'error', 'parentNodeId': ''}
type='tracer_workflow' payload={'traceId': '2d5474e9-7e25-46b9-b56d-36e47462a2ed', 'startTime': datetime.datetime(2025, 12, 6, 20, 44, 54, 875415), 'endTime': datetime.datetime(2025, 12, 6, 20, 44, 54, 887767), 'inputs': {'inputs': {'b': 2}}, 'outputs': None, 'error': None, 'invokeId': 'e1a75cf4e00d404fa545a069225b0bea', 'parentInvokeId': None, 'executionId': '2d5474e9-7e25-46b9-b56d-36e47462a2ed', 'onInvokeData': [], 'componentId': '', 'componentName': '', 'componentType': 'e1a75cf4e00d404fa545a069225b0bea', 'loopNodeId': None, 'loopIndex': None, 'status': 'finish', 'parentNodeId': ''}
```