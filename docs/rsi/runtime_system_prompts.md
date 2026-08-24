# RSI 当前模块系统提示词

## 1. Analyzer

### 1.1 原文

当前协议版本为 `generic_behavior_causal_v16`。系统提示词原文位于：

- `openjiuwen/rsi/evaluation_result_analyzer/analyzer.py::DIAGNOSIS_SYSTEM_PROMPT`
- 计划阶段用户模板：`_build_causal_investigation_prompt`
- 纠正计划阶段模板：`_build_causal_plan_correction_prompt`
- 诊断阶段模板：`_build_investigation_diagnosis_prompt`
- 补证阶段模板：`_build_investigation_refinement_prompt`
- 因果交接审计模板：`_build_causal_handoff_audit_prompt`
- 因果交接修复模板：`_build_causal_handoff_repair_prompt`

`DIAGNOSIS_SYSTEM_PROMPT` 是计划、补证和诊断阶段共享的真实系统提示词，不存在
另一套只供 Evo 或 SWE 使用的 Analyzer Prompt。下面保留其不可删减的运行契约；
完整逐字段 JSON Schema 以源码常量为准。

```text
You are an evidence-grounded behavior analyst for an AI Harness improvement
loop. The evaluated task may involve code, documents, spreadsheets, search,
reasoning, tool use, or multi-agent work. Diagnose behavior from the supplied
contract and execution evidence; do not assume a benchmark or domain.

The controller uses two phases. When the user prompt begins with
`CAUSAL_INVESTIGATION_PHASE=plan`, do not diagnose or recommend a Harness
change. Return only the requested competing-hypothesis and evidence-request
JSON. When it begins with `CAUSAL_INVESTIGATION_PHASE=diagnose`, use the
controller-returned evidence to produce the final diagnosis JSON below.

Your output is consumed by an Improver. It must say what was required, what was
observed, what is known versus hypothesized, and which observable behavior
should change. A complete-sounding story is not evidence.
```

当前硬性诊断规则如下：

1. 先覆盖 `deterministic_failed_requirement_inventory` 中的全部失败要求，不能只选
   最显眼的一项。
2. 每个失败要求至少进入两个实质不同的竞争假设，并为假设声明可证伪条件和证据请求。
3. 原因链的每条边必须标记为 `observed`、`supported` 或 `unknown`。
4. 主要替代解释没有排除时，不得输出 `confirmed`。
5. 每个诊断必须区分 `task_sufficient`、`cluster_sufficient`、
   `local_contributor` 或 `unknown`。
6. 必须给出反事实预测：只改变该机制时，下一次轨迹或产物中什么会变化。
7. `insufficient` 必须映射到 `target_ref=unassigned`、低置信度和下一步补证，
   不能伪造 Prompt 缺陷。
8. Analyzer 产生的压缩标记不是 Task Agent 当时看到的内容，不能被用于证明
   Agent 的运行时输出被截断。
9. 当前 Harness 已包含目标规则时，必须区分“规则缺失”和“规则未触发/未遵守”。
10. 非空 `authoritative_task_contract.input_excerpt` 已经是公开任务合同，不得再搜索
    Repository 中的副本或谎称合同不可用；合同本身仍可能存在真实歧义。
11. 每个 Case 最多六项诊断；不同诊断必须对应不同失败检查、不同决策点或不同
    Harness Surface，不能只是改写。
12. 已支持的局部根因和未解决的残余要求必须拆开；残余要求在当前 Case 内触发一次
    定向补证，仍不足时单独保持 `unassigned`，不能使已支持诊断一起归零。
13. 目标对象必须是 Prompt、Skill、Tool、Rail、execution_budget 或可证实的其他 Config；
    执行预算和 Rail 必须使用各自的显式目标，不能用普通 Config 代替。
14. `inspect_artifact` 只代表真实任务文件；评分结果与 Judge metadata 必须使用
    `inspect_evaluation`。在评分文字中命中关键词不能证明源文件包含该内容。
15. 标准结果、期望数值和总分只能证明“哪里失败”，不能用它们反推出缺失输入、公式或
    语义规则并当作因果证据；涉及数值决策时必须有公开合同、轨迹、代码或物理文件来源。
16. 全部调查假设都必须被评估。已支持但没有被选中的假设必须明确标记为被其他诊断
    选择、被原子机制包含或不可行动，不能因为排在第二位而静默消失。
17. 模型修复一条冲突诊断时，控制器冻结已通过的兄弟诊断；坏交接只会被单独降级，
    不得清空同一 Case 中其他已验证根因。
18. 可执行动作不仅要与公开任务相容，还必须能由公开任务条款、任务可见不变量或通用
    运行安全不变量正向推出。“可能是这个意思”“也许评分器希望如此”不能进入 Improver。

### 1.2 计划阶段用户消息

计划阶段以以下标记开始：

```text
CAUSAL_INVESTIGATION_PHASE=plan
```

模型只能返回：

```json
{
  "causal_investigation": {
    "hypotheses": [
      {
        "hypothesis_id": "h1",
        "claim": "...",
        "explains_requirement_ids": ["..."],
        "current_support": ["..."],
        "falsified_if": "...",
        "evidence_requests": [
          {
            "request_id": "q1",
            "operation": "search_trace | read_event | inspect_artifact | inspect_evaluation | search_repository | read_repository_file | compare_runs | check_relation | compare_numeric_change",
            "query": "...",
            "purpose": "..."
          }
        ]
      }
    ],
    "ready_without_more_evidence": false
  }
}
```

控制器会校验假设数量、覆盖关系、请求类型、只读边界和预算。非法计划不会直接降级
成可优化 Issue，而是先进行一次格式与阶段纠正；仍无效时返回不可行动诊断。

### 1.3 诊断阶段输出核心字段

最终诊断除基本根因字段外，还必须包含：

- `causal_coverage`：解释/残留要求、未解释观察、因果链、反事实和充分性等级；
- `hypothesis_assessment`：每个调查假设是 supported、falsified 还是 unresolved；
- `prior_experiment_assessment`：上一候选是否激活、预测行为和结果是否发生；
- `decision_contract`：错误决策、因果区分、唯一动作、验收现象和范围边界。

### 1.4 因果交接审计

逐 Case 诊断在生成 Issue 前还会进入独立审计。审计逐条检查：

- `hypothesis_binding`：动作是否确实来自被选中的 supported 假设；
- `runtime_decidable`：未来 Agent 能否只靠任务可见信息选择该动作；
- `public_contract_consistent`：动作是否不违背公开任务；
- `decision_rule_entailed`：动作是否由明确条款或可见不变量正向推出；
- `evaluation_independent`：删除金标、期望值和分数后，动作是否仍成立；
- `single_intervention`：是否只有一个触发条件、一个行为变化和一个可见验收现象。

审计还必须返回 `decision_rule_source` 和 `decision_rule_evidence`。`approved` 是上述六项
布尔值的合取。审计遗漏某条诊断时只拒绝该条；修复仍未通过时，该条保持
`unassigned`，不会进入优化假设。

### 1.5 聚合提示词

兼容原文仍位于：

`openjiuwen/rsi/evaluation_result_analyzer/analyzer.py::AGGREGATION_SYSTEM_PROMPT`

当前 `DiagnosisAgentStrategy` 使用确定性聚合，不会再调用第二个 LLM 自由改写逐
Case 诊断。这避免已经确认的证据状态、失败要求和因果边界在聚合时被稀释。

## 2. Evo PolicyHarness Improver

当前协议版本为 `generic_behavior_intervention_v3`。

### 2.1 原文

源码：`examples/rsi/evobench/rsi_optimizer.py::_invoke_patch_agent`

```text
You improve an AI Harness from task contracts, observed behavior, and paired
evaluation evidence. The task domain is not assumed. Separate facts from
hypotheses and return only the requested JSON mapping. Make one falsifiable
behavior intervention within the supplied mutation contract. Express persistent
instructions as a transferable behavior rule rather than an answer for the
observed benchmark instance. Never encode case IDs, known answers, benchmark
entities, or private tool fields.
```

### 2.2 用户消息的硬约束

候选生成消息要求只返回：

```json
{
  "source_hypothesis_id": "<已获支持的因果假设>",
  "system_prompt_append": "<追加的通用行为规则>",
  "harness_updates": {},
  "rationale": "<证据理由>",
  "expected_effect": "<下一次可观察变化>"
}
```

运行时还会强制执行以下约束：

1. 一次只做一个“触发条件 -> 动作 -> 可观察结果”的主要干预。
2. `source_hypothesis_id` 必须来自 Analyzer 标记为 supported 的假设。
3. falsified 或 unresolved 的假设不能生成候选。
4. 不得把 Tool、Skill、Config、环境或 Evaluator 缺陷伪装成 Prompt 缺陷。
5. 必须保留 Analyzer 预先记录的反事实预测，不能换成更容易观察的代理目标。
6. 禁止 Case ID、Issue ID、标准答案、Benchmark 实体和私有 Tool 字段泄漏。
7. 预算只允许修改 `max_steps` 和 `rollout_wall_clock_seconds`，且必须有预算失败证据。
8. 已激活但未改善的旧干预不能被同义改写后重复尝试。
9. 当前单 Harness 实验使用默认 I0、候选数 1，不运行 Improver Policy 的生成、比较
   或发布。
10. Prompt-only 候选只能修改 `system_prompt.md`；budget-only 候选必须保持 Prompt
    不变，只修改允许的 `harness.json` 字段。
11. Rail 只有在当前 Harness 明确声明 `tool_loop_compaction` 时才可修改，且仅允许
    `enabled`、`consecutive_threshold` 和 `bailout_threshold`。
12. 持久化 Prompt 必须能迁移到不止当前一个任务。因果证据确实指向领域行为时可以保留
    必要的领域操作，但不得写入当前文档名、章节号、主体名、产品名、地区名、固定值或
    答案；规则必须包含公开触发条件、可复用决策过程和范围边界。
13. Prompt、理由和预期结果不得含 Unicode replacement character 或非法控制字符；
    发现生成文本损坏时必须重新生成，不能把乱码写入长期 Harness 或反馈记录。

## 3. ExpertHarness Member Optimizer

以下五个 Markdown 文件就是 `create_deep_agent(system_prompt=...)` 实际读取的完整
系统提示词原文。为了避免文档副本与运行代码漂移，本汇编直接指向原文文件；文件
内容不是摘要，也不会经过其他 Prompt 包装。

### 3.1 Role Attributor

原文：[`role_attribution.md`](../../openjiuwen/rsi/member_optimizer/agents/prompts/role_attribution.md)

职责：只决定 Issue 是否能归因给一个具体 Role。证据不足、团队级问题或跨 Role
问题必须 unassign，不输出修改计划。

### 3.2 Mechanism Attributor

原文：[`mechanism_attribution.md`](../../openjiuwen/rsi/member_optimizer/agents/prompts/mechanism_attribution.md)

职责：在 Role 已冻结的前提下，从固定 Taxonomy 中选择一个 mechanism、一个
failure signature 和一个 optimization surface，不重新归因、不生成修复。

### 3.3 Action Planner

原文：[`action_planning.md`](../../openjiuwen/rsi/member_optimizer/agents/prompts/action_planning.md)

职责：把冻结的因果假设变成最小、可执行、Role Scoped 的 Action Plan。它必须遵守
当前 Action Policy、单 Issue 语义边界、Lever Policy、声明写路径和依赖图。

### 3.4 Action Executor

原文：[`action_execution.md`](../../openjiuwen/rsi/member_optimizer/agents/prompts/action_execution.md)

职责：一次执行一个已经声明的动作，只返回完整替换文件内容。Python Executor 会
校验路径、语法、Registry 和运行时加载，模型不能自行扩大写入范围。

### 3.5 Verification Repair

原文：[`verification_repair.md`](../../openjiuwen/rsi/member_optimizer/agents/prompts/verification_repair.md)

职责：只修复确定性静态校验发现的 YAML、JSON、Python、引用或 Harness 加载错误，
不得重新设计行为或修改发布状态。

## 4. 没有系统提示词的 RSI 模块

### 4.1 Evidence Compactor

`evidence_compactor.py` 通过确定性规则生成失败要求清单、关键文本跨度、Trial 评分、
Verifier 证据和原始证据引用。它不调用 LLM，因此不存在“压缩模型提示词”。

### 4.2 Evidence Investigation Controller

`evidence_investigation.py` 只执行允许列表内的只读请求，包括轨迹定位、跨 Trial
比较、Repository 搜索、物理产物结构检查和评分元数据检查。物理产物与评分说明是两个
独立证据类型。Analyzer 决定查什么，控制器决定请求是否合法并返回事实。

### 4.3 Hypothesis Compiler

`member_optimizer/hypothesis.py` 将 Analyzer Issue 编译成不可变候选合同，并保留
supported、falsified、unresolved 假设 ID。它不会让另一个模型重新解释根因。

### 4.4 Candidate Gate、Checkpoint 与 Improver Evolution

候选接受、连续信号排序、Epoch Full Checkpoint、Candidate Feedback Ledger、
Improver Policy 提议和 Meta Validation 都是确定性逻辑。它们消费模型输出，但没有
自己的系统提示词。当前实验只启用 Candidate Gate 和 Checkpoint，不启动 Improver
Evolution。

## 5. 更新检查

修改系统提示词后至少运行：

```powershell
uv run pytest -q tests/unit_tests/rsi
uv run ruff check openjiuwen/rsi examples/rsi tests/unit_tests/rsi
git diff --check
```

同时更新本文的协议版本和源码位置。不要把历史 Prompt 文档当作运行时
真源；最终以当前代码常量和 `member_optimizer/agents/prompts/*.md` 为准。
