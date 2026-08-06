# openjiuwen.auto_harness

Auto Harness Agent — 自主优化 harness 框架的编码 agent。

本子包在 `openjiuwen.harness` 之上构建了一套自驱动的编码 agent：由 `AutoHarnessOrchestrator` 调度 session 级与 task 级 pipeline，内置 `meta_evolve`（元演进）与 `extended_evolve`（扩展演进）两条 pipeline，覆盖 assess / plan / implement / verify / commit / publish_pr / learnings / activate / merge 等阶段，并配套经验库、安全护栏、预算控制、git worktree 隔离、CI 门控等基础设施。

> 说明：本页为子包级索引，按源码子包结构给出各类与函数的一句话说明；完整签名、字段、方法与示例请参见下方各子包详细文档。带 `_` 前缀的内部类型不在此列。

## 详细文档

各子包的完整 API 参考（含类签名、`__init__` 参数、方法列表、返回值与示例）：

- [orchestrator（顶层入口）](./openjiuwen.auto_harness/orchestrator.md)
- [schema（数据模型）](./openjiuwen.auto_harness/schema.md)
- [agents（智能体工厂）](./openjiuwen.auto_harness/agents.md)
- [artifacts（产物存储）](./openjiuwen.auto_harness/artifacts.md)
- [contexts（执行上下文）](./openjiuwen.auto_harness/contexts.md)
- [experience（经验库）](./openjiuwen.auto_harness/experience.md)
- [infra（基础设施）](./openjiuwen.auto_harness/infra.md)
- [pipelines（流水线）](./openjiuwen.auto_harness/pipelines.md)
- [prompts（提示词）](./openjiuwen.auto_harness/prompts.md)
- [rails（安全护栏）](./openjiuwen.auto_harness/rails.md)
- [registry（注册表）](./openjiuwen.auto_harness/registry.md)
- [stages（阶段）](./openjiuwen.auto_harness/stages.md)
- [tools（工具）](./openjiuwen.auto_harness/tools.md)
- [resources（配置模板）](./openjiuwen.auto_harness/resources.md)
- [skills（提示词技能）](./openjiuwen.auto_harness/skills.md)

## 顶层入口

| 类 / 函数 | 说明 |
|---|---|
| `AutoHarnessOrchestrator` | Session 控制器与顶层 pipeline 调度器。 |
| `create_auto_harness_orchestrator` | 创建一个 orchestrator 实例。 |
| `AutoHarnessConfig` | Auto Harness Agent 配置。 |
| `AutoHarnessPaths` | 运行所需的路径派生结果。 |
| `AutoHarnessRuntimeState` | 运行时状态。 |
| `normalize_pipeline_preference` | 规范化用户面向的 pipeline 偏好取值。 |
| `load_auto_harness_config` | 从 YAML 文件加载 `AutoHarnessConfig`。 |
| `is_placeholder_local_repo` | 判断路径是否为明显的模板/示例值。 |

## schema（数据模型）

枚举与数据类定义于 `schema.py`。

| 类 / 函数 | 说明 |
|---|---|
| `TaskStatus` | 优化任务状态（枚举）。 |
| `ExperienceType` | 经验记录类型（枚举）。 |
| `StageSlot` | 跨 pipeline 共享的阶段槽位名（枚举）。 |
| `Gap` | 竞品差距。 |
| `OptimizationTask` | 单个优化任务。 |
| `Experience` | 经验库记录。 |
| `ResearchContext` | Research 阶段收集的上下文。 |
| `CycleResult` | 单个 task 的执行结果。 |
| `AssessmentArtifact` | Assess 阶段的结构化产物。 |
| `TaskPlanArtifact` | Plan 阶段的结构化产物。 |
| `PipelineSelectionArtifact` | select_pipeline 阶段的结构化产物。 |
| `GapAnalysisArtifact` | extended evolve pipeline 的差距分析输出。 |
| `ExtensionDesign` | 单个扩展设计候选。 |
| `ExtensionDesignArtifact` | 运行时扩展生成的设计产物。 |
| `ExtensionBuildArtifact` | task worktree 中已验证的扩展构建产物。 |
| `RuntimeExtensionArtifact` | session 本地已晋升的运行时扩展。 |
| `SessionResultsArtifact` | session 聚合结果。 |
| `CodeChangeArtifact` | Implement 阶段输出。 |
| `VerifyReportArtifact` | Verify 阶段输出。 |
| `CommitArtifact` | Commit 阶段输出。 |
| `PullRequestArtifact` | Publish 阶段输出。 |
| `PullRequestDraft` | communicate agent 生成的结构化 PR 草稿。 |
| `CommitFacts` | 提交阶段的事实快照。 |
| `ProjectProfile` | 项目画像，承载 repo-specific 默认值。 |
| `StageResult` | 统一的 stage 执行结果。 |
| `StageSpec` | Stage 的声明式元数据。 |
| `PipelineSpec` | Pipeline 的声明式模板。 |
| `ActivateDecision` | activate 阶段的用户决策结果。 |

## agents（智能体工厂）

定义于 `agents/factory.py`。

| 函数 | 说明 |
|---|---|
| `create_auto_harness_agent` | 创建主任务实现 agent。 |
| `create_commit_agent` | 创建专用的 commit 阶段 agent。 |
| `create_assess_agent` | 创建评估阶段 agent。 |
| `create_plan_agent` | 创建规划阶段 agent。 |
| `create_eval_agent` | 创建 verify 修复循环使用的评估 agent。 |
| `create_select_pipeline_agent` | 创建 pipeline 选择 agent。 |
| `create_design_ext_agent` | 创建 Phase 2 的扩展设计 agent。 |
| `create_pr_draft_agent` | 创建仅用于 PR 草稿的 communicate agent。 |
| `create_learnings_agent` | 创建 session 经验反思 agent。 |
| `create_merge_ext_agent` | 创建扩展合并修复 agent。 |
| `create_activate_guide_agent` | 创建 activate 测试引导 agent。 |

## artifacts（产物存储）

| 类 / 函数 | 说明 |
|---|---|
| `ArtifactStore` | 带 session 与 task 命名空间的作用域产物存储，用于阶段间通信。 |

## contexts（执行上下文）

定义于 `contexts/execution.py`。

| 类 / 函数 | 说明 |
|---|---|
| `TaskRuntime` | 已准备的 task 作用域执行依赖。 |
| `BaseExecutionContext` | 共享执行上下文表面。 |
| `SessionContext` | 传入 session pipeline 与 stage 的运行时上下文。 |
| `TaskContext` | 传入 task pipeline 与 stage 的运行时上下文。 |
| `task_key` | 返回某 task 的作用域产物键。 |

## experience（经验库）

| 类 | 说明 |
|---|---|
| `ExperienceStore` | JSONL 后端的经验归档，支持关键词检索。 |
| `ActiveContextSynthesizer` | 将近期经验浓缩为 prompt 注入片段的活跃上下文合成器。 |

## infra（基础设施）

### 运行时与门控

| 类 / 函数 | 说明 |
|---|---|
| `CIGateRunner` | 执行 CI 门控检查（lint / test / type-check）并返回结构化结果。 |
| `decode_stdout` | 跨平台编码处理解码子进程 stdout。 |
| `FixLoopResult` | 修复循环执行结果。 |
| `FixLoopController` | 两阶段 CI 修复循环（不可变）。 |
| `SessionBudgetController` | 管理单次会话的时钟预算与 API 成本预算。 |

### Git 与 Worktree

| 类 / 函数 | 说明 |
|---|---|
| `GitOperations` | Git 操作（branch / push / PR，GitCode API）。 |
| `WorktreeManager` | 为每个 task 创建和清理 git worktree。 |
| `build_git_auth_env` | 为非交互式 GitCode 鉴权构建子进程环境。 |

### 运行时扩展

| 类 / 函数 | 说明 |
|---|---|
| `RuntimeHarnessManifest` | 运行时 `harness_config.yaml` 的顶层 schema。 |
| `MetaSchema` / `SectionSchema` | 治理元数据与单个 prompt section 条目 schema。 |
| `ToolResourceSchema` / `RailResourceSchema` | 工具 / 护栏资源规格 schema。 |
| `SkillsSchema` / `McpResourceSchema` | skills 与 MCP server 规格 schema。 |
| `ResourcesSchema` / `PromptsSchema` / `WorkspaceSchema` | 资源、prompt 声明、工作区 schema。 |
| `load_runtime_manifest` | 解析并校验单个 `harness_config.yaml`。 |
| `load_runtime_rails` / `load_runtime_tools` / `load_runtime_skill_dirs` | 从运行时扩展 manifest 加载 rail / tool 类与 skill 目录。 |
| `MergeRuntimeExtensionsResult` | `merge_runtime_extensions` 的输出。 |
| `MergedExtensionError` | 合并运行时扩展失败时抛出。 |
| `merge_runtime_extensions` | 确定性地将多个已验证扩展合并为单个扩展。 |
| `ExtStaticCheckResult` | 扩展的静态校验计数与错误。 |
| `check_ruff` | 对扩展根目录自动修复格式后做 lint 检查。 |
| `run_static_checks_against_runtime` | manifest schema + rail/tool 实例化 + skill_dirs + ruff 综合静态检查。 |

### 解析与选择

| 函数 | 说明 |
|---|---|
| `parse_tasks` | 从 agent 输出中解析 JSON 任务列表。 |
| `parse_learnings` | 从 learnings agent 输出中解析 JSON 经验列表。 |
| `parse_pr_draft` / `parse_pr_draft_with_error` | 解析 PR 草稿 JSON 响应（后者带详细错误）。 |
| `parse_pipeline_selection` | 解析 selector agent 的 JSON 响应。 |
| `extract_text` | 从 OutputSchema chunk 中提取文本内容。 |
| `parse_gaps` | 将结构化文本解析为 `Gap` 对象。 |
| `parse_extension_designs` | 从 agent 输出中解析 `ExtensionDesign` JSON 列表。 |
| `detect_pipeline_signal` | 检测 session 是否应路由到 extended evolve。 |
| `choose_session_pipeline` | 依据配置偏好与信号选择 session pipeline。 |
| `normalize_pipeline_name` | 将遗留 pipeline 名规范化为当前内置名。 |

### PR 模板与社区 skills

| 类 / 函数 | 说明 |
|---|---|
| `load_pr_template_fallback` | GitCode API 不可用时返回内置 PR 模板。 |
| `template_suffix_for_language` | 将语言配置映射为 GitCode 模板文件名后缀。 |
| `pick_pr_template_entry` | 从 GitCode 列表响应中挑选最佳模板元数据。 |
| `fetch_pr_template` | 为配置的仓库拉取上游 PR 模板文本。 |
| `GitHubCliStatus` | GitHub CLI 预检结果。 |
| `ensure_github_cli_ready` | 确保 `gh` 存在并在需要时输出登录指引。 |
| `SkillMatch` | 一个带元数据的已发现社区 skill。 |
| `ensure_skill_sources` | 下载或更新社区 skill 源仓库。 |
| `scan_skills` | 扫描所有缓存的 skill 源仓库。 |
| `copy_skill_to_extension` | 将一个社区 skill 复制到扩展的 skills/ 目录。 |
| `community_skill_cache_skill_dirs` | 返回每个缓存仓库内的 skill 根目录路径。 |
| `format_community_skill_list` | 将可用社区 skill 格式化为 prompt 友好的列表。 |

### 编辑范围

| 函数 | 说明 |
|---|---|
| `is_documentation_file` | 判断路径是否指向 docs/ 下的 markdown 文件。 |
| `is_allowed_documentation_file` | 判断文档文件是否位于允许的 docs 布局内。 |
| `derive_test_files` | 从声明的源文件派生候选测试文件。 |
| `is_derived_test_file` | 检查候选是否匹配源→测试映射规则。 |
| `extract_verify_related_files` | 从验证输出中提取显式提及的测试文件路径。 |
| `derive_legacy_related_test_files` | 仅当被改编的遗留测试同时被编辑且被直接引用时才允许。 |
| `normalize_repo_path` | 尽可能将工具路径规范化为 repo 相对 POSIX 路径。 |
| `is_allowed_repo_edit_path` | 判断路径是否位于允许的 auto-harness 编辑范围内。 |
| `render_edit_scope` | 为 prompt 渲染稳定的编辑范围块。 |

## pipelines（流水线）

| 类 / 函数 | 说明 |
|---|---|
| `PipelineStageMap` | 槽位 → stage 类的 pipeline 绑定。 |
| `BasePipeline` | 显式 pipeline 编排的基接口。 |
| `ExtendedEvolvePipeline` | 用于隔离扩展演进的 session 级 pipeline。 |
| `ExtensionTaskPipeline` | 为单个生成的运行时扩展构建、验证、提交并发布 PR 的 task 级 pipeline。 |
| `VerifiedExtensionTask` | 一个已验证、可串行激活的扩展任务。 |
| `build_extension_task` | 构建任务包装器以复用 task 上下文。 |
| `prepare_extension_task_runtime` | 为单个扩展构建准备干净 worktree。 |
| `MetaEvolvePipeline` | 内置的元演进 session 级 pipeline。 |
| `PRTaskPipeline` | 元演进专用的 task 级显式 pipeline。 |
| `prepare_task_runtime` | 为单个 task 准备 worktree、agents 与 rails。 |

## prompts（提示词）

| 函数 | 说明 |
|---|---|
| `build_auto_harness_sections` | 构建 Auto Harness Agent 的 prompt sections。 |

## rails（安全护栏）

所有护栏均继承 `DeepAgentRail` 或 `ContextProcessorRail`。

| 类 | 说明 |
|---|---|
| `BudgetRail` | 监控时钟与成本预算，记录 CI 迭代。 |
| `CancellationRail` | 检查 orchestrator.should_cancel 并请求强制结束。 |
| `AutoHarnessContextRail` | 不注入 workspace/context 的上下文处理护栏。 |
| `EditSafetyRail` | 跟踪已编辑文件并在 Python 写入后运行 ruff。 |
| `AutoHarnessExperienceRail` | 注册经验工具并注入经验 prompt section。 |
| `RevertOnFailureRail` | 跟踪 base commit SHA 以提供回退能力。 |
| `SecurityRail` | 不可变文件保护 + 输入净化。 |

## registry（注册表）

| 类 / 函数 | 说明 |
|---|---|
| `BaseRegistry` | 共享的泛型注册表实现。 |
| `StageRegistry` | stage 元数据注册表。 |
| `PipelineRegistry` | pipeline 元数据注册表。 |
| `register_builtin_stages` | 注册内置 stage 元数据。 |
| `build_stage_registry` | 从内置与扩展构建 stage 注册表。 |
| `build_pipeline_registry` | 从内置与扩展构建 pipeline 注册表。 |

## stages（阶段）

阶段基类定义于 `stages/base.py`：`BaseStage` / `SessionStage` / `TaskStage`。

| 类 / 函数 | 说明 |
|---|---|
| `BaseStage` | 所有阶段的基接口。 |
| `SessionStage` | session 作用域阶段基类。 |
| `TaskStage` | task 作用域阶段基类。 |
| `scope_output_event_stage` | 将嵌套 agent 进度事件作用域到外层 stage。 |
| `AssessStage` | 所有 assess 族阶段的抽象基类。 |
| `MetaAssessStage` | 评估当前 session 的仓库状态。 |
| `ExtendAssessStage` | 用 assess agent 分析运行时扩展能力差距。 |
| `run_assess_stream` | 流式评估，yield OutputSchema chunks。 |
| `run_gap_analysis` | 用 DeepAgent 分析与竞品的差距。 |
| `PlanStage` | 所有 plan 族阶段的抽象基类。 |
| `MetaPlanStage` | 为当前 session 生成任务计划。 |
| `ExtendPlanStage` | 将差距转换为具体的扩展设计。 |
| `run_plan_stream` | 用 DeepAgent 生成任务列表（流式）。 |
| `ImplementStage` | 所有 implement 槽位阶段的抽象基类。 |
| `MetaImplementStage` | 为当前 task 执行代码变更。 |
| `ExtendImplementStage` | 将一个扩展设计物化到 task worktree。 |
| `run_implement_stream` | 通过 task agent 流式执行任务实现。 |
| `promote_runtime` | 将已验证的扩展构建晋升到 session 运行时目录。 |
| `VerifyStage` | 所有 verify 阶段的抽象基类。 |
| `MetaVerifyStage` | 为当前 task 运行 CI 与修复循环。 |
| `ExtendVerifyStage` | 校验 manifest、import、lint 与构造器。 |
| `CommitRoundResult` | 单次提交尝试的结构化结果。 |
| `CommitStage` | 为当前 task 创建 git commit。 |
| `PublishPRStage` | 推送分支、开启 PR 并完成 task 结果。 |
| `LearningsStage` | session 结束后记录经验反思。 |
| `run_learnings` | session 结束后的反思与经验记录（流式）。 |
| `LoadedComponents` | 热加载的扩展组件。 |
| `ExtendActivateStage` | activate 阶段：预览、确认后热加载。 |
| `unload_extension` | 卸载热加载扩展：清理 sys.modules + 移除运行时目录。 |
| `MergeActivationBlock` | 合并多个已验证扩展并运行静态检查。 |
| `MergeSuccessResult` | 合并成功时 yield 的结构化结果。 |
| `run_select_pipeline_stream` | 以流式模式运行 selector agent。 |
| `run_select_pipeline` | 为 task 选择已配置或自动检测的 pipeline。 |

## tools（工具）

| 类 | 说明 |
|---|---|
| `ExperienceSearchTool` | 只读的经验检索工具。 |
| `ExperienceSearchMetadataProvider` | `ExperienceSearchTool` 的元数据提供者。 |

## resources（配置模板）

`auto_harness/resources/` 下内置的 YAML 配置模板，供 `load_auto_harness_config` 与运行时扩展 manifest 使用。详见 [resources.md](./openjiuwen.auto_harness/resources.md)。

## skills（提示词技能）

`auto_harness/skills/` 下的 `SKILL.md` 提示词技能文件，由 agent 工厂通过 `skill_names` 引用并注入。详见 [skills.md](./openjiuwen.auto_harness/skills.md)。
