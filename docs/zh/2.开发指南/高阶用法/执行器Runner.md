# 执行器Runner

Runner是openJiuwen执行所有核心组件（包括Workflow，Agent）的统一入口和控制中心。它将复杂的执行逻辑抽象化，为开发者提供了一个简洁、一致且强大的编程接口。

**重要说明**：Runner是一个单例类，所有方法调用和属性访问都会自动代理到全局的Runner实例。无需实例化Runner，直接通过类名调用即可，例如：`Runner.start()`、`Runner.resource_mgr`。

Runner的主要功能包括：

- 提供Agent标准的异步调用（invoke）和异步流式调用（stream）两种执行入口。
- 提供Workflow标准的异步调用（invoke）和异步流式调用（stream）两种执行入口。

## Agent执行

Runner支持所有Agent的单次输出执行和流式输出执行，包括ReActAgent、WorkflowAgent等内置Agent的执行，也包括用户自定义的Agent的执行。

下面以一个`WorkflowAgent`为例，介绍通过`Runner`执行`Agent`的过程。

首先，创建一个WorkflowAgent实例：

```python
from openjiuwen.core.common.constants.enums import ControllerType
from openjiuwen.core.single_agent.legacy import WorkflowSchema
from openjiuwen.core.application.workflow_agent import WorkflowAgentConfig, WorkflowAgent
from openjiuwen.core.workflow import End, Start, Workflow, WorkflowCard, generate_workflow_key
from openjiuwen.core.workflow.workflow_config import WorkflowConfig
from openjiuwen.core.runner.runner import Runner
from openjiuwen.core.common import BaseCard


def create_agent(runner):
    # 创建工作流 flow
    card = WorkflowCard(id="workflow_id", name="简单工作流", version="1",
                        description="this_is_a_demo")
    flow = Workflow(workflow_config=WorkflowConfig(card=card))
    flow.set_start_comp("start", Start(), inputs_schema={"query": "${query}"})
    flow.set_end_comp("end", End(), inputs_schema={"result": "${start.query}"})
    flow.add_connection("start", "end")

    # 将 flow 注册到资源管理器（使用 generate_workflow_key 生成正确的 key）
    # 注意：注册时需要使用 id_version 格式的 key
    register_card = WorkflowCard(
        id=generate_workflow_key(card.id, card.version),
        name=card.name,
        version=card.version,
        description=card.description
    )
    runner.resource_mgr.add_workflow(register_card, lambda: flow)

    # 创建Agent
    workflow_agent_config = WorkflowAgentConfig(id="agent_id", version="1", description="this_is_a_demo",
                                                workflows=[WorkflowSchema(
                                                    id="workflow_id",
                                                    version="1",
                                                    name="简单工作流",
                                                    description="this_is_a_demo",
                                                    inputs={"query": {"type": "string"}},
                                                )],
                                                controller_type=ControllerType.WorkflowController
                                                )
    agent = WorkflowAgent(agent_config=workflow_agent_config)
    return agent


# Runner 是单例类，直接使用类名即可
agent = create_agent(Runner)
```

然后，调用 `run_agent` 接口直接运行 WorkflowAgent：

```python
import asyncio

print(asyncio.run(Runner.run_agent(agent=agent, inputs={"conversation_id": "id1", "query": "哈哈"})))
```

执行结果：

```python
{'output': WorkflowOutput(result={'output': {'result': '哈哈'}},
                          state= < WorkflowExecutionState.COMPLETED: 'COMPLETED' >), 'result_type': 'answer'}
```

## Workflow执行

Runner支持Workflow的单次输出执行和流式输出执行。

下面通过构建一个简单工作流为例，介绍`Runner`执行`Workflow`的过程。

首先，创建一个Workflow:

```python
from openjiuwen.core.workflow import End, Start, Workflow, WorkflowCard
from openjiuwen.core.workflow.workflow_config import WorkflowConfig


def build_workflow(name, workflow_id, version):
    flow = Workflow(workflow_config=WorkflowConfig(
        card=WorkflowCard(id=workflow_id, name=name, version=version,
                          description="this_is_a_demo")))
    flow.set_start_comp("start", Start(), inputs_schema={"query": "${query}"})
    flow.set_end_comp("end", End(), inputs_schema={"result": "${start.query}"})
    flow.add_connection("start", "end")
    return flow


workflow = build_workflow("test_workflow", "test_workflow", "1")
```

然后，调用 `Runner` 的 `run_workflow` 直接运行 `Workflow`（无需再显式构造会话，内部会自动创建 `WorkflowSession`）：

```python
import asyncio
from openjiuwen.core.runner import Runner

result = asyncio.run(Runner.run_workflow(workflow=workflow, inputs={"query": "query workflow"}))
print(result)
```

执行结果：

```python
result = {'output': {'result': 'query workflow'}}
state = < WorkflowExecutionState.COMPLETED: 'COMPLETED' >
```
