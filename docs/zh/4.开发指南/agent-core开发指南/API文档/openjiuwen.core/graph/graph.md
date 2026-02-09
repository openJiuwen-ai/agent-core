# openjiuwen.core.graph

## class Graph

```python
class openjiuwen.core.graph.base.Graph()
```

`Graph`类是工作流执行图的基类，抽象了图的构建过程，支持添加起始节点、中间节点和结束节点，并支持在这些节点之间建立普通边或带有条件的边。用户在继承`WorkflowComponent`实现自定义节点时， 需要提供自定义`add_component`实现时，可调用`Graph`的方法，自定义如何加入`Graph`。

### start_node

```python
start_node(self, node_id: str) -> Self
```

指定图的起始节点，并返回当前图实例。

**参数**：

* **node_id**(str)：起始节点的id，若当前已设置过start_node，则覆盖为当前`node_id`。

**返回**：

**Graph**，接口返回Graph实例本身。

**异常**：

* **JiuWenBaseException**：openJiuwen异常基类，具体详细信息和解决方法，参见[StatusCode](../common/exception/status_code.md)。

**样例**：

自定义节点`DemoCmpWithRandomStart`，并自定义实现`add_component`，根据预设的start节点id列表，随机选择设置start节点。

```python
>>> import random
>>> 
>>> from openjiuwen.core.context_engine import ModelContext
>>> from openjiuwen.core.workflow.components import Session
>>> from openjiuwen.core.workflow import Input, Output, WorkflowComponent
>>> from openjiuwen.core.graph.base import Graph
>>> 
>>> class DemoCmpWithRandomStart(WorkflowComponent):
...     def __init__(self, start_ids):
...         super().__init__()
...         self.start_ids:list[str] = start_ids
... 
...     def add_component(self, graph: Graph, node_id: str, wait_for_all: bool = False) -> None:
...         choose_idx = random.randint(0, len(self.start_ids))
...         graph.start_node(self.start_ids[choose_idx])
...         graph.add_node(node_id, self.to_executable(), wait_for_all=wait_for_all)
... 
...     async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
...         return inputs
... 
...     def to_executable(self) -> Executable:
...         return self
... 
```

### end_node

```python
end_node(self, node_id) -> Self
```

指定图的结束节点，并返回当前图实例。

**参数**：

* **node_id**(str)：结束节点的id。

**返回**：

**Graph**，接口返回Graph实例本身。

**异常**：

* **JiuWenBaseException**：openJiuwen异常基类，具体详细信息和解决方法，参见[StatusCode](../common/exception/status_code.md)。

**样例**：

自定义节点`DemoCmpWithRandomEnd`，并自定义实现`add_component`，根据预设的end节点id列表，随机选择设置end节点。

```python
>>> import random
>>> 
>>> from openjiuwen.core.workflow import WorkflowComponent
>>> from openjiuwen.core.context_engine import ModelContext
>>> from openjiuwen.core.graph.base import Graph
>>> from openjiuwen.core.graph.executable import Input, Output, Executable
>>> from openjiuwen.core.workflow.components import Session
>>> 
>>> 
>>> class DemoCmpWithRandomEnd(WorkflowComponent):
...     def __init__(self, end_ids):
...         super().__init__()
...         self.end_ids:list[str] = end_ids
... 
...     def add_component(self, graph: Graph, node_id: str, wait_for_all: bool = False) -> None:
...         choose_idx = random.randint(0, len(self.end_ids))
...         graph.end_node(self.end_ids[choose_idx])
...         graph.add_node(node_id, self.to_executable(), wait_for_all=wait_for_all)
... 
...     async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
...         return inputs
... 
...     def to_executable(self) -> Executable:
...         return self
...
```

### add_node

```python
add_node(self, node_id: str, node: Executable, *, wait_for_all: bool=False) -> Self
```

将一个可执行节点添加到图中，并返回当前图实例。

**参数**：

* **node_id**(str)：节点`id`。
* **node**(Executable)：节点的实例。
* **wait_for_all**(bool，可选)：是否支持并发执行。`True`表示支持并发执行，允许多次执行该节点。`False`表示不支持并发执行，即需要等待前置节点全部执行完才能执行该节点。默认值：`False`。

**返回**：

**Graph**，接口返回Graph实例本身。

**异常**：

* **JiuWenBaseException**：openJiuwen异常基类，具体详细信息和解决方法，参见[StatusCode](../common/exception/status_code.md)。

**样例**：

自定义节点`DemoCmpWithNode`，并自定义实现`add_component`，将`inner_node`作为本节点的后置节点。

```python
>>> from openjiuwen.core.context_engine import ModelContext
>>> from openjiuwen.core.workflow import ComponentComposable, ComponentExecutable, WorkflowComponent
>>> from openjiuwen.core.graph.base import Graph
>>> from openjiuwen.core.graph.executable import Input, Output, Executable
>>> from openjiuwen.core.workflow import Session
>>> 
>>> 
>>> class DemoCmpWithNode(ComponentComposable):
...     def __init__(self):
...         super().__init__()
...         self.inner_node = InnerNode()
... 
...     def add_component(self, graph: Graph, node_id: str, wait_for_all: bool = False) -> None:
...         graph.add_node(node_id, self.to_executable(), wait_for_all=wait_for_all)
...         graph.add_node("inner", self.inner_node, wait_for_all=True)
...         graph.add_edge(node_id, "inner")
... 
...     def to_executable(self) -> Executable:
...         return self.inner_node
... 
>>> class InnerNode(ComponentExecutable):
...     def __init__(self):
...         super().__init__()
...         
...     async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
...         return inputs
... 
```

### add_edge

```python
add_edge(self, source_node_id: Union[str, list[str]], target_node_id: str) -> Self
```

为当前图添加一条从指定源节点到目标节点的边，支持添加多个源节点指向同一个目标节点的多条边，并返回当前图实例。

**参数**：

* **source_node_id**(Union[str, list[str]])：源节点的`node_id`，可以是单一字符串或字符串列表。若为字符串列表，表示从多个源节点分别指向目标节点。
* **target_node_id**(str)：目标节点的`node_id`。

**返回**：

**Graph**，接口返回Graph实例本身。

**异常**：

* **JiuWenBaseException**：openJiuwen异常基类，具体详细信息和解决方法，参见[StatusCode](../common/exception/status_code.md)。

**样例**：

自定义节点`DemoCmpWithNode`，并自定义实现`add_component`，将`inner_node`作为本节点的后置节点，并连接边。

```python
>>> from openjiuwen.core.context_engine import ModelContext
>>> from openjiuwen.core.workflow import ComponentComposable, ComponentExecutable
>>> from openjiuwen.core.graph.base import Graph
>>> from openjiuwen.core.graph.executable import Input, Output, Executable
>>> from openjiuwen.core.workflow import Session
>>> 
>>> 
>>> class DemoCmpWithNode(ComponentComposable):
...     def __init__(self):
...         super().__init__()
...         self.inner_node = InnerNode()
... 
...     def add_component(self, graph: Graph, node_id: str, wait_for_all: bool = False) -> None:
...         graph.add_node(node_id, self.to_executable(), wait_for_all=wait_for_all)
...         graph.add_node("inner", self.inner_node, wait_for_all=True)
...         graph.add_edge(node_id, "inner")
... 
...     def to_executable(self) -> Executable:
...         return self.inner_node
... 
>>> 
>>> class InnerNode(ComponentExecutable):
...     def __init__(self):
...         super().__init__()
... 
...     async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
...         return inputs
...
```

### add_conditional_edges

```python
add_conditional_edges(self, source_node_id: str, router: Router) -> Self
```

为当前图添加条件边，从源节点出发，根据条件路由器`router`决策连接的目标节点，并返回当前图实例。

**参数**：

* **source_node_id**(str)：源节点的`node_id`。
* **router**([Router](#type-alias-router))：条件边的路由器，用来决定条件边的目标节点或者目标节点列表，可以使用openJiuwen内置的[BranchRouter](../workflow/components/flow/branch_router.md)或者自定义实现[Router](#type-alias-router)。

**返回**：

**Graph**，接口返回Graph实例本身。

**异常**：

* **JiuWenBaseException**：openJiuwen异常基类，具体详细信息和解决方法，参见[StatusCode](../common/exception/status_code.md)。

**样例**：

```python
>>> import random
>>> 
>>> from openjiuwen.core.workflow import WorkflowComponent
>>> from openjiuwen.core.workflow import BranchRouter
>>> from openjiuwen.core.context_engine.base import ModelContext
>>> from openjiuwen.core.graph.base import Graph
>>> from openjiuwen.core.graph.executable import Input, Output
>>> from openjiuwen.core.workflow import Session
>>> from openjiuwen.core.workflow import Workflow
>>> 
>>> 
>>> # 直接使用内置的Router的实现BranchRouter 作为条件边的router
>>> class CustomIntentComponent(WorkflowComponent):
...     def __init__(self, default_intents):
...         super().__init__()
...         self._intents = default_intents
...         self._router = BranchRouter()
... 
...     def add_component(self, graph: Graph, node_id: str, wait_for_all: bool = False) -> None:
...         graph.add_node(node_id, self.to_executable(), wait_for_all=wait_for_all)
...         graph.add_conditional_edges(node_id, self._router)
... 
...     def add_branch(self, condition: str, target: list[str], branch_id:str):
...         self._router.add_branch(condition=condition, target=target, branch_id=branch_id)
... 
...     async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
...         self._router.set_session(session)
...         return {'result': self._intents[random.randint(0, len(self._intents) - 1)]}
... 
>>> # 创建组件时，将condition传入
>>> intent_component = CustomIntentComponent(["出行", "餐饮"])
>>> intent_component.add_branch(condition="${intent.result} == '出行'", target=['travel'], branch_id='1')
>>> intent_component.add_branch(condition="${intent.result} == '餐饮'", target=['eat'], branch_id='2')
>>> 
>>> flow = Workflow()
>>> flow.add_workflow_comp("intent", intent_component, inputs_schema={"query": "${start.query}"})
... 
```

## type alias Router

```python
openjiuwen.core.graph.base.Router: Union[
    Callable[..., Union[Hashable, list[Hashable]]],
    Callable[..., Awaitable[Union[Hashable, list[Hashable]]]],
]
```

`Router`为图或工作流的条件边的路由，用来决定条件边的目标节点或者目标节点列表。

**样例**：

```python
>>> from openjiuwen.core.graph.base import Router
>>> 
>>> def router_demo(data):
...     return ["a"]
... 
>>> router:Router = router_demo
```
