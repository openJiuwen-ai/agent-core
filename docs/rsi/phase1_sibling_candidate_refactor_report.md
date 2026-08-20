# RSI 第一阶段改造报告：同父候选实验层

## 1. 文档状态

- 开始日期：2026-08-10
- 完成日期：2026-08-10
- 当前状态：第一阶段代码改造与定向验收完成
- 改造范围：单 Harness 迭代优化控制面
- 核心目标：从同一父 Harness 生成并独立验证 K 个 sibling candidates，保存完整候选级反馈
- 本阶段不包含：Improver 自身修改、`I_t -> I_t+1`、跨代递归实验

本文档是第一阶段的持续改造记录。后续修改设计、代码、实验口径或证据边界时，必须同步更新本文档。

## 2. 阶段结论

第一阶段已把原来的“生成一个、评测一个、通过后立刻作为下一次父版本”的顺序爬山流程，改成了“冻结一次 improvement instance，先生成 K 个同父候选，再统一评测并只选择一个 winner”的实验流程。

原有单 Harness、单候选优化仍作为原子能力保留。`K=1` 时不启用 sibling 差异化 Prompt，只执行一个候选，并继续使用原有的 Analyzer、MemberOptimizer、target-local gate、provisional promotion、epoch full checkpoint 和发布链路。新增 cohort manifest 和 Candidate Feedback Ledger 只提供实验身份与记录，不替代这条原子优化链路。

一次 improvement instance 现在保证：

1. 冻结父 Harness、source evaluation、分析结果、优化假设和目标 Case 集合。
2. K 个候选全部从同一父 Harness 生成，并使用隔离的输出目录和 Planner session。
3. 本 instance 内后生成的候选只能看到前序 sibling 的静态方案摘要，不能看到执行结果、分数、Verifier 结果或拒绝信息。
4. K 个静态方案全部生成后，才计算并冻结 `predicted_rank`；生成序号不冒充预测排名。
5. 排名冻结和方案摘要落盘后才开始任务评测，评测期间不改变父 Harness。
6. 全部候选完成后，先按现有 primary gate 判断资格，再按真实 target-local 结果选择至多一个 winner。
7. 只有 winner 进入 provisional promotion、residual evaluation 和 epoch full checkpoint。
8. 所有候选均进入 Candidate Feedback Ledger，不只保留 winner。

## 3. 改造前问题

改造前，`IterativeSingleHarnessOptimizer` 对每个 Issue 执行以下流程：

1. 从当前 `current_refs` 生成一个候选。
2. 立即评测该候选。
3. 候选通过后立刻更新 `current_refs`。
4. 后续 attempt 从已经改变的 Harness 继续生成。

因此，多个 attempt 的父版本、source evaluation 和目标集合可能不同，不能作为同一次 Best-of-K 搜索，也无法严谨计算 Top-m Gain 或 Selection Regret。已有 `optimization_journal.yaml` 还是 capability 展开的经验记录，并不是完整的候选实验账本。

## 4. 最终设计

### 4.1 Improvement Instance

每个 cohort 固定并持久化以下身份：

- `cohort_id`
- `parent_harness_refs_path`
- `source_eval_ref_path`
- `analysis_ref_path`
- `optimization_hypotheses_path`
- `source_issue_id` 和稳定 Issue signature
- `frozen_target_case_ids`
- `requested_candidate_count`
- evaluation protocol 和 ranking policy

`cohort_id` 同时包含父版本、source evaluation、分析结果和目标集的摘要。恢复运行时还会再次校验 manifest 身份；身份不一致时直接失败，不会把旧候选错误挂到新父版本下。

### 4.2 候选生成与隔离

- 配置项：`member_optimizer.sibling_candidate_count`，默认值为 1。
- WorkBuddy CLI：`--sibling-candidate-count`。
- 环境变量：`WORKBUDDY_SIBLING_CANDIDATE_COUNT`。
- 每个候选使用独立 MemberOptimizer 输出目录。
- 每个候选的 Planner session 由 `candidate_id` 派生，避免 session 历史串扰。
- 所有候选强制 `defer_publish=True`，不会在生成阶段发布。
- 后续 sibling 只获得前序 sibling 的白名单静态字段；运行结果类字段不会进入 Prompt。
- 无法形成真实差异时允许留下可识别的重复方案，不通过跨 lever 修改伪造多样性。

### 4.3 执行前排序

所有候选生成完成后，`static_priority_v1` 才根据以下静态特征冻结排名：

- 是否可执行
- 对冻结目标集的覆盖率
- action 数量与原子性
- 语义指纹是否与更早候选重复
- `candidate_index` 作为稳定 tie-breaker

语义指纹忽略 action ID、文件名和 runtime name 等表面噪声，保留 action group、operation、target family、expected effect、lever 和 hypothesis 等方案语义。

### 4.4 独立评测与 Winner

第一版采用串行评测，优先保证实验语义正确。每个可执行候选使用同一父 Harness、同一 source evaluation 和同一冻结目标集执行 target-local gate。

Winner 必须先通过 primary gate，再按以下真实结果依次比较：

1. candidate target score
2. target score delta
3. Verifier progress Case 数
4. 更少的 actions
5. 更高的执行前 predicted rank

未被选中的合格候选记录为 `superseded`，不会被误计为失败；没有合格候选时，父 Harness 保持不变。

### 4.5 Candidate Feedback Ledger v1

系统新增 `candidate_feedback_ledger.yaml`。每个 cohort 保存：

- 冻结的 cohort 身份与候选总数
- 每个候选的语义指纹、预测分数和预测排名
- source/candidate target score 与 target gain
- target regression、紧凑 Verifier 摘要和证据引用
- skill/tool 的 pre-edit trigger 观测
- winner 和 Top-m 设置
- Best-of-K Gain、Top-m Gain、Selection Regret

指标只在 `K >= 2` 且父版本、source evaluation、目标集、协议、冻结排名和 target score 全部可比时计算。条件不满足时写入明确的 `unavailable` 原因，不补造数字。

## 5. 代码变更

| 文件 | 变更 |
| --- | --- |
| `openjiuwen/rsi/config/config.py` | 增加 K 配置、解析和合法性校验 |
| `examples/rsi/run_workbuddy_office_single_harness.py` | 增加 CLI、环境变量和配置写入 |
| `openjiuwen/rsi/member_optimizer/action_planner.py` | 增加 sibling 静态上下文、字段白名单和 session 隔离 |
| `openjiuwen/rsi/single_harness/candidate_feedback.py` | 新增语义指纹、静态排序和 Ledger v1 构建器 |
| `openjiuwen/rsi/single_harness/iterative.py` | 实现 cohort 冻结、先生成后评测、winner 选择、账本与恢复保护 |
| `tests/unit_tests/rsi/` | 增加配置、Planner、Ledger、CLI 和 K=3 控制流测试 |

状态版本更新为 7，请求链版本更新为 12。`K=1` 的新运行保持原有单候选决策语义，但旧版本运行状态不会被静默复用；跨版本 resume 会因 fingerprint 不一致而明确拒绝。

## 6. 子任务与验收

| 编号 | 工作项 | 负责人 | 状态 | 主 Agent 验收 |
| --- | --- | --- | --- | --- |
| P1-A | 控制流审计、K 配置与 K=3 测试 | 子 Agent：m0_iterative | 完成 | 已审阅并纳入整组测试 |
| P1-B | sibling Prompt 上下文与 Planner session 隔离 | 子 Agent：m0_multi_action | 完成 | 已纠正“生成序号等于预测排名”的语义后通过 |
| P1-C | Candidate Feedback Ledger v1 | 子 Agent：m0_analyzer | 完成 | 已迁入目标仓库、联调并通过测试 |
| P1-D | 控制面整合与恢复保护 | 主 Agent | 完成 | 已完成同父生成、独立评测和 winner 流程 |
| P1-E | 定向测试、Ruff 与报告 | 主 Agent | 完成 | 245 项定向测试及完整 RSI 单元测试通过，Ruff lint 通过 |

## 7. 验收结果

- [x] 配置可显式设置 sibling candidate 数量 K。
- [x] K 个候选使用完全相同的父 Harness、source evaluation 和 analysis refs。
- [x] K 个候选使用独立输出目录和 Planner session。
- [x] 单元测试证明任何候选评测开始前，K 个方案已经全部生成。
- [x] 同一个 cohort 的目标 Case 集合在所有候选间固定。
- [x] sibling 评测期间不会改变父 Harness。
- [x] 只让真实 utility 最优且满足 gate 的候选进入 promotion checkpoint。
- [x] 其他合格候选标记为 `superseded`，而不是伪装成 rejected。
- [x] Ledger 保存所有候选的预测排名、真实结果和 winner。
- [x] Ledger 可在证据充分时确定性计算 Best-of-K、Top-m Gain 和 Selection Regret。
- [x] `K=1` 新运行保持单候选关键行为，新的 cohort manifest 可恢复生成结果。
- [x] 定向单元测试与 Ruff lint 通过。

定向测试覆盖 5 个直接相关测试文件，结果为 `245 passed`；完整 `tests/unit_tests/rsi` 回归结果为 `769 passed, 3 skipped`。仅出现 2 条仓库已有的 Pydantic V2 弃用警告。本阶段新增的 Ledger 文件及测试已通过 Ruff format 检查；未对已有大型文件执行全文件格式化，以避免混入无关格式改动。

## 8. 证据边界与已知限制

| 项目 | 第一阶段口径 |
| --- | --- |
| Prompt/Rail trigger | 当前没有独立触发事件，Ledger 标记为 `not_instrumented`，不写 false |
| Skill/Tool trigger | 只记录目标 Case 中 edit 前是否实际调用，不宣称这是唯一因果原因 |
| 非目标回归 | 候选 gate 为 target-local，标记为 `not_evaluated`；只有 winner 的 epoch checkpoint 覆盖全量回归 |
| 候选成本 | 当前未形成可靠的候选级 token/cost 归属，标记为 `not_instrumented` |
| 不可执行候选 | 仍进入账本，但缺失真实 target score 时 cohort 搜索指标会明确不可用 |
| 随机噪声 | 单次运行只提供一次 realized outcome，不证明稳定因果收益 |
| 评测并行化 | 当前串行执行，尚未引入共享容器和全局资源并发风险 |
| 中途恢复 | 已生成方案可由 manifest 复用并校验身份；中断后的候选任务评测仍可能重新执行 |

## 9. 使用入口

WorkBuddy 运行时在原命令上增加以下参数即可启用同父 K 候选：

```powershell
--sibling-candidate-count 3
```

默认值仍为 1。建议第一轮真实实验先使用 `K=3`、一个小 batch 和一个 epoch，检查 `cohort_manifest.yaml`、`candidate_feedback_ledger.yaml`、candidate gates 和最终 checkpoint，再扩大数据量。

原有单 Harness 原子优化可以继续使用原命令，也可以显式指定：

```powershell
--sibling-candidate-count 1
```

该模式是一父一候选，不执行 Best-of-K 搜索。由于状态和请求指纹已升级，改造前已经产生的旧 run 不能直接 `--resume`；需要使用新的输出目录启动一次新运行。

## 10. 后续阶段入口

第一阶段解决的是 Harness 候选搜索和反馈采集基础设施，还不是 Improver 自我改进。下一阶段可以在不改变本阶段实验口径的前提下，使用 Ledger 的跨 cohort 数据训练或改造候选生成与执行前排序，并通过固定 Meta-Test 比较 `I_t` 与 `I_{t+1}`。

## 11. 变更日志

### 2026-08-10

- 建立第一阶段改造报告并完成旧控制流审计。
- 完成 K 配置、同父候选生成、执行前静态排序、独立评测和 winner 选择。
- 完成 Candidate Feedback Ledger v1 和严格的指标可用性边界。
- 增加 cohort manifest 身份校验，避免恢复时误用不同父版本的候选。
- 完成 245 项定向测试、完整 RSI 单元测试（769 passed）和 Ruff 验收。
