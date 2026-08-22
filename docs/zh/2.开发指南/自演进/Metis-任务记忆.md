# Metis 任务记忆

Metis 为 Agent 保存可复用的任务经验，并在后续任务开始前按需注入。

它保存两类记忆：

- `Tip`：环境事实、执行方案和常见陷阱。
- `CodeTool`：从重复执行方案中提炼出的可复用 Python 工具。

Metis 不使用向量检索。每次检索时，Manager 会看到当前用户的全部有效 Tip 和全部工具，并一次性选出与当前任务有关的内容。

---

## 快速开始

`MetisContextEvolveRail` 接入现有 DeepAgent 生命周期。下面假设已经创建好 `Model` 实例 `model_client`。

先安装 observability 依赖：

```bash
uv sync --extra observability
```

```python
import asyncio

from openjiuwen.agent_evolving.trajectory import TrajectorySpanProcessor
from openjiuwen.core.runner import Runner
from openjiuwen.extensions.observability.config import ObservabilityConfig
from openjiuwen.extensions.observability.setup import (
    init_observability,
    shutdown_observability,
)
from openjiuwen.harness import create_deep_agent
from openjiuwen.harness.rails import MetisContextEvolveRail


MODEL_NAME = "your-model-name"
USER_ID = "your-stable-user-id"


async def main():
    processor = TrajectorySpanProcessor()
    init_observability(
        ObservabilityConfig(
            exporter="console",
            redact_prompts=True,
            redact_completions=True,
        ),
        additional_span_processors=(processor,),
    )

    metis_rail = MetisContextEvolveRail(
        llm=model_client,
        model=MODEL_NAME,
        trajectory_span_processor=processor,
        user_id=USER_ID,
    )
    agent = create_deep_agent(
        model=model_client,
        rails=[metis_rail],
    )

    await Runner.start()
    try:
        result = await Runner.run_agent(
            agent,
            {"query": "分析这个项目的测试失败并给出修复方案"},
            session="metis-demo-session",
        )
        print(result)
    finally:
        # 默认使用后台演进；退出前等待记忆写入完成。
        await metis_rail.cleanup_background_tasks()
        await Runner.stop()
        shutdown_observability()


asyncio.run(main())
```

`TrajectorySpanProcessor` 必须注册到实际采集 Agent Span 的 observability runtime。宿主已经完成注册时，直接复用同一个 processor，不要重复创建。

---

## 必填参数

| 参数 | 说明 |
|---|---|
| `llm` | 用于 Manager 筛选和任务后反思的 `Model` 实例。 |
| `model` | 调用该 Model 时使用的模型名称。 |
| `trajectory_span_processor` | 用于采集本次执行轨迹的共享 processor。 |
| `user_id` | 记忆作用域，必须显式传入并在多次运行之间保持稳定。 |

`user_id` 不是 session ID。不同 session 可以共享同一个 `user_id` 的任务记忆。

---

## 记忆存储

默认写入：

```text
./memories/metis/<user_id>.json
```

相对路径以进程启动时的当前工作目录为基准。

指定其他目录：

```python
metis_rail = MetisContextEvolveRail(
    ...,
    user_id="user-123",
    persist_dir="/data/openjiuwen/metis",
)
```

对应文件为：

```text
/data/openjiuwen/metis/user-123.json
```

仅使用进程内存、不写磁盘：

```python
metis_rail = MetisContextEvolveRail(
    ...,
    user_id="user-123",
    persist_dir=None,
)
```

也可以通过 `store=` 传入自定义的 `MetisMemoryStore`。传入 `store` 后，由该 Store 决定持久化方式，`persist_dir` 不再生效。

JSON 快照包含：

```text
version
tips
tools
recent_queries
```

无效 Tip 会保留在快照中用于状态演进，但不会提供给 Manager，也不会注入 Agent。

---

## 运行顺序

### 任务开始前

```text
加载当前 user_id 的记忆
  → 向 Manager 提供全部有效 Tips 和全部工具
  → Manager 选择相关 Tip IDs 和 Tool IDs
  → 补齐执行方案关联工具及工具依赖
  → 渲染为 metis_task_memory prompt section
  → 注入 Agent
```

Manager 可以选择空结果。Metis 不会因为有历史记忆就强制注入内容。

### 任务完成后

```text
EvolutionRail 收集轨迹
  → SingleDimUpdater 执行 Metis Optimizer
  → 成功任务为已选执行方案累积证据
  → 达到阈值时提炼 CodeTool
  → 反思本次轨迹并创建或更新 Tip
  → execute_updates 执行 Operator 更新
  → MetisMemoryStore 写入新快照
```

默认 `threshold=3`。同一个执行方案被三个成功任务选中后，Metis 会尝试将该方案提炼为工具。失败或结果未知的任务不会增加这项证据。

---

## 常用配置

| 参数 | 默认值 | 作用 |
|---|---:|---|
| `persist_dir` | `./memories/metis` | JSON 快照目录；`None` 表示仅内存。 |
| `threshold` | `3` | 执行方案触发 CodeTool 提炼所需的成功任务数。 |
| `inject_memories` | `True` | 是否在任务开始前检索并注入记忆。 |
| `auto_evolve` | `True` | 是否在任务完成后自动演进并写入记忆。 |
| `async_evolution` | `True` | 是否在后台运行任务后演进。 |
| `max_concurrent_evolution` | `1` | 同一个 Rail 允许的并发演进数。 |
| `executor_context` | `""` | 提供给反思模型的额外执行环境说明。 |

如果只想读取已有记忆而不产生新记忆，设置：

```python
auto_evolve=False
```

当前 Metis 自动演进不会进入人工审批队列。`auto_evolve=True` 时，成功生成的更新会由 Rail 直接提交到 Store。

---

## 与 ContextEvolutionRail 的区别

| | `MetisContextEvolveRail` | `ContextEvolutionRail` |
|---|---|---|
| 算法 | Metis | ACE、ReasoningBank、ReMe、Cognition 等 |
| 检索 | 纯 Manager 筛选 | 原 Context Evolver 的算法检索流程 |
| 向量检索 | 不使用 | 部分算法使用 |
| 执行框架 | `EvolutionRail`、`SingleDimUpdater`、Optimizer、Operator | `extensions/context_evolver` 服务流程 |
| 默认存储 | `./memories/metis/<user_id>.json` | `./memories/<algo_name>/<user_id>.json` |

新接入 Metis 时使用 `MetisContextEvolveRail`。不要用名称相近的 `ContextEvolutionRail` 代替。

---

## 使用建议

- 为同一用户或工作区使用稳定的 `user_id`。
- 服务端部署建议使用绝对 `persist_dir`，避免工作目录变化导致读取到不同文件。
- 纯 Manager 筛选会把全部有效记忆作为候选发送给模型；记忆库很大时应关注 prompt 大小和筛选成本。
- 默认后台演进可能在主任务返回后继续运行；进程退出前应等待 `cleanup_background_tasks()`。
- 同一进程中，同一个 `user_id` 只应注册一个 Metis 写入 Rail。
