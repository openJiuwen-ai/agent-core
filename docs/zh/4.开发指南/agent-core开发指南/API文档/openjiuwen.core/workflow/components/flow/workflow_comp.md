# openjiuwen.core.workflow

## class SubWorkflowComponent

```python
class SubWorkflowComponent(sub_workflow: Workflow)
```

`SubWorkflowComponent`是openJiuwen内置的子工作流组件，该组件提供了嵌入子工作流的能力，用于实现工作流复杂业务逻辑的分层管理和灵活组合。当执行到子工作流组件时，该组件会将输入数据和配置信息传递给子工作流，并启动子工作流的执行。子工作流执行完成后，将返回结果继续执行主工作流。

**参数**：

* **sub_workflow**([Workflow](../../workflow.md#class-workflow))：子工作流实例，不可为`None`。


**样例**：

```python
>>> import asyncio
>>> 
>>> from openjiuwen.core.context_engine import ModelContext
>>> from openjiuwen.core.session.node import Session
>>> 
>>> from openjiuwen.core.workflow import Input, Output, SubWorkflowComponent, WorkflowCard, WorkflowComponent, Start, \
...     Workflow, End, create_workflow_session
... 
>>> 
>>> class CustomComponent(WorkflowComponent):
...     """自定义组件"""
... 
...     def __init__(self):
...         super().__init__()
... 
...     async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
...         """处理输入并返回用户query"""
...         query = inputs.get("query", "")
...         return {
...             "result": query
...         }
... 
>>> 
>>> # 初始化子工作流
>>> sub_workflow = Workflow()
>>> 
>>> # 添加以sub_start、sub_node、sub_end为ID的3个组件到子工作流
>>> sub_workflow.set_start_comp("sub_start", Start(), inputs_schema={"query": "${query}"})
>>> sub_workflow.add_workflow_comp("sub_custom_comp", CustomComponent(), inputs_schema={"query": "${sub_start.query}"})
>>> sub_workflow.set_end_comp("sub_end", End(), inputs_schema={"result": "${sub_custom_comp.result}"})
>>> 
>>> # 设置子工作流拓扑连接，sub_start -> sub_custom_comp -> sub_end
>>> sub_workflow.add_connection("sub_start", "sub_custom_comp")
>>> sub_workflow.add_connection("sub_custom_comp", "sub_end")
>>> 
>>> # 初始化主工作流
>>> main_workflow = Workflow(card=WorkflowCard(id="test_workflow"))
>>> 
>>> # 使用之前构造的子工作流实例构造工作流执行组件
>>> sub_workflow_comp = SubWorkflowComponent(sub_workflow)
>>> 
>>> # 添加以start、sub_workflow_comp、end为ID的3个组件到主工作流
>>> main_workflow.set_start_comp("start", Start(), inputs_schema={"query": "${user_inputs.query}"})
>>> main_workflow.add_workflow_comp("sub_workflow_comp", sub_workflow_comp, inputs_schema={"query": "${start.query}"})
>>> main_workflow.set_end_comp("end", End(), inputs_schema={"result": "${sub_workflow_comp.output.result}"})
>>> 
>>> # 设置主工作流拓扑连接，start -> sub_workflow_comp -> end
>>> main_workflow.add_connection("start", "sub_workflow_comp")
>>> main_workflow.add_connection("sub_workflow_comp", "end")
>>> 
>>> 
>>> async def run_workflow():
...     # 创建工作流运行时
...     runtime = create_workflow_session(session_id="test_session")
... 
...     # 构造输入
...     inputs = {"user_inputs": {"query": "hello"}}
... 
...     # 调用工作流
...     result = await main_workflow.invoke(inputs, runtime)
...     return result
... 
>>> 
>>> res = asyncio.run(run_workflow())
>>> print(f"main workflow with sub workflow run result: {res}")
main workflow with sub workflow run result: result={'output': {'result': 'hello'}} state=<WorkflowExecutionState.COMPLETED: 'COMPLETED'>
```
