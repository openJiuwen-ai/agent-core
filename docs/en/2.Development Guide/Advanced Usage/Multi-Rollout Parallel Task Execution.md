# Multi-Rollout Parallel Task Execution

Spawn N independent agent attempts for the same task, run them in parallel with different strategies, and return the best result.

## Problem

A single agent trajectory may get stuck in a local optimum. When faced with a hard bug fix, the first approach the agent tries might not be the best one. Re-trying with a different strategy requires restarting the entire task, which is slow and wasteful.

## Solution

**Multi-rollout** generates N isolated execution attempts from the same starting point:

1. Clone the agent workspace into N isolated sub-workspaces.
2. Inject a different strategy prompt into each attempt.
3. Run all attempts in parallel (each is a full `DeepAgent.invoke()`).
4. Collect the N results.
5. Run a selector to pick the best one.
6. Return the winner.

## Configuration

Add `multi_rollout` to your `DeepAgentConfig`:

```python
from openjiuwen.harness import DeepAgentConfig, MultiRolloutConfig

config = DeepAgentConfig(
    # ... other fields ...
    multi_rollout=MultiRolloutConfig(
        enabled=True,
        n_rollouts=3,
        max_parallel=3,
        timeout_per_rollout=600.0,
        selector_kind="first_successful",
    )
)
```

| Field | Default | Description |
|-------|---------|-------------|
| `enabled` | `False` | Turn multi-rollout on/off |
| `n_rollouts` | `3` | Number of parallel attempts |
| `max_parallel` | `0` | Max concurrent rollouts (`0` = unlimited) |
| `timeout_per_rollout` | `600.0` | Timeout per attempt in seconds |
| `selector_kind` | `"first_successful"` | How to pick the winner |

## Strategy Variants

Each attempt receives the same task but prefixed with a different strategy instruction. The default three strategies are:

1. **Correctness-focused** — explore deeply, consider all implications
2. **Minimal-diff** — change as few lines as possible
3. **Edge-case-focused** — consider boundaries, errors, defensive code

You can replace them:

```python
config = MultiRolloutConfig(
    strategy_variants=[
        "Focus on speed. Get a working fix quickly.",
        "Focus on robustness. Handle every edge case.",
        "Focus on minimal changes. Preserve existing style.",
    ]
)
```

## Result Selectors

| Selector | Behavior | Best for |
|----------|----------|----------|
| `first_successful` | Return the first non-error result | Speed; safest default |
| `longest_output` | Return the successful result with longest output | When completeness matters |
| `shortest_output` | Return the successful result with shortest output | When minimal diffs matter |

## How It Works

```python
# When DeepAgent.invoke() is called:
if multi_rollout.enabled and n_rollouts > 1:
    for i in range(n_rollouts):
        subagent = parent.create_subagent(
            "general-purpose",
            subsession_id=f"rollout-{i:03d}"
        )
        # Each subagent gets its own isolated workspace

    # Run all subagents in parallel via asyncio.gather
    # Apply strategy prefix to each attempt's query
    # Select best result via configured selector
    return winner
else:
    return await normal_invoke()
```

## Using Without DeepAgentConfig

You can also use `MultiRolloutExecutor` directly:

```python
from openjiuwen.harness.multi_rollout import MultiRolloutExecutor, MultiRolloutConfig

executor = MultiRolloutExecutor(parent_agent, MultiRolloutConfig(
    enabled=True,
    n_rollouts=3,
))
result = await executor.invoke({"query": "fix bug"})
```

## Caveats

- **Streaming**: Multi-rollout only works with `invoke()`, not `stream()`. If you need streaming with rollouts, run the executor first, then stream the winning result separately.
- **Cost**: Each rollout consumes full LLM tokens. 3 rollouts is approximately 3x cost.
- **Workspace state**: The parent workspace is untouched. The winning result is returned as text; copying files back to the parent workspace is the caller's responsibility.
