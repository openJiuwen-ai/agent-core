# openjiuwen.core.workflow

## class BranchComponent

`BranchComponent`是openJiuwen内置的工作流的分支组件，该组件提供了多分支条件判断、动态控制工作流跳转至目标组件的能力，用于设计工作流的分支流程，实现逻辑判断功能。

> **说明**
> 分支组件为可选组件，若在工作流中添加该组件，则无需显式地调用工作流的[add_connection]或[add_conditional_connection]方法来定义该组件和目标组件之间的连接，且需要遵循以下约束：
>
> * 输入：支持添加多个条件分支。但请注意，至少要设置一个分支在执行时能满足预设条件，以确保工作流能够正常运行。其中，分支预设条件的判断遵循以下规则：
>   * 分支组件按顺序逐个判断当前工作流数据状态是否满足分支中预设的条件，一旦发现某个分支满足条件，将立即跳转到该分支对应的目标组件继续执行，其余分支将不再进行判断及执行。
>   * 如果所有分支均不满足条件，会导致工作流执行失败并抛出`JiuWenBaseException`异常，错误码为101102，错误信息为"Branch meeting the condition was not found."。
> * 输出：支持通过条件分支将工作流定向至一个或多个预定义的目标组件。但请注意，返回的目标组件ID将用于控制工作流流程跳转，若不希望产生无效的分支，需要将所有目标组件ID均设置为已在工作流中定义注册的组件ID。当分支逻辑返回一个目标组件时，执行完分支组件将直接执行该目标组件；而当分支逻辑返回多个目标组件时，将通过任务调度机制并行执行列表中的目标组件。

### add_branch

```python
add_branch(condition: Union[str, Callable[[], bool], Condition], target: Union[str, list[str]], branch_id: str = None)
```

`add_branch`函数用于向分支组件中添加一个分支逻辑，包含该分支的预设条件、目标组件、分支标识。

**参数**：

* **condition**(Union[str, Callable[[], bool], Condition])：表示该分支的预设条件, `condition`不可取值为`None`。`condition`支持以下3种类型：
  
  * 若为`str`，表示字符串形式的`bool`表达式。该表达式遵循的规则参见[ExpressionCondition]。
  * 若为`Callable[[], bool]`，表示一个返回值为`bool`类型的函数，用于判断条件是否满足。示例：

    ```python
    >>> def branch_function() -> bool:
    >>>     return True
    ```

  * 若为`Condition`，表示一个预定义的、可调用的`openjiuwen.core.component.condition.condition.Condition`对象。
* **target**(Union[str, list[str]])：表示满足条件后要跳转到的单个目标组件ID或者目标组件ID列表, `target`不可取值为`None`，`''`或者`[]`。`target`支持以下2种类型：
  
  * 若为`str`，表示单个目标组件的组件ID。
  * 若为`list[str]`，表示多个目标组件的组件ID列表，列表中项不可取值为`None`或者`''`。
* **branch_id**(str, 可选)：表示该条件分支的标识。默认值：`None`。


**样例**：

以创建带有分支组件的工作流为例，介绍分支组件的使用方法。该工作流输入为数字，根据数字的取值分支组件设置不同的条件分支：

* 分支`pos_branch`：判断数值为正数直接执行结束组件。
* 分支`neg_branch`：若判断数值为负数，通过自定义的绝对值计算组件，计算绝对值后执行结束组件。
* 分支`zero_branch`：若判断数值为零，根据用户设置的环境变量`ALLOW_ZERO`来决定工作流走向，`ALLOW_ZERO`为`true`，则通过自定义的绝对值计算组件，计算绝对值后执行结束组件；反之，找不到符合条件的分支，工作流执行失败并抛出异常。

```python
>>> import asyncio
>>> import os
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
... 
>>> 
>>> 
>>> async def run_workflow(num: int) -> tuple[WorkflowOutput | str, bool]:
...     # 构造工作流、初始化工作流运行时
...     workflow = Workflow()
... 
...     # 添加开始、结束组件到工作流
...     workflow.set_start_comp("start", Start(), inputs_schema={"query": "${user_inputs.query}"})
...     workflow.set_end_comp("end", End(), inputs_schema={"raw": "${start.query}", "updated": "${abs.result}"})
... 
...     # 添加实现绝对值计算的自定义组件
...     abs_comp = AbsComponent()
...     workflow.add_workflow_comp("abs", abs_comp, inputs_schema={"num": "${start.query}"})
... 
...     # 添加分支组件
...     branch_comp = BranchComponent()
...     # 1. 分支pos_branch：condition为str类型
...     expression_str = "${start.query} > 0"
...     branch_comp.add_branch(expression_str, ["end"], "pos_branch")
... 
...     # 2. 分支neg_branch：condition为Condition类型
...     expression_condition = ExpressionCondition("${start.query} < 0")
...     branch_comp.add_branch(expression_condition, ["abs"], "neg_branch")
... 
...     # 3. 分支zero_branch：condition为Callable[[], bool]类型
...     def expression_callable() -> bool:
...         return str(os.environ.get("ALLOW_ZERO", "false")).lower() == "true"
... 
...     branch_comp.add_branch(expression_callable, ["abs"], "zero_branch")
...     workflow.add_workflow_comp("branch", branch_comp)
... 
...     # 定义节点连接：分支组件的条件边无需显式地调用工作流的add_connection或add_conditional_connection方法
...     workflow.add_connection("start", "branch")
...     workflow.add_connection("abs", "end")
... 
...     # 构造输入、调用工作流
...     inputs = {"user_inputs": {"query": num}}
... 
...     try:
...         workflow_output = await workflow.invoke(inputs, create_workflow_session())
...         return workflow_output, True
...     except JiuWenBaseException as e:
...         print(f"workflow execute error: {e}")
...         return e.message, False
...
>>> async def main():
...     # 当用户输入为10时，满足分支pos_branch，依次执行组件start，branch，end，工作流执行成功。
...     workflow_output, run_success = await run_workflow(num=10)
...     assert run_success is True
...     assert workflow_output is not None
...     result = workflow_output.result
...     assert result.get("output", {}).get("raw") == 10
...     assert result.get("output", {}).get("updated") is None
...     print(result)
... 
...     # 当用户输入为-10时，满足分支neg_branch，依次执行组件start，branch，abs，end，工作流执行成功。
...     workflow_output, run_success = await run_workflow(num=-10)
...     assert run_success is True
...     assert workflow_output is not None
...     result = workflow_output.result
...     assert result.get("output", {}).get("raw") == -10
...     assert result.get("output", {}).get("updated") == 10
...     print(result)
... 
...     # 当用户输入为0，环境变量ALLOW_ZERO为true时，满足zero_branch，依次执行组件start，branch，abs，end，工作流执行成功。
...     os.environ["ALLOW_ZERO"] = "true"
...     workflow_output, run_success = await run_workflow(num=0)
...     assert run_success is True
...     assert workflow_output is not None
...     result = workflow_output.result
...     assert result.get("output", {}).get("raw") == 0
...     assert result.get("output", {}).get("updated") == 0
...     print(result)
... 
...     # 当用户输入为0，环境变量ALLOW_ZERO为false时，找不到满足的条件分支，工作流执行失败并抛出异常。
...     os.environ["ALLOW_ZERO"] = "false"
...     workflow_output, run_success = await run_workflow(num=0)
...     assert run_success is False
...     assert workflow_output == "Branch meeting the condition was not found."
...     print(workflow_output)
...
>>> asyncio.run(main())
{'output': {'raw': 10}}
{'output': {'raw': -10, 'updated': 10}}
{'output': {'raw': 0, 'updated': 0}}
workflow execute error: [101102] Branch meeting the condition was not found.
Branch meeting the condition was not found.
```
