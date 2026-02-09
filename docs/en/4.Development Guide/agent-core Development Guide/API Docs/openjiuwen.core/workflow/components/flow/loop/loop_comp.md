# openjiuwen.core.workflow.components
## class LoopGroup

Loop body, used to organize and manage a group of components executed in loops. LoopGroup is the basic container for building loop logic. It can contain multiple components and define connection relationships between them, providing powerful loop processing capability foundation support for workflows.

### add_workflow_comp

```python
add_workflow_comp(comp_id: str, workflow_comp: ComponentComposable, *,  wait_for_all: bool = None, inputs_schema: dict | Transformer = None, stream_inputs_schema: dict | Transformer = None, outputs_schema: dict | Transformer = None, stream_outputs_schema: dict | Transformer = None, **kwargs) -> Self
```

Add workflow component to loop body. Note that components added to loop body can only be used within loop body, cannot be used outside loop body. Similarly, components added outside loop body (using `openjiuwen.core.workflow.Workflow.add_workflow_comp` interface) can only be used outside loop body, cannot be used within loop body.

**Parameters**:

- **comp_id** (str): Unique identifier of the component, used to reference this component in loop body. Note it is a unique identifier in the entire Workflow, cannot duplicate with other components.
- **workflow_comp** (ComponentComposable): Loop body component instance to be added, value type cannot be `LoopComponent`.
- *: Parameter separator
- **inputs_schema** (dict | Transformer, optional): Structure schema for component regular input parameters. Used to validate format legality of input data. Key names match component input parameter names, values match component input data types. When not configured, input format is not validated. Default value: `None`.
- **stream_inputs_schema** (dict | Transformer, optional): Structure schema for component streaming input parameters. Used to define format specification for streaming input data. Default value: `None`.
- **outputs_schema** (dict | Transformer, optional): Structure schema for component regular output results. Used to define format specification for output data, convenient for subsequent component parsing; when not configured, output format is determined by component itself. Default value: `None`.
- **wait_for_all** (bool, optional): Whether to wait for all preceding dependent components to complete execution before executing this component. `True` means need to wait for all preceding dependent components to complete execution, `False` means not needed. Default value: `None`, means automatically determined by system based on component capabilities (streaming components default to `True`, other components default to `False`).
- **stream_outputs_schema** (dict | Transformer, optional): Structure schema for component streaming output results. Used to define format specification for streaming output data. Default value: `None`.
- **kwargs**: Reserved parameters, configuration does not take effect.

**Example**:

```python
>>> from openjiuwen.core.context_engine import ModelContext
>>> from openjiuwen.core.session import Session
>>> from openjiuwen.core.workflow import WorkflowComponent, Input, Output, LoopGroup
>>> # Custom component AddTenNode, each call adds 10 to source in inputs
>>> class AddTenNode(WorkflowComponent):
...     def __init__(self, node_id: str):
...         super().__init__()
...         self.node_id = node_id
...
...     async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
...         return {"result": inputs["source"] + 10}
>>>
>>> # Create loop body
>>> loop_group = LoopGroup()
>>> # Add first component, process current item in loop array
>>> # AddTenNode is a simple component that adds 10 to source field value in input and outputs to result field
>>> loop_group.add_workflow_comp("1", AddTenNode("1"), inputs_schema={"source": "${l.item}"})
>>> # Add second component, process intermediate variable
>>> loop_group.add_workflow_comp("2", AddTenNode("2"), inputs_schema={"source": "${l.user_var}"})
```

### start_nodes

```python
start_nodes(nodes: list[str]) -> Self
```

Set starting component of loop body.

**Parameters**:

- **nodes** (list[str]): Starting component identifier list. Currently only supports setting one starting component, `nodes` list cannot contain `None` values. Example: `['start_node']`.

**Example**:

```python
>>> from openjiuwen.core.workflow  import LoopGroup
>>> # Set starting component of loop body
>>> loop_group = LoopGroup()
>>> loop_group.add_workflow_comp("1", AddTenNode("1"))
>>> loop_group.add_workflow_comp("2", AddTenNode("2"))
>>> # Specify component "1" as starting component of loop
>>> loop_group.start_nodes(["1"])
```

### end_nodes

```python
end_nodes(nodes: list[str]) -> Self
```

Set ending component of loop body.

**Parameters**:

- **nodes** (list[str]): Ending component identifier list. Currently only supports setting one ending component, `nodes` list cannot contain `None` values. Example: `['end_node']`.

**Example**:

```python
>>> from openjiuwen.core.workflow  import LoopGroup
>>> # Set ending component of loop body
>>> loop_group = LoopGroup()
>>> loop_group.add_workflow_comp("1", AddTenNode("1"))
>>> loop_group.add_workflow_comp("2", AddTenNode("2"))
>>> # Specify component "2" as ending component of loop
>>> loop_group.end_nodes(["2"])
```

## class LoopType

Loop type enumeration class, defines supported loop types.

- **Array**: Array loop, execute loop for each element in array. Value is "array".
- **Number**: Number loop, execute loop for specified number of times. Value is "number".
- **AlwaysTrue**: Infinite loop, execute continuously until interrupted. Value is "always_true".
- **Expression**: Expression loop, decide whether to continue looping based on expression result. Value is "expression".

## class LoopComponent

```python
class LoopComponent(loop_group: LoopGroup, output_schema: dict)
```

Standard loop component class, is a high-level encapsulation of loop functionality, provides more concise usage. Through LoopComponent, can conveniently implement array loops, number loops and other loop modes, and supports advanced features like loop interruption and intermediate variable passing.

**Parameters**:

- **loop_group** ([LoopGroup]): Loop group instance, defines components to be executed in loop body. Loop group can contain multiple components, and defines their execution order through connection relationships. `loop_group` must contain at least one component.
- **output_schema** (dict): Output schema definition, used to extract specific fields from loop results. Keys are output field names, values are expression paths for field values. For example: `{"results": "${1.result}", "user_var": "${l.user_var}"}` means using result field of component "1" in loop body and intermediate variable "user_var" as loop component output.

> **Note**
>
> 1. **Common loop body components**: In loop functionality implementation, usually need to work with the following components:
>
>    - [LoopBreakComponent]: Used to control loop interruption, when execution reaches this component, loop will immediately stop.
>    - [LoopSetVariableComponent]: Used to set variable values during loop process, supports reference paths and nested structure variable setting.
> 2. **Loop variable scope**: Variables inside loop are only visible within loop, external components cannot directly access them.
> 3. **Intermediate variable initialization**: Using LoopComponent's intermediate_var parameter can initialize intermediate variables before loop starts, ensuring correctness of loop logic.
> 4. **Loop interruption mechanism**:
>
>    - When using LoopBreakComponent, need to correctly configure interruption logic in LoopComponent's loop body.
>    - After loop interruption, collected results will still be retained and returned.
>    - For detailed information, see [LoopBreakComponent].
> 5. **Loop condition types**: Choose appropriate loop type based on different loop requirements:
>
>    - array: Suitable for scenarios requiring array traversal.
>    - always_true: Suitable for scenarios requiring infinite loop until explicit interruption.
> 6. **Performance considerations**: For loops with large datasets, should pay attention to memory usage and execution efficiency, avoid executing overly complex operations in loop body.

**Example**:

The following example demonstrates how to use LoopComponent with AlwaysTrue condition and LoopBreakComponent to implement infinite loop that needs to be interrupted under specific conditions.

**Note**: AlwaysTrue condition will cause loop to execute continuously until LoopBreakComponent explicitly interrupts the loop. For detailed information, see [LoopBreakComponent].

```python
>>> import asyncio
>>>
>>> from openjiuwen.core.context_engine import ModelContext
>>> from openjiuwen.core.session import Session
>>> from openjiuwen.core.session.workflow import create_workflow_session
>>> from openjiuwen.core.workflow import WorkflowComponent, Input, Output, LoopGroup, BranchComponent, LoopComponent, \
...   Workflow, Start, End, LoopSetVariableComponent, LoopBreakComponent
>>>
>>> # Common node component, returns input value
>>> class CommonNode(WorkflowComponent):
...     async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
...         return {"output": inputs["value"]}
>>>
>>> # Create LoopGroup
>>> loop_group = LoopGroup()
>>>
>>> # Create common node as loop body core component
>>> class commonNode(CommonNode):
...     async def invoke(self, inputs: Input, session: Session, context: Context) -> Output:
...         # Simulate loop body work, return result
...         return {"output": True}
>>>
>>> # Add workflow components to LoopGroup
>>> loop_group.add_workflow_comp("a", commonNode())
>>>
>>> # Create branch component for condition judgment
>>> branch_component = BranchComponent()
>>> loop_group.add_workflow_comp("branch", branch_component)
>>>
>>> # Create variable setting component, increment user_var
>>> set_variable_component = LoopSetVariableComponent({"user_var": "${loop.user_var} + 1"})
>>> loop_group.add_workflow_comp("setVar", set_variable_component)
>>>
>>> # Create loop termination component
>>> break_node = LoopBreakComponent()
>>> loop_group.add_workflow_comp("break", break_node)
>>>
>>> # Specify component "a" as loop start, component "setVar" as normal loop end
>>> loop_group.start_nodes(["a"])
>>> loop_group.end_nodes(["setVar"])
>>>
>>> # Set BranchComponent branch conditions
>>> # Branch 1: Continue loop, execute setVariableComponent
>>> branch_component.add_branch("${loop.index} < 2", ["setVar"])
>>> # Branch 2: Terminate loop, execute break component
>>> branch_component.add_branch("${loop.index} >= 2", ["break"])
>>>
>>> # Set component connections
>>> loop_group.add_connection("a", "branch")
>>>
>>> # Create LoopComponent, set output schema, capture final intermediate variable, loop count and loop results
>>> loop_component = LoopComponent(
...     loop_group,
...     {
...         "user_var": "${loop.user_var}",
...         "loop_count": "${loop.index}",
...         "results": "${a.output}"
...     }
... )
>>>
>>> # Create workflow instance
>>> workflow = Workflow()
>>>
>>> # Add start component
>>> workflow.set_start_comp("s", Start())
>>>
>>> # Add end component, reference loop component output results
>>> workflow.set_end_comp("e", End(),
...                        inputs_schema={
...                            "user_var": "${loop.user_var}",
...                            "loop_count": "${loop.loop_count}",
...                            "results": "${loop.results}"
...                        })
>>>
>>> # Add loop component, set to always_true loop type and intermediate variable initial value
>>> workflow.add_workflow_comp("loop", loop_component,
...                               inputs_schema={
...                                   "loop_type": "always_true",
...                                   "intermediate_var": {"user_var": 0}
...                               })
>>>
>>> # Serial connection of components: start->loop->end
>>> workflow.add_connection("s", "loop")
>>> workflow.add_connection("loop", "e")
>>>
>>> # Call invoke method to execute workflow
>>> result = asyncio.run(workflow.invoke({}, create_workflow_session()))
>>>
>>> print(f"执行结果: {result.result}")
>>> output = result.result.get('output', {})
>>>
>>> # Verify loop executed 3 times
>>> assert output.get("loop_count") == 3, f"Expected loop_count == 3, got {output.get('loop_count')}"
>>>
>>> # Verify execution result of each loop
>>> expected_results = [True, True, True]
>>> assert output.get("results") == expected_results, f"Expected {expected_results}, got {output.get('results')}"
```


## class LoopBreakComponent

Interruption component class, used to control loop interruption, provides powerful loop interruption capability for workflows, can actively terminate loop execution under specific conditions. When execution reaches this component, loop will immediately stop.

> **Note**
>
> * `LoopBreakComponent` can be used with [LoopComponent] and `loop_type="always_true"` (infinite loop). By setting infinite loop condition, loop executes continuously until encountering interruption component before exiting loop.
> * Can combine with condition judgment components to implement more complex condition interruption logic, for example, only execute interruption operation when specific conditions are satisfied.
> * In loop body, execution order of `LoopBreakComponent` will affect timing of loop interruption, need to reasonably arrange its position in loop body.

**Example**:

```python
>>> import asyncio
>>> 
>>> from openjiuwen.core.context_engine import ModelContext
>>> from openjiuwen.core.session import Session
>>> from openjiuwen.core.session.workflow import create_workflow_session
>>> from openjiuwen.core.workflow import WorkflowComponent, Workflow, Start, End, LoopGroup, LoopSetVariableComponent, \
...     BranchComponent, LoopBreakComponent, LoopComponent
>>> 
>>> 
>>> # Custom component AddTenNode, each call adds 10 to source in inputs
>>> class AddTenNode(WorkflowComponent):
...      def __init__(self, node_id: str):
...          super().__init__()
...          self.node_id = node_id
... 
...      async def invoke(self, inputs, session: Session, context: ModelContext):
...          return {"result": inputs["source"] + 10}
... 
>>> # Empty node, used to mark loop end node
>>> class EmptyNode(WorkflowComponent):
...      def __init__(self, node_id: str):
...          super().__init__()
...          self.node_id = node_id
... 
...      async def invoke(self, inputs, session: Session, context: ModelContext):
...          return inputs
... 
>>> 
>>> # Execute test
>>> # input_number=2, loop_number=2: loop executes 2 times
>>> async def run_workflow():
...      flow = Workflow()
...      flow.set_start_comp("s", Start())
...      flow.set_end_comp("e", End(),
...                        inputs_schema={"array_result": "${l.results}", "user_var": "${l.user_var}"})
... 
...      # Create loop body
...      loop_group = LoopGroup()
...      # AddTenNode("1"): Receives current loop index l.index as input, loop index automatically increments each time
...      # Note: "1" here is component identifier ID, used to reference its result in output_schema
...      loop_group.add_workflow_comp("1", AddTenNode("1"), inputs_schema={"source": "${l.index}"})
...      # AddTenNode("2"): Receives intermediate variable l.user_var as input
...      loop_group.add_workflow_comp("2", AddTenNode("2"),
...                                   inputs_schema={"source": "${l.user_var}"})
...      # SetVariableComponent: Updates intermediate variable l.user_var to AddTenNode("2") processing result
...      # This allows intermediate variable value of each loop to be updated based on previous loop result
...      set_variable_component = LoopSetVariableComponent({"${l.user_var}": "${2.result}"})
...      loop_group.add_workflow_comp("3", set_variable_component)
... 
...      # Add branch component to decide whether to continue loop
...      # BranchComponent is used to select different execution paths based on conditions
...      sw = BranchComponent()
...      # First branch condition: When current loop index (l.index) is greater than or equal to (loop_number - 1), execute break component
...      # Note: Index starts from 0, so when looping 3 times index values are 0,1,2, should interrupt when index=2
...      sw.add_branch("${l.index} >= ${loop_number} - 1", ["4"], "1")
...      # Second branch condition: When current loop index is less than (loop_number - 1), enter loop end node, start next round of loop
...      # Note: Branch target must be a node inside loop body, otherwise Channel not found error will occur
...      sw.add_branch("${l.index} < ${loop_number} - 1", ["5"], "2")
... 
...      loop_group.add_workflow_comp("sw", sw)
... 
...      # Add break component
...      # LoopBreakComponent is used to explicitly interrupt loop execution when conditions are satisfied
...      # This is a key component for controlling loop count when using always_true loop type
...      break_node = LoopBreakComponent()
...      loop_group.add_workflow_comp("4", break_node)
... 
...      # Add empty node as loop end node
...      empty_node = EmptyNode("5")
...      loop_group.add_workflow_comp("5", empty_node)
... 
...      # Set starting and ending nodes of loop body
...      loop_group.start_nodes(["1"])
...      loop_group.end_nodes(["5"])
...      # Set connection relationships between components in loop body
...      loop_group.add_connection("1", "2")
...      loop_group.add_connection("2", "3")
...      loop_group.add_connection("3", "sw")
...      loop_group.add_connection("4", "5")
... 
...      # Create LoopComponent
...      # Parameter 1: loop_group - defined loop body
...      # Parameter 2: output_schema - defines output structure of loop component
...      # - results: Collect results of AddTenNode("1") in each loop into array
...      # - user_var: Retain final intermediate variable value after loop ends
...      loop_component = LoopComponent(loop_group, {"results": "${1.result}", "user_var": "${l.user_var}"})
... 
...      # Add to workflow
...      # loop_type: "always_true" - means loop will execute continuously until encountering LoopBreakComponent interruption
...      # intermediate_var: Initialize intermediate variables in loop, here assign input_number value to user_var
...      # In always_true type loops, must control loop count or termination condition through LoopBreakComponent
...      flow.add_workflow_comp("l", loop_component, inputs_schema={"loop_type": "always_true",
...                                                                 "intermediate_var": {"user_var": "${input_number}"}})
... 
...      # Set connections
...      flow.add_connection("s", "l")
...      flow.add_connection("l", "e")
...      return await flow.invoke({"input_number": 2, "loop_number": 2}, create_workflow_session())
... 
>>> if __name__ == "__main__":
...      result = asyncio.get_event_loop().run_until_complete(run_workflow())
...      print(result.result)
... 
>>> # Expected result explanation:
>>> # - array_result: [10, 11] is result array after each AddTenNode("1") processing in loop
>>> #   (index 0+10=10, index 1+10=11)
>>> # - user_var: 22 is final intermediate variable value after loop ends, calculation process:
>>> #   Initial value 2 → 2+10=12 → 12+10=22 (2 loops total)
>>> # In always_true type loops, loop execution count is jointly controlled by LoopBreakComponent and branch conditions
>>> # In this example, when l.index >= loop_number - 1 (1 >= 2-1=1) triggers LoopBreakComponent to interrupt loop
>>> {'output': {'array_result': [10, 11], 'user_var': 22}}
```


## class LoopSetVariableComponent

```python
class LoopSetVariableComponent(variable_mapping: dict[str, Any])
```

`LoopSetVariableComponent` is openJiuwen's built-in workflow variable assignment component, this component provides ability to configure variable mapping, dynamically set and manage variables, used to implement dynamic data passing and state management in workflows. This component also supports use inside loop component [LoopComponent]. Through variable assignment component, can implement dynamic updating and passing of data during loop process.

**Parameters**:

* **variable_mapping** (dict[str, Any]): Variable mapping dictionary, defines variable names to be set and corresponding variable values, cannot be `None`. This mapping dictionary supports setting one or more key-value pairs, key-value pairs support the following types:
  * Variable mapping keys support the following 3 types:

    | Type| Description |  Example |
    | :---: |  :---: |  :---: |
    | Non-reference form variable name | String, directly get variable name | `"node_a.var_value"` |
    | Single-level reference form variable name | String, get variable name through `"${variable_path}"` placeholder syntax, reference path split by `.` symbol, only single-level path | `"${node_a.var_value}"` gets variable name which is `"node_a.var_value"` |
    | Nested reference form variable name |String, get variable name through `"${variable_path}"` placeholder syntax, reference path split by `.` symbol, has multiple levels  | `"${node_a.nested.var_value}"` gets variable name which is `"node_a.nested.var_value"` |

  * Variable mapping values support the following 3 types:

    | Type| Description |  Example |
    | :---: |  :---: |  :---: |
    | Fixed value | Directly input text, numbers and other static data | `"hello world"`, `123`, etc.|
    | Single-level dynamic reference | Get variable dynamic value through `"${variable_path}"` placeholder syntax, reference path split by `.` symbol, only single-level path | Assuming `node_b.var_value = "hello world"`, then `"${node_b.var_value}"` gets variable dynamic value which is `"hello world"` |
    | Nested dynamic reference | Get variable dynamic value through `"${variable_path}"` placeholder syntax, reference path split by `.` symbol, has multiple levels | Assuming `node_b.nested = {"var_value": 123}`, then `"${node_b.nested.var_value}"` gets variable dynamic value which is `123` |


**Examples**:

* Example 1:
  
```python
>>> # Use `LoopSetVariableComponent` to set intermediate variables in loop body
>>> import asyncio
>>> from openjiuwen.core.context_engine import ModelContext
>>> from openjiuwen.core.session import Session
>>> from openjiuwen.core.session.workflow import create_workflow_session
>>> from openjiuwen.core.workflow import WorkflowComponent, Workflow, Start, End, LoopGroup, LoopSetVariableComponent, \
...     BranchComponent, LoopBreakComponent, LoopComponent
>>> 
>>> 
>>> class AddTenNode(WorkflowComponent):
...     def __init__(self, node_id: str):
...         super().__init__()
...         self.node_id = node_id
... 
...     async def invoke(self, inputs, session: Session, context: ModelContext):
...         return {"result": inputs["source"] + 10}
>>> 
>>> 
>>> class CommonNode(WorkflowComponent):
...     def __init__(self, node_id: str):
...         super().__init__()
...         self.node_id = node_id
... 
...     async def invoke(self, inputs, session: Session, context: ModelContext):
...         return inputs
... 
...     async def stream(self, inputs, session: Session, context: ModelContext):
...         yield await self.invoke(inputs, session, context)
>>> 
>>> 
>>> # Create workflow
>>> flow = Workflow()
>>> # MockStartNode: Starting node of workflow, simply passes input data to next node
>>> flow.set_start_comp("s", Start())
>>> # MockEndNode: Ending node of workflow, collects and returns final result of workflow
>>> flow.set_end_comp("e", End(), inputs_schema={"array_result": "${b.array_result}", "user_var": "${b.user_var}"})
>>> # CommonNode: Common node, returns input data as-is
>>> flow.add_workflow_comp("a", CommonNode("a"))
>>> flow.add_workflow_comp("b", CommonNode("b"),
>>>                        inputs_schema={"array_result": "${l.results}", "user_var": "${l.user_var}"})
>>> 
>>> # Create loop body
>>> loop_group = LoopGroup()
>>> # AddTenNode: Special node, adds 10 to source field value in input then returns
>>> loop_group.add_workflow_comp("1", AddTenNode("1"), inputs_schema={"source": "${l.index}"})
>>> loop_group.add_workflow_comp("2", AddTenNode("2"), inputs_schema={"source": "${l.user_var}"})
>>> # Use SetVariableComponent to set intermediate variable
>>> set_variable_component = LoopSetVariableComponent({"${l.user_var}": "${2.result}"})
>>> loop_group.add_workflow_comp("3", set_variable_component)
>>> loop_group.start_nodes(["1"])
>>> loop_group.end_nodes(["3"])
>>> loop_group.add_connection("1", "2")
>>> loop_group.add_connection("2", "3")
>>> 
>>> # Create LoopComponent
>>> loop_component = LoopComponent(loop_group, {"results": "${1.result}", "user_var": "${l.user_var}"})
>>> 
>>> # Add to workflow
>>> flow.add_workflow_comp("l", loop_component, inputs_schema={"loop_type": "number", "loop_number": 3,
>>>                                                            "intermediate_var": {"user_var": "${input_number}"}})
>>> 
>>> # Set connections
>>> flow.add_connection("s", "a")
>>> flow.add_connection("a", "l")
>>> flow.add_connection("l", "b")
>>> flow.add_connection("b", "e")
>>> 
>>> 
>>> async def run_workflow():
...     return await flow.invoke({"input_number": 2}, WorkflowRuntime())
>>> 
>>> 
>>> if __name__ == "__main__":
...     # Execute test
...     result = asyncio.get_event_loop().run_until_complete(run_workflow())
...     print(result.result)
{'output': {'array_result': [10, 11, 12], 'user_var': 32}}
```

* Example 2:
```python
>>> # Use `LoopSetVariableComponent` to set intermediate variables in loop body
>>> import asyncio
>>> from openjiuwen.core.context_engine import ModelContext
>>> from openjiuwen.core.session import Session
>>> from openjiuwen.core.session.workflow import create_workflow_session
>>> from openjiuwen.core.workflow import WorkflowComponent, Workflow, Start, End, LoopGroup, LoopSetVariableComponent, \
...     BranchComponent, LoopBreakComponent, LoopComponent
>>> 
>>> 
>>> class AddTenNode(WorkflowComponent):
...     def __init__(self, node_id: str):
...         super().__init__()
...         self.node_id = node_id
... 
...     async def invoke(self, inputs, session: Session, context: ModelContext):
...         return {"result": inputs["source"] + 10}
>>> 
>>> 
>>> class CommonNode(WorkflowComponent):
...     def __init__(self, node_id: str):
...         super().__init__()
...         self.node_id = node_id
... 
...     async def invoke(self, inputs, session: Session, context: ModelContext):
...         return inputs
... 
...     async def stream(self, inputs, session: Session, context: ModelContext):
...         yield await self.invoke(inputs, session, context)
>>> 
>>> 
>>> flow = Workflow()
>>> flow.set_start_comp("s", Start())
>>> flow.set_end_comp("e", End(), inputs_schema={"result": "${a.nested.value}"})
>>> 
>>>  # Create SetVariableComponent, set nested structure variable
>>> set_variable_component = LoopSetVariableComponent({"${a.nested.value}": 123})
>>> flow.add_workflow_comp("a", set_variable_component)
>>> 
>>>  # Set connections
>>> flow.add_connection("s", "a")
>>> flow.add_connection("a", "e")
>>> 
>>> 
>>> async def run_workflow():
...     return await flow.invoke({"input_number": 2}, create_workflow_session())
>>> 
>>> 
>>> if __name__ == "__main__":
...     # Execute test
...     result = asyncio.get_event_loop().run_until_complete(run_workflow())
...     print(result.result)
{'output': {'result': 123}}
```

* Example 3:
  
```python
>>> import asyncio
>>> from openjiuwen.core.context_engine import ModelContext
>>> from openjiuwen.core.session import Session
>>> from openjiuwen.core.session.workflow import create_workflow_session
>>> from openjiuwen.core.workflow import WorkflowComponent, Workflow, Start, End, LoopGroup, LoopSetVariableComponent, \
    BranchComponent, LoopBreakComponent, LoopComponent
>>> 
>>> class AddTenNode(WorkflowComponent):
...     def __init__(self, node_id: str):
...         super().__init__()
...         self.node_id = node_id
...     async def invoke(self, inputs, session: Session, context: ModelContext):
...         return {"result": inputs["source"] + 10}
>>> 
>>> flow = Workflow()
>>> flow.set_start_comp("s", Start(), inputs_schema={"input_value": "${input_value}"})
>>> flow.set_end_comp("e", End(), inputs_schema={"result": "${c.result}"})
>>> 
>>>  # Create calculation node
>>>  # AddTenNode: Special node, adds 10 to source field value in input then returns
>>> flow.add_workflow_comp("a", AddTenNode("a"), inputs_schema={"source": "${s.input_value}"})
>>> 
>>>  # Use SetVariableComponent to set variable through reference path
>>> set_variable_component = LoopSetVariableComponent({"${b.value}": "${a.result}"})
>>> flow.add_workflow_comp("b", set_variable_component)
>>> 
>>>  # Create another calculation node, use set variable
>>> flow.add_workflow_comp("c", AddTenNode("c"), inputs_schema={"source": "${b.value}"})
>>> 
>>>  # Set connections
>>> flow.add_connection("s", "a")
>>> flow.add_connection("a", "b")
>>> flow.add_connection("b", "c")
>>> flow.add_connection("c", "e")
>>> 
>>>  # Execute test
>>> async def run_workflow():
...       return await flow.invoke({"input_value": 5}, create_workflow_session())
>>> 
>>> if __name__ == "__main__":
...     result = asyncio.get_event_loop().run_until_complete(run_workflow())
...     print(result.result)
{'output': {'result': 25}}
```
