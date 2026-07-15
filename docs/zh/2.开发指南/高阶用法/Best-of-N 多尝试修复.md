# Best-of-N 多尝试修复

当代码变更未通过 CI 时，默认行为是**两阶段修复循环**：智能体阅读错误日志、尝试修复、然后重跑 CI。对于简单的 lint 或类型错误这很高效，但对于复杂 bug，单一修复策略可能陷入局部最优。

**Best-of-N** 生成 *N* 个独立修复尝试，对每个结果的工作空间进行评分，然后提升最优补丁。这一方法与顶级 SWE-Bench 系统使用的技术一致。

## 工作原理

1. **克隆**当前工作空间 `N` 次到独立目录。
2. **修复**每个克隆，使用不同的策略提示：
   - 尝试 0：注重正确性
   - 尝试 1：最小化 diff 大小
   - 尝试 2+：考虑边界情况
3. **评分**每个克隆：
   - 主指标：通过测试数 / 总测试数
   - 打破平局 1：diff 行数（越小越好）
   - 打破平局 2：lint 错误数（越少越好）
4. **选择**得分最高的克隆。
5. **提升**回原始工作空间。
6. **清理**剩余克隆。

## 配置

通过 `AutoHarnessConfig` 全局启用 best-of-N：

```python
from openjiuwen.auto_harness.schema import AutoHarnessConfig

config = AutoHarnessConfig(
    # ... 其他字段 ...
    best_of_n_enabled=True,
    best_of_n_attempts=3,
    best_of_n_timeout_per_attempt=600.0,
)
```

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `best_of_n_enabled` | `False` | 为 `True` 时，验证阶段使用 best-of-N 替代经典修复循环。 |
| `best_of_n_attempts` | `3` | 生成的独立尝试数量。 |
| `best_of_n_timeout_per_attempt` | `600.0` | 每次尝试的超时时间（秒）。 |

## 使用建议

| 场景 | 建议 |
|------|------|
| 简单 lint / 类型错误 | 保持 `best_of_n_enabled=False`。修复循环更快且足够。 |
| 复杂 bug 修复、SWE-Bench 任务 | 设置 `best_of_n_enabled=True`。3 倍计算成本通常能被更高的通过率justify。 |
| Terminal / bash 密集型任务 | Best-of-N 同样有效，因为修复策略差异较大。 |

## 架构

```
MetaVerifyStage
    ├─ 如果 CI 通过 → 完成
    └─ 如果 CI 失败
         ├─ [best_of_n_enabled=True]
         │    └─ BestOfNController.run()
         │         ├─ WorkspaceCloner.clone_n()  → N 个工作空间
         │         ├─ 对每个工作空间
         │         │    ├─ attempt_factory(path, seed)  → 智能体修复
         │         │    └─ AttemptScorer.score()        → 测试 + diff + lint
         │         ├─ AttemptSelector.select()          → 最优候选
         │         ├─ 提升最优 → 原始工作空间
         │         └─ 删除失败克隆
         └─ [best_of_n_enabled=False]
              └─ FixLoopController.run()  → 经典两阶段修复
```

## 扩展

可以插入自定义评分器或选择器：

```python
from openjiuwen.auto_harness.infra.best_of_n import BestOfNController
from openjiuwen.auto_harness.infra.attempt_scorer import AttemptScorer
from openjiuwen.auto_harness.infra.attempt_selector import AttemptSelector

class CoverageScorer(AttemptScorer):
    async def score(self, workspace, ci_runner=None):
        # ... 自定义评分逻辑 ...
        pass

class MySelector(AttemptSelector):
    def select(self, candidates):
        # ... 自定义选择逻辑 ...
        pass

ctrl = BestOfNController(
    n_attempts=5,
    scorer=CoverageScorer(),
    selector=MySelector(),
)
```

## 核心组件

| 文件 | 用途 |
|------|------|
| `openjiuwen.auto_harness.infra.attempt_scorer` | 按测试通过数、diff 大小、lint 错误评分工作空间。 |
| `openjiuwen.auto_harness.infra.attempt_selector` | 从已评分尝试中选择最优候选。 |
| `openjiuwen.auto_harness.infra.workspace_cloner` | 使用 `shutil.copytree` 克隆工作空间 N 次。 |
| `openjiuwen.auto_harness.infra.best_of_n` | `BestOfNController` — 编排完整 best-of-N 流水线。 |
| `openjiuwen.auto_harness.infra.fix_loop` | 经典两阶段 CI 修复循环（增量修复）。 |
| `openjiuwen.auto_harness.stages.verify` | 验证阶段分发 — 在修复循环与 best-of-N 之间选择。 |
