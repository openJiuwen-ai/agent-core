# Metis Task Memory

Metis stores reusable task experience for an Agent and injects relevant memory before later tasks.

It stores two kinds of memory:

- `Tip`: environment facts, execution plans, and common pitfalls.
- `CodeTool`: reusable Python tools distilled from recurring execution plans.

Metis does not use vector retrieval. On each retrieval, the Manager sees all live Tips and all tools for the current user, then selects the items relevant to the current task in one decision.

---

## Quick Start

`MetisContextEvolveRail` plugs into the existing DeepAgent lifecycle. The example below assumes that a configured `Model` instance named `model_client` already exists.

Install the observability dependencies first:

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
            {"query": "Analyze this project's test failures and propose fixes"},
            session="metis-demo-session",
        )
        print(result)
    finally:
        # Evolution runs in the background by default. Wait for persistence.
        await metis_rail.cleanup_background_tasks()
        await Runner.stop()
        shutdown_observability()


asyncio.run(main())
```

The `TrajectorySpanProcessor` must be registered with the observability runtime that captures the Agent spans. If the host already registered one, reuse that same processor instead of creating another.

---

## Required Parameters

| Parameter | Description |
|---|---|
| `llm` | The `Model` instance used for Manager selection and post-task reflection. |
| `model` | The model name used when invoking that Model. |
| `trajectory_span_processor` | The shared processor that captures the task trajectory. |
| `user_id` | The memory scope. It must be passed explicitly and remain stable across runs. |

`user_id` is not a session ID. Different sessions can share task memory by using the same `user_id`.

---

## Memory Storage

The default location is:

```text
./memories/metis/<user_id>.json
```

Relative paths are resolved from the process working directory.

To use another directory:

```python
metis_rail = MetisContextEvolveRail(
    ...,
    user_id="user-123",
    persist_dir="/data/openjiuwen/metis",
)
```

The resulting file is:

```text
/data/openjiuwen/metis/user-123.json
```

To keep memory in the current process without writing to disk:

```python
metis_rail = MetisContextEvolveRail(
    ...,
    user_id="user-123",
    persist_dir=None,
)
```

You can also pass a custom `MetisMemoryStore` through `store=`. When a Store is supplied, that Store controls persistence and `persist_dir` is ignored.

Each JSON snapshot contains:

```text
version
tips
tools
recent_queries
```

Invalidated Tips remain in the snapshot for state evolution, but they are not offered to the Manager or injected into the Agent.

---

## Runtime Order

### Before a task

```text
Load memory for the current user_id
  → offer all live Tips and all tools to the Manager
  → select relevant Tip IDs and Tool IDs
  → add plan-linked tools and transitive tool dependencies
  → render the metis_task_memory prompt section
  → inject it into the Agent
```

The Manager may select nothing. Existing memory is never injected just because it exists.

### After a task

```text
EvolutionRail captures the trajectory
  → SingleDimUpdater runs the Metis Optimizer
  → successful tasks add evidence to selected execution plans
  → plans that reach the threshold are distilled into CodeTools
  → the trajectory is reflected into new or updated Tips
  → execute_updates applies the Operator update
  → MetisMemoryStore writes the new snapshot
```

The default `threshold` is `3`. After the same execution plan is selected by three successful tasks, Metis attempts to distill it into a tool. Failed tasks and tasks with an unknown outcome do not add this evidence.

---

## Common Configuration

| Parameter | Default | Purpose |
|---|---:|---|
| `persist_dir` | `./memories/metis` | JSON snapshot directory; `None` keeps memory in process only. |
| `threshold` | `3` | Successful task count required before plan-to-CodeTool distillation. |
| `inject_memories` | `True` | Retrieve and inject memory before each task. |
| `auto_evolve` | `True` | Evolve and persist memory after each task. |
| `async_evolution` | `True` | Run post-task evolution in the background. |
| `max_concurrent_evolution` | `1` | Maximum concurrent evolution runs for one Rail. |
| `executor_context` | `""` | Additional execution-environment guidance for reflection. |

To read existing memory without producing new memory, set:

```python
auto_evolve=False
```

Metis automatic evolution does not enter a human approval queue. With `auto_evolve=True`, successful updates are committed directly to the Store by the Rail.

---

## Difference from ContextEvolutionRail

| | `MetisContextEvolveRail` | `ContextEvolutionRail` |
|---|---|---|
| Algorithm | Metis | ACE, ReasoningBank, ReMe, Cognition, and others |
| Retrieval | Manager-only selection | Original Context Evolver algorithm flows |
| Vector retrieval | Not used | Used by some algorithms |
| Execution framework | `EvolutionRail`, `SingleDimUpdater`, Optimizer, and Operator | `extensions/context_evolver` service flows |
| Default storage | `./memories/metis/<user_id>.json` | `./memories/<algo_name>/<user_id>.json` |

Use `MetisContextEvolveRail` for new Metis integrations. Do not substitute the similarly named `ContextEvolutionRail`.

---

## Practical Guidance

- Use a stable `user_id` for the same user or workspace.
- In server deployments, prefer an absolute `persist_dir` so a working-directory change does not select a different file.
- Manager-only retrieval sends the full live library as candidates. Monitor prompt size and selection cost as the library grows.
- Background evolution may continue after the main task returns. Wait for `cleanup_background_tasks()` before process shutdown.
- Register only one Metis writer Rail for the same `user_id` in one process.
