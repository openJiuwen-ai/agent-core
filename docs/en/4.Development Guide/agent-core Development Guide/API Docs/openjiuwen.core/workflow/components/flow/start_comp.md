# openjiuwen.core.workflow

## class Start

```python
class Start()
```

`Start` is openJiuwen's built-in workflow start component, this component defines the entry point of the workflow, used to receive user input.


**Example**:

```python
>>> import asyncio
>>> from openjiuwen.core.workflow import Start, Workflow, create_workflow_session
>>> 
>>> # Demonstrate basic usage of Start component and workflow creation process
>>> async def demo_start_component():
...     # 1. Create workflow instance
...     workflow = Workflow()
.
...     
...     # 3. Create and set start component
...     start_comp = Start()
...     workflow.set_start_comp("start", start_comp,
...                            inputs_schema={"query": "${user_inputs.query}", "content": "${user_inputs.content}"})
...     
...     # 4. Set end component to capture output
...     workflow.set_end_comp("end", Start(),
...                          inputs_schema={"query": "${user_inputs.query}", "content": "${user_inputs.content}"})
...     
...     # 5. Add connections between components
...     workflow.add_connection("start", "end")
...     
...     # 6. Execute workflow
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
>>> # `Start` component will format user's `input` according to `conf` and pass to next component, formatted result is:
result={'query': '你好', 'content': '杭州'} state=<WorkflowExecutionState.COMPLETED: 'COMPLETED'>
```
