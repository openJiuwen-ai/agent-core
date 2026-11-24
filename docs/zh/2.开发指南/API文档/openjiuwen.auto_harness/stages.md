# openjiuwen.auto_harness.stages

内置 auto-harness 阶段模块。每个阶段（stage）对应流水线中的一个执行步骤，通过统一的 `BaseStage` 接口实现流式输出。阶段分为 session 级别（跨任务）和 task 级别（单任务）两种作用域，由 `SessionStage` 和 `TaskStage` 分别承载。

子模块：
- `base`：阶段基类与作用域工具函数
- `assess`：评估当前状态与竞品差距分析
- `plan`：任务规划与扩展方案设计
- `implement`：代码修改与扩展实现
- `verify`：CI 门禁检查与扩展验证
- `commit`：Git 提交
- `publish_pr`：推送分支并创建 PR
- `learnings`：Session 结束后的反思与经验记录
- `activate`：扩展热加载与激活
- `merge`：多扩展合并
- `select_pipeline`：流水线选择

---

## 基础

### class openjiuwen.auto_harness.stages.base.BaseStage

```
class openjiuwen.auto_harness.stages.base.BaseStage

def __init__(self) -> None
```

所有阶段的基类。定义了阶段的元数据属性和流式执行接口。

**类属性**：
* **name**(`str`)：阶段名称，默认 `""`
* **display_name**(`str`)：阶段显示名称，默认 `""`
* **description**(`str`)：阶段描述，默认 `""`
* **slot**(`str`)：阶段槽位，默认 `""`
* **consumes**(`list[str]`)：消费的上游产物列表，默认 `[]`
* **produces**(`list[str]`)：产出的下游产物列表，默认 `[]`
* **scope**(`str`)：作用域（`"session"` 或 `"task"`），默认 `"session"`

#### classmethod spec()

```
classmethod spec(cls) -> StageSpec
```

返回阶段元数据 `StageSpec`，包含 name、stage_cls、description、consumes、produces、scope、slot。

#### async stream(ctx: SessionContext | TaskContext) -> AsyncIterator[StageEvent]

```
async stream(self, ctx: "SessionContext | TaskContext") -> AsyncIterator[StageEvent]
```

以流式方式执行阶段。子类必须重写此方法。

**参数**：
* **ctx**(`SessionContext | TaskContext`)：执行上下文

**返回**：`AsyncIterator[StageEvent]`，其中 `StageEvent = OutputSchema | StageResult`

---

### class openjiuwen.auto_harness.stages.base.SessionStage

```
class openjiuwen.auto_harness.stages.base.SessionStage(BaseStage)
```

Session 级别阶段的基类。`scope` 固定为 `"session"`。

#### async stream(ctx: SessionContext) -> AsyncIterator[StageEvent]

```
async stream(self, ctx: "SessionContext") -> AsyncIterator[StageEvent]
```

以流式方式执行 session 级别阶段。子类必须重写此方法。

**参数**：
* **ctx**(`SessionContext`)：Session 级执行上下文

---

### class openjiuwen.auto_harness.stages.base.TaskStage

```
class openjiuwen.auto_harness.stages.base.TaskStage(BaseStage)
```

Task 级别阶段的基类。`scope` 固定为 `"task"`。

#### async stream(ctx: TaskContext) -> AsyncIterator[StageEvent]

```
async stream(self, ctx: "TaskContext") -> AsyncIterator[StageEvent]
```

以流式方式执行 task 级别阶段。子类必须重写此方法。

**参数**：
* **ctx**(`TaskContext`)：Task 级执行上下文

---

### function openjiuwen.auto_harness.stages.base.scope_output_event_stage

```
openjiuwen.auto_harness.stages.base.scope_output_event_stage(event: Any, stage: str) -> Any
```

将嵌套的 agent 进度事件限定到外部阶段。如果事件是 `OutputSchema` 且类型为 `"message"` 或 `"stage_result"`，则在 payload 中设置 `stage` 字段。

**参数**：
* **event**(`Any`)：待限定范围的事件
* **stage**(`str`)：外部阶段名称

**返回**：限定范围后的事件对象

---

## Assess

### class openjiuwen.auto_harness.stages.assess.AssessStage

```
class openjiuwen.auto_harness.stages.assess.AssessStage(SessionStage)
```

所有 assess 系列阶段的抽象基类。

**类属性**：
* **name** = `"assess"`
* **slot** = `"assess"`
* **display_name** = `"评估当前状态"`
* **description** = `"Assess current repository state."`
* **produces** = `["assessment"]`

#### async stream(ctx: SessionContext) -> AsyncIterator[Any]

```
async stream(self, ctx: SessionContext) -> AsyncIterator[Any]
```

抽象方法，子类必须重写。

---

### class openjiuwen.auto_harness.stages.assess.MetaAssessStage

```
class openjiuwen.auto_harness.stages.assess.MetaAssessStage(AssessStage)
```

评估当前 session 对应的仓库状态。调用 `run_assess_stream` 流式生成评估报告，并在完成后产出 `AssessmentArtifact`。

#### async stream(ctx: SessionContext) -> AsyncIterator[Any]

```
async stream(self, ctx: SessionContext) -> AsyncIterator[Any]
```

流式执行评估。从上下文中获取 `input_tasks`，调用 assess agent 生成报告，最终 yield `StageResult`，包含 `assessment` 产物。

**参数**：
* **ctx**(`SessionContext`)：Session 级执行上下文

**返回**：`AsyncIterator[Any]` — 流式 OutputSchema 事件 + 最终 StageResult

---

### class openjiuwen.auto_harness.stages.assess.ExtendAssessStage

```
class openjiuwen.auto_harness.stages.assess.ExtendAssessStage(AssessStage)
```

分析 runtime extension 能力缺口。使用 assess agent 执行差距分析，产出 `GapAnalysisArtifact`。

**类属性**：
* **name** = `"assess_ext"`
* **display_name** = `"评估扩展缺口"`
* **description** = `"Analyze runtime extension capability gaps."`
* **produces** = `["gap_analysis"]`

#### async stream(ctx: SessionContext) -> AsyncIterator[Any]

```
async stream(self, ctx: SessionContext) -> AsyncIterator[Any]
```

流式执行差距分析。优先使用 agent 分析，失败时回退到启发式方法。最终 yield `StageResult`，包含 `gap_analysis` 产物。

**参数**：
* **ctx**(`SessionContext`)：Session 级执行上下文

---

### function openjiuwen.auto_harness.stages.assess.run_assess_stream

```
openjiuwen.auto_harness.stages.assess.run_assess_stream(
    config: AutoHarnessConfig,
    experience_store: "ExperienceStore",
    *,
    input_tasks: list[OptimizationTask] | None = None,
    extra_rails: list | None = None,
) -> AsyncIterator[Any]
```

流式评估，yield OutputSchema 事件块。

**参数**：
* **config**(`AutoHarnessConfig`)：Auto Harness 配置
* **experience_store**(`ExperienceStore`)：ExperienceStore 实例
* **input_tasks**(`list[OptimizationTask] | None`)：可选的输入任务列表
* **extra_rails**(`list | None`)：调用方注入的额外 rails

**返回**：`AsyncIterator[Any]` — DeepAgent 输出的流式事件

---

### function openjiuwen.auto_harness.stages.assess.run_gap_analysis

```
openjiuwen.auto_harness.stages.assess.run_gap_analysis(
    config: AutoHarnessConfig,
    harness_state: str,
) -> List[Gap]
```

用 DeepAgent 分析与竞品的差距。

**参数**：
* **config**(`AutoHarnessConfig`)：Auto Harness 配置
* **harness_state**(`str`)：当前 harness 评估文本

**返回**：`List[Gap]` — 按优先级排序的差距列表；agent 失败时返回空列表

---

## Plan

### class openjiuwen.auto_harness.stages.plan.PlanStage

```
class openjiuwen.auto_harness.stages.plan.PlanStage(SessionStage)
```

所有 plan 系列阶段的抽象基类。

**类属性**：
* **name** = `"plan"`
* **slot** = `"plan"`
* **display_name** = `"制定优化计划"`
* **description** = `"Plan optimization tasks."`
* **produces** = `["task_plan"]`

#### async stream(ctx: SessionContext) -> AsyncIterator[Any]

```
async stream(self, ctx: SessionContext) -> AsyncIterator[Any]
```

抽象方法，子类必须重写。

---

### class openjiuwen.auto_harness.stages.plan.MetaPlanStage

```
class openjiuwen.auto_harness.stages.plan.MetaPlanStage(PlanStage)
```

为当前 session 生成任务计划。调用 `run_plan_stream` 流式生成计划文本，解析为任务列表后产出 `TaskPlanArtifact`。规划阶段只保留最高优先级的 1 个任务。

**类属性**：
* **consumes** = `["assessment"]`

#### async stream(ctx: SessionContext) -> AsyncIterator[Any]

```
async stream(self, ctx: SessionContext) -> AsyncIterator[Any]
```

流式执行规划。从上下文获取 `assessment` 和 `input_tasks`，调用 plan agent，解析任务，最终 yield `StageResult`，包含 `task_plan` 产物。

**参数**：
* **ctx**(`SessionContext`)：Session 级执行上下文

---

### class openjiuwen.auto_harness.stages.plan.ExtendPlanStage

```
class openjiuwen.auto_harness.stages.plan.ExtendPlanStage(PlanStage)
```

将能力缺口转化为具体的扩展设计方案。消费 `gap_analysis` 产物，产出 `ExtensionDesignArtifact`。

**类属性**：
* **name** = `"plan_ext"`
* **display_name** = `"设计扩展方案"`
* **description** = `"Design runtime extensions from analyzed gaps."`
* **consumes** = `["gap_analysis"]`
* **produces** = `["extension_design"]`

#### async stream(ctx: SessionContext) -> AsyncIterator[Any]

```
async stream(self, ctx: SessionContext) -> AsyncIterator[Any]
```

流式执行扩展设计。优先使用 design_ext agent，失败时回退到启发式方法。最终 yield `StageResult`，包含 `extension_design` 产物。

**参数**：
* **ctx**(`SessionContext`)：Session 级执行上下文

---

### function openjiuwen.auto_harness.stages.plan.run_plan_stream

```
openjiuwen.auto_harness.stages.plan.run_plan_stream(
    config: AutoHarnessConfig,
    assessment: str,
    experience_store: "ExperienceStore",
    *,
    input_tasks: list | None = None,
    extra_rails: list | None = None,
) -> AsyncIterator[Any]
```

用 DeepAgent 生成任务列表（流式）。

**参数**：
* **config**(`AutoHarnessConfig`)：Auto Harness 配置
* **assessment**(`str`)：评估报告文本
* **experience_store**(`ExperienceStore`)：ExperienceStore 实例
* **input_tasks**(`list | None`)：可选的输入任务列表
* **extra_rails**(`list | None`)：调用方注入的额外 rails

**返回**：`AsyncIterator[Any]` — plan agent 输出的流式事件

---

## Implement

### class openjiuwen.auto_harness.stages.implement.ImplementStage

```
class openjiuwen.auto_harness.stages.implement.ImplementStage(TaskStage)
```

所有 implement 系列阶段的抽象基类。

**类属性**：
* **name** = `"implement"`
* **slot** = `"implement"`
* **display_name** = `"执行代码修改"`
* **description** = `"Implement code changes."`
* **produces** = `["code_change"]`

#### async stream(ctx: TaskContext) -> AsyncIterator[Any]

```
async stream(self, ctx: TaskContext) -> AsyncIterator[Any]
```

抽象方法，子类必须重写。

---

### class openjiuwen.auto_harness.stages.implement.MetaImplementStage

```
class openjiuwen.auto_harness.stages.implement.MetaImplementStage(ImplementStage)
```

执行当前任务的代码修改。调用 `run_implement_stream` 流式驱动 task agent 完成改码，并通过 git status/diff 提取实际修改文件列表，产出 `CodeChangeArtifact`。

#### async stream(ctx: TaskContext) -> AsyncIterator[Any]

```
async stream(self, ctx: TaskContext) -> AsyncIterator[Any]
```

流式执行代码修改。构建实现 prompt，驱动 agent 改码，检测工作区 diff 提取编辑文件。如果 agent 失败或未产生 diff，会尝试复用分支已有提交或 cherry-pick 已有修复。最终 yield `StageResult`。

**参数**：
* **ctx**(`TaskContext`)：Task 级执行上下文

---

### class openjiuwen.auto_harness.stages.implement.ExtendImplementStage

```
class openjiuwen.auto_harness.stages.implement.ExtendImplementStage(ImplementStage)
```

将一个扩展设计物化到 task worktree 中。消费 `extension_target`（`ExtensionDesign`），产出 `ExtensionBuildArtifact`。

**类属性**：
* **name** = `"implement_ext"`
* **display_name** = `"实现扩展"`
* **produces** = `["extension_build"]`
* **consumes** = `["extension_target"]`

#### async stream(ctx: TaskContext) -> AsyncIterator[Any]

```
async stream(self, ctx: TaskContext) -> AsyncIterator[Any]
```

流式实现扩展。从设计产物解析扩展根目录和 manifest 路径，构建实现 prompt，驱动 agent 生成代码，最终 yield `StageResult`，包含 `extension_build` 产物。

**参数**：
* **ctx**(`TaskContext`)：Task 级执行上下文

---

### function openjiuwen.auto_harness.stages.implement.run_implement_stream

```
openjiuwen.auto_harness.stages.implement.run_implement_stream(
    agent: "DeepAgent | None",
    task: OptimizationTask,
    related: list[Experience],
    session: "Session | None" = None,
    prompt: str | None = None,
) -> AsyncIterator[Any]
```

通过 task agent 流式执行任务实现。

**参数**：
* **agent**(`DeepAgent | None`)：任务 agent；为 None 时跳过
* **task**(`OptimizationTask`)：待实现的优化任务
* **related**(`list[Experience]`)：相关经验列表
* **session**(`Session | None`)：可选的 session 实例
* **prompt**(`str | None`)：可选的自定义 prompt；为 None 时自动生成

**返回**：`AsyncIterator[Any]` — agent 输出的流式事件

---

### function openjiuwen.auto_harness.stages.implement.promote_runtime

```
openjiuwen.auto_harness.stages.implement.promote_runtime(
    ctx: "TaskContext",
) -> "RuntimeExtensionArtifact"
```

将已验证的扩展构建产物提升到 session 运行时目录。将扩展目录复制到 session runtime 根目录下，返回 `RuntimeExtensionArtifact`。

**参数**：
* **ctx**(`TaskContext`)：Task 级执行上下文

**返回**：`RuntimeExtensionArtifact` — 包含 extension_name、runtime_path、config_path

---

## Verify

### class openjiuwen.auto_harness.stages.verify.VerifyStage

```
class openjiuwen.auto_harness.stages.verify.VerifyStage(TaskStage)
```

所有 verify 系列阶段的抽象基类。

**类属性**：
* **name** = `"verify"`
* **slot** = `"verify"`
* **display_name** = `"CI 门禁检查"`
* **description** = `"Verify code changes."`
* **produces** = `["verify_report"]`

#### async stream(ctx: TaskContext) -> Any

```
async stream(self, ctx: TaskContext) -> Any
```

抽象方法，子类必须重写。

---

### class openjiuwen.auto_harness.stages.verify.MetaVerifyStage

```
class openjiuwen.auto_harness.stages.verify.MetaVerifyStage(VerifyStage)
```

为当前任务运行 CI 门禁和修复循环。消费 `code_change` 产物，产出 `VerifyReportArtifact`。

**类属性**：
* **consumes** = `["code_change"]`

#### async stream(ctx: TaskContext) -> AsyncIterator[Any]

```
async stream(self, ctx: TaskContext) -> AsyncIterator[Any]
```

流式执行 CI 检查。先运行所有 CI 门禁，若未通过则启动修复循环（fix loop），包含 agent 修复、CI 重跑和评审。修复失败时回滚变更并记录失败经验。最终 yield `StageResult`。

**参数**：
* **ctx**(`TaskContext`)：Task 级执行上下文

---

### class openjiuwen.auto_harness.stages.verify.ExtendVerifyStage

```
class openjiuwen.auto_harness.stages.verify.ExtendVerifyStage(VerifyStage)
```

验证扩展的 manifest、import、lint 和构造函数。消费 `extension_build` 产物，产出 `extension_build` 和 `verify_report`。

**类属性**：
* **name** = `"verify_ext"`
* **display_name** = `"验证扩展"`
* **consumes** = `["extension_build"]`
* **produces** = `["extension_build", "verify_report"]`

#### async stream(ctx: TaskContext) -> AsyncIterator[Any]

```
async stream(self, ctx: TaskContext) -> AsyncIterator[Any]
```

流式执行扩展验证。包含以下步骤：
1. 安装扩展依赖（requirements.txt）
2. 运行结构/静态校验（最多 3 轮修复）
3. 运行 agent 生成的验收测试（最多 3 轮修复）
4. 通过后调用 `promote_runtime` 提升到运行时目录

最终 yield `StageResult`，包含 `extension_build`、`verify_report`、`runtime_extension` 产物。

**参数**：
* **ctx**(`TaskContext`)：Task 级执行上下文

---

## Commit

### class openjiuwen.auto_harness.stages.commit.CommitRoundResult

```
class openjiuwen.auto_harness.stages.commit.CommitRoundResult
```

单次提交尝试的结构化结果。

**字段**：
* **ok**(`bool`)：提交是否成功
* **reason**(`str`)：失败原因（成功时为空）
* **status_text**(`str`)：提交后的 git status 输出
* **last_commit_stat**(`str`)：最近一次提交的统计摘要

---

### class openjiuwen.auto_harness.stages.commit.CommitStage

```
class openjiuwen.auto_harness.stages.commit.CommitStage(TaskStage)
```

为当前任务创建 git 提交。消费 `verify_report` 产物，产出 `commit_result`。采用确定性提交策略：收集提交事实、校验 issue 目标对齐、自动 `git add` + `git commit`。

**类属性**：
* **name** = `"commit"`
* **slot** = `"commit"`
* **display_name** = `"提交变更"`
* **description** = `"Create a git commit for the task."`
* **consumes** = `["verify_report"]`
* **produces** = `["commit_result"]`

#### async stream(ctx: TaskContext) -> AsyncIterator[Any]

```
async stream(self, ctx: TaskContext) -> AsyncIterator[Any]
```

流式执行提交流程。收集提交事实（分支名、编辑文件、允许文件等），校验 issue 目标对齐，执行确定性提交。失败时记录失败经验。最终 yield `StageResult`，包含 `commit_result` 产物。

**参数**：
* **ctx**(`TaskContext`)：Task 级执行上下文

---

## Publish

### class openjiuwen.auto_harness.stages.publish_pr.PublishPRStage

```
class openjiuwen.auto_harness.stages.publish_pr.PublishPRStage(TaskStage)
```

推送分支、创建 PR 并完成任务结果。消费 `verify_report` 和 `commit_result`，产出 `pull_request` 和 `task_result`。

**类属性**：
* **name** = `"publish_pr"`
* **slot** = `"publish"`
* **display_name** = `"发布 PR"`
* **description** = `"Push branch and create PR when configured."`
* **consumes** = `["verify_report", "commit_result"]`
* **produces** = `["pull_request", "task_result"]`

#### async stream(ctx: TaskContext) -> AsyncIterator[StageResult]

```
async stream(self, ctx: TaskContext) -> AsyncIterator[StageResult]
```

流式执行发布流程：
1. 校验 commit_result 是否已提交
2. 若配置了远程仓库，生成 PR draft（最多 2 次尝试，失败时使用确定性兜底）
3. 推送分支
4. 创建 PR（需配置 `git_remote` 和 `fork_owner`）
5. 记录成功经验，yield `StageResult`

**参数**：
* **ctx**(`TaskContext`)：Task 级执行上下文

**返回**：`AsyncIterator[StageResult]`

---

## Learnings

### class openjiuwen.auto_harness.stages.learnings.LearningsStage

```
class openjiuwen.auto_harness.stages.learnings.LearningsStage(SessionStage)
```

Session 结束后记录经验。消费 `session_results` 产物，产出更新后的 `session_results`。

**类属性**：
* **name** = `"learnings"`
* **slot** = `"learnings"`
* **display_name** = `"总结经验"`
* **description** = `"Record learnings after a session."`
* **consumes** = `["session_results"]`
* **produces** = `["session_results"]`

#### async stream(ctx: SessionContext) -> AsyncIterator[Any]

```
async stream(self, ctx: SessionContext) -> AsyncIterator[Any]
```

流式执行经验总结。调用 `run_learnings` 驱动 learnings agent 反思 session 结果，最终 yield `StageResult`。

**参数**：
* **ctx**(`SessionContext`)：Session 级执行上下文

---

### function openjiuwen.auto_harness.stages.learnings.run_learnings

```
openjiuwen.auto_harness.stages.learnings.run_learnings(
    config: AutoHarnessConfig,
    results: List[CycleResult],
    experience_store: "ExperienceStore",
    *,
    extra_rails: list | None = None,
) -> AsyncIterator[Any]
```

Session 结束后的反思与经验记录（流式）。

**参数**：
* **config**(`AutoHarnessConfig`)：Auto Harness 配置
* **results**(`List[CycleResult]`)：本次 session 的执行结果列表
* **experience_store**(`ExperienceStore`)：ExperienceStore 实例
* **extra_rails**(`list | None`)：调用方注入的额外 rails

**返回**：`AsyncIterator[Any]` — learnings agent 输出的流式事件

---

## Activate

### class openjiuwen.auto_harness.stages.activate.LoadedComponents

```
class openjiuwen.auto_harness.stages.activate.LoadedComponents
```

热加载的扩展组件信息。

**字段**：
* **rails**(`list[str]`)：已加载的 rail 列表，默认 `[]`
* **tools**(`list[str]`)：已加载的 tool 列表，默认 `[]`
* **skills**(`list[str]`)：已加载的 skill 列表，默认 `[]`

---

### class openjiuwen.auto_harness.stages.activate.ExtendActivateStage

```
class openjiuwen.auto_harness.stages.activate.ExtendActivateStage(TaskStage)
```

激活阶段：预览扩展信息、等待用户确认、执行热加载。消费 `runtime_extension` 和 `verify_report`，产出 `activate_decision`。

**类属性**：
* **name** = `"activate_ext"`
* **display_name** = `"激活扩展"`
* **slot** = `StageSlot.ACTIVATE`
* **consumes** = `["runtime_extension", "verify_report"]`
* **produces** = `["activate_decision"]`

#### async stream(ctx: TaskContext) -> AsyncIterator[Any]

```
async stream(self, ctx: "TaskContext") -> AsyncIterator[Any]
```

流式执行激活流程：
1. 产出 `extension_ready` 事件，展示扩展信息
2. 创建交互请求（`activate_confirm`），等待用户 accept/reject
3. 若用户拒绝，清理运行时目录并返回失败
4. 若用户接受，将扩展配置排入热加载队列
5. 流式生成测试引导信息
6. yield `StageResult`

**参数**：
* **ctx**(`TaskContext`)：Task 级执行上下文

---

### function openjiuwen.auto_harness.stages.activate.unload_extension

```
openjiuwen.auto_harness.stages.activate.unload_extension(
    runtime_ext: RuntimeExtensionArtifact,
    session_id: str,
) -> None
```

卸载已热加载的扩展：清理 `sys.modules` 中的相关模块并删除运行时目录。

**参数**：
* **runtime_ext**(`RuntimeExtensionArtifact`)：要卸载的运行时扩展
* **session_id**(`str`)：Session ID

---

## Merge

### class openjiuwen.auto_harness.stages.merge.MergeSuccessResult

```
class openjiuwen.auto_harness.stages.merge.MergeSuccessResult
```

合并成功时产出的结构化结果，与状态事件区分。

**字段**：
* **artifact**(`RuntimeExtensionArtifact`)：合并后的运行时扩展产物

---

### class openjiuwen.auto_harness.stages.merge.MergeActivationBlock

```
class openjiuwen.auto_harness.stages.merge.MergeActivationBlock
```

合并多个已验证的运行时扩展并执行静态检查。首次静态检查失败时创建 merge agent 进行修复，最多重试 3 轮。成功时 yield `MergeSuccessResult` 和 `"success"` 状态事件；耗尽重试时抛出 `MergedExtensionError`。

**类属性**：
* **name** = `"merge_ext"`

#### async stream(orchestrator: Any, verified_tasks: list[VerifiedExtensionTask], package_name: str = "") -> AsyncIterator[Any]

```
async stream(self, orchestrator: Any, verified_tasks: list[VerifiedExtensionTask], package_name: str = "") -> AsyncIterator[Any]
```

流式执行合并流程：
1. 合并多个运行时扩展到 session 目录
2. 运行静态检查
3. 若失败，使用 merge agent 修复（最多 3 轮）
4. 成功后清理源扩展目录，yield `MergeSuccessResult`

**参数**：
* **orchestrator**(`Any`)：编排器实例
* **verified_tasks**(`list[VerifiedExtensionTask]`)：已验证的扩展任务列表
* **package_name**(`str`)：可选的合并包名；为空时由模型或规则生成

---

## Select Pipeline

### function openjiuwen.auto_harness.stages.select_pipeline.run_select_pipeline_stream

```
openjiuwen.auto_harness.stages.select_pipeline.run_select_pipeline_stream(
    config: AutoHarnessConfig,
    task: OptimizationTask,
    *,
    assessment: str = "",
    available_pipelines: List[str] | None = None,
) -> AsyncIterator[Any]
```

以流式模式运行流水线选择 agent。

**参数**：
* **config**(`AutoHarnessConfig`)：Auto Harness 配置
* **task**(`OptimizationTask`)：优化任务
* **assessment**(`str`)：评估摘要文本，默认 `""`
* **available_pipelines**(`List[str] | None`)：可选的流水线名称列表；为 None 时默认 `[META_EVOLVE_PIPELINE]`

**返回**：`AsyncIterator[Any]` — selector agent 输出的流式事件

---

### function openjiuwen.auto_harness.stages.select_pipeline.run_select_pipeline

```
openjiuwen.auto_harness.stages.select_pipeline.run_select_pipeline(
    config: AutoHarnessConfig,
    task: OptimizationTask,
    *,
    assessment: str = "",
    available_pipelines: List[str] | None = None,
) -> PipelineSelectionArtifact
```

选择已配置或自动检测的流水线。直接调用 `choose_session_pipeline` 进行确定性选择。

**参数**：
* **config**(`AutoHarnessConfig`)：Auto Harness 配置
* **task**(`OptimizationTask`)：优化任务
* **assessment**(`str`)：评估摘要文本（当前未使用），默认 `""`
* **available_pipelines**(`List[str] | None`)：可选的流水线名称列表；为 None 时默认 `[META_EVOLVE_PIPELINE, EXTENDED_EVOLVE_PIPELINE]`

**返回**：`PipelineSelectionArtifact` — 流水线选择结果
