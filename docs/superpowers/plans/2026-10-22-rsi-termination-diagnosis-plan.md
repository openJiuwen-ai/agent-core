# RSI 终止机制与诊断改进方案

**状态：** 用户已授权并完成代码修改与独立源码复核；本轮未运行测试、未重启服务、未重跑实验。

**目标：** 对 rsi-2a790c7d 暴露的迭代耗尽、诊断归因偏差及机械引用失败进行分层修复，同时保持有限调用预算和评测可比性。

**架构：** 复用现有 ReAct 的 `on_max_iterations` 能力，由 DeepAgent 配置透传，RSI 明确选择策略。程序先提取确定性终止事实，Analyzer 再分析原因并生成有证据的改进假设。任务引用使用程序生成的条款 ID。

**技术：** Python 3.11+，原生 DeepAgent/ReAct/RSI 配置与现有评测产物。

## 事实与边界

- 本次 case 达到 100 次 ReAct 迭代，仍在写临时实现，返回 `result_type=error, termination_reason=max_iterations`。
- ReAct 已有 `error/final_answer/return_observations` 策略，默认 error；DeepAgent 当前未透传该策略。
- Analyzer 看到了 100 步耗尽，但其输入没有完整的生效运行配置；提示词未强制先检查执行器终止机制。
- 两条诊断最终因引用多写一个“出”而全部失效；三次修正上限确实生效。
- 到达上限不能单独证明预算偏小，也不能证明缺少交付 Prompt。两种解释需要不同证据。
- 保留当前工作区全部已有修改；不覆盖既有任务、日志或候选产物；本方案不授权提交、重启或实验。

## 默认决策（方案建议）

1. 通用 DeepAgent 维持 `on_max_iterations=error`，保持兼容。
2. 按最新用户要求，新建 RSI 使用 `solver_on_max_iterations=error`；实际默认上限与宿主硬上限均为 300，Harness 可在宿主范围内修改实际步数。
3. RSI 不追加任何耗尽后的模型收尾调用；候选也不能配置 final_answer 绕过此规则。通用 ReAct 的既有 final_answer 能力保留给非 RSI 用途。
4. 输出修正保留默认总计 3 次、无进展阈值 2 次，不增加次数。
5. 按本轮用户反馈，将实际迭代预算与耗尽策略纳入候选 Harness 的受限配置修改范围。实验宿主提供独立硬上限；候选不能修改硬上限、评分规则、认证或模型连接配置。框架本身的故障仍由研发修复。

## Task 1：接通预算耗尽后的有界收尾

**文件：** `openjiuwen/harness/schema/config.py`、`harness/deep_agent.py`、`rsi/harness_rsi/config/config.py`、`evaluator/case_backend.py`、默认配置，以及 Swarm `services.py/materializer.py`。

- [x] 在 DeepAgentConfig 增加受校验的 on_max_iterations 字段，经现有 config_kwargs 装配；不扩展工厂显式参数列表。
- [x] 初次构造和 configure 重配置都把策略传入内层 ReAct，核对 invoke/stream 一致性。
- [x] RSI 新增 solver_on_max_iterations 配置，在任务创建时冻结，并纳入评测身份；旧任务沿用记录的策略或旧默认 error，避免恢复旧任务时悄悄改变评测条件。
- [x] RSI 采用直接终止策略，保留原终止原因和预算耗尽标记；实际默认预算 300，达到上限不再追加调用。
- [x] 宿主硬上限与评分规则在实验中固定。基线、候选分别使用各自冻结的 Harness 预算/收尾策略；预算是允许优化的变量，必须随候选保存，进入评测身份和复测配置，并记录实际调用数、耗时和 token 成本。是否通过仍由原 Judge/rubric 判断。

**验收场景：** 达到上限后恰好增加一次无工具调用；正常提前完成不增加调用；error 模式保持旧行为；收尾空输出、截断、调用工具、服务失败、取消均不被当作成功。

## Task 2：为 Analyzer 提供真实运行上下文，先诊断终止再分析行为

**文件：** `evaluator/case_backend.py`、`evaluation_result_analyzer/case_reader.py`、`analyzer.py`。

- [x] 提取不可由模型改写的 execution_termination：生效步数上限、已用步数、终止原因、生效收尾策略、收尾是否执行/成功、最终输出与制品可用性。
- [x] 只传递白名单运行配置，排除密钥、完整模型配置和环境变量；对未知/缺失证据显式标记。
- [x] 增加按 task/evaluation/case/role/invocation 标识的运行日志归档，支持求解器、Analyzer、Planner、Executor、Repair 各自的调用。共享进程日志保留为运维记录，不能仅按时间截取就当作某个 Agent 的独立日志。
- [x] 将本次 case 的脱敏日志、异常堆栈、有效配置和终止事件复制到诊断证据工作区，并提供 evidence_manifest.json，逐项列出路径、范围、截断/缺失状态。历史实验缺少日志时保持 unknown，不能伪造隔离记录。
- [x] 明确提示 Analyzer：运行日志也是根因证据；先查看运行配置、termination.json 和日志清单，再搜索对应调用日志，最后根据命中补读 execution_history.json 和制品。不默认全量读取日志，不要求模型猜共享日志路径。
- [x] 默认先按结构化日志级别筛选 WARNING/ERROR/CRITICAL，再搜索 Traceback、exception、timeout/timed out、max_iterations、termination_reason、failed 等终止关键词；关键词是线索，失败可能记录在 INFO 或结果字段中。
- [x] 搜索须限制 case/role/invocation 范围，返回文件、事件/行号和有限摘要；建议首次最多 50 条命中、单条摘要最多 500 字符，再按需分页读取前后约 10 行。总返回量设置字节上限，单条超长 JSON 记录也必须截断并报告。相邻命中去重，不把完整 Prompt 中的 error 字样误当运行异常。
- [x] Analyzer 输出 failure_layer（runtime/harness/evidence/unknown）、observed_failure、候选解释和能区分解释的轨迹证据。
- [x] max_iterations 必须先报告为确定性终止事实。模型必须区分预算不足、重复无效操作、环境阻塞、缺少收尾与已完成但未提交；不能凭最后一步直接高置信归因为 Prompt/Skill 缺失。
- [x] 证据不足时保留 unknown；预算不足的判断需要可见推进证据或后续受控预算对照，不能仅根据达到 100 步得出结论。

**验收场景：** 对现有 T083 case 给出“预算耗尽+未执行收尾”事实，同时将“预算不足还是低效调试”保留为待验证解释；不得自动断言增加预算或增加 Prompt 必然解决。

## Task 3：让诊断建议进入正确的修改流程

**文件：** `analyzer.py`、`member_optimizer/hypothesis.py`、`single_harness/iterative.py`，必要时扩展 analysis 元数据。

- [x] 将候选可修的 runtime 配置问题映射为受限 Harness config Issue；证据采集损坏、框架崩溃等不可由候选修复的问题单独记录，不伪造 Prompt/Skill 修复。
- [x] 增加受限 config 动作及静态/运行校验，仅允许声明 max_iterations、on_max_iterations；模型、认证、宿主硬上限、评分规则不在写入白名单。Planner/Executor 必须看到当前有效值与允许范围。
- [x] 有明确 Harness 行为证据时，生成 config/prompt/skill/tool/rail 建议，并附适用条件、预期改变、验证方式和不确定性。
- [x] 候选配置必须有界且在宿主硬上限内；候选可修改的 Rail 仍受既有写入和运行接口约束，不能用运行时回调越过宿主预算。
- [x] 没有可执行 Harness 建议时记录原因并结束该候选分支；其他已验证诊断、候选和 batch 结果保持可追溯。真实运行基础设施故障继续显式报告。

**验收场景：** 预算不足的诊断可生成有限、可复测的 config 变更；API 认证失败不能被转成“增加交付 Prompt”的计划；其他有效 Harness 建议仍可独立进入优化。

## Task 4：用条款 ID 消除引用抄写导致的机械失败

**文件：** `evaluation_result_analyzer/analyzer.py`、`feedback.py`。

- [x] 根据原始 case.input 生成稳定条款表，保留原文与行区间，提供 clause_id。模型引用 ID，原文由程序回填。
- [x] 无效 ID 返回具体字段、可用条款和错误原因；不能依靠模糊字符串相似度证明任务语义相同。
- [x] 兼容旧 task_quote 输出：能唯一精确定位时映射到 ID；其他情况通过有界反馈要求选择合法 ID。引用格式失败与真实任务契约冲突采用不同错误类别。
- [x] 无进展指纹使用未解决的错误码、字段/条款和已通过项，文件修改用内容摘要；其他文字改写不重置同一阻塞问题的停机计数。
- [x] 保留已通过的诊断；原始输出、错误和停止原因继续落盘。

**验收场景：** 本次“再给/再给出”问题可通过选择同一原题条款解决；错误 ID 不通过；无关总结文字变化不能让同一阻塞错误持续消耗预算。

## 实施顺序与验证边界

先完成 Task 1，确保预算耗尽时能保留有用交付；随后完成 Task 2/3，避免错误归因；Task 4 同时解决本次诊断被机械引用拦截的问题。

实施完成后先审查调用上限、配置身份和失败状态；回归或真实实验须按后续用户指令执行，不能把代码审查当成已验证改善。历史实验保持不变；验证新配置需要新的、可对比的评测记录。

## 本轮反馈确认

- 用户希望迭代次数也可由 Harness 优化；方案已改为候选实际预算可编辑、实验宿主硬上限固定。
- 用户要求明确告诉 Analyzer 通过日志分析根因；方案已加入调用级隔离、日志清单与读取指令。
- 原 Task 3 的用途是选择正确修改目标，现用“预算问题改 config，步骤策略问题改 Rail/Prompt，Skill 操作问题改 Skill”的具体映射表达。
- 用户同意条款 ID 方案；已实现，尚未通过真实实验验证。

## 修改前的 agent-core 日志能力核对

- `core/common/logging/utils.py` 已提供基于 ContextVar 的 trace_id/member_id；可作为协程调用身份隔离的基础。
- 现有默认日志配置有 log_path/log_file/log_file_pattern，按 log_type 管理 logger；默认文件模板支持进程号与时间，没有 DeepAgent 实例的自动分文件配置。
- `DeepAgentConfig` 没有独立日志文件开关；当前 RSI 样例大量记录仍为 default_trace_id。补齐调用身份绑定与基于身份的文件路由即可复用现有基础，不能在并发 Agent 调用前后反复修改进程全局 log_path。
- 用户要求日志先搜索后按需读取，已纳入 Task 2 并实现。

## 实施记录与审查

- DeepAgent/ReAct：配置透传、单轮宿主硬上限、一次无工具收尾、耗尽元数据、调用级日志和流式独立 producer。通用默认 error，缺字段的历史配置保留 error；新建 RSI 显式 final_answer。
- Harness config：runtime.yaml 两字段白名单，规划、执行、静态验证、候选求解、checkpoint 复制/删除及全量复测贯通；宿主硬上限默认 300，显式更高初始预算未单列上限时取初始预算。
- 日志：core default/loguru 后端采集每次调用的有限脱敏摘要；子 Agent 默认继承日志目录并独立成文件。每源文件最大 4 MiB 的运行副本与总源读取 16 MiB，超大历史文件保留头尾与截断标记；模型搜索输出仍有 50 条/500 字/32 KiB 限制。
- Analyzer：稳定条款 ID 与程序回填，日志清单及初次关键词命中、永久证据归档，先终止事实后区分根因；无进展按失败字段与已通过项判断。
- 已修复审查发现：流式 ContextVar 跨 Task、producer 取消/满队列死锁、外层 Rail 改动硬上限、收尾被通用恢复/重试扩大、回调覆盖抹掉耗尽事实、日志只保留前缀遗漏末尾、独立 TeamEvaluator 缓存缺运行配置身份。
- 原生执行与 RSI 流水线分别由独立 Agent 只读复核；所报重要问题均已处理。日志证据的最终头尾修正由主代理接手检查并经复核确认。
- 开发者约束优先于 skill 的测试流程：没有新增/运行测试、代码导入、语法检查、格式化或静态检查；没有运行付费请求、重启服务、修改旧实验、提交或推送。代码行为仍需后续回归确认。

## 最新用户修订：300 步，不追加收尾

用户撤回 RSI 的“100 步后额外一次无工具收尾”方案。新建任务默认实际预算 300、宿主
硬上限 300；runtime.yaml 的 max_iterations 仍可在宿主范围内优化。RSI 配置和候选策略
拒绝 final_answer，避免优化 Agent 重新启用额外调用。历史实验与通用 DeepAgent 原有能力保留。
已同步核心默认值、Swarm 创建参数、默认模板、作者提示与中英文说明；本次未运行测试或重启服务。
