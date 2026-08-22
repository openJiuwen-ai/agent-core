# Multi-Rollout 并行任务执行

为同一任务生成 N 个独立的智能体尝试，以不同策略并行运行，返回最优结果。

## 问题

单一智能体执行路径可能陷入局部最优。面对复杂 bug 修复时，智能体的第一次尝试往往并非最佳方案。手动重试需要重启整个任务，既慢又浪费。

## 解决方案

**Multi-Rollout** 从同一出发点生成 N 个独立执行尝试：

1. 将智能体工作空间克隆为 N 个独立子空间
2. 为每个尝试注入不同策略提示
3. 并行运行所有尝试（每个都是完整的 `DeepAgent.invoke()`）
4. 收集 N 个结果
5. 通过选择器挑选最优
6. 返回胜者

## 配置

在 `DeepAgentConfig` 中添加 `multi_rollout`：

```python
from openjiuwen.harness import DeepAgentConfig, MultiRolloutConfig

config = DeepAgentConfig(
    # ... 其他字段 ...
    multi_rollout=MultiRolloutConfig(
        enabled=True,
        n_rollouts=3,
        max_parallel=3,
        timeout_per_rollout=600.0,
        selector_kind="first_successful",
    )
)
```

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `enabled` | `False` | 开启/关闭多轮执行 |
| `n_rollouts` | `3` | 并行尝试次数 |
| `max_parallel` | `0` | 最大并发数（`0` = 不限） |
| `timeout_per_rollout` | `600.0` | 每次尝试超时（秒） |
| `selector_kind` | `"first_successful"` | 选择胜者的方式 |

## 策略变体

每个尝试接收相同任务，但前缀策略指令不同。默认三种策略：

1. **注重正确性** — 深入探索，考虑所有影响
2. **最小化改动** — 改动行数越少越好
3. **注重边界情况** — 考虑边界、错误、防御性代码

可自定义：

```python
config = MultiRolloutConfig(
    strategy_variants=[
        "Focus on speed. Get a working fix quickly.",
        "Focus on robustness. Handle every edge case.",
        "Focus on minimal changes. Preserve existing style.",
    ]
)
```

## 结果选择器

| 选择器 | 行为 | 适用场景 |
|--------|------|----------|
| `first_successful` | 返回第一个无错误结果 | 速度优先；最安全的默认 |
| `longest_output` | 返回输出最长的成功结果 | 完整性优先 |
| `shortest_output` | 返回输出最短的成功结果 | 最小化 diff 优先 |

## 工作原理

```
用户调用 DeepAgent.invoke()
  └─ 如果 multi_rollout.enabled 且 n_rollouts > 1:
       创建 N 个子智能体（各带独立工作空间）
       为每个尝试的 query 添加策略前缀
       并行运行（asyncio.gather）
       选择最优结果
       返回胜者
  └─ 否则:
       正常单路径执行
```

## 脱离 DeepAgentConfig 直接使用

也可直接使用 `MultiRolloutExecutor`：

```python
from openjiuwen.harness.multi_rollout import MultiRolloutExecutor, MultiRolloutConfig

executor = MultiRolloutExecutor(parent_agent, MultiRolloutConfig(
    enabled=True,
    n_rollouts=3,
))
result = await executor.invoke({"query": "fix bug"})
```

## 注意事项

- **流式输出**：Multi-Rollout 仅支持 `invoke()`，不支持 `stream()`。如需流式，先运行选择器，再对胜果单独流式输出。
- **成本**：每次尝试消耗完整 LLM token。3 次尝试 ≈ 3 倍成本。
- **工作空间状态**：父工作空间不受影响。返回结果为文本；如需将文件复制回父空间，由调用方负责。
