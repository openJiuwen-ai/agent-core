# openjiuwen.core.workflow

## class End

```python
class End(conf: Union[EndConfig, dict] = None)
```

`End`组件是openJiuwen内置的工作流结束组件，该组件定义了工作流的输出，支持按照预定义的模板格式输出数据和按照输入定义输出两个方式。`End`组件实现了基类组件`ComponentExecutable`的四个能力 `invoke`，`stream`，`transform`和`collect`，实现的原理是对不同的输入进行相应的格式化输出。

**参数**：

- **conf**(Union[[EndConfig]), dict], 可选)：输出格式配置信息。当为dict类型时，需键为"response_template"，值为str。默认值：`None`，表示不需要格式化输出。

> **说明**
>
> - `conf`当前仅支持`response_template`配置。
> - `response_template`表示渲染模板，非空时按模版输出；为`None`和""都表示不使用模板。默认值：`None`。例如`End`组件执行时输入为`{"user_var": "你好"}`，`conf`配置为`{"response_template": "输出:{{user_var}}"}`，则`End`组件输出为`"输出:你好"`；若`conf`为空或`response_template`为空，则`End`组件输出为`{"user_var": "你好"}`。


**样例**：

```python
>>> # 样例1: 演示End组件的基本用法和工作流创建流程
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
>>> # 样例2: 演示End组件的响应模板用法
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

`End`组件的配置数据类。

- **response_template**(str, 可选)：`End`组件格式化输出的模板配置。默认值：`None`，表示按照输入定义输出。
