本章节介绍 openJiuwen 多智能体框架中的 **TeamRuntime** 和 **CommunicableAgent**，它们共同构成了多 Agent 通信的核心基础设施。`TeamRuntime` 负责管理 Agent 注册与消息路由，`CommunicableAgent` 为 Agent 提供通信能力。两者结合，可以快速搭建支持点对点（P2P）、发布-订阅（Pub-Sub）及混合通信模式的多智能体系统。

# TeamRuntime

`TeamRuntime` 是一个自包含的运行时，可单独使用，也可作为 `BaseTeam` 的通信底座。它负责：

- 以 **Card + Provider** 模式注册 Agent（延迟实例化，首次消息时才创建）
- 通过内置 `MessageBus` 路由 P2P 和 Pub-Sub 消息
- 在 Agent 首次创建时自动为 `CommunicableAgent` 绑定运行时
- 提供生命周期管理（`start` / `stop` / 异步上下文管理器）

## 使用方式

### 注册 Agent

```python
from openjiuwen.core.multi_agent.team_runtime import TeamRuntime
from openjiuwen.core.single_agent.schema.agent_card import AgentCard

runtime = TeamRuntime()

planner_card = AgentCard(id="planner", name="planner", description="任务规划")
runtime.register_agent(planner_card, lambda: PlannerAgent(card=planner_card))
```

`register_agent` 接受一个 `AgentCard`（身份元数据）和一个 Provider（工厂函数），实例在首次被路由时才会创建。

### 启动与停止

```python
# 显式启动/停止
await runtime.start()
# ... 执行通信 ...
await runtime.stop()

# 或使用异步上下文管理器（推荐）
async with TeamRuntime() as runtime:
    runtime.register_agent(card, provider)
    result = await runtime.send(...)
```

### P2P 通信

`send()` 向指定 Agent 发送消息并等待其 `invoke()` 返回结果：

```python
result = await runtime.send(
    message={"task": "分析需求"},
    recipient="planner",
    sender="main",
    timeout=30.0,   # 可选，超时秒数
)
```

### Pub-Sub 通信

`subscribe()` 为 Agent 注册主题订阅，`publish()` 向主题广播消息（Fire-and-Forget）：

```python
# 订阅主题（支持通配符）
await runtime.subscribe("worker1", "task_events")
await runtime.subscribe("worker2", "task_*")   # 通配符订阅

# 发布消息
await runtime.publish(
    message={"event": "new_task", "task": "处理数据"},
    topic_id="task_events",
    sender="coordinator",
)
```

# CommunicableAgent

`CommunicableAgent` 是一个 Mixin 类，赋予 Agent 在自身 `invoke()` 逻辑内直接通信的能力。使用时只需在类定义中同时继承 `CommunicableAgent` 和 `BaseAgent`：

```python
from openjiuwen.core.multi_agent.team_runtime import CommunicableAgent
from openjiuwen.core.single_agent.base import BaseAgent

class MyAgent(CommunicableAgent, BaseAgent):
    ...
```

当 Agent 通过 `TeamRuntime.register_agent()` 注册后，运行时会在首次实例化时自动调用 `bind_runtime()`，Agent 即可直接使用以下方法：

| 方法 | 说明 |
|------|------|
| `await self.send(message, recipient, session_id, timeout)` | P2P 发送并等待响应 |
| `await self.publish(message, topic_id, session_id)` | 发布到主题 |
| `await self.subscribe(topic)` | 订阅主题 |
| `await self.unsubscribe(topic)` | 取消订阅 |
| `self.agent_id` | 获取自身 Agent ID |
| `self.is_bound` | 检查是否已绑定运行时 |

> **注意**：未继承 `CommunicableAgent` 的 Agent 仍可注册到 `TeamRuntime`，但无法在内部使用 `send()` / `publish()` 等方法，运行时会发出警告日志。

# 混合通信完整示例

以下示例展示了 P2P 与 Pub-Sub 组合使用的完整流程（来自 `examples/multi_agent/runtime_hybrid.py`）：

**通信流程：**
```
主流程 -P2P-> orchestrator -Pub-Sub-> [executor1, executor2, executor3]
executors -Pub-Sub-> aggregator
主流程 -P2P-> reporter
```

```python
import asyncio
from typing import Any, AsyncIterator, Optional

from openjiuwen.core.common.logging import multi_agent_logger
from openjiuwen.core.multi_agent.team_runtime import TeamRuntime, CommunicableAgent
from openjiuwen.core.single_agent.base import BaseAgent
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.session.session import Session


class OrchestratorAgent(CommunicableAgent, BaseAgent):
    """编排 Agent：收到任务后用 Pub-Sub 广播给所有 Executor。"""

    def configure(self, config): return self

    async def invoke(self, inputs: Any, session: Optional[Session] = None) -> Any:
        task = inputs.get("task", "") if isinstance(inputs, dict) else str(inputs)
        multi_agent_logger.info(f"[Orchestrator] task: {task}")
        # 通过 Pub-Sub 广播给所有订阅了 execution_events 的 Executor
        await self.publish(
            message={"event": "execution_request", "task": task},
            topic_id="execution_events",
        )
        return {"status": "broadcast_done", "task": task}

    async def stream(self, inputs: Any, session: Optional[Session] = None) -> AsyncIterator[Any]:
        yield await self.invoke(inputs, session)


class ExecutorAgent(CommunicableAgent, BaseAgent):
    """执行 Agent：订阅 execution_events，完成后 Pub-Sub 发布结果。"""

    def __init__(self, card, executor_id: int):
        super().__init__(card=card)
        self.executor_id = executor_id

    def configure(self, config): return self

    async def invoke(self, inputs: Any, session: Optional[Session] = None) -> Any:
        if not isinstance(inputs, dict) or inputs.get("event") != "execution_request":
            return {"status": "ignored"}
        task = inputs.get("task", "")
        result = f"executor-{self.executor_id} done: {task}"
        multi_agent_logger.info(f"[Executor-{self.executor_id}] {result}")
        # 完成后向 aggregator 发布结果
        await self.publish(
            message={"event": "task_completed", "executor": self.executor_id, "result": result},
            topic_id="completion_events",
        )
        return {"status": "executed"}

    async def stream(self, inputs: Any, session: Optional[Session] = None) -> AsyncIterator[Any]:
        yield await self.invoke(inputs, session)


class AggregatorAgent(CommunicableAgent, BaseAgent):
    """聚合 Agent：收集所有 Executor 结果，完成后 set done_event。"""

    def __init__(self, card, done_event: asyncio.Event, expected: int = 3):
        super().__init__(card=card)
        self._results: list = []
        self._lock = asyncio.Lock()
        self._done_event = done_event
        self._expected = expected

    def configure(self, config): return self

    def get_results(self) -> list:
        return list(self._results)

    async def invoke(self, inputs: Any, session: Optional[Session] = None) -> Any:
        if not isinstance(inputs, dict) or inputs.get("event") != "task_completed":
            return {"status": "ignored"}
        async with self._lock:
            self._results.append(inputs.get("result", ""))
            count = len(self._results)
            multi_agent_logger.info(f"[Aggregator] ({count}/{self._expected}): {inputs.get('result')}")
            if count == self._expected:
                self._done_event.set()
        return {"status": "aggregated"}

    async def stream(self, inputs: Any, session: Optional[Session] = None) -> AsyncIterator[Any]:
        yield await self.invoke(inputs, session)


class ReporterAgent(CommunicableAgent, BaseAgent):
    """报告 Agent：由主流程 P2P 调用，生成最终报告。"""

    def configure(self, config): return self

    async def invoke(self, inputs: Any, session: Optional[Session] = None) -> Any:
        results = inputs.get("results", []) if isinstance(inputs, dict) else []
        multi_agent_logger.info(f"[Reporter] final report ({len(results)} items)")
        for i, r in enumerate(results, 1):
            multi_agent_logger.info(f"  {i}. {r}")
        return {"status": "report_generated", "total": len(results)}

    async def stream(self, inputs: Any, session: Optional[Session] = None) -> AsyncIterator[Any]:
        yield await self.invoke(inputs, session)


async def main():
    done_event = asyncio.Event()

    orchestrator_card = AgentCard(id="orchestrator", name="orchestrator", description="orchestrator")
    executor1_card = AgentCard(id="executor1", name="executor1", description="executor1")
    executor2_card = AgentCard(id="executor2", name="executor2", description="executor2")
    executor3_card = AgentCard(id="executor3", name="executor3", description="executor3")
    aggregator_card = AgentCard(id="aggregator", name="aggregator", description="aggregator")
    reporter_card = AgentCard(id="reporter", name="reporter", description="reporter")

    agg = AggregatorAgent(card=aggregator_card, done_event=done_event, expected=3)

    runtime = TeamRuntime()
    runtime.register_agent(orchestrator_card, lambda: OrchestratorAgent(card=orchestrator_card))
    runtime.register_agent(executor1_card, lambda: ExecutorAgent(card=executor1_card, executor_id=1))
    runtime.register_agent(executor2_card, lambda: ExecutorAgent(card=executor2_card, executor_id=2))
    runtime.register_agent(executor3_card, lambda: ExecutorAgent(card=executor3_card, executor_id=3))
    runtime.register_agent(aggregator_card, lambda: agg)
    runtime.register_agent(reporter_card, lambda: ReporterAgent(card=reporter_card))

    # 设置 Pub-Sub 订阅
    await runtime.subscribe("executor1", "execution_events")
    await runtime.subscribe("executor2", "execution_events")
    await runtime.subscribe("executor3", "execution_events")
    await runtime.subscribe("aggregator", "completion_events")

    await runtime.start()

    try:
        # Step 1: P2P -> orchestrator（内部广播给 executors）
        orch_result = await runtime.send(
            message={"task": "build new feature"},
            recipient="orchestrator",
            sender="main",
        )
        multi_agent_logger.info(f"[main] orchestrator result: {orch_result}")

        # Step 2: 等待 aggregator 收集完 3 条结果
        await asyncio.wait_for(done_event.wait(), timeout=5.0)

        # Step 3: P2P -> reporter 生成最终报告
        report = await runtime.send(
            message={"results": agg.get_results()},
            recipient="reporter",
            sender="main",
        )
        multi_agent_logger.info(f"[main] report: {report}")
    finally:
        await runtime.stop()


if __name__ == "__main__":
    asyncio.run(main())
```

运行后输出示例：

```text
[Orchestrator] task: build new feature
[Executor-1] executor-1 done: build new feature
[Executor-2] executor-2 done: build new feature
[Executor-3] executor-3 done: build new feature
[Aggregator] (1/3): executor-1 done: build new feature
[Aggregator] (2/3): executor-2 done: build new feature
[Aggregator] (3/3): executor-3 done: build new feature
[Reporter] final report (3 items)
  1. executor-1 done: build new feature
  2. executor-2 done: build new feature
  3. executor-3 done: build new feature
[main] report: {'status': 'report_generated', 'total': 3}
```

