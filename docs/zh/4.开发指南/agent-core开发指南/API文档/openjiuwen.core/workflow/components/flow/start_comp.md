# openjiuwen.core.workflow

## class Start

```python
class Start()
```

`Start`为openJiuwen内置的工作流开始组件，该组件定义了工作流的入口，用于接收用户的输入。


**样例**：

```python
>>> import asyncio
>>> from openjiuwen.core.workflow import Start, Workflow, create_workflow_session
>>> 
>>> # 演示Start组件的基本用法和工作流创建流程
>>> async def demo_start_component():
...     # 1. 创建工作流实例
...     workflow = Workflow()
...     
...     # 3. 创建并设置开始组件
...     start_comp = Start()
...     workflow.set_start_comp("start", start_comp,
...                            inputs_schema={"query": "${user_inputs.query}", "content": "${user_inputs.content}"})
...     
...     # 4. 设置结束组件捕获输出
...     workflow.set_end_comp("end", Start(),
...                          inputs_schema={"query": "${user_inputs.query}", "content": "${user_inputs.content}"})
...     
...     # 5. 添加组件间连接
...     workflow.add_connection("start", "end")
...     
...     # 6. 执行工作流
...     result = await workflow.invoke(
...        {"user_inputs": {"query": "你好", "content": "杭州"}}, create_workflow_session()
...     )
... 
...     print(f"{result}")
>>> 
>>> def main():
...     asyncio.run(demo_start_component())
>>> 
>>> if __name__ == "__main__":
...     main()
>>> # `Start`组件会根据`conf`对于用户的`input`进行格式化，传递给下一个组件，格式化完的结果为：
result={'query': '你好', 'content': '杭州'} state=<WorkflowExecutionState.COMPLETED: 'COMPLETED'>
```
