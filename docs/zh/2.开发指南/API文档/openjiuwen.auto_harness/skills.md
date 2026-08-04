# openjiuwen.auto_harness.skills

Auto Harness 技能（Skill）参考。每个技能是一个 `SKILL.md` 提示词文件，定义了 auto-harness 各阶段 agent 的行为规范和工作流约束。技能文件被 agent 在运行时加载，作为系统提示的一部分注入到 LLM 上下文中。

所有技能文件均标记为 `immutable: true`，运行时不可被 agent 修改。

---

## assess

| 属性 | 值 |
|---|---|
| **名称** | `assess` |
| **描述** | 评估方法论 — 根据 query 中的评估模式执行代码库健康评估或 runtime extension 能力缺口评估 |
| **触发词** | `repository_health_assessment`、`runtime_extension_gap_assessment` |
| **工具** | read_file, glob_tool, grep_tool, list_dir, experience_search |

根据评估模式选择不同评估方法：代码库健康评估适用于优化 auto-harness 自身、增强 CLI、修 pipeline 等场景；Runtime Extension 能力缺口评估用于分析用户目标能力与当前可用能力之间的缺口。

---

## commit

| 属性 | 值 |
|---|---|
| **名称** | `commit` |
| **描述** | 基于 commit skill 的自主提交流程。适用于 implement 阶段在提交前规划范围并通过 bash 完成 git 提交 |
| **触发词** | commit、git add、git commit、提交变更 |
| **工具** | read_file, glob_tool, grep_tool, bash_tool |

定义提交阶段的固定工作流：读取 git 状态 → 收缩提交范围 → 生成 commit message → `git add` 明确文件 → `git commit` → 提交后自检。禁止混入旧脏文件和当前任务之外的文件。

---

## communicate

| 属性 | 值 |
|---|---|
| **名称** | `communicate` |
| **描述** | 沟通规范 — 约束 commit message、PR、journal 和求助信息的表达方式 |
| **触发词** | commit message、PR 描述、journal、沟通、表达 |
| **工具** | bash_tool, experience_search |

定义技术沟通内容的写作规范，包括 conventional commits 格式的 commit message、PR 描述模板、journal 条目格式和求助信息的标准表达方式。

---

## design_ext

| 属性 | 值 |
|---|---|
| **名称** | `design_ext` |
| **描述** | 扩展方案设计 — 将能力缺口转化为 ExtensionDesign 结构 |
| **触发词** | 扩展设计、ExtensionDesign、gap 转化、能力缺口 |
| **工具** | read_file, glob_tool, grep_tool, experience_search, bash_tool |

将 GapAnalysisArtifact 中的能力缺口转化为可执行的运行时扩展方案。只读阶段，不得修改文件。优先复用社区 skill，检查是否有匹配的社区 skill 可设置 `skill_source='community:<skill_name>'`；无匹配时才从零设计。

---

## implement

| 属性 | 值 |
|---|---|
| **名称** | `implement` |
| **描述** | 实现阶段主操作手册 — 指导 agent 完成改码与局部验证，并把提交留给独立 commit phase |
| **触发词** | 实现、改码、代码修改、implement、局部验证 |
| **工具** | read_file, write_file, edit_file, glob_tool, grep_tool, bash_tool, experience_search |

指导 agent 在严格范围内完成单个优化任务。固定工作流：理解任务 → 收集上下文 → 最小修改 → 局部验证 → 检查改动事实 → 生成提交计划 → 停止在未提交状态。禁止执行 git commit/push 等提交动作。

---

## implement_ext

| 属性 | 值 |
|---|---|
| **名称** | `implement_ext` |
| **描述** | 扩展实现阶段 — 在 worktree 中生成运行时扩展代码 |
| **触发词** | 扩展实现、runtime extension、implement_ext、worktree |
| **工具** | read_file, write_file, edit_file, glob_tool, grep_tool, bash_tool, experience_search |

在隔离 worktree 中生成运行时扩展代码。严格按 ExtensionDesign 的 components 实现，不自动补充未声明组件。支持社区 skill 复用（`skill_source='community:<skill_name>'` 时跳过 skill 创建）。包含依赖识别与 requirements.txt 生成、代码生成、manifest 生成和局部语法验证。

---

## plan

| 属性 | 值 |
|---|---|
| **名称** | `plan` |
| **描述** | 规划规范 — 将评估结果收敛为结构化任务计划 |
| **触发词** | 规划、任务计划、plan、优化计划 |
| **工具** | read_file, glob_tool, grep_tool, experience_search, bash_tool |

将评估事实转成可执行任务。固定工作流：阅读评估报告 → 检查经验 → 收敛到单个最高优先级任务 → 明确范围和文件。当前固定走 `extended_evolve_pipeline`，每轮只允许输出 1 个 task，每个任务最多涉及 3 个源文件。

---

## select_pipeline

| 属性 | 值 |
|---|---|
| **名称** | `select_pipeline` |
| **描述** | 流水线选择规范 — 根据任务和事实选择最合适的 pipeline |
| **触发词** | 流水线选择、pipeline、select_pipeline |
| **工具** | read_file, experience_search, bash_tool |

流水线选择代理。当前策略固定选择 `extended_evolve_pipeline`，用于优先产出可隔离加载的 runtime extension。`fallback_pipeline` 也必须是 `extended_evolve_pipeline`。

---

## verify

| 属性 | 值 |
|---|---|
| **名称** | `verify` |
| **描述** | 验证规范 — 定义实现阶段应满足的验证等级与通过标准 |
| **触发词** | 验证、verify、lint、type-check、测试 |
| **工具** | read_file, bash_tool, glob_tool, grep_tool |

定义代码变更的验证等级和通过标准。按变更范围分 4 级：L1（单文件 lint + 单测）→ L2（多文件 + 类型检查）→ L3（跨模块 + 全量测试）→ L4（公共 API 变更 + 示例验证）。

---

## verify_ext

| 属性 | 值 |
|---|---|
| **名称** | `verify_ext` |
| **描述** | Runtime extension 验证规范 — 验证 harness package 中 tools、rails、skills 是否能真实热加载并可运行 |
| **触发词** | 扩展验证、verify_ext、runtime extension 验证、热加载验证 |
| **工具** | read_file, bash_tool, glob_tool, grep_tool |

验证生成的 runtime extension 在真实 harness 加载路径里可注册、可观测、可调用。验证分 3 层：L1 结构校验（manifest schema、module 路径、ToolCard 构造）→ L2 临时热加载（DeepAgent.load_expert_harness 验证注册）→ L3 运行时验收（Tool invoke、Rail 副作用、Skill 加载、文件产物格式校验）。
