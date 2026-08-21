# RSI Analyzer 证据压缩改造报告

## 1. 目标

本次改造提升“执行轨迹到根因”的链路能力，不新增诊断 Agent，也不增加候选后的筛选门槛。核心目标是让 Analyzer 直接获得任务契约、分次结果、关键决策和候选实验反馈，减少重复日志对归因的干扰。

## 2. 已确认的问题

1. 原实现把多次执行轨迹平铺后，只重点保留失败调用和最后若干事件，成功与失败执行之间的差异会丢失。
2. 关键文本按整段字符数截断，工具参数、数值、最终交付内容和证据位置可能一起被截掉。
3. Evo-Bench 已保存公开工具 Schema，但没有传给 Analyzer。T012 中 `finance_submit_report` 的请求只允许 `title`、`transactions`、`total_amount`，Analyzer 却把响应中的 `content:null` 和 `report_type:null` 误判为缺失请求字段。
4. 候选反馈回流时遗漏 `source_target_score`、`candidate_target_score` 和 `target_score_delta`。对没有原子 Verifier 子项的 Evo-Bench，Analyzer 因此几乎看不到候选的实际效果。

## 3. 已完成的改造

### 3.1 确定性因果证据摘要

新增 `evidence_compactor.py`，不调用模型，按以下结构生成 `causal_digest`：

- 公开任务和工具契约；
- 每次执行的通过状态、连续分数、退出原因和工具序列；
- 有状态操作、失败调用、首次和末次关键调用；
- 每次执行真正交付给用户的内容及交付渠道；
- 成功与失败执行的工具差异、最早序列分歧和终止动作参数差异；
- 原始 `trace_id`、`message_index` 和 `step_pointer`。

压缩过程删除重复叙述和完全相同的调用结果，但结构化工具参数中的标识符、数值和字段名保持不变。长文本采用首尾保留，不再只保留开头。

### 3.2 工具字段可控性

Evo-Bench evaluator 现在把公开工具 Schema 写入 `evaluation.metadata.analysis_task_contract`。旧运行没有该字段时，Analyzer 会从已物化的 `official/suite.json` 读取同一份公开契约，不读取 scorer、grader 或答案。

证据摘要会并列给出：

- 公开请求允许字段；
- 公开请求必填字段；
- Agent 实际请求字段；
- 服务端实际响应字段；
- 仅出现在响应、但不属于公开请求 Schema 的字段。

Analyzer Prompt 明确禁止把仅存在于响应中的字段当作缺失入参，除非公开 Schema 或任务要求声明该字段可由 Agent 提交。

### 3.3 多次执行对照

Analyzer 不再把三次 Evo-Bench rollout 当作一条长轨迹。每次执行分别保留分数、通过状态、最终交付和关键动作，再生成跨次对照。对同时存在成功和失败执行的题目，优先分析与结果共同变化的行为；三次均失败时，只比较不同终止决策及评分变化，不虚构成功模式。

`finish` 工具的 `answer` 被视为真实交付内容。这样可以区分“文件中写了完整正文”和“最终只返回路径或摘要”等交付渠道问题。

### 3.4 候选实验回流

单 Harness 控制流现在把以下字段传回下一轮 Analyzer：

- 候选预测排名和预测分；
- Source 与 Candidate 的目标分；
- 目标分变化；
- 是否被选择晋升；
- 状态、原因和 Verifier 变化。

Analyzer 据此区分两类失败：预期行为没有触发，或者行为已经触发但没有改善结果。后者必须用于否定上一轮因果假设，不能继续生成同义改动。

## 4. 现有轨迹离线验收

使用 `rsi_claw20_v2` 已保存的真实轨迹进行确定性离线检查，不重新调用任务模型和 Analyzer 模型。

| Case | 原始轨迹大小 | 因果摘要字符数 | 摘要/原始比例 | 关键恢复信息 |
|---|---:|---:|---:|---|
| T012 | 93,330 | 35,960 | 38.5% | 三次提交的交易集合与 7596.99、4351.99、7551.99 均保留；`content/report_type` 被标记为非公开请求字段 |
| T027 | 50,918 | 26,355 | 51.8% | 三次安全输出、公开配置工具字段及最终交付分别保留 |
| T068 | 158,016 | 44,350 | 28.1% | 成功/失败执行、`finish` 交付正文及连续分差异分别保留 |
| T091 | 347,421 | 51,112 | 14.7% | 两次失败与一次成功的最终正文、文件路径和交付渠道分别保留 |

压缩比例不是唯一目标。验收重点是：影响因果判断的分次差异、精确动作、数字、工具契约和交付内容均存在，同时去掉大部分重复读取、重复结果和过程叙述。

## 5. 测试结果

- 新增证据压缩与 Analyzer 接入测试 6 项；
- Analyzer、Evo-Bench evaluator、单 Harness 控制流定向回归共 `147 passed, 2 skipped`；
- Ruff format 和 Ruff check 通过；
- 仅存在仓库已有的 Pydantic V2 弃用警告。

## 6. 第二轮闭环改造

### 6.1 逐次评分证据

Evo-Bench evaluator 现在严格读取每道 General 任务三个 `trial_N/score.json`，逐次保存评分、是否通过、评分理由、Judge 明细以及 completion、robustness、communication、safety 四个维度。该结构同时写入 Case Result 和 `causal_digest.trials[].trial_evaluation`。缺失或无法解析的维度会明确记录 availability，不会被折算为 0。

### 6.2 连续信号参与递归搜索

候选是否晋升仍只由 Pass^3 决定。三次原始分的均值和评分维度变化只用于同 Pass^3 候选之间的排序、修复父候选选择以及下一轮 Analyzer 反馈。

这修复了“候选从较低连续分提升到较高连续分，但由于尚未三次全部通过而被系统视为完全没有改善”的问题。连续信号不能让候选绕过 Candidate Gate 或 Full Checkpoint。

### 6.3 Analyzer 到 Optimizer 的证据闭环

Analyzer 现在把每个失败 Case 的因果摘要和候选反馈物化到 `causal_evidence.json`，并由 `analysis_ref.metadata.causal_evidence_path` 暴露。Evo-Bench Optimizer 优先读取：

- Analyzer 因果摘要；
- 结构化诊断结论；
- Source/Candidate 的实际分数和维度变化。

存在这些结构化证据时，Optimizer 不再读取大段原始轨迹；旧 Analysis 产物仍保留有界的 Result/Trace 回退。候选生成输入保留真实行为证据，但全局 Prompt 补丁禁止固化 Case ID、已知答案、题目实体集合以及公共 Tool Schema 之外的字段。

优化链指纹已升级到版本 14。旧运行状态不会被当作本轮新证据链继续 Resume，避免复用缺少逐次评分明细的缓存结果。

## 7. 本轮验收

- 核心定向回归共 `165 passed, 2 skipped`，Evo-Bench Launcher/示例回归另有 `14 passed`；
- Ruff format、Ruff check 和 `git diff --check` 通过；
- 已保存的 20 个真实 Case、共 60 个逐次评分文件全部通过严格读取；其中 T068 三次分数 `0.84 / 0.68 / 0.60` 以及对应 completion 维度 `0.80 / 0.60 / 0.50` 均被正确恢复；
- 未重新调用任务模型、Analyzer 模型和 Judge，因此当前结论仅证明链路已打通，不能提前声称最终 Pass^3 已提升。

下一步应使用新链路重新运行现有失败 Case，重点观察：候选行为是否实际触发、连续分是否沿正确维度改善，以及后续修复候选能否把局部改善转化为 Pass^3 提升。

## 8. 通用 Analyzer 与 Improver 协议

本轮把诊断与改进提示词从特定 Benchmark 叙述中解耦，形成两份可审计协议：

- Analyzer `generic_behavior_causal_v2`：适用于代码、Office、Search、General、工具调用和多 Agent 任务；固定按“失败要求、观察行为、竞争假设、判别证据、证据充分度、行为干预”顺序输出。
- Improver `generic_behavior_intervention_v1`：读取 Analyzer 的行为协议，将一个问题转换为一个“触发条件 -> 行为变化 -> 可观察验收”的候选实验；具体可写文件仍由当前 Harness Adapter 的 mutation contract 限定。

Analyzer 新增 `confirmed`、`supported_hypothesis`、`insufficient` 三档证据状态。只有总分、模糊症状或缺少产物时不得把解释写成确定根因；`insufficient` 必须使用 `target_ref=unassigned` 和低置信度。候选执行后，只有新增证据才能替换原根因；同一聚合分数本身不能支持另一套事后叙事。

Improver 不再把 sibling 的轮换方向当成强制根因。轮换项只作为证据支持下的次级差异化视角，不能覆盖 Analyzer 已诊断的失败要求。Candidate 激活但目标指标不改善时，后续候选必须停止重复或改写同一干预；未激活时则优先修复激活或路由。

协议版本会分别写入 `analysis_ref.metadata.analyzer_protocol_version`、候选计划和 `member_optimization_ref.metadata.improver_protocol_version`，便于实验结果按提示词版本追溯。现有 JSON/YAML 核心字段保持兼容。

定向回归结果：`105 passed, 2 skipped`；Ruff、compileall 和 `git diff --check` 通过。该结果证明提示词与产物链路兼容，实际提升仍需在新进程的新一轮候选实验中验证。

## 9. Office 判分证据与有效 Harness 对齐

真实 Office 轨迹审计发现，原压缩链路存在一处会直接制造错误监督信号的问题：官方 Result 中已经包含逐项 Judge rationale，但逐次 `score.json` 只有聚合分；Analyzer 只读取旧的 `metadata.parsed`，导致逐项判分在诊断输入中消失。同时，固定最多 8 条工具调用的选择策略保留了一个 `/tmp` 读取失败，却丢掉了此前两次成功的目标文档提取。Analyzer 因而把“最终结论与判分标准相反”误诊成“没有读取到文档内容”。

本轮完成以下修复：

- Evo-Bench evaluator 将聚合 Judge criteria 规范化为 `evaluation.metadata.judge_evidence`，同时保留旧 `judge_detail`；旧结果由 Analyzer 直接兼容读取。
- `causal_digest.outcome.judge_evidence` 保留 criterion ID、Verifier ID、分数、状态和 rationale，不再把非标量 criteria 列表丢掉。
- 工具调用从固定 8 条改为 8 至 20 条动态预算，优先保留响应失败、状态修改、内容读取、工具首尾和结束窗口；每条保留动作记录选择原因。
- 失败识别同时解析调用异常和响应中的 `success=False`、非零 Exit Code、Traceback、Access Denied。
- Bash 中的文件写入、Office 保存、复制、转换等操作被识别为状态修改；文档、PDF、表格内容提取被识别为内容证据。
- 每个 Trial 新增 `selection_coverage`，显式报告失败、修改和内容证据各有多少、保留多少，避免静默丢失决定性证据。
- Analyzer 输入新增 `effective_harness`：包含脱敏后的有效配置、系统提示词、Skill、Harness 实现摘要、相关 Tool/Rail 文件及哈希。已有规则不得再被诊断为“缺少规则”。
- Analyzer 输入新增只读 Workspace 文件索引，并明确只有执行后快照时不能把 mtime 当作修改证明。
- 优化链指纹升级到版本 17，旧证据链状态不会被当成本轮结果继续 Resume。

对已保存的 `apex-327f...` 真实轨迹离线复验后，新的输入恢复了 4 条官方 Judge criteria，并保留了消息 15、17 的两次成功文档提取及消息 21 的读取失败；17 次调用动态保留 12 次，所有 1 个失败调用、2 个状态修改和 9 个内容证据均被覆盖。新的证据结构不再支持“目标文档没有读取”的旧归因。

随后使用 GLM-5.2 对同一批已保存轨迹重新执行 Analyzer，不重新运行 Task Agent 和 Judge。新版诊断明确确认消息 15、17 已成功提取两份目标文档，并将消息 21 的 `/tmp` 读取失败判定为已恢复的非根因；诊断改为依据 4 条官方评分项，定位到 Agent 把“仍可改进”错误等同于“不合规”，导致最终 Yes/No 结论与评分标准相反。该结果验证了修复目标：压缩后的输入不仅更短，而且保留了足以排除显眼伪因、定位真正决策错误的证据。

最终组合回归覆盖 Analyzer、证据压缩、Evo-Bench evaluator、单 Harness 控制流、Launcher 和统一入口，共 `190 passed, 2 skipped`；Ruff check 通过。跳过项和警告均为仓库已有配置，不影响本轮证据链功能。

## 10. 因果调查与候选实验硬约束

本轮将此前主要依赖提示词遵守的要求改成控制器可验证协议。Analyzer 协议升级为 `generic_behavior_causal_v6`，Improver 协议升级为 `generic_behavior_intervention_v2`。

- 默认强制执行“调查计划 -> 控制器取证 -> 因果诊断”。旧格式诊断不能直接进入优化；一次纠正后仍无法生成有效调查计划时，该 Case 不产生可优化 Issue。
- 首轮调查必须包含 2 至 3 个不同假设，每个假设都必须绑定至少一条判别证据请求。完全重复的假设会被规范化器拒绝。
- 首轮最多 8 条证据请求；证据不足时立即在同一 Case 内追加一次补证，总上限 12 条。仍无法区分时必须保持 `target_ref=unassigned`。
- 只读控制器支持轨迹搜索、精确事件读取、候选前后对照、安全数值关系检查、结构化 Office 产物读取，以及隔离仓库内的受限搜索和文件读取。绝对路径、路径穿越、任意 Shell 和未由仓库搜索返回的文件路径均被拒绝。
- 多 Trial 中相同 `message_index` 不再默认取第一条；缺少 `trace_id` 时返回 `ambiguous`，避免跨次执行串证据。
- xlsx、docx、pdf 和 pptx 产物采用有界结构化读取；Excel 公式、单元格位置、文档段落、表格、PDF 页和幻灯片文本可以作为精确证据，同时设置单元格、页数和字符预算。
- 严格因果分析没有可归因 Issue 时，单 Harness 控制器停止候选生成，不再把失败 Case 包装成虚构的低置信度优化目标。旧第三方 Analyzer 仅保留显式兼容路径。
- Evo-Bench Improver 输出必须声明 `source_hypothesis_id`，且只能选择 Analyzer 标为 `supported` 的假设；`falsified` 和 `unresolved` 会被代码拒绝。通用 MemberOptimizer 也会把 supported/falsified ID 写入不可变优化合同。
- 候选的因果干预合同在候选评测前写入并冻结，随后与实际行为、Verifier 变化和结果变化一起进入 Candidate Feedback。若实验已否定某个因果假设，下一轮 Analyzer 和 Improver 不能复用同一假设 ID。

兼容开关 `causal_investigation_required` 默认值为 `true`。它只用于旧 Analyzer 单测或外部适配，不改变当前 RSI 实验的严格默认行为。

最终 RSI 单元回归为 `964 passed, 3 skipped`；Ruff format、Ruff check 和 `git diff --check` 均通过。未调用付费任务模型或 Judge，本轮验收确认的是控制流、证据边界和因果合同已完整落地，实际分数提升仍需新实验验证。
