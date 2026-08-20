# RSI WorkBuddy Office 迁移说明

本文记录 WorkBuddy Office 单 Harness 优化链路在新版 Agent Core 上的迁移范围、运行方式和当前边界，避免后续实验混用旧目录与旧配置。

## 代码基线

- 新版 Agent Core 基线：`upstream/develop@5612254e6`
- 迁移分支：`codex/rsi-workbuddy-migration`
- 独立工作树：`D:\code\code1\agent-core-rsi-latest-audit`
- 原实验目录 `D:\code\code1\agent-core` 保持不动，其中的未提交实验改动没有被覆盖。

## 本轮迁移

- WorkBuddy Office 数据集、Docker 工作区、容器内执行和官方 Verifier 适配器。
- 每个 Case 提取 1 至 3 个独立问题，并将格式异常隔离在单个 Case 内。
- 每轮最多规划 3 个关联动作，并检查动作依赖和整体执行完整性。
- Candidate 局部提升后，只对当前未通过 Case 继续评测和分析；满分 Case 才进入 retained 集合。
- Skill、Tool 等能力只有在候选执行中真实触发，才通过相应激活门槛。
- 模型服务对临时 429、502、503 等错误进行有限退避重试；预算耗尽和认证错误直接失败。

## 本轮不处理

- 输出 token 耗尽后的自动恢复。
- 重复工具调用检测与熔断。
- Candidate 通过后的全数据集重放和严格晋升规则调整。
- 全局唯一 Action ID 等后续工程增强。

## 模型约定

- 任务执行：DeepSeek V4 Flash。
- 分析与优化：GLM。
- 本地模型配置放在 `.local/rsi/models/`，不得提交 API Key。

## 统一运行入口

新实验统一从 `run_single_harness.py` 启动，数据集实现通过注册表按需加载：

```powershell
uv run python examples/rsi/run_single_harness.py --help
uv run python examples/rsi/run_single_harness.py workbuddy-office optimize --help
uv run python examples/rsi/run_single_harness.py evobench optimize --help
uv run python examples/rsi/run_single_harness.py evobench evaluate --help
```

原有数据集脚本暂时保留为兼容入口，不再为新数据集增加独立启动脚本。

## 运行

先确认 Docker Desktop 已启动，并确认 WorkBuddy Office 数据集位于默认搜索路径，或通过 `--dataset-root` 指定路径。

单 Case 基线冒烟：

```powershell
uv run python examples/rsi/run_single_harness.py workbuddy-office optimize `
  --run-name latest_seed_smoke `
  --task-id <task-id> `
  --seed-only
```

单轮优化：

```powershell
uv run python examples/rsi/run_single_harness.py workbuddy-office optimize `
  --run-name latest_office_rsi `
  --limit 50 `
  --batch-size 2 `
  --max-epochs 1
```

中断后继续同一运行：

```powershell
uv run python examples/rsi/run_single_harness.py workbuddy-office optimize `
  --run-name latest_office_rsi `
  --limit 50 `
  --batch-size 2 `
  --max-epochs 1 `
  --resume
```

## Improver 演化

第一阶段的 `K=1` 单 Harness 优化继续保留。第二阶段使用多个 `K>=3` 运行产生的
Candidate Feedback Ledger 提出版本化 Improver Policy 候选：

```powershell
uv run python examples/rsi/evolve_improver_policy.py propose `
  --ledger <candidate-feedback-ledger-a.yaml> `
  --ledger <candidate-feedback-ledger-b.yaml> `
  --output-dir .office_runs/improver_evolution/meta_train_001
```

用候选策略运行同父多候选优化时，在原命令上增加：

```powershell
--sibling-candidate-count 3 `
--improver-policy-ref <candidate-policy.yaml>
```

候选策略必须在未参与策略生成的 checkpoint 上与父版本做配对验证：

```powershell
uv run python examples/rsi/evolve_improver_policy.py validate `
  --baseline-results <i0-meta-test-results.yaml> `
  --candidate-results <candidate-meta-test-results.yaml> `
  --mode live_generation `
  --output .office_runs/improver_evolution/meta_validation.yaml
```

离线重排只能验证 Ranker，不能发布完整 Improver；`K=1` 也不能作为递归改进证据。

## 验证

```powershell
uv run pytest -q tests/unit_tests/rsi
uv run ruff check openjiuwen/rsi examples/rsi tests/unit_tests/rsi
git diff --check
```

实际实验结果应记录运行目录、数据集 Case 列表、模型配置文件名、代码提交、优化前后分数和每个 Candidate 的接受或拒绝原因。不要只记录最终平均分。

## 迁移验收记录

- RSI 单元测试：`717 passed, 3 skipped`。
- Ruff 与 `git diff --check`：通过。
- WorkBuddy 真实冒烟 Case：`service-channel-ticket-daily`。
- 官方 Verifier：`0.9963`，通过 272/273 个原子检查；唯一失败项是产物中出现了禁止的隐藏 SLA 结论，说明执行、产物保存和官方评分链路均已贯通。
- 冒烟运行目录：`.office_runs/runs/latest_seed_-dddb03e9`。
- 冒烟过程中发现并修复了新版 Loader 的 Rail manifest 格式及 Expert Harness `identity` 覆盖冲突。
