This section introduces **BaseTeam** in the openJiuwen multi-agent framework. `BaseTeam` is the abstract base class for multi-agent teams. By inheriting it and implementing `invoke()` / `stream()`, a group of collaborating Agents can be exposed as a single callable team service that integrates seamlessly with the Runner.

# Core Concepts

`BaseTeam` uses the **Card + Config** pattern:

- `TeamCard`: Immutable team identity (id, name, description, member list, etc.).
- `TeamConfig`: Mutable runtime parameters (max agents, message timeout, etc.) with chainable methods.
- Internally holds a `TeamRuntime` — all Agent management and messaging are delegated to it.

```
+-------------------------------------+
|              BaseTeam               |
|  card: TeamCard                     |
|  config: TeamConfig                 |
|  runtime: TeamRuntime  <------------+-- Agent registration / message routing
|                                     |
|  add_agent()   send()   publish()   |
|  invoke()  <- implemented by subclass|
|  stream()  <- implemented by subclass|
+-------------------------------------+
```

# Creating a Custom Team

Inherit `BaseTeam` and implement `invoke()` and `stream()`:

```python
from openjiuwen.core.multi_agent import BaseTeam, TeamCard, TeamConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard

class MyTeam(BaseTeam):
    def __init__(self, card: TeamCard, config=None):
        super().__init__(card=card, config=config)
        agent_card = AgentCard(id="worker", name="worker", description="Worker")
        self.add_agent(agent_card, lambda: WorkerAgent(card=agent_card))

    async def invoke(self, message, session=None):
        await self.runtime.start()
        try:
            return await self.runtime.send(
                message=message, recipient="worker", sender="team"
            )
        finally:
            await self.runtime.stop()

    async def stream(self, message, session=None):
        yield await self.invoke(message, session)
```

# TeamConfig

```python
from openjiuwen.core.multi_agent import TeamConfig

config = (TeamConfig()
    .configure_max_agents(20)
    .configure_timeout(60.0)
    .configure_concurrency(200))
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_agents` | `10` | Maximum Agents in the team |
| `message_timeout` | `30.0` | Message processing timeout (seconds) |
| `max_concurrent_messages` | `100` | Maximum concurrent messages |

# Integration with Runner

```python
from openjiuwen.core.runner import Runner

team = MyTeam(card=team_card, config=team_config)
await Runner.resource_mgr.add_agent_team(team.card, lambda: team)

result = await Runner.run_agent_team(
    agent_team=team.card.id,
    inputs={"task": "execute task"},
)
await Runner.resource_mgr.remove_agent_team(team_id=team.card.id)
```

# Complete Example

The following example (based on `examples/multi_agent/team_hybrid.py`) shows hybrid P2P + Pub-Sub encapsulated in `TaskExecutionTeam` and invoked through the Runner.

**Communication flow:**
```
Runner.run_agent_team -> TaskExecutionTeam.invoke
  -P2P-> orchestrator -Pub-Sub-> [executor1, executor2, executor3]
  executors -Pub-Sub-> aggregator
  -P2P-> reporter
```

```python
import asyncio
from typing import Any, AsyncIterator, Optional

from openjiuwen.core.common.logging import multi_agent_logger
from openjiuwen.core.multi_agent import BaseTeam, TeamCard, TeamConfig
from openjiuwen.core.multi_agent.team_runtime import CommunicableAgent
from openjiuwen.core.runner import Runner
from openjiuwen.core.session.agent_team import Session
from openjiuwen.core.session.session import Session as AgentSession
from openjiuwen.core.single_agent.base import BaseAgent
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


class OrchestratorAgent(CommunicableAgent, BaseAgent):
    def configure(self, config): return self
    async def invoke(self, inputs: Any, session: Optional[AgentSession] = None) -> Any:
        task = inputs.get("task", "") if isinstance(inputs, dict) else str(inputs)
        session_id = session.get_session_id() if session else None
        await self.publish(
            message={"event": "execution_request", "task": task},
            topic_id="execution_events", session_id=session_id,
        )
        return {"status": "broadcast_done", "task": task}
    async def stream(self, inputs: Any, session: Optional[AgentSession] = None) -> AsyncIterator[Any]:
        await self.invoke(inputs, session)
        if False: yield None


class ExecutorAgent(CommunicableAgent, BaseAgent):
    def __init__(self, card, executor_id: int):
        super().__init__(card=card)
        self.executor_id = executor_id
    def configure(self, config): return self
    async def invoke(self, inputs: Any, session: Optional[AgentSession] = None) -> Any:
        if not isinstance(inputs, dict) or inputs.get("event") != "execution_request":
            return {"status": "ignored"}
        task = inputs.get("task", "")
        result = f"executor-{self.executor_id} done: {task}"
        session_id = session.get_session_id() if session else None
        await self.publish(
            message={"event": "task_completed", "executor": self.executor_id, "result": result},
            topic_id="completion_events", session_id=session_id,
        )
        return {"status": "executed", "result": result}
    async def stream(self, inputs: Any, session: Optional[AgentSession] = None) -> AsyncIterator[Any]:
        await self.invoke(inputs, session)
        if False: yield None


class AggregatorAgent(CommunicableAgent, BaseAgent):
    def __init__(self, card, done_event: asyncio.Event, expected: int = 3):
        super().__init__(card=card)
        self._results: list = []
        self._lock = asyncio.Lock()
        self._done_event = done_event
        self._expected = expected
    def configure(self, config): return self
    def reset(self, done_event: asyncio.Event, expected: int) -> None:
        self._results.clear()
        self._done_event = done_event
        self._expected = expected
    def get_results(self) -> list:
        return list(self._results)
    async def invoke(self, inputs: Any, session: Optional[AgentSession] = None) -> Any:
        if not isinstance(inputs, dict) or inputs.get("event") != "task_completed":
            return {"status": "ignored"}
        async with self._lock:
            self._results.append(inputs.get("result", ""))
            if len(self._results) == self._expected:
                self._done_event.set()
        return {"status": "aggregated"}
    async def stream(self, inputs: Any, session: Optional[AgentSession] = None) -> AsyncIterator[Any]:
        await self.invoke(inputs, session)
        if False: yield None


class ReporterAgent(CommunicableAgent, BaseAgent):
    def configure(self, config): return self
    async def invoke(self, inputs: Any, session: Optional[AgentSession] = None) -> Any:
        results = inputs.get("results", []) if isinstance(inputs, dict) else []
        return {"status": "report_generated", "total": len(results), "results": results}
    async def stream(self, inputs: Any, session: Optional[AgentSession] = None) -> AsyncIterator[Any]:
        await self.invoke(inputs, session)
        if False: yield None


class TaskExecutionTeam(BaseTeam):
    """Encapsulates hybrid communication into a unified invoke() interface."""

    def __init__(self, card: TeamCard, config: Optional[TeamConfig] = None):
        super().__init__(card=card, config=config)
        self._subscriptions_ready = False

        self.orchestrator_card = AgentCard(id="orchestrator", name="orchestrator", description="orchestrator")
        self.executor1_card = AgentCard(id="executor1", name="executor1", description="executor1")
        self.executor2_card = AgentCard(id="executor2", name="executor2", description="executor2")
        self.executor3_card = AgentCard(id="executor3", name="executor3", description="executor3")
        self.aggregator_card = AgentCard(id="aggregator", name="aggregator", description="aggregator")
        self.reporter_card = AgentCard(id="reporter", name="reporter", description="reporter")

        self.aggregator = AggregatorAgent(
            card=self.aggregator_card, done_event=asyncio.Event(), expected=3
        )
        (self
         .add_agent(self.orchestrator_card, lambda: OrchestratorAgent(card=self.orchestrator_card))
         .add_agent(self.executor1_card, lambda: ExecutorAgent(card=self.executor1_card, executor_id=1))
         .add_agent(self.executor2_card, lambda: ExecutorAgent(card=self.executor2_card, executor_id=2))
         .add_agent(self.executor3_card, lambda: ExecutorAgent(card=self.executor3_card, executor_id=3))
         .add_agent(self.aggregator_card, lambda: self.aggregator)
         .add_agent(self.reporter_card, lambda: ReporterAgent(card=self.reporter_card)))

    async def _setup_subscriptions(self) -> None:
        if self._subscriptions_ready:
            return
        await self.subscribe("executor1", "execution_events")
        await self.subscribe("executor2", "execution_events")
        await self.subscribe("executor3", "execution_events")
        await self.subscribe("aggregator", "completion_events")
        self._subscriptions_ready = True

    async def invoke(self, message: Any, session: Optional[Session] = None) -> Any:
        if session is None:
            raise ValueError("Requires a team session. Use Runner.run_agent_team().")
        done_event = asyncio.Event()
        session_id = session.get_session_id() if session else None
        self.aggregator.reset(done_event=done_event, expected=3)
        await self.runtime.start()
        await self._setup_subscriptions()
        try:
            orch_result = await self.runtime.send(
                message=message, recipient="orchestrator",
                sender="main_process", session_id=session_id,
            )
            try:
                await asyncio.wait_for(done_event.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                multi_agent_logger.warning("[Team] aggregator timeout")
            report = await self.runtime.send(
                message={"results": self.aggregator.get_results()},
                recipient="reporter", sender="main_process", session_id=session_id,
            )
            return {"orchestration": orch_result, "report": report}
        finally:
            await self.runtime.stop()

    async def stream(self, message: Any, session: Optional[Session] = None) -> AsyncIterator[Any]:
        if session is None:
            raise ValueError("Requires a team session. Use Runner.run_agent_team_streaming().")
        async def run_workflow() -> None:
            try:
                await self.invoke(message, session)
            finally:
                await session.close_stream()
        task = asyncio.create_task(run_workflow())
        try:
            async for chunk in session.stream_iterator():
                yield chunk
        finally:
            await task


async def main():
    team_card = TeamCard(
        id="task_execution_team", name="task_execution_team",
        description="Task execution team"
    )
    team = TaskExecutionTeam(card=team_card, config=TeamConfig(max_agents=10))
    await Runner.resource_mgr.add_agent_team(team.card, lambda: team)
    try:
        result = await Runner.run_agent_team(
            agent_team=team.card.id,
            inputs={"task": "develop new feature module"},
        )
        multi_agent_logger.info(f"[main] result: {result}")
    finally:
        await Runner.resource_mgr.remove_agent_team(team_id=team.card.id)
        await team.runtime.stop()


if __name__ == "__main__":
    asyncio.run(main())
```

Sample output:

```text
[main] result: {
  'orchestration': {'status': 'broadcast_done', 'task': 'develop new feature module'},
  'report': {'status': 'report_generated', 'total': 3, 'results': [...]}
}
```

# TeamCard Reference

| Field | Type | Description |
|-------|------|-------------|
| `id` | `str` | Unique team identifier |
| `name` | `str` | Team name |
| `description` | `str` | Team description |
| `agent_cards` | `List[AgentCard]` | Member Agent list (auto-maintained by `add_agent`) |
| `topic` | `str` | Team topic / domain |
| `version` | `str` | Version string |
| `tags` | `List[str]` | Classification tags |
