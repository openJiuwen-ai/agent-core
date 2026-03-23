This section introduces **TeamRuntime** and **CommunicableAgent** in the openJiuwen multi-agent framework. Together they form the core communication infrastructure for multi-agent systems. `TeamRuntime` manages Agent registration and message routing, while `CommunicableAgent` equips Agents with communication capabilities. Combined, they enable rapid construction of multi-agent systems supporting point-to-point (P2P), publish-subscribe (Pub-Sub), and hybrid communication patterns.

# TeamRuntime

`TeamRuntime` is a self-contained runtime that can be used standalone or as the communication backbone of `BaseTeam`. It is responsible for:

- Registering Agents using the **Card + Provider** pattern (lazy instantiation — instances are created only when the first message arrives)
- Routing P2P and Pub-Sub messages through the built-in `MessageBus`
- Automatically binding the runtime to `CommunicableAgent` instances on first creation
- Lifecycle management (`start` / `stop` / async context manager)

## Usage

### Registering Agents

```python
from openjiuwen.core.multi_agent.team_runtime import TeamRuntime
from openjiuwen.core.single_agent.schema.agent_card import AgentCard

runtime = TeamRuntime()

planner_card = AgentCard(id="planner", name="planner", description="Task Planner")
runtime.register_agent(planner_card, lambda: PlannerAgent(card=planner_card))
```

`register_agent` accepts an `AgentCard` (identity metadata) and a Provider (factory callable). The instance is created only when it is first routed a message.

### Starting and Stopping

```python
# Explicit start/stop
await runtime.start()
# ... perform communication ...
await runtime.stop()

# Or use async context manager (recommended)
async with TeamRuntime() as runtime:
    runtime.register_agent(card, provider)
    result = await runtime.send(...)
```

### P2P Communication

`send()` delivers a message to a specific Agent and waits for its `invoke()` return value:

```python
result = await runtime.send(
    message={"task": "analyze requirements"},
    recipient="planner",
    sender="main",
    timeout=30.0,   # optional, seconds
)
```

### Pub-Sub Communication

`subscribe()` registers a topic subscription for an Agent; `publish()` broadcasts to a topic (fire-and-forget):

```python
# Subscribe to topics (wildcards supported)
await runtime.subscribe("worker1", "task_events")
await runtime.subscribe("worker2", "task_*")   # wildcard subscription

# Publish a message
await runtime.publish(
    message={"event": "new_task", "task": "process data"},
    topic_id="task_events",
    sender="coordinator",
)
```

# CommunicableAgent

`CommunicableAgent` is a mixin class that grants an Agent the ability to communicate directly inside its own `invoke()` logic. Simply inherit both `CommunicableAgent` and `BaseAgent`:

```python
from openjiuwen.core.multi_agent.team_runtime import CommunicableAgent
from openjiuwen.core.single_agent.base import BaseAgent

class MyAgent(CommunicableAgent, BaseAgent):
    ...
```

Once the Agent is registered via `TeamRuntime.register_agent()`, the runtime automatically calls `bind_runtime()` on the first instantiation, after which the Agent can use:

| Method | Description |
|--------|-------------|
| `await self.send(message, recipient, session_id, timeout)` | P2P send and wait for response |
| `await self.publish(message, topic_id, session_id)` | Publish to a topic |
| `await self.subscribe(topic)` | Subscribe to a topic |
| `await self.unsubscribe(topic)` | Unsubscribe from a topic |
| `self.agent_id` | Get this Agent's ID |
| `self.is_bound` | Check whether the runtime is bound |

> **Note**: Agents that do not inherit `CommunicableAgent` can still be registered with `TeamRuntime`, but `send()` / `publish()` and related methods will not be available inside them. The runtime will emit a warning log.

# Hybrid Communication Example

The following example demonstrates the complete hybrid P2P + Pub-Sub flow (from `examples/multi_agent/runtime_hybrid.py`):

**Communication flow:**
```
main -P2P-> orchestrator -Pub-Sub-> [executor1, executor2, executor3]
executors -Pub-Sub-> aggregator
main -P2P-> reporter
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
    """Broadcasts the task to all Executors via Pub-Sub."""

    def configure(self, config): return self

    async def invoke(self, inputs: Any, session: Optional[Session] = None) -> Any:
        task = inputs.get("task", "") if isinstance(inputs, dict) else str(inputs)
        multi_agent_logger.info(f"[Orchestrator] task: {task}")
        await self.publish(
            message={"event": "execution_request", "task": task},
            topic_id="execution_events",
        )
        return {"status": "broadcast_done", "task": task}

    async def stream(self, inputs: Any, session: Optional[Session] = None) -> AsyncIterator[Any]:
        yield await self.invoke(inputs, session)


class ExecutorAgent(CommunicableAgent, BaseAgent):
    """Subscribes to execution_events and publishes results via Pub-Sub."""

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
        await self.publish(
            message={"event": "task_completed", "executor": self.executor_id, "result": result},
            topic_id="completion_events",
        )
        return {"status": "executed"}

    async def stream(self, inputs: Any, session: Optional[Session] = None) -> AsyncIterator[Any]:
        yield await self.invoke(inputs, session)


class AggregatorAgent(CommunicableAgent, BaseAgent):
    """Collects all Executor results and sets done_event when complete."""

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
    """Called via P2P by the main flow to generate the final report."""

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

    # Set up Pub-Sub subscriptions
    await runtime.subscribe("executor1", "execution_events")
    await runtime.subscribe("executor2", "execution_events")
    await runtime.subscribe("executor3", "execution_events")
    await runtime.subscribe("aggregator", "completion_events")

    await runtime.start()

    try:
        # Step 1: P2P -> orchestrator (broadcasts to executors internally)
        orch_result = await runtime.send(
            message={"task": "build new feature"},
            recipient="orchestrator",
            sender="main",
        )
        multi_agent_logger.info(f"[main] orchestrator result: {orch_result}")

        # Step 2: Wait for aggregator to collect all 3 results
        await asyncio.wait_for(done_event.wait(), timeout=5.0)

        # Step 3: P2P -> reporter to generate final report
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

Sample output:

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

