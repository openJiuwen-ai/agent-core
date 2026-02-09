# openjiuwen.core.graph

## class Graph

```python
class openjiuwen.core.graph.base.Graph()
```

The `Graph` class is the base class for workflow execution graphs. It abstracts the process of building a graph, supports adding start, intermediate, and end nodes, and allows creating regular edges or conditional edges between them. When you implement custom nodes by inheriting `WorkflowComponent` and provide a custom `add_component` implementation, you can call `Graph` methods to customize how nodes are added to the `Graph`.

### start_node

```python
start_node(self, node_id: str) -> Self
```

Specify the start node of the graph and return the current graph instance.

**Parameters**:

* **node_id** (str): The ID of the start node. If a start node has already been set, it will be overwritten by the current `node_id`.

**Returns**:

**Graph**, the interface returns the Graph instance itself.

**Exceptions**:

* **JiuWenBaseException**: The base exception class for openJiuwen. For detailed information and solutions, see [StatusCode](../common/exception/status_code.md).

**Example**:

Define a custom node `DemoCmpWithRandomStart` and implement `add_component` to randomly choose and set a start node from a predefined list of start node IDs.

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

Specify the end node of the graph and return the current graph instance.

**Parameters**:

* **node_id** (str): The ID of the end node.

**Returns**:

**Graph**, the interface returns the Graph instance itself.

**Exceptions**:

* **JiuWenBaseException**: The base exception class for openJiuwen. For detailed information and solutions, see [StatusCode](../common/exception/status_code.md).

**Example**:

Define a custom node `DemoCmpWithRandomEnd` and implement `add_component` to randomly choose and set an end node from a predefined list of end node IDs.

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

Add an executable node to the graph and return the current graph instance.

**Parameters**:

* **node_id** (str): The node `id`.
* **node** (Executable): The node instance.
* **wait_for_all** (bool, optional): Whether concurrent execution is supported. `True` means concurrent execution is supported, allowing the node to be executed multiple times. `False` means concurrent execution is not supported, i.e., the node must wait for all predecessor nodes to finish before executing. Default value: `False`.

**Returns**:

**Graph**, the interface returns the Graph instance itself.

**Exceptions**:

* **JiuWenBaseException**: The base exception class for openJiuwen. For detailed information and solutions, see [StatusCode](../common/exception/status_code.md).

**Example**:

Define a custom node `DemoCmpWithNode` and implement `add_component` to add `inner_node` as a downstream node of this node.

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

Add an edge from the specified source node(s) to the target node for the current graph, supporting adding multiple edges from multiple source nodes pointing to the same target node, and return the current graph instance.

**Parameters**:

* **source_node_id** (Union[str, list[str]]): The `node_id` of the source node(s), can be a single string or a list of strings. If it is a list of strings, it means multiple source nodes pointing to the target node respectively.
* **target_node_id** (str): The `node_id` of the target node.

**Returns**:

**Graph**, the interface returns the Graph instance itself.

**Exceptions**:

* **JiuWenBaseException**: The base exception class for openJiuwen. For detailed information and solutions, see [StatusCode](../common/exception/status_code.md).

**Example**:

Define a custom node `DemoCmpWithNode` and implement `add_component` to add `inner_node` as a downstream node of this node and connect an edge.

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

Add conditional edges to the current graph, starting from the source node, and decide the target node to connect based on the conditional router `router`, returning the current graph instance.

**Parameters**:

* **source_node_id** (str): The `node_id` of the source node.
* **router** ([Router](#type-alias-router)): The router for conditional edges, used to determine the target node or list of target nodes for conditional edges. You can use openJiuwen's built-in [BranchRouter](../workflow/components/flow/branch_router.md) or implement a custom [Router](#type-alias-router).

**Returns**:

**Graph**, the interface returns the Graph instance itself.

**Exceptions**:

* **JiuWenBaseException**: The base exception class for openJiuwen. For detailed information and solutions, see [StatusCode](../common/exception/status_code.md).

**Example**:

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
>>> # Directly use the built-in Router implementation BranchRouter as the router for conditional edges
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
>>> # When creating the component, pass the condition
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

`Router` is the router for conditional edges of graphs or workflows, used to determine the target node or list of target nodes for conditional edges.

**Example**:

```python
>>> from openjiuwen.core.graph.base import Router
>>> 
>>> def router_demo(data):
...     return ["a"]
... 
>>> router:Router = router_demo
```
