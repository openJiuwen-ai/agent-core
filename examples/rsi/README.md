# RSI 启动示例

下面用 Evo-Bench General 的 2 道题跑通一轮单 Harness 优化。该命令会依次执行
当前 Harness 评测、证据分析、候选生成、候选评测和 Epoch 全量检查，不需要提前
单独运行 H0。

## 准备

在仓库根目录执行：

```powershell
uv sync
```

本示例需要 WSL，并确保以下两个模型配置中的 API Key 可用：

- `.local/rsi/models/token_plan_deepseek_v4_flash_single_harness.yaml`
- `.local/rsi/models/bailian_glm5_1_single_harness.yaml`

## 运行

```powershell
uv run python examples/rsi/run_single_harness.py evobench optimize `
  --run-name rsi_general_smoke_v1 `
  --domain general `
  --execution-mode local `
  --limit 2 `
  --batch-size 2 `
  --max-epochs 1 `
  --sibling-candidate-count 1 `
  --rollout-concurrency 2
```

运行结束后，终端会输出：

- `SINGLE_HARNESS_REPORT`：本轮优化报告；
- `BEST_HARNESS_REFS`：当前最佳 Harness；
- `BEST_PASS_HAT_K`：最终通过率；
- `RUN_DIR`：全部运行产物目录。

RSI 各模块提示词见
[`docs/rsi/runtime_system_prompts.md`](../../docs/rsi/runtime_system_prompts.md)。
