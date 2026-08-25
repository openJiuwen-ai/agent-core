# WorkBuddy Office 与 task86 JiuWenSwarm 对齐实施基线

> 状态：执行器切换已实现，`H0` 实跑验收待完成
>
> 更新日期：2026-08-08
>
> 目标：先复现可信的 `H0`，再评估 RSI 产生的 Harness 增量。当前代码已接通 JiuwenSwarm 执行链路，但还不能在真实冒烟和锚点评测前宣称分数已经对齐。

## 1. 范围与原则

本轮只替换 WorkBuddy Office 的任务执行 Agent：

```text
当前：RSI -> WorkBuddyOfficeBackend -> Agent Core DeepAgent -> 官方 Verifier
目标：RSI -> WorkBuddyOfficeBackend -> JiuWenSwarm agent.plan -> 官方 Verifier
```

DataLoader、Analyzer、MemberOptimizer、候选记录和官方 Verifier 保持在现有 RSI 链路内。实验必须满足：

- `H0` 和候选使用同一执行器、模型配置、数据、容器及评分器。
- 唯一实验变量是声明过的 Harness Overlay。
- JiuWenSwarm 自带 Evolution 必须关闭，避免两个优化器同时修改能力。
- `H0` 复现门禁未通过前，不运行或宣称 RSI 提升实验。

## 2. task86 参考运行不变量

task86 的已知参考结果为 WorkBuddy Office 50 题单次平均分 `0.8218`。该数值用于工程复现，不等同于三次运行的论文统计结果。参考配置必须从 task86 原始配置或镜像固化为机器可读快照，不能长期只依赖下表中的人工记录。

| 项目 | 必须固定的参考值或行为 |
| --- | --- |
| 执行入口 | `workbuddy_bench.agents.jiuwenswarm_agent:JiuWenSwarmAgent` |
| JiuWenSwarm | 精确版本 `0.2.3`，同时固定发布包或源码提交的 SHA256 |
| 协议 | AgentServer + Gateway + WebSocket，调用 `agent.plan` |
| Task Loop | 开启 |
| 迭代上限 | ReAct `100`，外层 Agent 总上限 `200` |
| 上下文 | `context_window=200000`，完整复制其上下文压缩、reasoning 压缩和 tool-loop 压缩配置 |
| 工具 | 完整复制 task86 的 `code`、Shell、文件和任务管理工具及其权限，不得降级为 Shell-only |
| 任务模型 | 同一 DeepSeek V4 Flash 路由、相同 endpoint 指纹和请求参数 |
| 采样 | `temperature=0.95`，`top_p=0.1`；其余参数从原配置逐项复制 |
| 隔离 | 每个 Case 新容器、新 Session、新运行目录，不复用 Skill、缓存或对话状态 |
| Harness | `H0` 期间固定不变，不挂载 RSI 候选或历史改进 |
| 评分 | 同一 WorkBuddy Office 数据快照和官方 CompositeVerifier |

超时、重试、最大输出、语言、时区、字体、LibreOffice/Office 依赖、环境变量和工具权限同样属于运行不变量。只有拿到 task86 原始值并写入 `runtime_profile` 后才能标记为已对齐。

## 3. 当前实现与差异

现有 WorkBuddy 运行、容器和评分实现见[运行入口](../../examples/rsi/run_workbuddy_office_single_harness.py)、[Office 适配器](../../examples/rsi/workbuddy_office/adapter.py)、[工作区与官方评分](../../examples/rsi/workbuddy_office/runtime.py)和[容器工具运行时](../../examples/rsi/workbuddy_office/container_runtime.py)。

| 能力 | 当前状态 | 与参考的差异 |
| --- | --- | --- |
| WorkBuddy 数据与评分 | 已从任务目录提取 `workspace.tar.gz`，使用任务 Dockerfile，并调用官方 CompositeVerifier；保留原子检查证据 | 仍需固定数据集、Verifier 和最终镜像摘要 |
| Case 容器 | 每次创建带随机后缀的新容器；JiuwenSwarm 服务、Gateway 和 `chat.send` 都在该容器内启动，并显式绑定 `project_dir/cwd/trusted_dirs=/workspace` | 仍需用真实轨迹确认各类文件、Shell 和 Office 工具均未越出容器 |
| 任务执行器 | [Office 适配器](../../examples/rsi/workbuddy_office/adapter.py)已按 `solver_backend` 分流；`jiuwenswarm` 路径不再创建宿主 Agent Core DeepAgent | CLI 为兼容旧运行仍默认 `deep_agent`，对齐实验必须显式选择 `jiuwenswarm` |
| Solver 配置 | 已有 `deep_agent`/`jiuwenswarm` 选择、版本期望值、启动/运行超时和 `task86` profile 字段 | 真实运行前仍需把制品与配置哈希写入 manifest |
| JiuWenSwarm 运行脚手架 | [JiuwenSwarm solver](../../examples/rsi/workbuddy_office/jiuwenswarm_solver.py)已接入 Office backend，包含版本检查、AgentServer/Gateway 生命周期、`agent.plan`、进程清理和有界日志 | 尚需完成容器内真实冒烟与逐项运行证据核对 |
| Harness 注入 | 每个 Case 会把当前 RSI Expert Harness 完整复制为隔离扩展包，在 Gateway 启动后通过 `harness.packages.activate` 激活；JiuWenSwarm 自带 Evolution 已关闭 | Tool/Rail 的实际加载效果仍需由真实激活结果和工具轨迹验收 |

曾检查的本地 JiuwenSwarm 工作树版本为 `0.2.2`，且存在未提交改动。**该工作树不能作为 task86 的 `0.2.3` 基线，也不能用于生成基线镜像。**

仓库内旧的 [JiuWenSwarmAgent 适配器](../../openjiuwen/agent_evolving/evaluator/evaluator_pipeline/adapters/agents/jiuwenswarm.py)可以参考 AgentServer、Gateway 和 WebSocket 通信过程，但**不得原样复用**。它属于旧 Agent Evolving Pipeline，默认可安装浮动 `develop`，默认参数和 task86 不一致，并混合了旧 Skill/Evolution 生命周期。

## 4. `H0` 复现门禁

### 4.1 `H0` 定义

`H0` 是以下内容的不可变组合：

```text
JiuWenSwarm 0.2.3 精确制品
+ task86 runtime profile
+ DeepSeek V4 Flash 固定模型配置
+ task86 原始固定 Harness
+ WorkBuddy Office 固定 50 题
+ 官方 Verifier 固定快照
+ 无 RSI Overlay、无历史 Skill、无跨 Case 状态
```

### 4.2 执行顺序

1. **制品预检**：验证 JiuWenSwarm 版本、wheel/源码 SHA256、Agent Core 提交、数据集树哈希、Verifier 哈希、模型配置哈希和 Docker image ID。
2. **协议冒烟**：选 1 个短任务，证明 `agent.plan`、Task Loop、文件工具、容器内落盘和官方 Verifier 全部生效。
3. **10 题锚点**：覆盖 `rank-ic-topn-L4-001`、ticket 周报类、长上下文类、表格公式类以及历史上出现目标文件缺失的任务。
4. **完整 50 题**：门禁验证至少完整运行一次；用于论文或正式对比前独立运行三次。
5. **冻结 `H0`**：通过后保存不可变 manifest，后续候选只能引用该 manifest，不能重新解析浮动路径。

### 4.3 通过标准

| 门禁 | 通过条件 |
| --- | --- |
| 版本与制品 | 安装版本严格等于 `0.2.3`；所有制品哈希与 manifest 相符；拒绝本地可编辑安装和 dirty checkout |
| 协议与工具 | 轨迹明确出现 `agent.plan`；Task Loop 与压缩配置可观测；工具只在指定 WorkBuddy 容器和 `/workspace` 内执行 |
| 隔离 | 每个 Case 的容器、Session 和 JiuWenSwarm home 均唯一；Case 开始前基线状态哈希一致；无跨 Case Skill/缓存泄漏 |
| 10 题锚点 | 平均分与 task86 对应任务差值不超过 `0.03`；不得新增目标文件缺失或无法打开的任务 |
| 完整 50 题 | 50/50 均产生官方评分，无基础设施失败；单次平均分与 `0.8218` 差值不超过 `0.03` |
| 正式统计 | 三次独立运行均完整，三次均值与 `0.8218` 差值不超过 `0.03`，同时报告标准差和逐题结果 |
| 证据完整 | 保存 manifest、逐题分数、轨迹、工具调用、产物哈希、Verifier 原始输出、Token/费用和失败分类 |

任一项失败都应标记为 `H0_ALIGNMENT_FAILED`，优先修复执行对齐，不得把差异交给 Analyzer 或 Optimizer “学习掉”。

## 5. RSI Overlay 与候选评测协议

### 5.1 Overlay 边界

运行环境分为两层：

```text
只读 H0 层：版本、模型、Task Loop、上下文、工具运行时、容器、Verifier
可变 Overlay：Prompt、Identity、Soul、Skill、Skill 路由、获准的工具说明和 Rail 配置
```

第一阶段只开放 `prompt + skill`。Tool 和 Rail 只有在 H0 稳定、能记录真实激活证据且可完整回滚后再开放。禁止候选修改模型参数、系统超时、Dockerfile、Verifier、数据、JiuWenSwarm 源码或 task86 profile。

每个 Overlay 必须具备：父 Harness 哈希、结构化变更清单、Overlay SHA256、允许表面检查、安装日志、激活证据和卸载/回滚结果。候选只安装到临时 JiuWenSwarm home；被拒绝的文件不得进入下一批 source。

### 5.2 配对评测

对每个候选 `H_t + delta`：

1. 从同一 `H0 manifest + H_t` 启动 source 与 candidate，使用相同 Case、模型配置、Token/时间预算和镜像摘要。
2. 每个 source/candidate 使用独立干净容器；不得复制上一次任务的 home、缓存或生成 Skill。
3. 目标 Case 至少配对重跑两次。高采样温度下交替运行顺序，避免把短时服务波动当作候选收益。
4. 记录目标原子检查的新增通过项，并确认声明的 Prompt/Skill/Tool/Rail 在轨迹中真实激活。
5. 通过目标评测后，运行固定的 4 至 6 题跨类型回归集；最后仍以完整 50 题 Epoch Full Checkpoint 决定晋升。

建议的首阶段晋升阈值：目标配对平均提升至少 `0.02`，回归集平均退化不超过 `0.01`，完整 50 题平均分必须严格高于当前 `H_t`。阈值应写入实验 manifest，运行后不得追改。

以下情况直接拒绝候选：

- 目标文件缺失、为空、损坏或无法被官方 Verifier 打开。
- 声明修改的能力没有在轨迹中激活，收益无法归因。
- 出现新的基础设施错误、越权访问、跨 Case 状态或非目标行为明显退化。
- 提升只出现在一次随机高分，重复实验不能复现。
- 完整 50 题没有优于当前 `H_t`。

晋升后生成新的只读 `H_{t+1}` 快照。下一轮 source 必须来自该快照，不能从候选临时目录或经验库全量 Skill 目录继续执行。

## 6. 版本与镜像固定

每次运行目录必须包含脱敏的 `run_manifest.yaml`，至少记录：

```yaml
agent_core:
  commit: <40-char sha>
jiuwenswarm:
  version: 0.2.3
  artifact: <wheel-or-source-ref>
  sha256: <sha256>
workbuddy:
  dataset_tree_sha256: <sha256>
  verifier_tree_sha256: <sha256>
runtime:
  profile: task86
  profile_sha256: <sha256>
  solver_image_id: <sha256:image-id>
  task_image_ids: <case-id-to-image-id>
model:
  logical_name: deepseek-v4-flash
  endpoint_fingerprint: <non-secret-id>
  config_sha256: <sha256>
harness:
  base_sha256: <sha256>
  overlay_sha256: <sha256-or-null>
```

固定要求：

- JiuWenSwarm 使用精确 wheel SHA256、精确 Git commit 或 OCI image digest，不使用 `develop`、浮动 tag 或本地路径安装。
- Dockerfile 的 `FROM` 也要解析为 digest；仅记录自动生成的本地 tag 不足以复现。
- 同时记录 `docker image inspect` 返回的 image ID、平台架构和构建上下文哈希。
- 模型配置记录非秘密参数和配置哈希，API Key 只通过环境注入，绝不写入 manifest 或日志。
- 运行启动后重新计算哈希；任一制品变化立即中止，不能静默重建后继续比较。

## 7. 实施清单

- [x] 接通 WorkBuddy workspace、Case 容器与官方 CompositeVerifier，并保留原子检查证据。
- [x] 增加 Solver 选择、JiuWenSwarm 版本期望值和运行超时配置。
- [x] 增加独立 AgentServer/Gateway/`agent.plan` 运行脚手架与版本不匹配拒绝。
- [ ] 保存 PyPI `jiuwenswarm==0.2.3` wheel、Agent Core `0.1.16` 依赖、task86 原始配置和工具清单的哈希。
- [x] 建立隔离 `task86` runtime profile，开启 Task Loop，配置压缩、工具、采样和执行上限；有效上下文窗口仍需从真实启动轨迹确认。
- [x] 在 `WorkBuddyOfficeBackend` 中按 `solver_backend` 分流，JiuWenSwarm 路径不依赖 `create_deep_agent()`。
- [ ] 用真实冒烟证明 JiuWenSwarm 的文件、代码和 Shell 工具全部在 Case 容器 `/workspace` 内执行；代码和协议测试已固定目录参数。
- [x] 实现 Agent Core Harness 到 JiuWenSwarm Overlay 的逐 Case 导出、Gateway 激活记录和容器销毁回滚。
- [ ] 生成并校验 `run_manifest.yaml`，对 floating ref、dirty checkout 和版本不匹配 fail closed。
- [ ] 完成 1 题协议冒烟、10 题锚点和 50 题 `H0` 复现门禁。
- [ ] 实现两次目标配对、4 至 6 题回归和完整 Epoch 晋升，并隔离被拒绝候选。
- [ ] 正式实验独立运行三次，报告逐轮均值、方差、费用、失败类型和逐题变化。

## 8. 最终验收

只有同时满足以下条件，才能开始对外报告 `H0 -> H1 -> H2` 的递归提升：

- `H0` 通过第 4 节全部门禁，且证据可从 manifest 复算。
- Source 与 candidate 的 runtime manifest 完全一致，只有 Harness Overlay 哈希不同。
- 候选提升能在重复配对、回归集和完整 50 题中复现。
- 每轮晋升均有可审计的能力激活证据、官方原子检查增量和拒绝原因。
- 任意历史轮次都能由固定制品重新运行，不依赖本地 `0.2.2` dirty 工作树或临时缓存。
