# openjiuwen.auto_harness.pipelines

Auto Harness 流水线实现子包，定义了流水线的基础接口和具体实现。流水线是 auto-harness 的核心编排单元，负责将多个阶段（stage）按顺序或依赖关系组合执行。内置两条流水线：`meta_evolve_pipeline`（默认的元演化流水线，执行评估、规划、实现、验证、提交、发布和经验总结）和 `extended_evolve_pipeline`（扩展演化流水线，执行竞品差距分析、扩展设计、并行实现/验证和激活合并）。

子模块：
- `base`：流水线基础接口（`PipelineStageMap`、`BasePipeline`）
- `meta_evolve_pipeline`：元演化流水线（`MetaEvolvePipeline`、`PRTaskPipeline`、`prepare_task_runtime`）
- `extended_evolve_pipeline`：扩展演化流水线（`ExtendedEvolvePipeline`、`ExtensionTaskPipeline`、`build_extension_task`、`prepare_extension_task_runtime`）

---

### function openjiuwen.auto_harness.pipelines.normalize_pipeline_name

```
normalize_pipeline_name(name: str) -> str
```

将遗留流水线名称规范化为当前内置名称。映射关系：`"pr_pipeline"` -> `"meta_evolve_pipeline"`，`"extended_harness_pipeline"` -> `"extended_evolve_pipeline"`。未识别的名称原样返回。

**参数**：
* **name**(`str`)：流水线名称

**返回**：规范化后的流水线名称

---

### class openjiuwen.auto_harness.pipelines.base.PipelineStageMap

```
@dataclass(frozen=True)
class openjiuwen.auto_harness.pipelines.base.PipelineStageMap
```

Slot -> stage 类的绑定映射。用于声明流水线中每个阶段槽位对应的 stage 实现类。

**字段**：
* **mapping**(`dict[str, type[BaseStage]]`)：slot 名称到 stage 类的映射，默认为空字典

#### resolve(slot: str) -> BaseStage

实例化绑定到指定 slot 的 stage。

**参数**：
* **slot**(`str`)：阶段槽位名称

**返回**：实例化的 `BaseStage` 对象

**异常**：
* `KeyError`：没有绑定到该 slot 的 stage

---

### class openjiuwen.auto_harness.pipelines.base.BasePipeline

```
class openjiuwen.auto_harness.pipelines.base.BasePipeline
```

流水线编排的基础接口。子类通过设置 `name`、`description`、`expected_outputs`、`stage_map` 和 `stage_order` 类属性来声明流水线元数据和阶段绑定。提供 stage 流式执行、结果捕获和失败检测等通用能力。

**类属性**：
* **name**(`str`)：流水线名称
* **description**(`str`)：流水线描述
* **expected_outputs**(`list[str]`)：预期产出物名称列表
* **stage_map**(`PipelineStageMap`)：slot -> stage 类绑定
* **stage_order**(`list[tuple[str, str]]`)：阶段顺序，每个元素为 `(slot, display_name)`

#### spec() -> PipelineSpec

```
@classmethod spec(cls)
```

返回流水线元数据。

**返回**：`PipelineSpec` 实例

#### stream(ctx: SessionContext | TaskContext) -> AsyncIterator[Any]

```
async stream(self, ctx: 'SessionContext | TaskContext') -> AsyncIterator[Any]
```

执行流水线。子类必须实现此方法。

**参数**：
* **ctx**(`SessionContext | TaskContext`)：会话或任务上下文

**返回**：异步事件迭代器

#### resolve_stage(slot: str) -> BaseStage

实例化绑定到指定 slot 的 stage。

**参数**：
* **slot**(`str`)：阶段槽位名称

**返回**：实例化的 `BaseStage` 对象

---

### class openjiuwen.auto_harness.pipelines.meta_evolve_pipeline.MetaEvolvePipeline

```
class openjiuwen.auto_harness.pipelines.meta_evolve_pipeline.MetaEvolvePipeline(BasePipeline)
```

内置元演化流水线。执行完整的评估 -> 规划 -> 实现 -> 验证 -> 提交 -> 发布 -> 经验总结流程。assess 和 plan 阶段在只读快照 worktree 中运行，确保分析基于最新的远程 base 分支。

**类属性**：
* **name**：`"meta_evolve_pipeline"`
* **description**：`"Default meta evolve pipeline."`
* **expected_outputs**：`["session_results"]`
* **stage_order**：`[("assess", "评估当前状态"), ("plan", "制定优化计划"), ("implement", "执行代码修改"), ("verify", "CI 门禁检查"), ("commit", "提交变更"), ("publish", "发布 PR"), ("learnings", "总结经验")]`
* **stage_map**：`{ASSESS: MetaAssessStage, PLAN: MetaPlanStage, LEARNINGS: LearningsStage}`

**示例**：

```python
pipeline = MetaEvolvePipeline()
async for event in pipeline.stream(session_ctx):
    if isinstance(event, StageResult):
        print(f"Stage {event.stage} finished: {event.status}")
```

#### stream(ctx: SessionContext) -> AsyncIterator[Any]

```
async stream(self, ctx: SessionContext) -> AsyncIterator[Any]
```

执行完整的元演化流水线。依次运行 assess+plan、task pipeline（逐任务执行 implement/verify/commit/publish）、记录会话结果、最后运行 learnings 阶段。

#### run_assess_and_plan_stream(ctx: SessionContext) -> AsyncIterator[Any]

```
async run_assess_and_plan_stream(self, ctx: SessionContext) -> AsyncIterator[Any]
```

运行评估和规划阶段。如果检测到显式 GitCode issue 修复任务，跳过 assess/plan 直接进入实现流程。否则在只读快照中运行 MetaAssessStage 和 MetaPlanStage。

#### run_task_pipeline_stream(ctx: SessionContext) -> AsyncIterator[Any]

```
async run_task_pipeline_stream(self, ctx: SessionContext) -> AsyncIterator[Any]
```

逐任务执行 PRTaskPipeline。受 `max_tasks_per_session` 限制，并在预算耗尽时停止。

#### run_learnings_stage_stream(ctx: SessionContext) -> AsyncIterator[Any]

```
async run_learnings_stage_stream(self, ctx: SessionContext) -> AsyncIterator[Any]
```

运行经验总结阶段。

#### run_assess_stage_stream(ctx: SessionContext, *, result_holder: list) -> AsyncIterator[Any]

```
async run_assess_stage_stream(self, ctx: SessionContext, *, result_holder: list) -> AsyncIterator[Any]
```

运行评估阶段并捕获结果。

#### run_plan_stage_stream(ctx: SessionContext, *, result_holder: list) -> AsyncIterator[Any]

```
async run_plan_stage_stream(self, ctx: SessionContext, *, result_holder: list) -> AsyncIterator[Any]
```

运行规划阶段并捕获结果。

---

### function openjiuwen.auto_harness.pipelines.meta_evolve_pipeline.meta_evolve_task_pipeline.prepare_task_runtime

```
async prepare_task_runtime(orchestrator: 'AutoHarnessOrchestrator', task: OptimizationTask) -> TaskRuntime
```

为单个任务准备 worktree、agent 和 rail。创建隔离的 worktree，初始化 EditSafetyRail，创建 task_agent、fix_agent 和 commit_agent，并记录已有的脏文件列表。

**参数**：
* **orchestrator**(`AutoHarnessOrchestrator`)：编排器实例
* **task**(`OptimizationTask`)：任务对象

**返回**：`TaskRuntime` 实例

---

### class openjiuwen.auto_harness.pipelines.meta_evolve_pipeline.PRTaskPipeline

```
class openjiuwen.auto_harness.pipelines.meta_evolve_pipeline.PRTaskPipeline(BasePipeline)
```

元演化流水线的任务级流水线。执行单个任务的完整流程：实现 -> 验证 -> 提交 -> 发布 PR。每个阶段失败时停止后续阶段。

**stage_map**：`{IMPLEMENT: MetaImplementStage, VERIFY: MetaVerifyStage, COMMIT: CommitStage, PUBLISH: PublishPRStage}`

#### stream(ctx: TaskContext) -> AsyncIterator[Any]

```
async stream(self, ctx: TaskContext) -> AsyncIterator[Any]
```

执行任务级流水线。依次运行实现、验证、提交和发布 PR 阶段。

#### run_implement_stage_stream(ctx: TaskContext) -> AsyncIterator[Any]

```
async run_implement_stage_stream(self, ctx: TaskContext) -> AsyncIterator[Any]
```

运行实现阶段。失败时抛出内部停止异常。

#### run_verify_stage_stream(ctx: TaskContext) -> AsyncIterator[Any]

```
async run_verify_stage_stream(self, ctx: TaskContext) -> AsyncIterator[Any]
```

运行验证阶段。失败时抛出内部停止异常。

#### run_commit_stage_stream(ctx: TaskContext) -> AsyncIterator[Any]

```
async run_commit_stage_stream(self, ctx: TaskContext) -> AsyncIterator[Any]
```

运行提交阶段。失败时抛出内部停止异常。

#### run_publish_pr_stage_stream(ctx: TaskContext) -> AsyncIterator[Any]

```
async run_publish_pr_stage_stream(self, ctx: TaskContext) -> AsyncIterator[Any]
```

运行发布 PR 阶段。失败时抛出内部停止异常。

#### run_isolated_stream(orchestrator, task) -> AsyncIterator[Any]

```
@classmethod async run_isolated_stream(cls, orchestrator: 'AutoHarnessOrchestrator', task: OptimizationTask) -> AsyncIterator[Any]
```

在超时保护内运行一个任务。创建 worktree 和 runtime，执行完整流水线，并在完成后清理 worktree。超时或异常时记录失败经验。

**参数**：
* **orchestrator**(`AutoHarnessOrchestrator`)：编排器实例
* **task**(`OptimizationTask`)：任务对象

**返回**：异步事件迭代器

---

### class openjiuwen.auto_harness.pipelines.extended_evolve_pipeline.ExtendedEvolvePipeline

```
class openjiuwen.auto_harness.pipelines.extended_evolve_pipeline.ExtendedEvolvePipeline(BasePipeline)
```

隔离扩展演化的会话流水线。执行竞品差距评估 -> 扩展方案设计 -> 依赖波次并行实现/验证 -> 激活（单扩展直接激活，多扩展先合并再激活）。

**类属性**：
* **name**：`"extended_evolve_pipeline"`
* **description**：`"Extended evolve generation pipeline."`
* **expected_outputs**：`["extension_design", "session_results"]`
* **stage_order**：`[("assess", "评估扩展缺口"), ("plan", "设计扩展方案"), ("build_verify", "实现/验证扩展"), ("activate", "激活扩展")]`
* **stage_map**：`{ASSESS: ExtendAssessStage, PLAN: ExtendPlanStage}`

#### stream(ctx: SessionContext) -> AsyncIterator[Any]

```
async stream(self, ctx: SessionContext) -> AsyncIterator[Any]
```

执行扩展演化流水线。先确保社区 skill 源仓库已克隆，然后依次运行评估、规划、依赖波次构建/验证，最后激活（单扩展直接激活，多扩展合并后激活）。

---

### function openjiuwen.auto_harness.pipelines.extended_evolve_pipeline.extension_task_pipeline.build_extension_task

```
build_extension_task(design: ExtensionDesign) -> OptimizationTask
```

构建任务包装器，使扩展运行可以复用任务上下文。生成的 topic 格式为 `runtime-extension:<extension_name>`。

**参数**：
* **design**(`ExtensionDesign`)：扩展设计方案

**返回**：`OptimizationTask` 实例

---

### function openjiuwen.auto_harness.pipelines.extended_evolve_pipeline.extension_task_pipeline.prepare_extension_task_runtime

```
async prepare_extension_task_runtime(orchestrator: 'AutoHarnessOrchestrator', design: ExtensionDesign, *, configure_shared_workspace: bool = True) -> TaskRuntime
```

为单个扩展构建准备干净的 worktree。创建隔离 worktree，配置共享工作区（可选），创建 task_agent 和 commit_agent。

**参数**：
* **orchestrator**(`AutoHarnessOrchestrator`)：编排器实例
* **design**(`ExtensionDesign`)：扩展设计方案
* **configure_shared_workspace**(`bool`)：是否配置共享工作区，默认 `True`

**返回**：`TaskRuntime` 实例

---

### class openjiuwen.auto_harness.pipelines.extended_evolve_pipeline.extension_task_pipeline.VerifiedExtensionTask

```
@dataclass
class openjiuwen.auto_harness.pipelines.extended_evolve_pipeline.extension_task_pipeline.VerifiedExtensionTask
```

已验证的扩展任务，准备进行串行激活。

**字段**：
* **design**(`ExtensionDesign`)：扩展设计方案
* **task**(`OptimizationTask`)：任务对象
* **ctx**(`TaskContext`)：任务上下文

---

### class openjiuwen.auto_harness.pipelines.extended_evolve_pipeline.ExtensionTaskPipeline

```
class openjiuwen.auto_harness.pipelines.extended_evolve_pipeline.ExtensionTaskPipeline(BasePipeline)
```

单个运行时扩展的构建、验证、提交和 PR 发布流水线。

**stage_map**：`{IMPLEMENT: ExtendImplementStage, VERIFY: ExtendVerifyStage, ACTIVATE: ExtendActivateStage}`

#### stream(ctx: TaskContext) -> AsyncIterator[Any]

```
async stream(self, ctx: TaskContext) -> AsyncIterator[Any]
```

执行完整的扩展任务流水线：实现 -> 验证 -> 激活。

#### run_build_verify_stream(ctx: TaskContext) -> AsyncIterator[Any]

```
async run_build_verify_stream(self, ctx: TaskContext) -> AsyncIterator[Any]
```

仅运行实现和验证阶段，用于并行波次。

#### run_isolated_stream(orchestrator, design) -> AsyncIterator[Any]

```
@classmethod async run_isolated_stream(cls, orchestrator: 'AutoHarnessOrchestrator', design: ExtensionDesign) -> AsyncIterator[Any]
```

在超时保护内运行一个完整的扩展任务（实现 + 验证 + 激活）。

**参数**：
* **orchestrator**(`AutoHarnessOrchestrator`)：编排器实例
* **design**(`ExtensionDesign`)：扩展设计方案

#### run_build_verify_isolated_stream(orchestrator, design, *, verified_tasks=None) -> AsyncIterator[Any]

```
@classmethod async run_build_verify_isolated_stream(cls, orchestrator: 'AutoHarnessOrchestrator', design: ExtensionDesign, *, verified_tasks: list[VerifiedExtensionTask] | None = None) -> AsyncIterator[Any]
```

在隔离 worktree 中运行实现和验证阶段。成功时将 `VerifiedExtensionTask` 追加到 `verified_tasks` 列表。

**参数**：
* **orchestrator**(`AutoHarnessOrchestrator`)：编排器实例
* **design**(`ExtensionDesign`)：扩展设计方案
* **verified_tasks**(`list[VerifiedExtensionTask] | None`)：可选的已验证任务收集列表

#### run_activate_stream(orchestrator, verified) -> AsyncIterator[Any]

```
@classmethod async run_activate_stream(cls, orchestrator: 'AutoHarnessOrchestrator', verified: VerifiedExtensionTask) -> AsyncIterator[Any]
```

激活一个已验证的扩展并记录最终结果。

**参数**：
* **orchestrator**(`AutoHarnessOrchestrator`)：编排器实例
* **verified**(`VerifiedExtensionTask`)：已验证的扩展任务
