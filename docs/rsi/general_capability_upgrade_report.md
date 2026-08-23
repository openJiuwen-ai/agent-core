# RSI 通用优化能力改造报告

## 1. 目标

本轮改造不向任务 Harness 写入数据集或 Case 专用答案，而是提升 RSI 核心链路的通用能力：

1. 从统一、可追溯的证据中形成覆盖全部失败要求的因果诊断；
2. 将根因路由到 Prompt、Skill、Tool、Rail 或预算策略等真实可修改对象；
3. 将候选的预期行为和实际行为差异回流到同一 Case 的下一次修复；
4. 使用严格任务分决定晋级，使用连续分和逐项变化辅助选择修复父版本。

本阶段冻结 Improver 自身进化，不在主循环中更新候选生成、排序或预算策略；先验证
Harness 进化链路本身能稳定地产生、执行和验证有效候选。

## 2. 上一轮基线

证据运行：`rsi_office64_causal_analyzer_v11`。

- 32 个有效 APEX Case：6 个首次通过，26 个失败；
- 26 个失败 Case 中仅 5 个产生可执行 Issue，Issue 覆盖率为 19.2%；
- 14 个诊断因证据冲突被拒，7 个保持未归因；
- 5 个 Case 共生成 6 个候选，5 个候选改变了目标行为，1 个候选完整通过；
- `3/9 -> 8/9` 等逐项改善未被候选反馈链识别；
- 唯一局部通过候选在目标 Case 上可复现，但未证明非目标题泛化；
- 全量 checkpoint 被 32 个 GDPVal 基础设施跳过污染，不能作为有效泛化结论。

## 3. 实施阶段

### P0：统一评分与候选反馈

- [x] 将 SWE `FAIL_TO_PASS/PASS_TO_PASS`、WorkBuddy `atomic_checks`、Evo-Bench
      `judge_detail.criteria` 规范化为统一 requirement results；
- [x] 修复 paired feedback 与压缩器字段不匹配；
- [x] 保留严格分、连续分、逐项变化、干预预测和激活证据；
- [x] 残余失败项变化时不得仅因原 Issue 相似而停止修复。

### P1：Analyzer 通用归因能力

- [x] 逐项建立失败要求清单；
- [x] 一个 Case 允许输出多个独立或有依赖关系的根因；
- [x] 证据不足时在当前 Case 内执行定向补证；
- [x] 输出 Issue 前检查反事实是否覆盖所声明的全部失败要求；
- [x] 不可用证据不得进入支持集合。

### P2：Improver 通用干预能力

- [x] 根据根因和当前 Harness 能力修改真实对象；当前 DeepAgent PolicyHarness 支持
      Prompt、执行预算和已声明的 `tool_loop_compaction` Rail；
- [x] 不允许把 Tool/Skill 能力缺口静默降级成 Prompt 补丁；
- [x] Prompt、预算和 Rail 严格按 Analyzer 目标路由，禁止 Prompt 根因顺手修改预算；
- [x] 候选绑定受支持的因果假设，并声明触发条件、行为变化、可观察结果和适用边界；
- [x] 禁止把 Case ID、固定答案、评分器文本写入长期 Harness；
- [x] 候选部分改善时以该候选为修复父版本，继续处理残余要求。

### P3：Improver 自身进化（本阶段冻结）

- [x] 将候选实验聚合为跨 cohort 的稳定机制模式；
- [x] 版本化修改候选生成、排序和预算策略；
- [x] 每个 Improver 候选只包含一个可归因策略变化；
- [x] 支持使用未参与策略生成的冻结 checkpoint 对比 `I_t` 与 `I_t+1`；
- [x] 代码层只允许 live paired meta-validation 合格的 Improver 晋级；
- [ ] 在 Harness 进化稳定后，再决定是否启动 `I_t -> I_t+1` 发布实验。

## 4. 验收规则

每个阶段必须依次完成：

1. 定向单元测试和兼容回归；
2. 使用上一轮 32 个 Case 的保存产物进行确定性回放；
3. 至少一次真实 Analyzer 或 Improver 模型请求；
4. 检查模型输出是否实际满足新协议，而不是只验证请求成功；
5. 更新本文档的结果、失败原因和剩余风险。

严格任务通过仍是 Harness 晋级依据。连续分、逐项变化和行为激活只用于因果判断、
修复父版本选择和下一轮监督，不用于降低晋级标准。

## 5. 当前状态

- 改造开始日期：2026-08-22；
- 当前阶段：P0-P2 主链已完成定向验收；PolicyHarness 可改 Prompt、预算和其运行时
  明确声明的工具循环压缩 Rail，Tool/Skill 仍因没有安全可写接口而诚实拒绝；
- P3 代码作为独立基础设施保留，但当前实验不传 `improver_policy_ref`、候选数固定为 1，
  不运行 Improver Policy 的生成、比较或发布；
- 本轮尚未宣称 Analyzer、Improver 或任务最终分数已经提升。

### 5.1 改造前真实模型基线

使用 GLM-5.2 对上一轮 `3/9` 的失败 Case 重新执行当前 Analyzer，而不是只做离线
规则检查。模型正确提出了“工资/工资加福利口径”和 Cedar Rapids 残余计算两类假设，
并完成 10 次定向证据请求；但最终把尚未闭合的残余假设与已支持问题一起输出，触发
确定性因果完整性校验，结果仍为 `evidence_conflict`，没有生成可执行 Issue。

这个基线说明问题不只是输入压缩：模型已经看见关键证据，但当前协议不能稳定地把
“已证实的局部根因”和“仍待补证的残余根因”分开提交。P1 的验收目标因此明确为：
保留已支持 Issue，同时将未闭合部分留在当前 Case 内继续补证，不能让一个未闭合分支
使整个 Case 的诊断归零。

### 5.2 P0 回放与在线验收

- 历史 B3 候选已从旧的“strict 仍失败”恢复为逐项实验信号：`3/9 -> 8/9`、
  新通过 5 项、剩余失败 1 项、无回退，明确标记为部分改进；
- paired feedback 能区分“候选 Harness 已执行”和“Prompt 行为是否真正触发未知”，
  不再用运行成功冒充因果机制已激活；
- 使用真实 GLM-5.2 读取压缩后的 B3 反馈，模型正确识别 strict 未变化、连续分
  `+0.555556`、新通过 5 项和剩余 1 项，并选择继续诊断残余要求；
- 相同 Issue 文本在残余 requirement 集合变化后会得到新语义签名，可继续修复；
- Evo-Bench PolicyHarness 若收到 Tool/Skill 或未声明、不可安全修改的 Rail 根因，会输出
  结构化 `unsupported_surface`，不调用模型伪造 Prompt 补丁，也不执行无意义候选评测；
  已声明的 `tool_loop_compaction` Rail 只允许修改三个受控配置字段。

### 5.3 P1 真实 Analyzer 验收

对 B3 和 B8 分别发起了新的 GLM-5.2 Analyzer 请求，检查的不是调用是否成功，而是
最终归因是否符合证据：

- B3 不再把“已支持局部原因”和“未解决残余”混成一个 `evidence_conflict`。模型拆出
  两个独立失败簇；进一步核对公开任务文本后发现工资口径确实没有定义清楚，因此保留
  为 `unassigned/insufficient`。该结果没有生成可执行 Issue，但避免了把评分器隐含口径
  写进长期 Prompt；
- B8 生成 1 个可执行 Issue，完整解释 3 个失败要求。模型确认 Agent 已读到合同中的
  `equals or exceeds`，却只应用了另一句的 `exceed`，并将根因路由到
  `member_harness.policy_harness.prompt`。输出含受支持假设、完整因果链、反事实和
  `task_sufficient` 覆盖结论。

这组对照证明新协议不会“一律输出 Issue”或“一律保守”：证据歧义时保持未知，证据
闭合时产出可执行根因。

### 5.4 P2 真实 Improver 验收

使用 B8 的受支持假设和默认 I0 执行真实 GLM-5.2 Improver 请求，不启用 Improver
Policy 进化。`generic_behavior_intervention_v3` 生成了一个带公开触发条件、操作步骤和
范围边界的可迁移规则：遇到同一文本中服务于不同目的的多个阈值操作符时，分别映射
规则再计算。该规则保留了必要的合同解析行为，但不含 Case ID、文档名、主体名、固定
金额、标准答案或评分器文本，只修改 `system_prompt.md`。这比强行要求“完全领域无关”
更具体，也比记住当前题目更可迁移；仍需冻结 Case 验证后才能宣称泛化。

预算目标也已改为只修改 `harness.json` 的受限预算字段，不再同时伪造 Prompt 变化；
Rail 目标只有在当前 Harness 明确声明 `tool_loop_compaction` 时，才允许修改
`enabled/consecutive_threshold/bailout_threshold`；任何 Python 执行文件保持字节不变。
Tool、Skill 等当前适配器不能修改的对象会在生成模型候选前明确拒绝。

进一步的真实模型验收发现并修复了一个控制器 Bug：Prompt-only 候选过去会因重新序列化
而改变 `harness.json` 哈希。修复后真实候选只改变 `system_prompt.md`，配置和 Python 文件
均字节一致。

Improver 输入也由“结构化但重复”改为引用驱动的证据投影：保留 Analyzer 引用的动作、
完整关键窗口、失败要求、决策合同、反事实和候选反馈；删除重复的原始 result/trace、
完整假设元数据和 retroactive check。相同真实请求的输入 token 从 `31,857` 降至
`12,287`，下降约 61%，模型仍生成了正确的单一 Prompt 干预。最终持久化文本不含
Case ID、任务固定数值和该法律样本的命名概念，只使用 primary rule、inclusion rule、
item、aggregate 等抽象角色。

### 5.5 P3 状态

候选 ledger 和版本化 Policy 代码保留，但本阶段不会从 ledger 生成新 Policy，也不会
在单 Harness 主循环中发布 `I_t+1`。当前运行使用默认 I0、候选数 1，避免候选排序和
Improver 自进化干扰 Harness 进化有效性的判断。

### 5.6 自动化验收

- RSI 全量单元测试共 `1009 passed, 3 skipped`；
- RSI 目录 Ruff check、本轮 31 个 Python 变更文件的 Ruff format check，以及
  `git diff --check` 均通过；
- 三条 skip 为已有的可选集成场景，不是本轮失败；
- 尚未重跑完整 64 题，因此当前结论是“优化链路能力增强已通过定向验收”，不是
  “Evo-Bench 总分已经提升”。

## 6. 方案一致性复查（2026-08-22）

本节记录实现后反向审计发现的偏差，优先级高于前文阶段性结论。

1. Analyzer v7、统一 requirement results 和 paired feedback 中没有 B3、B8、APEX
   ID、固定金额或标准答案等 Case 常量；两次真实模型验收产物也没有把这些内容写入
   长期 Harness。
2. Evo-Bench 启动器、Evaluator 和 PolicyHarness Adapter 保留数据集协议，这是适配层
   的职责；核心控制器现只消费统一 `requirement_results`、`optimization_signals` 和
   `public_task_contract`，不再直接解释 Evo 连续分或公开任务字段。
3. 无可执行 Issue、缺少目标、证据不足、无目标 Case 或 supported hypothesis 未闭合时，
   Hypothesis Compiler 和 PolicyHarness Optimizer 都不会生成候选，旧 Prompt fallback
   已删除。
4. 只有显式 budget/execution_budget 才能修改预算；普通 Config 根因会被标记
   `unsupported_surface`。Prompt-only 候选不能携带预算或 Rail 更新。
5. Prompt 泄漏保护会拦截 Case ID、已标注答案、任务实体、固定数值和私有 Tool 字段；
   持久化指令还被要求使用抽象行为角色。未知且未标注实体仍是剩余风险，因此最终全量
   实验仍需要候选文本审计和冻结集验证。
6. Improver evolution 当前由 `examples/rsi/evolve_improver_policy.py` 独立执行。单 Harness
   主循环会写 Candidate Feedback Ledger、也会消费显式传入的 Policy，但不会自动执行
   跨 cohort 分析、Policy 提议、paired meta-validation 或发布。因此尚不能称为运行时
   闭环自进化。

### 6.1 查漏补缺结果

反向追踪实际运行入口、Analyzer、Hypothesis Compiler、Improver、Candidate Gate 和
Epoch Checkpoint 后，又修复了以下通用链路偏差：

1. **基线与 Batch 流程混淆**：WorkBuddy 入口仍可按需冻结完整 H0；Evo-Bench 正式
   入口不再额外执行一遍 64 题 H0，而是把每个 Batch 的 source 执行作为该次干预的
   成对基准，并在 Batch 结束后立即分析和优化。候选已通过的目标 Case 进入能力保留
   集合，Epoch 末只对最终 Harness 做全量回归与发布检查。
2. **诊断被候选预算误截断**：两个入口的 `max_issues` 改为
   `max(20, batch_size * 6)`；候选评测预算仍由独立字段控制。Analyzer 可以保留多根因，
   不会因为本轮只评少量候选就丢掉其余诊断。
3. **预算和 Rail 路由不可达**：Analyzer 的合法目标集合补入显式
   `execution_budget` 和 `rail`，Lever Compiler 同步识别；普通 `config` 不能冒充这两类
   根因。
4. **未知判定被写成失败**：旧 WorkBuddy `atomic_checks` 缺少 `passed` 时现在保持
   unknown，不再伪造 `false` 监督信号。
5. **假设绑定过松**：Hypothesis Compiler 与 PolicyHarness Optimizer 都要求至少一个
   supported hypothesis。`confirmed` 结论仍不允许混有 unresolved；但
   `supported_hypothesis/local_contributor` 可以保留未解决备选，Compiler 只把已经支持的
   局部机制交给候选生成，未解决部分继续留在审计和补证链路。多个 supported hypothesis
   时模型必须明确选择一个。
6. **候选合同不可证伪**：`rationale` 和 `expected_effect` 现在是必填字段；Analyzer
   原始反事实与 Improver 自己的预期结果分别保存，下一轮不能偷偷用代理目标替换原预测。
7. **无关基础设施错误污染目标实验**：Candidate Gate 只在源运行的目标 Case 出错时
   判为 inconclusive；同 Batch 的无关 Case 错误不再阻止有效的成对实验。
8. **Opaque Snapshot 部分保留不安全**：同一 Epoch 中若整包候选出现部分保留、部分
   拒绝，当前实现会原子拒绝该组，而不是让被拒候选的字节混入已发布 Harness。以后若要
   提高保留效率，需要单独实现可重放的 snapshot composer。
9. **生成文本乱码风险**：候选 Prompt、理由和预期结果会拒绝 Unicode replacement
   character 与非法控制字符，要求模型重新生成干净文本。真实 GLM-5.2 请求已通过该校验。
10. **同目录数据集污染**：Batch Loader 过去会忽略请求中的 `dataset_files`，扫描同目录
    全部 JSON；现在优先只加载冻结文件，并校验请求 Case 没有重复或遗漏。训练集、验证集
    或不同子集放在同一目录时不会再被静默混跑。
11. **单个候选失败中断全实验**：模型输出、适配器生成或候选评测在某个候选上失败时，
    现在分别记录 `generation_error` 或 inconclusive evaluation 并继续剩余 Batch；错误信息
    会先脱敏，API Key 不会写入 Candidate Gate。源评测、冻结输入和 checkpoint 不一致仍然
    保持硬失败，因为这些情况下没有可信的比较基准。
12. **同一 Harness 随机复测制造假提分**：过去即使没有候选通过，Epoch 末尾对同一
    Harness 的随机复测也可能覆盖 `best_score`。现在只有真正保留的候选才能更新 Best；
    无候选复测只作为稳定性记录，不改变冻结 H0 或最终提升值。

真实模型复验结果为：协议 `generic_behavior_intervention_v3`、因果假设绑定 `h2`、只修改
`system_prompt.md`、Analyzer 反事实保留 1 条、静态验证通过、持久化 Prompt 无 Case ID
和固定金额。本次复验只证明候选生成合同有效，尚未执行完整 64 题评分。

### 6.2 仍然保留的边界

1. 当前 Evo PolicyHarness 没有安全的 Tool/Skill 写接口，因此这两类根因会被诚实拒绝，
   不能声称已完成 Tool/Skill 进化。
2. Opaque Snapshot 目前采用全有或全无的保守发布语义，安全但可能丢失可组合的局部收益。
3. 直接调用通用控制器的新适配器可以提供冻结 `baseline_eval_ref_path`，也可以使用
   在线 Batch source 作为成对基准；只有确实需要独立全量 H0 时才设置
   `auto_full_baseline=True`。Evo-Bench 当前采用在线 Batch 模式，不启用该开关。
4. 尚未重跑完整 64 题，所以不能把单 Case 的真实模型验收当作 SOTA 或总分提升证据。

下一步不进入 Improver evolution。先用当前单候选配置重跑 Harness 优化，统计可执行
Issue 覆盖率、候选实际触发率、逐项改善率、严格通过率和冻结集保留率；再根据真实失败
判断是否值得为当前 PolicyHarness 增加可验证的 Tool/Skill 注入接口。

## 7. 低候选覆盖率的完整通用修复（2026-08-22）

对当前 64 题运行已完成的前 23 个 Batch 做产物级审计后，确认低分的首要瓶颈不是
Candidate Gate 太严，而是失败 Case 很少进入候选实验：旧链路大量记录为
`evidence_conflict`，已有 Issue 又会因一个 unresolved 备选被 Compiler 整体丢弃。

本轮按通用合同完成四项修复：

1. Analyzer 在模型修复仍不合格时执行单调的确定性协调：未知请求、不可用证据、缺少
   数值差分或从标准结果反推原因的假设只会降级为 unresolved；不会提升证据，也不会把
   其他有效局部根因一起清空。遗漏的失败要求会显式保留为 unassigned residual。
2. `inspect_artifact` 只搜索真实落盘文件；评分说明和 result metadata 改由
   `inspect_evaluation` 查询。远程 APEX 运行结束时，将 `/filesystem` 中受限类型、数量和
   总大小的任务文件随 rollout 下载，再复制进 Case 的 `artifacts/workspace`。取不到文件
   时明确为 `not_available`，不能再用评分文字冒充文件证据。
3. supported local contributor 即使存在尚未解决的备选原因，也能进入 Hypothesis
   Compiler；confirmed 结论仍维持完整闭合要求。历史前 23 个 Batch 的相同 Analysis
   产物回放中，可执行假设由 3 个增加到 7 个，B7、B8、B13、B23 的入口被恢复。
4. 没有候选时不再统一写成 `no_residual_issues/rejected`。状态会区分
   `no_actionable_analysis_issues`、`no_executable_hypotheses`、
   `no_applicable_hypotheses_for_active_cases` 和 `repeated_issue_detected`，并记录最后一轮
   Issue/Hypothesis 数量。

真实 GLM-5.2 使用旧 B1 的两个失败 Case 复验：旧产物为两个 `evidence_conflict`、0 个
Issue；新链路将证据不足的租约 Case 保持为 unassigned，同时为超时且未产出工作簿的
Case 生成 1 个 confirmed Prompt Issue，随后成功编译 1 个优化假设并生成 1 个只修改
`system_prompt.md` 的候选。该复验验证了 Analyzer 到 Improver 的完整可达性，但没有执行
目标 Case 的新一轮官方评分，因此不把它表述为已经提分。

自动化验收更新为 RSI 全量 `1014 passed, 3 skipped`，Ruff、Python 编译、远程证据脚本
WSL 冒烟和 diff check 均通过。当前仍不启用 Improver Policy 自进化。
