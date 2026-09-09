# 轨迹归档 Rail

`TrajectoryRail` 为每次完成的 Agent invoke 归档一条 canonical execution trajectory。它从共享的
`TrajectorySpanProcessor` 消费已结束的 OpenTelemetry span，并同步写入 `TrajectoryStore`。
当前继承的 subscription 只选择 `llm` 和 `tool` 类别；run-root 与 agent-tier span 保留在 observability
exporter 中，不会复制进该 archive。

导入 Rail 前需要安装 observability 依赖：

```bash
uv sync --extra observability
```

## 导入

```python
from openjiuwen.agent_evolving.trajectory import FileTrajectoryStore, TrajectorySpanProcessor
from openjiuwen.core.runner import Runner
from openjiuwen.core.session.agent import Session
from openjiuwen.harness.rails import TrajectoryRail
```

## 构造

```text
class TrajectoryRail(
    *,
    trajectory_span_processor: TrajectorySpanProcessor,
    trajectory_store: TrajectoryStore,
    max_trajectory_spans: int | None = 200,
)
```

- `trajectory_span_processor` 是 `get_trajectory_span_processor()` 返回的进程级共享 processor。
  demand coordinator 会为 Agent 与 Team observability 注册同一个对象；轨迹归档、Skill 演进和 Team
  Skill Rails 必须复用它。
- `trajectory_store` 是同步 canonical archive，例如 `FileTrajectoryStore` 或
  `InMemoryTrajectoryStore`。
- `max_trajectory_spans` 限制单次 invoke 的归档 span 数量；传入 `None` 表示不限制。

`priority = 10`，因此 recorder 会在优先级更高的行为 Rail 已观察本次 invoke 后执行最终 drain。存储失败只
记录日志，不会改变 Agent 结果。

## 端到端配置

可运行模块 `examples.agent_evolving.trajectory_rail_example` 展示独立的文件持久化流程，不包含 Skill
演进行为。

```python
from openjiuwen.agent_evolving.trajectory import FileTrajectoryStore
from openjiuwen.extensions.observability.config import ObservabilityConfig
from openjiuwen.extensions.observability.demand import get_trajectory_span_processor
from openjiuwen.harness import create_deep_agent
from openjiuwen.harness.observability import (
    AgentObservabilityRail,
    acquire_observability,
    close_agent_run_span,
    open_agent_run_span,
    release_observability,
)
from openjiuwen.harness.rails import TrajectoryRail

acquire_observability(ObservabilityConfig(exporter="file", traces_dir="./traces"))
processor = get_trajectory_span_processor()

store = FileTrajectoryStore("./trajectory-archive")
agent = create_deep_agent(
    model=model_client,
    rails=[
        AgentObservabilityRail(),
        TrajectoryRail(
            trajectory_span_processor=processor,
            trajectory_store=store,
        )
    ],
)
session_id = "trajectory-example-1"
session = Session(session_id=session_id, card=agent.card)

# 为当前 Agent turn 打开 canonical run root。该 helper 还会注册按 session
# 路由的 fallback，供 detached task 中结束的 callback 使用。
root_span = open_agent_run_span(session_id=session_id, mode="trajectory.archive")
try:
    result = await Runner.run_agent(agent, {"query": "总结当前任务。"}, session=session)
except BaseException as exc:
    close_agent_run_span(root_span, session_id=session_id, exception=exc)
    raise
else:
    close_agent_run_span(root_span, session_id=session_id, output=result.get("output", result))

# Runner 停止且所有 run root 关闭后再释放 runtime demand。
release_observability()
```

invoke 完成后可按稳定 resource metadata 查询：

```python
trajectories = store.query(session_id=session_id)
```

observability 文件 exporter 和 `TrajectoryStore` 是两类不同输出：前者记录原始 span 用于可观测性，后者由
`TrajectoryRail` 写入单次 invoke 的 canonical archive。不要把 exporter 文件读回 Rail。

## 属性

### trajectory_store -> TrajectoryStore

返回配置的同步归档。

## Trace 前置条件

单 Agent 场景中，先 acquire Agent observability runtime，再构建 Agent，并在 `TrajectoryRail` 前挂载
`AgentObservabilityRail`。每个 Agent turn 使用 `open_agent_run_span()` 打开一个 run root；run root、
`Session` 和 store query 必须使用同一个 session ID。invoke 完成后再关闭 run root，Runner 停止后释放
observability。应用代码不要另外创建 `TrajectorySpanProcessor`，也不要手工管理 `set_root_span()`。

Team Rails 通过当前 Team root trace 路由 subscription。先 acquire Team observability，并挂载
`maybe_observability_rails()` 返回的 Agent/Team Rail 组合，再创建或挂载一个正在 recording、trace ID
非零且包含 `AT_TEAM_NAME` 的 root span；需要共享 Team 轨迹的所有 invoke 完成后才能 finalize，最后
release Team observability demand。
