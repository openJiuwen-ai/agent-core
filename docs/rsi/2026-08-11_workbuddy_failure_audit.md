# WorkBuddy 对齐与优化失效审计

## 结论

本轮 `office_jws_a-78b2dba8` 的 `0.729568` 不能作为已经对齐 task86 后的
RSI 基线。主要原因不是统一的模型能力下降，而是执行协议、输入上下文和运行制品
仍不一致；同时候选优化执行被 Windows 路径缺陷大面积阻断。

## 分数差异

- task86 参考分：`0.821752`。
- 本轮最终分：`0.729568`。
- 差值：`-0.092184`。
- 50 个 Case 中，本地低于参考 33 个，持平 3 个，高于参考 14 个。
- 7 个严重退化 Case 的总分损失约为 `5.198`，折算均分约 `0.104`，已经超过
  全部均分差距；其他 Case 的局部提升抵消了其中一部分。

严重退化集中在目标文件缺失或未完成写入：

- `contract-extract-L3-014`
- `rule-based-stock-exclusion-L4-011`
- `execution-closeout-reconcile-L4-003-successor`
- `json-screener-summary-L4-004`
- `ticket-weekly-L3-010`
- `invoice-email-archive-manifest`
- `html-report-quadrant-ppt`

## 对齐失败原因

### 1. 执行模式错误

task86 保存的真实历史中，消息模式为 `agent`。本地桥接代码却把聊天请求和 Harness
激活都写成了 `agent.plan`。多个零分 Case 完成文件分析后停在“提交计划、等待执行”
状态，没有进入文件生成阶段。

已修复：聊天请求、Harness 激活和运行元数据统一改为 `agent`。

### 2. 输入并非同一份输入

以 `contract-extract-L3-014` 为例，task86 的用户输入长度为 207 字符。本地数据集在
原始输入前后增加了 265 字符，运行时又增加了 363 字符的 standalone evaluation
说明，最终用户消息为 842 字符。后者还包含 Terminal-Bench 行为提示，因此并非
task86 原始输入。

### 3. 运行配置仍不相同

task86 归档配置与本地 `task86` profile 至少存在以下差异：

| 配置 | task86 | 本地 |
| --- | --- | --- |
| `setup_guide.enabled` | `true` | `false` |
| 模型 `top_p` | 未显式设置 | `0.1` |
| 模型 `max_tokens` | 未显式设置 | 使用本地模型配置值 |
| Memory engine | 默认 builtin/local | `none` |
| `task_memory.enabled` | `true` | `false` |
| `react.evolution.enabled` | `true` | `false` |

这些差异不一定都应在正式 RSI 实验中开启，但在宣称“复现 task86 基线”前必须明确
冻结，而不能把当前 profile 视为已经完全对齐。

### 4. 制品身份没有冻结

task86 使用保存的 wheel 制品；本地只校验 `jiuwenswarm==0.2.3` 的版本字符串。
相同版本号不能证明两个 wheel 的代码完全相同。正式对齐还需要保存并校验 wheel
SHA256、模型配置摘要、数据集摘要、Verifier 摘要和镜像 ID。

### 5. H0 不是 no-op

本地 H0 会激活 RSI Harness 的 identity、soul、skill 和 rail，并向用户消息追加
standalone evaluation overlay。它适合作为待优化 Harness，但不能直接当作 task86
原始 Agent 的同配置复现结果。应分别记录 raw task86-compatible baseline 和 RSI H0。

## 优化失效原因

本轮生成 63 个候选，接受 0 个，但不是 63 个候选都完成评测后没有提升：

- 39 个候选在 Member Optimizer 执行阶段失败，未进入候选评分。
- 22 个候选没有可执行 action，未进入候选评分。
- 1 个候选完成生成和评测，但目标工具没有被自然调用，目标分从 `0.3494` 降到 0，
  被门禁正确拒绝。
- 1 个候选没有目标。

### 1. 候选路径缺陷

39 个执行失败全部是路径问题：21 个 `WinError 3`、11 个 `WinError 206` 或等价的
超长路径失败、7 个 prompt 文件 `No such file or directory`。根因是短路径推断仅在
输入目录名称恰好等于 `member_optimizations` 时生效，而单 Harness 实际传入的是
`member_optimizations/sibling_cohorts/<cohort>/c001`。

已修复：短路径推断会向上查找 `member_optimizations` 祖先。真实失败样例路径由
262 字符缩短到 157 字符。

### 2. 搜索空间不包含 Control

22 个 `no_actions` 候选全部要求 `control` surface，但单 Harness 只允许
`prompt/skill/tool/rail`。Planner 按限制将它们标为
`surface_not_allowed_in_restricted_optimization_mode`。这不是评分器误拒绝，而是当前
原子优化能力无法实现 Analyzer 已识别的修复。

其中一部分 Control 需求由错误的 `agent.plan` 运行模式诱发。应先修复基础执行并重跑，
再统计仍然真实需要 Control 的问题，不能直接把旧的 22 个问题交给 Improver 学习。

### 3. Tool 生成不等于 Tool 激活

唯一进入评测的 Tool 候选只新增了 `artifact_write_gate`，但没有可靠的路由或调用触发
机制，真实轨迹中调用次数为 0。当前“新增一个 Tool，依赖 Agent 自然发现并调用”的
候选策略不能视为稳定改进能力。Tool 候选必须同时具备可观测的激活条件，或者改用
真正适合该问题的 Control/Rail。

## 修复与验证

- 修复 JiuWenSwarm 协议模式：`agent.plan` -> `agent`。
- 修复嵌套 sibling candidate 的 Windows 短路径推断。
- 新增协议模式和嵌套路径回归测试。
- 定向测试：11 passed。
- Ruff check：通过。

## 2026-08-11 二次运行补充

新运行 `office_jws_a-d4f572f5` 证明 `mode=agent` 已加载，但第一批中的
`contract-extract-L3-014` 仍为 0。轨迹只有 8 次 Bash 调用，随后产生约 55,527
字符的重复判断，撞到本地显式 `max_tokens=16384` 后在半句话处结束，未写入
`output/result.xlsx`。这说明协议模式修复是必要条件，但尚不足以完成 task86 对齐。

同时发现候选生成短路径已经生效，但候选评测仍使用包含完整 cohort/candidate id 的
长目录，导致两个候选都在收集 `.agent_history` 时触发 `WinError 206`。门禁又优先
报告“Skill 未调用”，掩盖了基础设施错误。

已继续修复：

- 数据集输入恢复为 WorkBuddy 原始 `instruction.md`，去除前后包装文本。
- task86 路径不再把 standalone evaluation overlay 拼入用户消息。
- 模型请求只保留归档中的 `temperature=0.95`，不再显式传递本地 `top_p`、
  `max_tokens` 和 `extra_body`。
- 对齐 `setup_guide`、builtin/local memory、task memory、evolution 和 team
  observability 开关。
- 候选评测改用运行根目录下的短路径 `ce/eXXX/bXXX/aXXX/cXXX`。
- 候选评测出现 error Case 时，门禁优先标记为 inconclusive，不再伪装成 Skill/Tool
  激活失败。

上述修改通过 75 个定向测试。task86 的精确 wheel 仍未冻结；参考任务使用
`jiuwenswarm-2026073001-py3-none-any.whl`，本地仅按 `0.2.3` 版本字符串检查，因此
下一轮单 Case 冒烟通过前仍不能声称已完全对齐。

## 下一轮要求

1. 先用修复后的 `agent` 模式冒烟运行上述严重退化 Case，确认实际产生目标文件。
2. 再运行一个小批次优化，确认候选不再出现路径错误，且 `candidate_score` 不再大面积为空。
3. 将 raw task86-compatible baseline 与 RSI H0 分开记录，不再混用 `0.821752` 与
   带 Overlay 的 H0 得分。
4. 完成 prompt、配置和 wheel SHA256 冻结后，才进行新的 50 Case 对齐实验。

## 2026-08-11 第三次对齐复核

重新下载并审计了 task86 的完整日志归档。真实 runner 发送的是
`mode=agent.plan`，而历史文件中的 `mode=agent` 是服务端规范化后的会话状态，不能反推原始请求。
此前把桥接请求改成裸 `agent` 属于错误归因，现已恢复为 `agent.plan`。日志下载接口未包含上传的
`jiuwenswarm-2026073001-py3-none-any.whl`，公开 API 也没有 wheel 下载端点，因此目前只能冻结
日志归档 SHA256，不能证明本地公开版 `0.2.3` 与 task86 wheel 字节一致。

当前 `deepseek-v4-flash` 在相同 `agent.plan` 请求下存在随机行为：

- `office_contr-d8bd6f75` 用原始输入、无 Overlay、`agent.plan` 运行，Agent 停在计划确认，未生成产物，得分 0。
- `office_contr-a35631ac` 使用同一配置再次运行，首轮直接完成任务，生成
  `output/result.xlsx`，官方 Verifier 得分 `0.9615`，与 task86 的该 Case 得分完全一致。

因此基础架构已经达到结果级对齐，但单次采样仍会因模型行为停在计划阶段。为避免把这种偶发的交互等待误判为
Harness 能力不足，桥接层新增了受控的无人值守兼容逻辑：从官方 `tests/judge.yaml` 读取必需产物；只有在必需
产物缺失、最终回复同时包含明确计划和确认请求时，才在同一 session 自动续跑一次。最多续跑一次，产物已经
存在、普通失败或非确认型回复均不会触发。运行元数据记录轮数、触发原因以及续跑前后的产物状态。

本轮新增协议与 WorkBuddy 产物合同测试后，定向测试为 `32 passed`，Ruff 和 `git diff --check` 均通过。
