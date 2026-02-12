openJiuwen supports interruption and resumption for workflows with either a single component or multiple components simultaneously. Interruption recovery includes two scenarios:

- **Interactive interruption**: During the execution of a workflow component, if user interaction is needed to collect more information, the workflow pauses. After the user provides input, the workflow resumes its execution.
- **Exception interruption**: When a workflow component encounters an error during execution, the workflow saves its state. On re-execution, it continues from the component where the exception occurred.

# Interactive Interruptions

openJiuwen supports two interactive interruption modes: single-component interruption and multi-component interruption. These modes are suitable for business scenarios of varying complexity:

- Single-component interruption: Suitable for independent interaction scenarios, such as a single confirmation operation.
- Multi-component interruption: Suitable for complex collaborative scenarios, such as synchronizing and completing multiple types of information.

## Single-component interruption

A single component pauses and waits for user input. The user input does not need to specify the component; once the interrupted component obtains the input, it continues its execution.

First, define an interactive component. The component implements the `invoke` method, which extracts the user-provided command `cmd`, calls the `Session`'s `interact` interface to ask the user whether to execute the command, waits for the user's response, and returns the response as the output of the `invoke` method.

```python
import asyncio
import uuid

from openjiuwen.core.workflow import WorkflowComponent
from openjiuwen.core.workflow import End
from openjiuwen.core.workflow import Start
from openjiuwen.core.context_engine import ModelContext
from openjiuwen.core.workflow import Input, Output
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.workflow.components import Session
from openjiuwen.core.workflow import create_workflow_session
from openjiuwen.core.workflow import Workflow

# Interactive component
class InteractiveNode(WorkflowComponent):
    def __init__(self):
        super().__init__()

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        cmd = inputs.get("cmd")
        # Ask the user, wait for the answer
        result = await session.interact(f"Do you want to execute the command '{cmd}'?")
        return {"confirm_result": result}
```

Build a workflow composed of a start component, an interactive component, and an end component. Components execute in order.

```python
flow = Workflow()
flow.set_start_comp("start", Start(), inputs_schema={"command": "${cmd}"})
flow.add_workflow_comp("interactive_node", InteractiveNode(),
                       inputs_schema={"cmd": "${start.command}"},
                       outputs_schema={"result": "${confirm_result}"})
flow.set_end_comp("end", End(),
                  inputs_schema={"result": "${interactive_node.result}"})
flow.add_connection("start", "interactive_node")
flow.add_connection("interactive_node", "end")
```

Then execute the workflow. Before execution, generate a `session_id` to create the `Session`. Print the workflow output after execution.

```python
# Generate session id
session_id = uuid.uuid4().hex
    
# First user input
output = asyncio.run(flow.invoke({"cmd": "delete all files"}, create_workflow_session(session_id=session_id)))

print(output)
```

After running the above code, you will see:

```python
result=[OutputSchema(type='__interaction__', index=0, payload=InteractionOutput(id='interactive_node', value="Do you want to execute the command 'delete all files'?"))] state=<WorkflowExecutionState.INPUT_REQUIRED: 'INPUT_REQUIRED'>
```

Here, `output` is the workflow output; see [Workflow Output Introduction](./Streaming%20Output.md#workflow-streaming-output). `result` is a list, where each element is a streaming output entry; see [Streaming Output Introduction](./Streaming%20Output.md). `type` being `__interaction__` indicates the message type is an interruption message. `index` being `0` indicates the first interruption of the component. The content in `payload` is the interruption output; see [Interactive Interruptions](#interactive-interruptions).

Finally, execute the workflow again. Before re-execution, construct the interactive input. Since there is only one interactive component, you do not need to specify the interactive component `id` when constructing the input. Pass the interactive input to the workflow's `invoke` method. Use the same `session_id` to construct the `Session` to ensure correct resumption. Print the workflow output after execution.

```python
# Wrap user response
user_input = InteractiveInput("Yes")
# Pass user response into the workflow; session_id must be the same
output = asyncio.run(flow.invoke(user_input, create_workflow_session(session_id=session_id)))

print(output.result)
```

After running the above code, you will see:

```python
{'output': {'result': 'Yes'}}
```

**Complete code:**

```python
import asyncio
import uuid

from openjiuwen.core.workflow import WorkflowComponent
from openjiuwen.core.workflow import End
from openjiuwen.core.workflow import Start
from openjiuwen.core.context_engine import ModelContext
from openjiuwen.core.workflow import Input, Output
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.workflow.components import Session
from openjiuwen.core.workflow import create_workflow_session
from openjiuwen.core.workflow import Workflow


class InteractiveNode(WorkflowComponent):
    def __init__(self):
        super().__init__()

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        cmd = inputs.get("cmd")
        # Ask the user, wait for the answer
        result = await session.interact(f"Do you want to execute the command '{cmd}'?")
        return {"confirm_result": result}
    
    
flow = Workflow()
flow.set_start_comp("start", Start(), inputs_schema={"command": "${cmd}"})
flow.add_workflow_comp("interactive_node", InteractiveNode(),
                       inputs_schema={"cmd": "${start.command}"},
                       outputs_schema={"result": "${confirm_result}"})
flow.set_end_comp("end", End(),
                  inputs_schema={"result": "${interactive_node.result}"})
flow.add_connection("start", "interactive_node")
flow.add_connection("interactive_node", "end")

# Generate session id
session_id = uuid.uuid4().hex

# First user input
output = asyncio.run(flow.invoke({"cmd": "delete all files"}, create_workflow_session(session_id=session_id)))

print(output)


# Wrap user response
user_input = InteractiveInput("Yes")
# Pass user response into the workflow; session_id must be the same
output = asyncio.run(flow.invoke(user_input, create_workflow_session(session_id=session_id)))

print(output.result)
```

Output:

```python
result=[OutputSchema(type='__interaction__', index=0, payload=InteractionOutput(id='interactive_node', value="Do you want to execute the command 'delete all files'?"))] state=<WorkflowExecutionState.INPUT_REQUIRED: 'INPUT_REQUIRED'>
{'output': {'result': 'Yes'}}
```

## Multi-component interruption

Multiple components pause simultaneously waiting for user input. The user needs to provide inputs separately for each interrupted component. Once all interrupted components have received user input, the workflow continues execution.

First, define the first interactive component. The component implements the `invoke` method, which extracts the user-provided command `cmd`, calls the `Session`'s `interact` interface to ask the user whether to execute the command, waits for the user's response, and returns the response as the output of the `invoke` method.

```python
import asyncio
import uuid

from openjiuwen.core.workflow import WorkflowComponent
from openjiuwen.core.workflow import End
from openjiuwen.core.workflow import Start
from openjiuwen.core.context_engine import ModelContext
from openjiuwen.core.workflow import Input, Output
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.workflow.components import Session
from openjiuwen.core.workflow import create_workflow_session
from openjiuwen.core.workflow import Workflow

# First interactive component
class InteractiveNode1(WorkflowComponent):
    def __init__(self):
        super().__init__()

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        cmd = inputs.get("cmd")
        # Ask the user, wait for the answer
        result = await session.interact(f"Do you want to execute the command '{cmd}'?")
        return {"confirm_result": result}
```

Next, define the second interactive component. It also implements the `invoke` method, which extracts the user-provided command `cmd`, calls the `Session`'s `interact` interface to ask the user whether to execute the command, waits for the user's response, and returns the response as the output of the `invoke` method.

```python
# Second interactive component
class InteractiveNode2(WorkflowComponent):
    def __init__(self):
        super().__init__()

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        cmd = inputs.get("cmd")
        # Ask the user, wait for the answer
        result = await session.interact(f"Do you want to execute the command '{cmd}'?")
        return {"confirm_result": result}
```

Build a workflow composed of a start component, two interactive components, and an end component, where the two interactive components execute concurrently within the workflow.

```python
flow = Workflow()
flow.set_start_comp("start", Start(), inputs_schema={"command1": "${cmd1}", "command2": "${cmd2}"})
flow.add_workflow_comp("interactive_node1", InteractiveNode1(),
                       inputs_schema={"cmd": "${start.command1}"},
                       outputs_schema={"result": "${confirm_result}"})
flow.add_workflow_comp("interactive_node2", InteractiveNode2(),
                   inputs_schema={"cmd": "${start.command2}"},
                   outputs_schema={"result": "${confirm_result}"})
flow.set_end_comp("end", End(),
                  inputs_schema={"result1": "${interactive_node1.result}", "result2": "${interactive_node2.result}"})
flow.add_connection("start", "interactive_node1")
flow.add_connection("start", "interactive_node2")
flow.add_connection("interactive_node1", "end")
flow.add_connection("interactive_node2", "end")
```

Specify `session_id` to execute the workflow and print the workflow output after execution. Because there are two concurrently executing interactive components, there are two interaction outputs.

```python
# Generate session id
session_id = uuid.uuid4().hex
    
# First user input
output = asyncio.run(flow.invoke({"cmd1": "delete all files", "cmd2": "kill all processes"}, create_workflow_session(session_id=session_id)))

print(output)
```

After running the above code, you will see:

```python
result=[OutputSchema(type='__interaction__', index=0, payload=InteractionOutput(id='interactive_node1', value="Do you want to execute the command 'delete all files'?")), OutputSchema(type='__interaction__', index=0, payload=InteractionOutput(id='interactive_node2', value="Do you want to execute the command 'kill all processes'?"))] state=<WorkflowExecutionState.INPUT_REQUIRED: 'INPUT_REQUIRED'>
```

Here, `output` is the workflow output; see [Workflow streaming output](./Streaming%20Output.md#workflow-streaming-output). `result` is a list, where each element is a streaming output entry; see [Streaming Output Introduction](./Streaming%20Output.md). `type` being `__interaction__` indicates the message type is an interruption message. `index` being `0` indicates the first interruption of each component. The content in `payload` is the interruption output; see [Interruption Output Introduction](#interactive-interruptions).

Finally, execute the workflow again. Before re-execution, construct the interactive input. Because there are two simultaneously executing interactive components, you need to specify the `id` of each interactive component when constructing the input. Pass the interactive input to the workflow's `invoke` method. Use the same `session_id` to construct the `Session` to ensure correct resumption. Print the workflow output after execution.

```python
# Get interrupted component IDs
node_id1 = output.result[0].payload.id
node_id2 = output.result[1].payload.id
# Wrap user responses; provide different answers for different interrupted components
user_input = InteractiveInput()
user_input.update(node_id1, "Yes")
user_input.update(node_id2, "No")
# Pass user responses into the workflow; session_id must be the same
output = asyncio.run(flow.invoke(user_input, create_workflow_session(session_id=session_id)))

print(output.result)
```

After running the above code, you will see:

```python
{'output': {'result1': 'Yes', 'result2': 'No'}}
```

**Complete code:**

```python
import asyncio
import uuid

from openjiuwen.core.workflow import WorkflowComponent
from openjiuwen.core.workflow import End
from openjiuwen.core.workflow import Start
from openjiuwen.core.context_engine import ModelContext
from openjiuwen.core.workflow import Input, Output
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.workflow.components import Session
from openjiuwen.core.workflow import create_workflow_session
from openjiuwen.core.workflow import Workflow

# First interactive component
class InteractiveNode1(WorkflowComponent):
    def __init__(self):
        super().__init__()

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        cmd = inputs.get("cmd")
        # Ask the user, wait for the answer
        result = await session.interact(f"Do you want to execute the command '{cmd}'?")
        return {"confirm_result": result}
    
# Second interactive component
class InteractiveNode2(WorkflowComponent):
    def __init__(self):
        super().__init__()

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        cmd = inputs.get("cmd")
        # Ask the user, wait for the answer
        result = await session.interact(f"Do you want to execute the command '{cmd}'?")
        return {"confirm_result": result}
    
flow = Workflow()
flow.set_start_comp("start", Start(), inputs_schema={"command1": "${cmd1}", "command2": "${cmd2}"})
flow.add_workflow_comp("interactive_node1", InteractiveNode1(),
                       inputs_schema={"cmd": "${start.command1}"},
                       outputs_schema={"result": "${confirm_result}"})
flow.add_workflow_comp("interactive_node2", InteractiveNode2(),
                   inputs_schema={"cmd": "${start.command2}"},
                   outputs_schema={"result": "${confirm_result}"})
flow.set_end_comp("end", End(),
                  inputs_schema={"result1": "${interactive_node1.result}", "result2": "${interactive_node2.result}"})
flow.add_connection("start", "interactive_node1")
flow.add_connection("start", "interactive_node2")
flow.add_connection("interactive_node1", "end")
flow.add_connection("interactive_node2", "end")

# Generate session id
session_id = uuid.uuid4().hex

# First user input
output = asyncio.run(
    flow.invoke({"cmd1": "delete all files", "cmd2": "kill all processes"}, create_workflow_session(session_id=session_id)))

print(output)

# Get interrupted component IDs
node_id1 = output.result[0].payload.id
node_id2 = output.result[1].payload.id
# Wrap user responses; provide different answers for different interrupted components
user_input = InteractiveInput()
user_input.update(node_id1, "Yes")
user_input.update(node_id2, "No")
# Pass user responses into the workflow; session_id must be the same
output = asyncio.run(flow.invoke(user_input, create_workflow_session(session_id=session_id)))

print(output.result)

```

Output:
```python
result=[OutputSchema(type='__interaction__', index=0, payload=InteractionOutput(id='interactive_node1', value="Do you want to execute the command 'delete all files'?")), OutputSchema(type='__interaction__', index=0, payload=InteractionOutput(id='interactive_node2', value="Do you want to execute the command 'kill all processes'?"))] state=<WorkflowExecutionState.INPUT_REQUIRED: 'INPUT_REQUIRED'>
{'output': {'result1': 'Yes', 'result2': 'No'}}
```

# Exception Interruptions

Exception interruption recovery means that when a workflow encounters an unexpected exception during execution, it saves the state. When the workflow is re-executed, it continues from the component where the exception occurred.

Using a network connection timeout as an example to illustrate the recovery process: On the first run, a connection timeout exception is thrown; on the second run, it executes normally.

First, define a normal component. This component implements the `invoke` method, which prints `invoke normal node` and returns an empty dictionary.

```python
import asyncio
import uuid

from openjiuwen.core.workflow import WorkflowComponent
from openjiuwen.core.workflow import End
from openjiuwen.core.workflow import Start
from openjiuwen.core.context_engine import ModelContext
from openjiuwen.core.workflow import Input, Output
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.workflow.components import Session
from openjiuwen.core.workflow import create_workflow_session
from openjiuwen.core.workflow import Workflow

# Normal component
class NormalNode(WorkflowComponent):
    def __init__(self):
        super().__init__()

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        print("invoke normal node")
        return {}
```

Next, define a component that throws an exception. It also implements the `invoke` method, which throws an exception the first time the component is called and returns an empty dictionary on the second execution.

```python
# Component that throws an exception
class AbnormalNode(WorkflowComponent):
    def __init__(self):
        super().__init__()
        self.first_time_invoke = True

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        # Simulate a network timeout on the first call
        if self.first_time_invoke:
            self.first_time_invoke = False
            raise Exception("connection timeout")
        return {}
```

Build a workflow composed of a start component, a normal component, an abnormal component, and an end component. Components execute in order.

```python
flow = Workflow()
flow.set_start_comp("start", Start(), inputs_schema={"query": "${query}"})
flow.add_workflow_comp("normal_node", NormalNode())
flow.add_workflow_comp("abnormal_node", AbnormalNode())
flow.set_end_comp("end", End(),
                  inputs_schema={"result": "${start.query}"})
flow.add_connection("start", "normal_node")
flow.add_connection("normal_node", "abnormal_node")
flow.add_connection("abnormal_node", "end")
```

Execute the workflow. Before execution, generate a `session_id` to create the `Session`. Because there is an abnormal component, catch the workflow execution exception and print the error message.

```python
# Generate session id
session_id = uuid.uuid4().hex
    
# First user input
try:
    asyncio.run(flow.invoke({"query": "hello"}, create_workflow_session(session_id=session_id)))
except Exception as e:
    # Output connection timeout
    print(e)
```

After running the above code, you will see:

```python
invoke normal node
[100005] workflow component runtime error, reason: node_id: abnormal_node, ability: invoke, error: connection timeout
```

Finally, execute the workflow again. Before re-execution, construct the interactive input; for exception scenarios, you only need to construct an empty interactive input. Pass the interactive input to the workflow's `invoke` method. Use the same `session_id` to construct the `Session` to ensure correct resumption. Print the workflow output after execution.

```python
# User retries; use the same session id and an empty InteractiveInput
user_input = InteractiveInput()
output = asyncio.run(flow.invoke(user_input, create_workflow_session(session_id=session_id)))
# This prints hello
print(output.result.get("output").get("result"))
```

After running the above code, you will see:

```python
hello
```

The second execution no longer prints `invoke normal node`, indicating that the workflow resumed from after the normal component.

**Complete code:**

```python
import asyncio
import uuid

from openjiuwen.core.workflow import WorkflowComponent
from openjiuwen.core.workflow import End
from openjiuwen.core.workflow import Start
from openjiuwen.core.context_engine import ModelContext
from openjiuwen.core.workflow import Input, Output
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.workflow.components import Session
from openjiuwen.core.workflow import create_workflow_session
from openjiuwen.core.workflow import Workflow


# Normal component
class NormalNode(WorkflowComponent):
    def __init__(self):
        super().__init__()

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        print("invoke normal node")
        return {}
    
    
# Component that throws an exception
class AbnormalNode(WorkflowComponent):
    def __init__(self):
        super().__init__()
        self.first_time_invoke = True

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        # Simulate a network timeout on the first call
        if self.first_time_invoke:
            self.first_time_invoke = False
            raise Exception("connection timeout")
        return {}
    
flow = Workflow()
flow.set_start_comp("start", Start(), inputs_schema={"query": "${query}"})
flow.add_workflow_comp("normal_node", NormalNode())
flow.add_workflow_comp("abnormal_node", AbnormalNode())
flow.set_end_comp("end", End(),
                  inputs_schema={"result": "${start.query}"})
flow.add_connection("start", "normal_node")
flow.add_connection("normal_node", "abnormal_node")
flow.add_connection("abnormal_node", "end")

# Generate session id
session_id = uuid.uuid4().hex

# First user input
try:
    asyncio.run(flow.invoke({"query": "hello"}, create_workflow_session(session_id=session_id)))
except Exception as e:
    # Output connection timeout
    print(e)
    
    
# User retries; use the same session id and an empty InteractiveInput
user_input = InteractiveInput()
output = asyncio.run(flow.invoke(user_input, create_workflow_session(session_id=session_id)))
# This prints hello
print(output.result.get("output").get("result"))
```

Output:
```python
invoke normal node
[100005] component [abnormal_node] encountered an exception while executing ability [invoke], error detail: connection timeout
hello
```
