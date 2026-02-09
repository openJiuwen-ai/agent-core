# openjiuwen.core.workflow

## class BranchRouter

```python
class BranchRouter(report_trace: bool = False)
```

`BranchRouter` is the core class implementing branch router, provides ability to manage and switch different execution branches, used to design branch flows of workflows, usually used in conjunction with workflow's [add_conditional_connection] method. After source component execution completes, workflow will call `BranchRouter`'s callable object, decide next workflow direction based on its return value. If all branches managed by branch router do not satisfy preset conditions, will throw `JiuWenBaseException` exception, error code 101102, error message "Branch meeting the condition was not found.".

**Parameters**:

* **report_trace** (bool, optional): Debug information recording flag. `True` means workflow will record and report execution situation of each branch, `False` means workflow will not record and report execution situation of each branch. Default value: `False`.

### add_branch

```python
add_branch(self, condition: Union[str, Callable[[], bool], Condition], target: Union[str, list[str]], branch_id: str = None)
```

`add_branch` function is used to add a branch logic to the branch router, including preset conditions, target components, and branch identifier for this branch.

**Parameters**:

* **condition** (Union[str, Callable[[], bool], Condition]): Represents preset conditions for this branch, cannot be `None`. `condition` supports the following 3 types:
  
  * If it is `str`, represents a `bool` expression in string form. Rules followed by this expression see [ExpressionCondition].
  * If it is `Callable[[], bool]`, represents a function that returns a `bool` type, used to judge whether conditions are satisfied. Example:

    ```python
    def branch_function() -> bool:
        return True
    ```

  * If it is `Condition`, represents a predefined, callable `openjiuwen.core.component.condition.condition.Condition` object.
* **target** (Union[str, list[str]]): Represents a single target component ID or target component ID list to jump to after conditions are satisfied, cannot be `None`. `target` supports the following 2 types:
  
  * If it is `str`, represents a single target component's component ID.
  * If it is `list[str]`, represents multiple target components' component ID list.
* **branch_id** (str, optional): Represents the identifier for this condition branch. Default value: `None`.


**Example**:

Taking adding conditional edges in workflow as an example, introduces usage of branch router. This workflow input is a number, branch combines `add_conditional_connection` method to add different condition branches based on the number's value:

* Branch `pos_branch`: If number is positive, directly execute end component.
* Branch `neg_branch`: If number is negative, through custom absolute value calculation component, calculate absolute value then execute end component.
* Branch `zero_branch`: If number is zero, based on user-set environment variable `ALLOW_ZERO` to decide workflow direction. If `ALLOW_ZERO` is `true`, through custom absolute value calculation component, calculate absolute value then execute end component; otherwise, no branch meeting conditions is found, workflow execution fails and throws exception.

```python
>>> import asyncio
>>> import os
>>> 
>>> from openjiuwen.core.common.exception.exception import JiuWenBaseException
>>> from openjiuwen.core.context_engine import ModelContext
>>> from openjiuwen.core.workflow.components import Session
>>> from openjiuwen.core.workflow import create_workflow_session
>>> from openjiuwen.core.workflow import WorkflowComponent, WorkflowOutput, Workflow, Start, End, BranchRouter, \
...     ExpressionCondition
>>> 
>>> 
>>> class AbsComponent(WorkflowComponent):
...      async def invoke(self, inputs, session: Session, context: ModelContext):
...          num = inputs["num"]
...          if num < 0:
...              return {"result": -num}
...          return {"result": num}
>>> 
>>> async def run_workflow(num: int) -> tuple[WorkflowOutput | str, bool]:
...      # Construct workflow, initialize workflow runtime
...      workflow = Workflow()
... 
...      # Add start, end components to workflow
...      workflow.set_start_comp("start", Start(), inputs_schema={"query": "${user_inputs.query}"})
...      workflow.set_end_comp("end", End(), inputs_schema={"raw": "${start.query}", "updated": "${abs.result}"})
... 
...      # Add custom component implementing absolute value calculation
...      abs_comp = AbsComponent()
...      workflow.add_workflow_comp("abs", abs_comp, inputs_schema={"num": "${start.query}"})
... 
...      # Define branch router, add branches
...      router = BranchRouter()
... 
...      # 1. Branch pos_branch: condition is str type
...      expression_str = "${start.query} > 0"
...      router.add_branch(expression_str, ["end"], "pos_branch")
... 
...      # 2. Branch neg_branch: condition is Condition type
...      expression_condition = ExpressionCondition("${start.query} < 0")
...      router.add_branch(expression_condition, ["abs"], "neg_branch")
... 
...      # 3. Branch zero_branch: condition is Callable[[], bool] type
...      def expression_callable() -> bool:
...          return str(os.environ.get("ALLOW_ZERO", "false")).lower() == "true"
... 
...      router.add_branch(expression_callable, ["abs"], "zero_branch")
... 
...      # Define node connections
...      workflow.add_conditional_connection("start", router=router)
...      workflow.add_connection("abs", "end")
... 
...      # Construct input, invoke workflow
...      inputs = {"user_inputs": {"query": num}}
... 
...      try:
...          workflow_output = await workflow.invoke(inputs, create_workflow_session())
...          return workflow_output, True
...      except JiuWenBaseException as e:
...          print(f"workflow execute error: {e}")
...          return e.message, False
... 
>>> async def main():
...      # When user input is 10, satisfies branch pos_branch, executes components start, branch, end in order, workflow execution succeeds.
...      workflow_output, run_success = await run_workflow(num=10)
...      assert run_success is True
...      assert workflow_output is not None
...      result = workflow_output.result
...      assert result.get("output", {}).get("raw") == 10
...      assert result.get("output", {}).get("updated") is None
...      print(result)
... 
...      # When user input is -10, satisfies branch neg_branch, executes components start, branch, abs, end in order, workflow execution succeeds.
...      workflow_output, run_success = await run_workflow(num=-10)
...      assert run_success is True
...      assert workflow_output is not None
...      result = workflow_output.result
...      assert result.get("output", {}).get("raw") == -10
...      assert result.get("output", {}).get("updated") == 10
...      print(result)
... 
...      # When user input is 0, environment variable ALLOW_ZERO is true, satisfies zero_branch, executes components start, branch, abs, end in order, workflow execution succeeds.
...      os.environ["ALLOW_ZERO"] = "true"
...      workflow_output, run_success = await run_workflow(num=0)
...      assert run_success is True
...      assert workflow_output is not None
...      result = workflow_output.result
...      assert result.get("output", {}).get("raw") == 0
...      assert result.get("output", {}).get("updated") == 0
...      print(result)
... 
...      # When user input is 0, environment variable ALLOW_ZERO is false, no branch meeting conditions is found, workflow execution fails and throws exception.
...      os.environ["ALLOW_ZERO"] = "false"
...      workflow_output, run_success = await run_workflow(num=0)
...      assert run_success is False
...      assert workflow_output == "Branch meeting the condition was not found."
...      print(workflow_output)
... 
>>> asyncio.run(main())
{'output': {'raw': 10}}
{'output': {'raw': -10, 'updated': 10}}
{'output': {'raw': 0, 'updated': 0}}
workflow execute error: [101102] Branch meeting the condition was not found.
Branch meeting the condition was not found.
```
