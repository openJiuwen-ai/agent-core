# Harness 优化流程

本文描述当前 `release/v0.1.20` 源码里的单 Harness 自优化。入口是 `SingleHarnessIterativeOptimizationOrchestrator.run`，实现在 `openjiuwen/rsi/harness_rsi/single_harness/iterative.py`。JiuwenSwarm 通过 `jiuwenswarm/agents/harness/common/rsi/harness_provider.py` 调用它。

这不是 Auto Harness，也不是团队多角色优化。单 Harness 任务只有一个角色包。角色归因和机制归因两个模型在这条路径上不运行；诊断结果被直接编译成规划输入。规划、执行、验证修复、求解、诊断、评判这些模型仍会运行。

一次公开迭代是一个 epoch，不是模型的一次工具调用。模型步数上限和 epoch 次数是两套预算。

## 1. 总流程

```text
冻结基线评测
  -> 每个 epoch
       -> 按批次取出仍未通过的用例
            -> 源评测
            -> 诊断
            -> 编译优化假设
            -> 对每个未处理问题：
                 成员优化（规划、执行、验证、修复、发布候选）
                 -> 候选门（只评目标用例）
                 -> 行为核对
                 -> 通过则标为 provisional，工作 Harness 切到候选
                 -> 用新 Harness 重评本批，留下仍失败的用例
            -> 修复轮次用尽或没有可执行问题则结束本批
       -> 本 epoch 没有任何 provisional 变更：整轮拒绝，不跑全量回放
       -> 有 provisional 变更：全量用例回放
            -> 逐个候选决定保留或剔除
            -> 全局不回退才晋升为新的 best
  -> 所有用例都已通过，或 epoch 用尽：写最终报告
```

批次内通过只表示“先留下来，等 epoch 结束再裁决”。`best_harness_refs_path` 在 epoch 检查点之前不变。

## 2. 任务目录

JiuwenSwarm 把一次任务放在 `~/.jiuwenswarm/rsi/tasks/<task_id>/`。引擎读写的运行目录是其中的 `run/`。

| 路径 | 谁写入 | 内容 | 谁再读 |
|---|---|---|---|
| `task.json` | 服务 | 任务名、数据集、epoch 上限、状态 | 服务恢复任务 |
| `config/harness_orchestrator.yaml` | 任务创建时从包内模板复制 | 评测、诊断、成员优化预算 | 每次启动编排器 |
| `tree.json`、`events.jsonl` | 服务根据引擎事件投影 | 界面上的 epoch / 批次节点 | Web |
| `harness/versions/<id>/` | 发布步骤 | 某一版角色包的文件 | 评测和下一轮优化 |
| `run/single_harness_state.yaml` | 编排器，几乎每步覆盖写 | 恢复点：best、working、批次、门、检查点 | 恢复运行、界面、下一轮规划 |
| `run/single_harness_report.yaml` | 结束、取消或失败时 | 对外报告，附 usage | `read_report` |
| `run/model_calls.jsonl` | 模型用量观察器 | 每次模型调用的用量 | 报告和对账 |
| `run/optimization_journal.yaml` | 每次刷新经验 | 每个候选动作的实验结果 | 下一次规划的 `optimization_experience.journal` |
| `run/lever_scoreboard.yaml` | 同上 | 按杠杆汇总的尝试、接受、回退 | 下一次规划的 `lever_scoreboard` |

`single_harness_state.yaml` 里和后续最相关的字段：

- `baseline_eval_ref_path`、`baseline_score`：冻结基线，创建后不再被候选覆盖。
- `best_harness_refs_path`、`best_eval_ref_path`、`best_score`：已经晋升的最好版本。
- `current_harness_refs_path`：对外可见的当前版本，epoch 结束前等于 best。
- `working_harness_refs_path`：本 epoch 内已经临时接上的版本。下一批在它上面继续改。
- `retained_case_ids`：上一版 best 里通过的用例。晋升时用来防止这些用例变差。
- `completed_batches`：每个 `epoch_XXX:batch_XXX` 的源评测、诊断、尝试和门状态。
- `candidate_gates`：每一次候选的完整裁决。被拒绝的记录会进入下一轮规划。
- `epoch_checkpoints`：每一轮结束时的回放和晋升结果。
- `request_fingerprint`、`evaluation_protocol_signature`：恢复时核对数据集、初始 Harness 和评测协议没有变。

状态在取消时写成 `terminated`，异常时写成 `failed`。这两种状态不会在重启后自动接着跑。

## 3. 什么叫通过

读取 `eval_ref.yaml` 里每个 case：

- `status` 是 `failed`、`error`、`skipped`，或者 metadata 标明 `infrastructure_skip`，都不算通过。
- `llm_as_judge` 看 metadata 里的 `evaluation_passed`。
- 其他评测在没有显式布尔值时，要求 `score >= 1.0`。

平均分只用于全局晋升时和上一版 best 比较。一个用例从 0 变成 0.8，只要没达到通过线，候选门仍然视为这个目标用例没有改进。

## 4. 启动和冻结基线

读取：

- 请求里的初始 `harness_refs`、数据集文件、`output_dir`、是否 resume、可选的已有基线评测。
- 编排配置：`max_epochs`、评测并发、成员优化的 sibling 数和策略摘要。

逻辑：

1. 校验 Harness 引用，计算请求指纹。
2. 没有状态文件就创建；`resume=true` 时要求指纹、epoch 上限、任务号、评测协议和已有状态一致。
3. 若配置允许且状态里还没有基线，对全部用例跑一次评测。
4. 基线评测必须覆盖数据集里的每一个 `case_id`，且 `eval_ref.yaml` 记录的 Harness 必须就是这次任务的初始 Harness。

写入：

- `run/evaluations/frozen_baseline/eval_ref.yaml`
- `run/evaluations/frozen_baseline/evaluation_context.yaml`
- `run/evaluations/frozen_baseline/cases/cXXX_<hash>/`
- 状态里的 `baseline_eval_ref_path`、初始 `best_*`

后续：基线分数是第一轮晋升的比较对象。界面根节点展示它。后面的批次不会重跑已经在同一 Harness、同一评测协议下通过的用例。

## 5. Epoch 和批次

每个 epoch 开始时，工作版本回到当时的 best。历史通过只保护晋升，不让失败用例永远免检。

数据加载器按 `harness_orchestrator.yaml` 的 `batch_size` 和 `batch_balance_keys` 把数据集成批。计划外或重复的 `case_id` 会被丢掉。

一个批次里，如果用例都已经在当前 Harness 的历史评测中通过，本批直接记为：

- `candidate_gate_status: skipped`
- `candidate_gate_reason: no_active_cases`

不诊断，不生成 Harness。

本 epoch 开始时如果全部用例都已通过，循环结束，不再开新 epoch。

## 6. 源评测

目录：`run/evaluations/e001/b001/source/`。

读取：本批未通过用例、当前工作 Harness、数据集、同一 Harness 上已经完成的评测。

逻辑：评测协议一致时，复用旧用例结果，只补跑缺失用例。新出现的基础设施失败保留，不用更早的好分数盖掉。协议或 Harness 变了，旧的 `eval_ref.yaml` 不能复用，必须新开一轮。

一个用例目录里实际留下的信息：

| 文件 | 内容 | 后续用途 |
|---|---|---|
| `result.json` | 分数、`evaluation_passed`、状态、错误 | 写进 `eval_ref.yaml`，所有门都读它 |
| `trace.json` | 归一化执行轨迹 | 诊断和行为核对 |
| `termination.json` | 是否打满迭代、终止原因 | 诊断判断是预算问题还是交付问题 |
| `runtime_config.json` | 这次求解实际的 `max_iterations`、`on_max_iterations` | 诊断和配置类改动的上限 |
| `logs/invocation-*.jsonl` | 求解模型调用 | 诊断时复制到证据目录 |
| `tr/` | 轨迹事件 | 评判和技能/工具是否被调用 |
| `artifacts/` | 求解写出的交付物 | 评判读取 |
| `judge/evaluation_*/` | 评判请求、证据、原始输出、校验错误、`assessment.json` | 最终分数和 rubric |
| `workspace/` | 求解现场 | 一般不作为下一轮优化输入 |

`eval_ref.yaml` 汇总本批：每个 case 的分数、状态、路径，以及 `harness_refs_path` 和 `evaluation_context`。源评测的通过用例会更新本 epoch 的工作保留集。仍未通过的用例才进入诊断。

求解器默认 300 步。打满后轨迹里是 `Max iterations reached without completion`，这个事实留给诊断，不在评测阶段自动加步数。

## 7. 诊断

目录：第一轮是 `run/evaluations/e001/b001/analysis/`。残差修复的下一轮是 `residual_analyses/r002/`。恢复时如果旧诊断不完整，新结果写到 `retry_001/`，不覆盖原证据。

读取：

- `eval_ref.yaml` 和 `cases/`。
- 当前 Harness。诊断只能看到角色包的公开投影：说明、技能和工具清单、受限的 `runtime.yaml`。
- 用例原文、评分合同、上一轮候选反馈。
- 从用例目录整理出的运行证据：`runtime_evidence/runtime_config.json`、`termination.json`、`evidence_manifest.json`、调用日志、`execution_history.json`。

诊断模型默认 300 步。它必须返回 JSON。每个诊断至少要有非空的 `summary`、`root_cause`、`target_ref`。`task_quote` 必须是任务原文的精确子串，省略号和改写都会失败。

格式或引用错误会把上一份输出和具体错误塞回下一次提示。默认一共 3 次。已通过校验的诊断保留，后续只要求修无效的那几条。

写入：

- `issues.yaml`：聚合后的可执行问题。
- `analysis_ref.yaml`：诊断状态、问题列表、证据路径。
- `per_case_diagnoses.json`：每个用例的原始诊断。
- `diagnosis_attempts/<case>/attempt_01.json`：每次尝试的原文、校验错误、停止原因。

`analysis_ref.require_usable()` 要求诊断完整，或者至少有可用问题。校验耗尽且没有问题时，整个任务在这里失败，已经 provisional 的改动不会晋升。

后续：假设编译读 `issues.yaml` 和 `analysis_ref.yaml`。成员优化只处理本次指定的 `issue_id`。

## 8. 优化假设

写入：`analysis/optimization_hypotheses.yaml`。

每条假设冻结诊断给出的 `required_behavior`、证据引用和建议杠杆。下游不能改写这条行为要求。杠杆再映射到允许的改动面：`config`、`prompt`、`skill`、`tool`。当前配置没有可执行改动面的问题会被标成 deferred，不进入执行。

没有问题或没有可执行假设时，本批停止，原因分别是 `no_actionable_analysis_issues`、`no_executable_hypotheses`。同一问题签名已经试过，则记 `repeated_issue_detected`，避免在同一批里反复打同一个点。

## 9. 成员优化

目录：`run/member_optimizations/member_optimization_001/`，序号继续递增。`defer_publish=true` 时，相同评测、诊断和输入 Harness 的未发布结果可以复用。

单 Harness 不调用角色归因和机制归因模型。`_compile_single_harness_planning_inputs` 把唯一角色和假设绑在一起，置信度写成 1.0。

这一步写入：

- `role_attribution.yaml`
- `mechanism_attribution.yaml`
- `member_selection.yaml`

这三个文件是编译结果，给规划和发布做来源记录。团队模式才会真正运行那两个模型。

### 9.1 规划

工作区：`stage_workspaces/action_planning/`。单 Harness 会把评测引用、假设和 Harness 摘录准备进这个目录。

规划模型读取：选中的目标、编译后的归因、动作定义、被拒绝过的能力和证据、假设、`optimization_journal`、`lever_scoreboard`。默认 300 步。最后一轮仍可使用读工具；循环结束后还有一次无工具请求，只根据已收集证据产出计划 JSON。

计划最多 3 个动作。动作只能落在允许的改动面上。`config` 只能改 `runtime.yaml` 的 `max_iterations` 和 `on_max_iterations`，并且不能超过宿主上限。

写入：

- `plan.yaml`
- `candidate_manifest.yaml`：假设、计划、来源 Harness 的对应关系。
- 规划失败时写 `action_planning_error.json`。计划被策略拒绝时，成员优化状态是 `planning_rejected`，不进入执行。

后续：执行器按 `plan.yaml` 的 `declared_write_paths` 限制写入。候选门从计划里取出技能名、工具名、目标用例和 `decision_contract`。

### 9.2 执行

每个动作在独立 worktree 里运行。执行模型默认 300 步。

提示里已经放进公开运行合同和声明文件摘录，同时仍允许 `read_file`、`grep`、`glob`、`list_files`，再用 `write_file` 或 `edit_file` 落盘。结束时要返回 JSON：`status`、`changed_files`、`errors`。控制器只承认声明路径上的真实文件变化；旧式 `file_writes` 仍兼容。

格式不对、报成功但没有写入、写到声明路径之外，都会把上一份输出和校验错误放进下一次提示。默认一共 3 次。连续两次错误和文件快照都相同，以 `no_progress` 停止。轮次用尽返回的纯文本也会被当成非法 JSON 重试一次新会话，不会接着上一次工具对话继续。

目标文件在修改前不存在时，执行前直接失败，模型不会启动。错误形如 `modify baseline mismatch: target does not exist`。

写入：

- `execution_results.json`
- 运行副本目录里的同名结果和 `plan.yaml`
- worktree 中的角色包文件

### 9.3 验证和修复

`verification.json` 记录确定性检查：路径、清单、技能合同、运行配置范围。检查通过则 `fix_result.json` 的状态是 `not_needed`。

检查失败才启动验证修复模型，默认 300 步。它只修报告里的确定性问题。修复后会再验证一次。验证仍失败时，成员优化状态不是 `success` 或 `partial_success`，候选门直接拒绝，原因是 `member_optimization_status_failed`。这一步产生不了新 Harness 引用时，原因是 `member_optimizer_produced_no_candidate`。

### 9.4 发布

`member_optimization_ref.yaml` 记录状态、计划、执行、验证、修复、优化后的 Harness 引用。单 Harness 使用延迟发布：文件先写好，但是否成为工作版本由候选门决定。

## 10. 候选门

评测目录：`run/ce/e001/b001/a001/c001/`。这里只跑该动作声明的目标用例，不把同批其他用例算进这次通过条件。

通过必须同时满足：

1. 成员优化状态是 `success` 或 `partial_success`，并且 Harness 引用变了。
2. 源评测和候选评测都没有 error、skip 或失败的机器证据。
3. 每个原先失败的目标用例分数都严格高于源评测。默认最小增幅是 0。
4. 没有目标用例退步。
5. 新增技能或工具必须在激活窗口内被调用。调用发生在首次持久编辑之后，原因是 `expected_*_invoked_outside_activation_window`；完全没调用则是 `expected_*_not_invoked_on_target_case`。`prompt` 和 `config` 不要求点名调用。
6. 行为核对的 `next_action` 必须是 `verified`。

原生 rubric 分数和验证器细项只作为诊断，不能单独把候选判成通过。

行为核对读取源评测和候选评测里同一用例的交付物与轨迹，对照计划里的 `decision_contract`。模型要判断源侧是否已有该行为、候选侧是否出现、行为是否改变，并给出原文引用。结果按证据哈希缓存在 `behavior_check/<digest>.json`。

路由规则：

- 源侧没有、候选侧有、并且用例通过：`verified`。
- 源侧没有、候选侧有、但用例仍未通过：`investigate_residuals`。
- 行为没变，但能力已被调用：`repair_execution`。
- 行为没变，且不能确认能力被调用：`collect_behavior_evidence`。
- 行为变了，候选侧仍不满足检查：`revise_implementation_or_cause`。

分数已经变好但行为核对不是 `verified` 时，通过会被撤销，原因是 `candidate_behavior_change_not_verified`。

未通过且证据充分时，会对候选再做一次诊断，写到 `failure_analysis/`。这次诊断和分数差、补丁摘录一起放进 `candidate_behavior_by_case`。

候选门先内部标成 `accepted`。编排器立刻改写成：

- `status: provisional`
- `reason: candidate_passed_batch_gate_pending_epoch_checkpoint`

然后把 `working_harness_refs_path` 指到候选。`best_harness_refs_path` 不动。`_persist_promotion` 把这个 provisional 状态写回 `member_optimization_ref.yaml` 和 `candidate_manifest.yaml`。

## 11. 批次内修复

候选被 provisional 接受后，用新 Harness 重评本批全部用例，目录是 `evaluations/e001/b001/attempts/a001/residual_source/`。仍未通过的用例成为下一条问题的诊断输入。全部通过则本批停止，原因是 `all_batch_cases_completed`。

候选被拒绝时，只有下面两类证据才允许在本批剩余预算里继续修：

- 验证器有局部进展，或行为路由是 `investigate_residuals`，并且没有回退、没有调用缺失、没有机器证据。工作版本临时停留在这个候选上。
- 行为路由是 `repair_activation` 或 `repair_execution`，或者已有失败诊断。同一问题会重新开放。若只是交付或激活问题，且 Harness 没变，则复用原诊断；否则重新诊断。

每次成员优化加一，上限来自 `max_repair_rounds_per_batch`。用尽后本批原因是 `repair_round_limit_reached`。

被拒绝的动作进入 `candidate_gates`。下一次规划通过 `rejected_capabilities` 读到动作、目标用例、失败类别和证据，避免原样再交一次。

## 12. Epoch 检查点

本 epoch 一个 provisional 都没有：不跑全量评测。检查点状态是 `unchanged`，原因是 `no_provisional_harness_change`，工作版本退回 epoch 开始时的 Harness。

有 provisional 变更时，对全部用例评测累积后的工作 Harness：

`run/evaluations/e001/full/`

每个 provisional 候选单独裁决：

- 它自己的目标用例必须仍然通过，否则 `candidate_failed_target_replay_checkpoint`。
- 技能或工具必须再次出现在对应用例轨迹里，否则 `candidate_capability_not_used_at_epoch_checkpoint`。
- 目标用例出现 error 或机器证据失败：`candidate_target_inconclusive_at_epoch_checkpoint`。
- 其他用例的失败只记录，不直接证明这个候选造成了回退。

部分候选留下、部分剔除时，从 epoch 开始的 Harness 复制被留下的改动，生成过滤后的包，再对全部用例评测一次：`evaluations/e001/selected_full/`。过滤包自己的回放失败，则整轮候选都移除。

全局晋升还要看过滤后的最终评测：

- 平均分不能低于上一版 best。比较容差只有 `1e-9`。
- 上一版 best 通过的每一个用例仍要通过。
- epoch 开始时保留的用例，以及本 epoch 里非本次目标、但工作版本曾经保住的用例，也必须通过。
- 最终评测不能有 error 或失败的机器证据。

全部满足后，`promotion_applied: true`，`best_harness_refs_path`、`best_score`、`best_eval_ref_path` 和 `retained_case_ids` 更新。候选状态从 provisional 改成 `accepted`。否则整轮退回 epoch 开始的 Harness，候选改成 `rejected`。

日志里的 `outcome` 只有最终 `accepted` 才是 `flipped`。批次里分数提高但还没晋升，记为 `improved_but_rejected`。

## 13. 经验如何进入下一轮

每次门或检查点之后重建两份文件：

- `run/optimization_journal.yaml`：每个动作一条。包含表面、杠杆、目标用例、源分数、候选分数、状态、原因、失败类别、行为核对、验证器差值、失败诊断路径。`inconclusive` 和基础设施失败不进入日志。
- `run/lever_scoreboard.yaml`：按杠杆累计尝试、接受、拒绝、平均目标分差、部分进展和回退。

下一次 `optimize` 把这两份放进 `optimization_experience`。规划模型用它们避开已经失败的杠杆，而不是只看当前这一条诊断。

## 14. 结束、取消和已经存在的任务

所有 epoch 结束或中途已经全部通过时，状态写成 `completed`，刷新报告，并发布最终 Harness。

取消写入 `terminated`。未捕获异常写入 `failed`。两者都会尽量把未完成的模型调用记进 `model_calls.jsonl`。

`config/harness_orchestrator.yaml` 在任务创建时复制。之后修改源码默认值或包内 `orchestrating.default.yaml`，不会改写已有任务的诊断步数、求解步数和修复次数。新任务才会复制新的默认值。成员优化模型的步数来自代码里的 agent profile，不在这份任务 YAML 里，所以重启并加载新源码后，新的规划、执行和修复会使用新的步数。

## 15. 默认预算

当前源码和包内默认模板：

| 项目 | 默认 |
|---|---|
| epoch 数 | 模板里 5；任务 YAML 可以更小 |
| 求解步数 | 300，硬上限 300 |
| 诊断步数 | 300 |
| 评判步数 | 300 |
| 规划、执行、验证修复步数 | 300 |
| 单 Harness 角色归因、机制归因 | 不调用模型 |
| 输出格式修复 | 一共 3 次，含第一次；相同错误连续 2 次停止 |
| 诊断额外模型重试配置 | `diagnosis_agent_max_retries: 2`，和上面的 3 次对齐 |
| 每批修复轮次 | 配置项 `max_repair_rounds_per_batch`，循环至少允许 1 轮 |
| 每个计划的动作数 | 单 Harness 最多 3 |
| 目标分最小增幅 | 0 |
