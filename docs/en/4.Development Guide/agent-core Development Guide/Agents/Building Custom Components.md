This chapter demonstrates how to build a workflow sample using custom components based on openJiuwen. This sample supports selecting different custom component branches based on user input via a custom branch component to generate the final result. Through this example, you will learn the following information:

* How to create custom components.
* How to create a workflow based on custom components.

# Creating Workflow Process

The overall process of creating a Workflow is as follows: First, initialize the workflow via `Workflow`, then define each component and register them to the workflow, and set the connections between components, thereby completing the creation of the entire workflow. The Workflow designed in this example is as follows: User Input → Intent Recognition → Travel/Dining → Return Result. Example code is as follows:

## Initializing Workflow

Initialize workflow:

```python
from openjiuwen.core.workflow import Workflow
# Create workflow
flow = Workflow()
```

## Registering Components to Workflow

Create core component instances according to task requirements, configure input/output rules for each component, and register components to the workflow to achieve process automation and logical control. In addition to using openJiuwen's built-in Start and End components, this tutorial also customizes branch components and processing components.

### Start Component

Create a Start component object via `Start` and set the start component for the workflow:

```python
from openjiuwen.core.workflow import Start

flow.set_start_comp("start", Start(), inputs_schema={"query":"${user_inputs.query}"})
```

### Custom Branch Component

Create a custom branch component `CustomIntentComponent`, inheriting both `WorkflowComponent` and `ComponentExecutable`. Implement the `add_component` interface to customize built-in conditional edges for this component, and implement the `add_branch` interface to bind target nodes for the built-in conditional edges:

```python
import random

from openjiuwen.core.context_engine import ModelContext
from openjiuwen.core.graph.base import Graph
from openjiuwen.core.workflow import (
    BranchRouter,
    ComponentExecutable,
    Input,
    Output,
    Session,
    WorkflowComponent
)


class CustomIntentComponent(WorkflowComponent, ComponentExecutable):
    def __init__(self, default_intents):
        super().__init__()
        self._intents = default_intents
        self._router = BranchRouter()

    def add_component(self, graph: Graph, node_id: str, wait_for_all: bool = False) -> None:
        graph.add_node(node_id, self.to_executable(), wait_for_all=wait_for_all)
        graph.add_conditional_edges(node_id, self._router)

    def add_branch(self, condition: str, target: list[str], branch_id:str):
        self._router.add_branch(condition=condition, target=target, branch_id=branch_id)

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        self._router.set_session(session)
        return {'result': self._intents[random.randint(0, len(self._intents) - 1)]}
```

Instantiate the custom branch component, with branches containing `Travel` and `Dining`:

```python
intent_component = CustomIntentComponent(["出行", "餐饮"])
intent_component.add_branch(condition="${intent.result} == '出行'", target=['travel'], branch_id='1')
intent_component.add_branch(condition="${intent.result} == '餐饮'", target=['eat'], branch_id='2')
```

Register the custom branch component to the workflow:

```python
flow.add_workflow_comp("intent", intent_component, inputs_schema={"query": "${start.query}"})
```

### Custom Component

Create custom component `TravelComponent` for the `Travel` branch:

```python
from openjiuwen.core.context_engine import ModelContext
from openjiuwen.core.workflow import (
    ComponentExecutable,
    Input,
    Output,
    Session,
    WorkflowComponent
)


class TravelComponent(WorkflowComponent, ComponentExecutable):
    def __init__(self, node_id):
        super().__init__()
        self.node_id = node_id

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        print(f'[{self.node_id}] inputs = {inputs}')
        return inputs
```

Create custom component `EatComponent` for the `Dining` branch:

```python
from openjiuwen.core.context_engine import ModelContext
from openjiuwen.core.workflow import (
    ComponentExecutable,
    Input,
    Output,
    Session,
    WorkflowComponent
)


class EatComponent(WorkflowComponent, ComponentExecutable):
    def __init__(self, node_id):
        super().__init__()
        self.node_id = node_id

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        print(f'[{self.node_id}] inputs = {inputs}')
        return inputs
```

Register custom components `TravelComponent` and `EatComponent` to the workflow.

```python
flow.add_workflow_comp("eat", EatComponent("eat"), inputs_schema={"intent": '${intent.result}'})
flow.add_workflow_comp("travel", TravelComponent("travel"), inputs_schema={"intent": '${intent.result}'})
```

### End Component

Register the `End` component to the workflow:

```python
from openjiuwen.core.workflow import End

flow.set_end_comp("end", End(), inputs_schema={"eat": "${eat.intent}", "travel": "${travel.intent}"})
```

## Connecting Components

By calling the `flow.add_connection` method, clear connection relationships can be established for each component in the workflow, defining their execution order and logical flow:

```python
flow.add_connection("start", "intent")
flow.add_connection("eat", "end")
flow.add_connection("travel", "end")
```

## Running Workflow

Based on the workflow built above, trigger the `invoke` function to trigger workflow execution:

```python
import asyncio
from openjiuwen.core.workflow import create_workflow_session

session = create_workflow_session()
output = asyncio.run(flow.invoke({"user_inputs": {"query": "去新疆"}}, session))
print(output.result)
```

Select `Travel` branch:

```python
[travel] inputs = {'intent': '出行'}
```

Get workflow output result:

```python
{'output': {'travel': '出行'}}
```

# Complete Example Code

The complete example reference is shown below:

```python
import asyncio
import random

from openjiuwen.core.context_engine import ModelContext
from openjiuwen.core.graph.base import Graph
from openjiuwen.core.workflow import (
    BranchRouter,
    ComponentExecutable,
    create_workflow_session,
    End,
    Input,
    Output,
    Session,
    Start,
    Workflow,
    WorkflowComponent
)


class CustomIntentComponent(WorkflowComponent, ComponentExecutable):
    def __init__(self, default_intents):
        super().__init__()
        self._intents = default_intents
        self._router = BranchRouter()

    def add_component(self, graph: Graph, node_id: str, wait_for_all: bool = False) -> None:
        graph.add_node(node_id, self.to_executable(), wait_for_all=wait_for_all)
        graph.add_conditional_edges(node_id, self._router)

    def add_branch(self, condition: str, target: list[str], branch_id: str):
        self._router.add_branch(condition=condition, target=target, branch_id=branch_id)

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        self._router.set_session(session)
        return {'result': self._intents[random.randint(0, len(self._intents) - 1)]}


class TravelComponent(WorkflowComponent, ComponentExecutable):
    def __init__(self, node_id):
        super().__init__()
        self.node_id = node_id

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        print(f'[{self.node_id}] inputs = {inputs}')
        return inputs


class EatComponent(WorkflowComponent, ComponentExecutable):
    def __init__(self, node_id):
        super().__init__()
        self.node_id = node_id

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        print(f'[{self.node_id}] inputs = {inputs}')
        return inputs


# Create workflow
flow = Workflow()
flow.set_start_comp("start", Start(), inputs_schema={"query": "${user_inputs.query}"})
intent_component = CustomIntentComponent(["出行", "餐饮"])
intent_component.add_branch(condition="${intent.result} == '出行'", target=['travel'], branch_id='1')
intent_component.add_branch(condition="${intent.result} == '餐饮'", target=['eat'], branch_id='2')

flow.add_workflow_comp("intent", intent_component, inputs_schema={"query": "${start.query}"})
flow.add_workflow_comp("eat", EatComponent("eat"), inputs_schema={"intent": '${intent.result}'})
flow.add_workflow_comp("travel", TravelComponent("travel"), inputs_schema={"intent": '${intent.result}'})

flow.set_end_comp("end", End(), inputs_schema={"eat": "${eat.intent}", "travel": "${travel.intent}"})

flow.add_connection("start", "intent")
flow.add_connection("eat", "end")
flow.add_connection("travel", "end")


if __name__ == '__main__':
    session = create_workflow_session()
    output = asyncio.run(flow.invoke({"user_inputs": {"query": "去新疆"}}, session))
    print(output.result)
```

Output result
```python
{'output': {'travel': '出行'}}
```