本章节介绍 openJiuwen 多智能体框架中的 **BaseTeam**。`BaseTeam` 是多智能体团队的抽象基类，提供统一的团队封装方案：通过继承它并实现 `invoke()` / `stream()` 方法，可以将一组协作 Agent 对外暴露为单一的、可调用的团队服务，与 Runner 无缝集成。

# 核心概念

`BaseTeam` 采用 **Card + Config** 模式：

- `TeamCard`：团队身份定义（id、name、description、成员列表等），不可变。
- `TeamConfig`：运行时参数配置（最大 Agent 数、消息超时等），可变且支持链式调用。
- 内部持有一个 `TeamRuntime` 实例，所有 Agent 管理和消息通信均委托给它。

```
┌─────────────────────────────────────┐
│              BaseTeam               │
│  card: TeamCard                     │
│  config: TeamConfig                 │
│  runtime: TeamRuntime  ◄────────────┼── Agent 注册 / 消息路由
│                                     │
│  add_agent()   send()   publish()   │
│  invoke()  ← 子类实现               │
│  stream()  ← 子类实现               │
└─────────────────────────────────────┘
```

# 创建自定义 Team

继承 `BaseTeam` 并实现 `invoke()` 和 `stream()` 即可构建完整团队：

```python
from openjiuwen.core.multi_agent import BaseTeam, TeamCard, TeamConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard

class MyTeam(BaseTeam):
    def __init__(self, card: TeamCard, config=None):
        super().__init__(card=card, config=config)
        agent_card = AgentCard(id="worker", name="worker", description="工作者")
        self.add_agent(agent_card, lambda: WorkerAgent(card=agent_card))

    async def invoke(self, message, session=None):
        await self.runtime.start()
        try:
            result = await self.runtime.send(
                message=message,
                recipient="worker",
                sender="team",
            )
            return result
        finally:
            await self.runtime.stop()

    async def stream(self, message, session=None):
        result = await self.invoke(message, session)
        yield result
```

# TeamConfig

`TeamConfig` 控制团队运行时行为，所有方法支持链式调用：

```python
from openjiuwen.core.multi_agent import TeamConfig

config = (TeamConfig()
    .configure_max_agents(20)
    .configure_timeout(60.0)
    .configure_concurrency(200))
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `max_agents` | `10` | 团队内最大 Agent 数量 |
| `message_timeout` | `30.0` | 消息处理超时（秒） |
| `max_concurrent_messages` | `100` | 最大并发消息数 |

# 与 Runner 集成

将 `BaseTeam` 子类注册到 Runner 后，即可通过 `Runner.run_agent_team()` 或 `Runner.run_agent_team_streaming()` 调用：

```python
from openjiuwen.core.runner import Runner

team = MyTeam(card=team_card, config=team_config)
await Runner.resource_mgr.add_agent_team(team.card, lambda: team)

# 非流式调用
result = await Runner.run_agent_team(
    agent_team=team.card.id,
    inputs={"task": "执行任务"},
)

# 使用完毕后注销
await Runner.resource_mgr.remove_agent_team(team_id=team.card.id)
```

# 完整示例

以下示例来自 `examples/multi_agent/team_hybrid.py`，展示了如何将混合通信（P2P + Pub-Sub）封装为 `TaskExecutionTeam`，并通过 Runner 统一调用。

**通信流程：**
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


# --- Agent 定义 ---

class OrchestratorAgent(CommunicableAgent, BaseAgent):
    def configure(self, config): return self

    async def invoke(self, inputs: Any, session: Optional[AgentSession] = None) -> Any:
        task = inputs.get("task", "") if isinstance(inputs, dict) else str(inputs)
        session_id = session.get_session_id() if session else None
        await self.publish(
            message={"event": "execution_request", "task": task},
            topic_id="execution_events",
            session_id=session_id,
        )
        return {"status": "broadcast_done", "task": task}

    async def stream(self, inputs: Any, session: Optional[AgentSession] = None) -> AsyncIterator[Any]:
        await self.invoke(inputs, session)
        if False:
            yield None


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
            topic_id="completion_events",
            session_id=session_id,
        )
        return {"status": "executed", "result": result}

    async def stream(self, inputs: Any, session: Optional[AgentSession] = None) -> AsyncIterator[Any]:
        await self.invoke(inputs, session)
        if False:
            yield None


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
        if False:
            yield None


class ReporterAgent(CommunicableAgent, BaseAgent):
    def configure(self, config): return self

    async def invoke(self, inputs: Any, session: Optional[AgentSession] = None) -> Any:
        results = inputs.get("results", []) if isinstance(inputs, dict) else []
        return {"status": "report_generated", "total": len(results), "results": results}

    async def stream(self, inputs: Any, session: Optional[AgentSession] = None) -> AsyncIterator[Any]:
        await self.invoke(inputs, session)
        if False:
            yield None


# --- 团队封装 ---

class TaskExecutionTeam(BaseTeam):
    """任务执行团队：将混合通信封装为统一的 invoke() 接口。"""

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
            card=self.aggregator_card,
            done_event=asyncio.Event(),
            expected=3,
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
            raise ValueError("TaskExecutionTeam.invoke 需要团队 session，请使用 Runner.run_agent_team()")

        done_event = asyncio.Event()
        session_id = session.get_session_id() if session else None
        self.aggregator.reset(done_event=done_event, expected=3)

        await self.runtime.start()
        await self._setup_subscriptions()
        try:
            # Step 1: P2P -> orchestrator（内部广播给 executors）
            orch_result = await self.runtime.send(
                message=message,
                recipient="orchestrator",
                sender="main_process",
                session_id=session_id,
            )
            multi_agent_logger.info(f"[Team] orchestrator result: {orch_result}")

            # Step 2: 等待 aggregator 收集完 3 条结果
            try:
                await asyncio.wait_for(done_event.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                multi_agent_logger.warning("[Team] aggregator timeout")

            # Step 3: P2P -> reporter 生成最终报告
            report = await self.runtime.send(
                message={"results": self.aggregator.get_results()},
                recipient="reporter",
                sender="main_process",
                session_id=session_id,
            )
            return {"orchestration": orch_result, "report": report}
        finally:
            await self.runtime.stop()

    async def stream(self, message: Any, session: Optional[Session] = None) -> AsyncIterator[Any]:
        if session is None:
            raise ValueError("TaskExecutionTeam.stream 需要团队 session，请使用 Runner.run_agent_team_streaming()")

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


# --- 主流程 ---

async def main():
    team_card = TeamCard(
        id="task_execution_team",
        name="task_execution_team",
        description="任务执行团队"
    )
    team_config = TeamConfig(max_agents=10)
    team = TaskExecutionTeam(card=team_card, config=team_config)

    await Runner.resource_mgr.add_agent_team(team.card, lambda: team)
    try:
        result = await Runner.run_agent_team(
            agent_team=team.card.id,
            inputs={"task": "开发新功能模块"},
        )
        multi_agent_logger.info(f"[main] 任务完成，结果: {result}")
    finally:
        await Runner.resource_mgr.remove_agent_team(team_id=team.card.id)
        await team.runtime.stop()


if __name__ == "__main__":
    asyncio.run(main())
```

运行后输出示例：

```text
[Team] orchestrator result: {'status': 'broadcast_done', 'task': '开发新功能模块'}
[Team] aggregator 收集完成 (3/3)
[main] 任务完成，结果: {
  'orchestration': {'status': 'broadcast_done', 'task': '开发新功能模块'},
  'report': {'status': 'report_generated', 'total': 3, 'results': [...]}
}
```

# TeamCard

`TeamCard` 定义团队的身份信息，在构造时传入：

```python
from openjiuwen.core.multi_agent import TeamCard

team_card = TeamCard(
    id="my_team_001",
    name="my_team",
    description="我的多智能体团队",
    topic="task_execution",
    version="1.0.0",
    tags=["production"],
)
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | `str` | 团队唯一标识符 |
| `name` | `str` | 团队名称 |
| `description` | `str` | 团队描述 |
| `agent_cards` | `List[AgentCard]` | 成员 Agent 列表（由 `add_agent` 自动维护） |
| `topic` | `str` | 团队主题/领域 |
| `version` | `str` | 版本号 |
| `tags` | `List[str]` | 分类标签 |
