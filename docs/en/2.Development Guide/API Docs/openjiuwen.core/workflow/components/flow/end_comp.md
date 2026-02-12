# openjiuwen.core.workflow

## class End

```python
class End(conf: Union[EndConfig, dict] = None)
```

`End` component is openJiuwen's built-in workflow end component, this component defines the output of the workflow, supports outputting data according to predefined template format and outputting according to input definition. `End` component implements the four capabilities of base component `ComponentExecutable`: `invoke`, `stream`, `transform` and `collect`, implementation principle is to format output accordingly for different inputs.

**Parameters**:

- **conf** (Union[[EndConfig]), dict], optional): Output format configuration information. When it is dict type, key must be "response_template", value is str. Default value: `None`, means no formatted output is needed.

> **Note**
>
> - `conf` currently only supports `response_template` configuration.
> - `response_template` represents rendering template, when non-empty outputs according to template; `None` and "" both mean not using template. Default value: `None`. For example, when `End` component execution input is `{"user_var": "你好"}`, `conf` configuration is `{"response_template": "输出:{{user_var}}"}`, then `End` component output is `"输出:你好"`; if `conf` is empty or `response_template` is empty, then `End` component output is `{"user_var": "你好"}`.


**Examples**:

```python
>>> # Example 1: Demonstrate basic usage of End component and workflow creation process
>>> import asyncio
>>> from openjiuwen.core.workflow  import Start, End, Workflow, create_workflow_session
>>> 
>>> async def demo_end_component():
...     workflow = Workflow()
... 
...     workflow.set_start_comp("start", Start(),
...                             inputs_schema={
...                                 "query": "${user_inputs.query}",
...                                 "content": "${user_inputs.content}",
...                             })
... 
...     workflow.set_end_comp("end", End(),
...                           inputs_schema={"param1": "${start.query}", "param2": "${start.content}"})
... 
...     workflow.add_connection("start", "end")
... 
...     result = await workflow.invoke(
...         inputs={"user_inputs": {
...             "query": "你好",
...             "content": "杭州"
...         }},
...         session=create_workflow_session()
...     )
... 
...     print(f"{result}")
>>> 
>>> def main():
...     asyncio.run(demo_end_component())
>>> 
>>> if __name__ == "__main__":
...     main()
result={'output': {'param1': '你好', 'param2': '杭州'}} state=<WorkflowExecutionState.COMPLETED: 'COMPLETED'>
```
```python
>>> # Example 2: Demonstrate End component response template usage
>>> import asyncio
>>> 
>>> import asyncio
>>> from openjiuwen.core.workflow  import Start, End, Workflow, create_workflow_session
>>> 
>>> async def demo_end_component():
...     workflow = Workflow()
... 
...     workflow.set_start_comp("start", Start(),
...                             inputs_schema={
...                                 "query": "${user_inputs.query}",
...                                 "content": "${user_inputs.content}",
...                             })
... 
...     conf = {
...         "response_template": "渲染结果:{{param1}},{{param2}}"
...     }
...     workflow.set_end_comp("end", End(conf),
...                           inputs_schema={"param1": "${start.query}", "param2": "${start.content}"})
... 
...     workflow.add_connection("start", "end")
... 
...     result = await workflow.invoke(
...         inputs={"user_inputs": {
...             "query": "你好",
...             "content": "杭州"
...         }},
...         create_workflow_session()
...     )
... 
...     print(f"{result}")
>>> 
>>> def main():
...     asyncio.run(demo_end_component())
>>> 
>>> if __name__ == "__main__":
...     main()
result={'response': '渲染结果:你好,杭州'} state=<WorkflowExecutionState.COMPLETED: 'COMPLETED'>
```

## class EndConfig

Configuration data class for `End` component.

- **response_template** (str, optional): Template configuration for `End` component formatted output. Default value: `None`, means output according to input definition.
