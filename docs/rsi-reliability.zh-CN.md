# Harness RSI 运行控制与异常恢复

## 求解预算

`evaluator.solver_max_iterations` 是初始求解预算，默认 300，宿主硬上限也默认 300。
达到实际预算后结束，不再追加无工具收尾或其他模型调用。Harness 可以在宿主硬上限内
通过 runtime.yaml 修改实际 max_iterations。历史任务若冻结了 100，仍保留该记录。
RSI 的 on_max_iterations 仅允许 error 或 return_observations，均不追加模型调用。

```yaml
evaluator:
  solver_max_iterations: 300
  solver_on_max_iterations: error
  solver_max_iterations_hard_limit: 300
```

候选 Harness 可通过受限 config/add、config/modify、config/remove 修改包根目录
`runtime.yaml`。只允许两个字段：

```yaml
max_iterations: 150
on_max_iterations: error
```

候选不能修改宿主硬上限、认证、模型连接或评分。默认宿主硬上限为 300；
显式初始预算更高且未指定硬上限时，服务将硬上限取初始预算。配置、包内容及有效预算
进入评测身份，变化后不复用旧结果。候选、历史保留与全量复测使用各自冻结的配置，
记录真实资源成本，继续执行原有回归门槛。

JiuwenSwarm 顶层参数或 training_options 支持 solver_max_iterations、
solver_on_max_iterations、solver_max_iterations_hard_limit，并冻结到任务配置。


## 交互式修改与修复

Action Execution 与 Repair 均提供 read_file、grep、glob、list_files、write_file、
edit_file 原生工具。作者可读取候选包依赖和支持的宿主接口，只能写入声明路径。
Repair 只能修改集成包内允许的文件，不能修改审计、评估或发布状态文件。
写入范围由程序执行约束，关闭隐式 skill 工具发现，避免额外引入 Shell。

Agent 可以读取、编辑、检查后返回完成报告；控制器依据实际文件变化和语法校验判断结果。
保留旧 file_writes 响应的兼容处理，但不再以一次模型生成完整文件作为主要执行流程。
修改操作缺少原文件时，会在生成前报告版本不匹配。修复没有文件变化时收到反馈；达到无进展或总次数上限后停止。
调用结束后释放本次工具和文件操作资源，不停止进程共享的 Runner。

## 执行证据

单 Harness 执行先取得原生 observability 并建立 agent run root，再调用求解器。
角色轨迹放在 case/tr，exporter 记录放在 case/otel。
`execution_evidence` 保存归档位置、调用次数、采集问题、时序完整性和截断状态。
归档缺失或损坏时，次数未知；局部和轮末门槛不会据此断言能力未调用或调用过晚，
而是标为证据不足。普通和流式单轮调用均向内部 ReAct 传递本次调用独立的 steering 队列。

## Judge 与输出长度

Judge 使用有界的字面子串搜索，不再依赖外部 rg 可执行文件，保持证据目录只读隔离。
达到搜索上限或省略证据时明确报告，不能将搜索不完整当成证据不存在。

配置和调用中的 max_tokens、max_completion_tokens（包括 extra_body）会被保留。
null 值不发给服务；未设置仍使用上游策略。既有 RSI reasoning 策略单独生效。

评审重试覆盖模型调用、解析、格式修复和评分校验。格式修复和重新评分共用尝试次数，
不分别累乘预算。输出非法、截断或格式无法修复时，带上具体错误与上次输出，
使用同一完整冻结证据进行有界重新评分。格式修复不编造分数。
取消与永久服务错误继续向上传播。每次尝试保存输出、失败类别，以及服务可提供的
finish_reason 和 token usage。`judge_max_retries` 表示额外评审尝试次数。

## 局部进展与诊断

当独立验收条目有带证据的分数进展、且没有条目退步时，可在 batch 预算内继续修复；
只有总分上升不足以保留。局部继续不发布候选，最终仍需通过全量复测、历史保留和全局门槛。

诊断区分原题保证和自建探针假设。task_contract_checks 使用程序生成的 clause_id，
原文和行范围由程序回填。旧 task_quote 仅在唯一精确匹配时兼容；字段、引用和真实契约
冲突分别反馈，不能只改布尔字段掩盖冲突。该检查不等于证明任意数学或因果结论。


## 有界的输出反馈修正

以下配置分别放在 `evaluator`、`evaluation_result_analyzer`、`member_optimizer` 下：

```yaml
output_repair:
  max_attempts: 3
  max_stagnant_attempts: 2
```

- `max_attempts` 包含首次生成；1 表示不进行输出修正。两个值都必须是正整数，没有无限重试值。
- 同一错误且输出/包文件状态完全相同连续达到 `max_stagnant_attempts` 次，提前以 `no_progress` 停止；达到总上限记录 `attempt_limit_reached`。
- Agent 内部工具迭代仍使用各阶段已有上限。这些参数限制外部反馈尝试，不代表只允许三次工具调用。
- Planner 的解析恢复与语义校验共享预算。Judge 的格式修复与完整证据重评共享预算；`judge_max_retries + 1` 可进一步缩小上限，Judge 原有最大 6 次限制保持生效。旧 `diagnosis_agent_max_retries`、`stage_retry_limit`、`attribution_retry_limit` 也可缩小对应阶段预算。
- Analyzer 区分缺字段、类型错误、引用不匹配和明确的契约冲突；保存每次输出及错误，保留已通过校验的诊断。只剩部分诊断通过时标记 partial。
- Executor 将可修正的失败状态、输出结构问题和静态校验结果反馈给 Agent。Repair 将未修改文件和临时调用失败反馈给 Agent，重复无进展后停止。越界写入、永久服务错误和取消不进入普通重试。
- Behavior Check 将不合法输出、无效引用反馈修正；错误结果虽保留诊断文件，但不会作为成功缓存复用。真实证据不足仍然返回 unknown，不能通过重试伪造证据。

JiuwenSwarm 创建任务时支持在顶层或 `training_options` 中设置
`output_repair_max_attempts`（默认 3）与 `output_repair_max_stagnant_attempts`（默认 2）。
服务将它们冻结到三个阶段的编排配置。已有任务的冻结配置不被覆盖。

## 每次 Agent 调用的运行日志

DeepAgentConfig.invocation_log_dir（经 create_deep_agent 的 config_kwargs）启用独立日志。
每个调用生成 invocation-<id>.jsonl，记录 Agent、角色、session、调用与父调用 ID。
子 Agent 默认继承日志目录，但独立成文件。RSI 的 solver 日志在 case/logs，Judge 与
优化 Agent 日志在各自运行目录，Analyzer 自身日志在永久 diagnosis_attempts 目录。
全局日志保持原样；这里收集核心 logging 模块的脱敏运行摘要，工具原始输出仍由轨迹保存。
单个日志最多 4 MiB、单条最多 2 KiB，缺失、截断和写入异常均须显式处理。

case/runtime_config.json 和 termination.json 保存有效配置与终止事实。
Analyzer 临时证据与永久诊断档案提供 evidence_manifest.json、task_clauses.json 和
runtime_log_search.json。诊断先查看终止与配置，再搜索本调用的
WARNING/ERROR/CRITICAL、Traceback、exception、timeout、max_iterations 等关键词。
INFO 记录也可能包含终止信息。默认不全量阅读日志；初次搜索最多 50 条，每条摘要
500 字、总量 32 KiB，再按命中分页读取上下文。

物理证据复制每源文件最多 4 MiB、总源读取 16 MiB；超大历史文件优先保留尾部并采样头部，
记录字节范围与未知行号，不能把未复制内容当作没有异常。预算、重复无效操作、环境阻塞
和没有收尾必须分别分析；只看到 100 步耗尽不能证明某一解释。

宿主 max_iterations_hard_limit 只适用于 DeepAgent 单轮模式，配置时拒绝与 task-loop
组合；未设置该字段的既有 task-loop 行为保持不变。流式调用使用独立 producer Task，
以有界队列传递事件，确保消费者跨 Task 读取/关闭时日志上下文正确清理。

## Judge 输出解析边界

Judge 执行层只提取模型原文，不提前解析评分 JSON。外层先保存 response_<n>.json，
再解析和评分校验；非法 JSON、字段不合法与格式修复/重新评分共同消耗既有总次数上限。
JSONDecodeError 属于可修正的输出问题，不能在原文保存前绕过修复循环抛出。

每个 judge/evaluation_<id>/grading_events.jsonl 记录收到原文、校验失败、下一步修正模式、
成功或终止原因，并引用真实存在的原文文件。它与 DeepAgent 的调用日志分开，调用日志
显示 success 不代表评分已通过校验。永久服务错误与取消继续按原有方式结束。
