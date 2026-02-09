# State Management

During the conversation execution, state data is accessed uniformly through `Session`, supporting cross-component sharing mechanisms and effectively reducing the complexity of property passing. All state data is centrally stored in `Session` and can be read and written via standardized interfaces, mainly divided into two categories:

- **Global state data**: Data that all components can read and update, such as session history data. Use the `get_global_state` method to retrieve global state data and the `update_global_state` method to update global state data.
- **Component state data**: State data that belongs to the workflow component itself, such as the component's execution count or intermediate computation data. Use the `get_state` method to retrieve component state data and the `update_state` method to update component state data. This data is only visible to the current component and cannot be accessed by other components.

When developing components, choose whether to record new state data in the global state or the component state based on your needs. It is recommended to choose based on the visibility of the state. If the state only needs to be visible within the component, prefer recording it in the component state.

## Global State Data

Global state is a data space shared by all components and is suitable for scenarios such as recording execution trace.

For example, to update state data in a workflow, add global state information `debug_messages` to record runtime information during the workflow execution. Any component in the workflow can retrieve it through the `get_global_state` method.

Create a custom component `NodeDemo` to get and update the current state data `debug_messages`:

```python
from openjiuwen.core.workflow import WorkflowComponent
from openjiuwen.core.context_engine import ModelContext
from openjiuwen.core.workflow.components import Session
from openjiuwen.core.workflow import Input, Output


class NodeDemo(WorkflowComponent):
    def __init__(self, node_id):
        super().__init__()
        self.node_id = node_id

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        # Get the current state data of debug_messages
        debug_messages = session.get_global_state("debug_messages")
        print(f"{self.node_id}: debug_messages={debug_messages}")

        # Update the state data
        new_debug_messages = []
        if debug_messages:
            new_debug_messages.extend(debug_messages)
        new_debug_messages.append({self.node_id: {"inputs": inputs}})
        session.update_global_state({"debug_messages": new_debug_messages})

        # Print the updated state data
        print(f"{self.node_id}: after update debug_messages={session.get_global_state('debug_messages')}")
        return {"output": inputs}
```

Create a simple workflow and execute it:

```python
import asyncio
from openjiuwen.core.workflow import create_workflow_session
from openjiuwen.core.workflow import  Workflow

flow = Workflow()
flow.set_start_comp("start", NodeDemo("start"), inputs_schema={'a': '${user_inputs.a}'})
flow.add_workflow_comp("node", NodeDemo("node"), inputs_schema={'a': '${start.output.a}'})
flow.set_end_comp("end", NodeDemo("end"), inputs_schema={'a': '${node.output.a}'})
flow.add_connection("start", "node")
flow.add_connection("node", "end")

# Execute the workflow
session = create_workflow_session()
output = asyncio.run(flow.invoke({"user_inputs": {"a": 1}}, session))
print(output)
```

The results are as follows; you can see that updates to the workflow's state take effect after the current component finishes execution:

```python
start: debug_messages=None
start: after update debug_messages=None
node: debug_messages=[{'start': {'inputs': {'a': 1}}}]
node: after update debug_messages=[{'start': {'inputs': {'a': 1}}}]
end: debug_messages=[{'start': {'inputs': {'a': 1}}}, {'node': {'inputs': {'a': 1}}}]
end: after update debug_messages=[{'start': {'inputs': {'a': 1}}}, {'node': {'inputs': {'a': 1}}}]
result={'output': {'a': 1}} state=<WorkflowExecutionState.COMPLETED: 'COMPLETED'>
```

## Component State Data

Component state data is the private data space of a custom component, used to store component-specific information (such as execution count and intermediate results).

Take two custom components as an example. Create a custom component `AddTenComponent`, which records the current component's execution count through `call_times`. Then create a second custom component `CustomEnd` to attempt reading `call_times`.

`AddTenComponent` is a custom component that is executed repeatedly in the workflow to accumulate a global numeric value (adding +10 each time), while maintaining its private execution count state `call_times`. The component records its execution count using the component state data (`update_state`), ensuring that the state is only visible inside the component; it also updates the shared accumulated value `num` using the global state data (`update_global_state`) for other components to read:

```python
from openjiuwen.core.workflow import WorkflowComponent
from openjiuwen.core.context_engine import ModelContext
from openjiuwen.core.workflow.components import Session
from openjiuwen.core.workflow import Input, Output

class AddTenComponent(WorkflowComponent):
    def __init__(self, node_id):
        super().__init__()
        self._node_id = node_id

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output: 
        # Get the component's private state: execution count
        call_times = session.get_state("call_times") or 0
        # Get the global shared state: accumulated value
        num = session.get_global_state("num") or 0
        
        # Update private state: execution count +1
        call_times += 1
        session.update_state({"call_times": call_times})
        
        # Perform computation: num += 10
        num += 10
        session.update_global_state({"num": num})
        
        # Print debug info
        print(f"[{self._node_id}] Executed {call_times} time(s), num = {num - 10} → {num}")
        
        return inputs
```

`CustomEnd` is a terminal component used to verify the isolation of component state. It attempts to read the private state `call_times` maintained by `AddTenComponent` and retrieves the global state `num` to demonstrate that component state is only visible to its own component. This component does not modify any state and is only used for observation and verification:

```python
from openjiuwen.core.workflow import WorkflowComponent
from openjiuwen.core.context_engine import ModelContext
from openjiuwen.core.workflow.components import Session
from openjiuwen.core.workflow import Input, Output

class CustomEnd(WorkflowComponent):
    def __init__(self, node_id):
        super().__init__()
        self._node_id = node_id

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        # Attempt to read another component's private state (expected to be invisible)
        call_times = session.get_state("call_times")
        print(f'->[CustomEnd] Got call_times = {call_times}')  # Output: None
        
        # Read the global shared state (expected to be visible)
        num = session.get_global_state("num")
        print(f'->[CustomEnd] Got num = {num}')
        
        return inputs
```

Create a workflow and add a branch component:

```python
from openjiuwen.core.workflow import  Workflow
from openjiuwen.core.workflow import BranchComponent
from openjiuwen.core.workflow import Start

# Create workflow
flow = Workflow()
flow.set_start_comp("start", Start())
flow.set_end_comp("end", CustomEnd("end"))
flow.add_workflow_comp("a", AddTenComponent("a"))

# Add a branch component to trigger multiple invocations of AddTenComponent

sw = BranchComponent()
sw.add_branch("${num} <= 30", ["a"], "1")
sw.add_branch("${num} > 30", ["end"], "2")

flow.add_workflow_comp("sw", sw)
flow.add_connection("start", "a")
flow.add_connection("a", "sw")
```

Invoke the workflow:

```python
import asyncio
from openjiuwen.core.workflow import create_workflow_session
# Invoke the workflow
asyncio.run(flow.invoke({}, create_workflow_session()))
```

The execution results are as follows. You can see that `AddTenComponent` can retrieve the latest `call_times` state each time it runs. In addition, since `call_times` is `AddTenComponent`'s private state, `CustomEnd` cannot access it:

```python
[a] Executed 1 time(s), num = 0 → 10
[a] Executed 2 time(s), num = 10 → 20
[a] Executed 3 time(s), num = 20 → 30
[a] Executed 4 time(s), num = 30 → 40
->[CustomEnd] Got call_times = None
->[CustomEnd] Got num = 40
```
