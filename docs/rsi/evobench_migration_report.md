# RSI Evo-Bench 迁移报告

## 结论

Evo-Bench 不是 WorkBuddy Office 的同类替代数据集，而是直接评测 Harness 递归改进能力的协议。它固定任务执行模型、Seed Harness、验证/评估划分和迭代预算，比较不同 Evolver 能否把同一个通用 Harness 持续改强。因此，后续主实验切换到 Evo-Bench；WorkBuddy 单 Harness 优化仍保留为原子能力和故障分析工具。

## 官方协议

- 数据：160 个可见验证任务和 448 个不可见评估任务，二者不重叠。
- 领域：Search、Office、General，分别来自 BrowseComp/HLE、GDPval/APEX-Agents、Claw-Eval。
- 执行模型：DeepSeek V4 Flash，所有 Evolver 共用。
- Judge：Qwen3.7-Plus。
- Evolver 预算：20 次完整验证评测、1,000 个 Evolver step、48 小时。
- Task Agent 预算：每题最多 300 step 和 1 小时；General 使用三次 rollout 的 Pass^3，其他领域一次。
- 最终评分：Evolver 只能看到验证集；Harness 冻结且 Evolver 沙箱销毁后，才运行评估集。

这几个条件属于实验定义，不能为了复用 WorkBuddy 代码而修改。尤其不能把 160 个 Case 分批当作单 Harness 局部晋级，也不能让 Evolver 访问 448 题评估结果。

## 本地角色映射

| Evo-Bench 角色 | 当前本地服务 | 用途 |
| --- | --- | --- |
| Policy | `deepseek-v4-flash` | 固定 Task Agent 模型 |
| Evolver | `GLM-5.2` | 第一组官方 Evolver 对照实验 |
| Judge | `Qwen3.7-Plus` | 按官方口径评分 |

启动器从现有 `.local/rsi/models/*.yaml` 读取 endpoint 和 credential，只在子进程环境中传递；生成的 Evo-Bench JSON 配置仅记录环境变量名，不落盘 API Key。

## 已完成

- 官方代码、608 个任务定义和 GDPval 附件需放在 `EVOBENCH_ROOT` 指向的 Evo-Bench 工作目录中。
- 官方 Release 完整性检查通过：任务数量、划分互斥、领域标签、附件哈希和 Seed Harness 加载均正常。
- 固定版本 Claw-Eval 源码已准备并安装到官方运行环境。
- 新增 `examples/rsi/run_evobench.py`，提供 `prepare`、`verify`、`smoke`、`baseline`、`evolve` 五个入口。
- 新增 `examples/rsi/run_evobench_one.py`，可在没有 E2B 的情况下用 WSL 本地隔离跑一条官方验证题；该结果仅用于迁移验收，不冒充正式分数。
- Smoke 自动抽取五个来源各一道验证题和评估题，覆盖完整 evolve -> validation -> freeze -> evaluation 流程。
- WorkBuddy 代码和现有单 Harness 优化入口没有删除或改写。

## 单题迁移验收

2026-08-12 已完成一条真实官方验证题：`hle-670980821053a19619c30869`。DeepSeek V4 Flash 正常执行，Qwen3.7-Plus 正常判分，运行期错误和 Judge 错误均为 0。模型回答 `10`，官方参考答案为 `510`，因此得分为 `0.0`。这说明单题执行、轨迹、Token 统计和官方 Judge 链路已经打通；零分属于模型语义错误，不是迁移故障。

本次 Policy 使用 57,284 Tokens，Judge 使用 815 Tokens，结果位于 `.evobench_runs/single_task/hle-670980821053a19619c30869/evaluation/result.json`。

## 当前阻塞

正式评测必须使用 E2B，把 Policy rollout 与答案、Verifier 和 Evolver 隔离；Search 任务还需要 Serper。当前环境没有 `E2B_API_KEY` 和 `SERPER_API_KEY`，所以启动器会明确报告未就绪，并拒绝把本地非隔离结果冒充正式分数。

为了先验证本地迁移链路，当前 20 题子集已改为从 32 道可见 Claw-Eval 验证题中固定随机抽取 20 道，并排除 APEX、BrowseComp、GDPval 和 HLE。运行后端切换为 WSL/Linux 本地隔离，因此不需要 E2B 或 Serper；该子集只用于本地能力验证，不代表 Evo-Bench 五源完整分数。

```powershell
uv run python examples/rsi/run_evobench_subset.py prepare --run-name local_claw20_v1
uv run python examples/rsi/run_evobench_subset.py preflight --run-name local_claw20_v1
uv run python examples/rsi/run_evobench_subset.py run --run-name local_claw20_v1
```

## 运行顺序

先跑一条真实验证题：

```powershell
uv run python examples/rsi/run_evobench_one.py
```

检查数据与运行环境：

```powershell
uv run python examples/rsi/run_evobench.py prepare
uv run python examples/rsi/run_evobench.py verify
```

基础设施凭证可用后，先跑五来源端到端冒烟：

```powershell
uv run python examples/rsi/run_evobench.py smoke `
  --run-name glm52_smoke_v1
```

完整 Seed Harness 验证基线：

```powershell
uv run python examples/rsi/run_evobench.py baseline `
  --run-name seed_validation_v1
```

GLM-5.2 官方协议完整对照组：

```powershell
uv run python examples/rsi/run_evobench.py evolve `
  --run-name glm52_official_v1
```

## RSI 接入边界

第一组实验先保持官方 Evolve Harness 不变，用 GLM-5.2 得到可复现的 43.5 论文参照目标。第二组才把我们的 Analyzer、候选生成、验证和 Improver Policy 接入 Evo-Bench 的 Evolver 工具层，但必须继续复用官方 `run_validation_eval`、冻结逻辑、Evaluation 隔离和 Overall/AnytimeVal 计算。

RSI 对比至少保留四项：最终 Overall、AnytimeVal、每轮 best-so-far 曲线、验证提升到评估提升的泛化比。候选预测误差可以作为 Improver 的监督信号，但不得用不可见评估集反向更新。

## 本地 Claw20 RSI 接入进展（2026-08-13）

本地 20 道 General/Claw 固定验证题已经接入单 Harness 优化原子流程。H0 直接复用已经完成的官方结果，主指标为 General 三次运行均通过的 `Pass^3=0.70`；连续评分均值 `0.7984` 和每次 rollout 只作为诊断证据，不会替代晋级指标。

新增接入层包括：官方 Evo-Bench 结果与三次轨迹转换、GLM-5.2 基于失败轨迹生成差异化 PolicyHarness 候选、候选小范围原题验证、20 题全量检查、候选反馈账本和最终发布。PolicyHarness 候选按完整快照处理，不再套用 ExpertHarness 的 Skill/Tool 文件拼装逻辑。

全量晋级同时要求：`Pass^3` 不下降、H0 已通过题不回退、不新增基础设施失败、不新增策略违规。第一阶段仅允许修改 `system_prompt.md`，以及在证据明确表明预算不足时修改 `harness.json` 的 `max_steps` / `rollout_wall_clock_seconds`；Python 执行框架、工具和官方 grader 均被冻结。

当前入口：

```powershell
uv run python examples/rsi/run_evobench_single_harness.py `
  --run-name rsi_claw20_v1 `
  --batch-size 20 `
  --sibling-candidate-count 2 `
  --rollout-concurrency 5
```

这是本地迁移和 RSI 能力开发实验，不是正式 Evo-Bench 排行榜成绩。正式论文实验仍需 E2B、完整 160 道可见验证题、冻结后的 448 道不可见评估题以及官方完整预算协议。

## 单题 RSI 端到端验收（2026-08-13）

使用 `claw-T027zh_api_config_audit` 完成真实端到端验收。H0 的三次 rollout 均未通过，`Pass^3=0.0`；Analyzer 从官方轨迹定位到敏感凭据输出问题，Improver 只修改 `system_prompt.md`，随后候选 Gate、独立 residual replay 和 epoch full checkpoint 三组评估均通过。最终 `Pass^3=1.0`、原生连续分均值 `0.8044`，候选通过晋级并发布。

验收同时修复了两项实际基础问题：Windows 深层 sibling 路径导致候选复制超过 `MAX_PATH`；失败候选虽已产生新归因，但旧控制流没有将它作为下一轮 repair parent。现在候选快照使用短路径暂存，失败候选的新轨迹和新归因可继续驱动下一轮修复；如果修复预算耗尽仍未通过，则回滚到批次开始时的 Harness，不会误发布未验证候选。

验收产物位于 `.evobench_runs/rsi_claw20/t027_v3`。当前定向回归共 `183 passed, 2 skipped`，覆盖单 Harness 控制流、候选反馈、Analyzer、Evo-Bench launcher/evaluator/optimizer 和运行入口。

## Claw20 首轮优化缺陷修复（2026-08-13）

首轮 20 题 RSI 运行暴露出两个控制面缺陷，现已在不改变单 Harness 主流程的前提下修复：

1. Evo-Bench Optimizer 虽然按当前 Issue 选择了分析记录，却仍把整份 optimization hypotheses 交给候选生成模型。现在 evidence bundle 只保留 `optimization_issue_ids` 对应的 hypothesis；缺失目标 hypothesis 会直接报错，避免其他题目的修复要求污染当前候选。
2. `max_repair_rounds_per_batch` 曾同时限制“一个 Issue 的修复深度”和“整批可以尝试的 Issue 数”，导致第一个失败 Issue 消耗预算后，其余问题得不到候选。现在新增独立的 `max_issue_attempts_per_batch`，第一分析轮会广度优先覆盖不同 Issue，之后才从失败候选中选择 repair parent；`max_repair_rounds_per_batch` 只限制单条修复链深度。

Evo-Bench 入口默认设置 `max_issue_attempts_per_batch=8`、`max_repair_rounds_per_batch=1`。因此当前 Claw20 一轮最多尝试 8 个不同问题，每个问题先执行一轮候选搜索；不会再由第一道题独占整批预算。定向回归结果为 `184 passed, 2 skipped`，并新增了“首个候选失败后仍覆盖第二个 Issue”以及“无关 hypothesis 不进入候选请求”的测试。

## 本地40题无Key实验（2026-08-14）

可见验证集只有32道 General/Claw，无法构成40道纯 General。当前本地40题固定集因此采用32道 Claw和8道 GDPval Office；APEX与全部Search任务被排除。该组合使用WSL本地隔离，不读取 `E2B_API_KEY` 或 `SERPER_API_KEY`。

General任务仍执行三次并采用严格 Pass^3；Office任务执行一次并采用官方单次通过判定。混合实验主指标记为 `strict_task_pass_rate`，同时保留分域通过率和原生连续分，不冒充官方三域宏平均。一次H0共执行104次 Rollout。

```powershell
uv run python examples/rsi/run_evobench_single_harness.py `
  --run-name rsi_mix40_p0_v1 `
  --source-run .evobench_runs/local_mix40/local_mix40_h0_v1 `
  --auto-baseline `
  --baseline-task-count 40 `
  --baseline-sample-seed 20260812 `
  --output-dir .evobench_runs/rsi_mix40 `
  --batch-size 2 `
  --max-epochs 1 `
  --sibling-candidate-count 2 `
  --max-issue-attempts 8 `
  --max-repair-rounds 1 `
  --rollout-concurrency 5
```

40题 Suite 和 Manifest 已生成到 `.evobench_runs/local_mix40/local_mix40_h0_v1`；本地运行时、模型端点和 GDPval 资产预检均已通过。

完整 H0 只作为冻结的全局基线。RSI Epoch 内继续按小 Batch 顺序执行，每个 Batch 完成评测、诊断、候选生成和局部验证后，Working Harness 才进入下一 Batch；Epoch 结束时再做一次40题全量检查并与 H0 成对比较。该结构保留原单 Harness 优化节奏，同时避免把已经优化过的中间结果误报为基线。
