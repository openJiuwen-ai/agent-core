# openjiuwen.auto_harness.pipelines

Auto Harness pipeline implementation subpackage, defining the base interface and concrete implementations of pipelines. Pipelines are the core orchestration units of auto-harness, responsible for combining multiple stages in sequence or dependency order. Two built-in pipelines: `meta_evolve_pipeline` (the default meta-evolution pipeline, executing assessment, planning, implementation, verification, commit, publishing, and experience summarization) and `extended_evolve_pipeline` (the extended evolution pipeline, executing competitor gap analysis, extension design, parallel implementation/verification, and activation merging).

Submodules:
- `base`: Pipeline base interface (`PipelineStageMap`, `BasePipeline`)
- `meta_evolve_pipeline`: Meta-evolution pipeline (`MetaEvolvePipeline`, `PRTaskPipeline`, `prepare_task_runtime`)
- `extended_evolve_pipeline`: Extended evolution pipeline (`ExtendedEvolvePipeline`, `ExtensionTaskPipeline`, `build_extension_task`, `prepare_extension_task_runtime`)

---

### function openjiuwen.auto_harness.pipelines.normalize_pipeline_name

```
normalize_pipeline_name(name: str) -> str
```

Normalize legacy pipeline names to current built-in names. Mapping: `"pr_pipeline"` -> `"meta_evolve_pipeline"`, `"extended_harness_pipeline"` -> `"extended_evolve_pipeline"`. Unrecognized names are returned as-is.

**Parameters**:
* **name**(`str`): Pipeline name

**Returns**: Normalized pipeline name

---

### class openjiuwen.auto_harness.pipelines.base.PipelineStageMap

```
@dataclass(frozen=True)
class openjiuwen.auto_harness.pipelines.base.PipelineStageMap
```

Slot -> stage class binding map. Used to declare the stage implementation class corresponding to each stage slot in a pipeline.

**Fields**:
* **mapping**(`dict[str, type[BaseStage]]`): Slot name to stage class mapping, default empty dict

#### resolve(slot: str) -> BaseStage

Instantiate the stage bound to the specified slot.

**Parameters**:
* **slot**(`str`): Stage slot name

**Returns**: Instantiated `BaseStage` object

**Raises**:
* `KeyError`: No stage bound to this slot

---

### class openjiuwen.auto_harness.pipelines.base.BasePipeline

```
class openjiuwen.auto_harness.pipelines.base.BasePipeline
```

Base interface for explicit pipeline orchestration. Subclasses declare pipeline metadata and stage bindings by setting `name`, `description`, `expected_outputs`, `stage_map`, and `stage_order` class attributes. Provides common capabilities for stage streaming execution, result capture, and failure detection.

**Class attributes**:
* **name**(`str`): Pipeline name
* **description**(`str`): Pipeline description
* **expected_outputs**(`list[str]`): Expected output artifact name list
* **stage_map**(`PipelineStageMap`): Slot -> stage class binding
* **stage_order**(`list[tuple[str, str]]`): Stage order, each element is `(slot, display_name)`

#### spec() -> PipelineSpec

```
@classmethod spec(cls)
```

Return pipeline metadata.

**Returns**: `PipelineSpec` instance

#### stream(ctx: SessionContext | TaskContext) -> AsyncIterator[Any]

```
async stream(self, ctx: 'SessionContext | TaskContext') -> AsyncIterator[Any]
```

Execute the pipeline. Subclasses must implement this method.

**Parameters**:
* **ctx**(`SessionContext | TaskContext`): Session or task context

**Returns**: Async event iterator

#### resolve_stage(slot: str) -> BaseStage

Instantiate the stage bound to the specified slot.

**Parameters**:
* **slot**(`str`): Stage slot name

**Returns**: Instantiated `BaseStage` object

---

### class openjiuwen.auto_harness.pipelines.meta_evolve_pipeline.MetaEvolvePipeline

```
class openjiuwen.auto_harness.pipelines.meta_evolve_pipeline.MetaEvolvePipeline(BasePipeline)
```

Built-in meta-evolution pipeline. Executes the full assess -> plan -> implement -> verify -> commit -> publish -> learnings flow. The assess and plan stages run in a read-only snapshot worktree to ensure analysis is based on the latest remote base branch.

**Class attributes**:
* **name**: `"meta_evolve_pipeline"`
* **description**: `"Default meta evolve pipeline."`
* **expected_outputs**: `["session_results"]`
* **stage_order**: `[("assess", "Assess current state"), ("plan", "Create optimization plan"), ("implement", "Execute code changes"), ("verify", "CI gate checks"), ("commit", "Commit changes"), ("publish", "Publish PR"), ("learnings", "Summarize learnings")]`
* **stage_map**: `{ASSESS: MetaAssessStage, PLAN: MetaPlanStage, LEARNINGS: LearningsStage}`

**Example**:

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

Execute the full meta-evolution pipeline. Runs assess+plan, task pipeline (per-task implement/verify/commit/publish), records session results, then runs the learnings stage.

#### run_assess_and_plan_stream(ctx: SessionContext) -> AsyncIterator[Any]

```
async run_assess_and_plan_stream(self, ctx: SessionContext) -> AsyncIterator[Any]
```

Run assessment and planning stages. If an explicit GitCode issue fix task is detected, skips assess/plan and proceeds directly to implementation. Otherwise runs MetaAssessStage and MetaPlanStage in a read-only snapshot.

#### run_task_pipeline_stream(ctx: SessionContext) -> AsyncIterator[Any]

```
async run_task_pipeline_stream(self, ctx: SessionContext) -> AsyncIterator[Any]
```

Execute PRTaskPipeline per task. Limited by `max_tasks_per_session`, stops when budget is exhausted.

#### run_learnings_stage_stream(ctx: SessionContext) -> AsyncIterator[Any]

```
async run_learnings_stage_stream(self, ctx: SessionContext) -> AsyncIterator[Any]
```

Run the learnings stage.

#### run_assess_stage_stream(ctx: SessionContext, *, result_holder: list) -> AsyncIterator[Any]

```
async run_assess_stage_stream(self, ctx: SessionContext, *, result_holder: list) -> AsyncIterator[Any]
```

Run the assessment stage and capture results.

#### run_plan_stage_stream(ctx: SessionContext, *, result_holder: list) -> AsyncIterator[Any]

```
async run_plan_stage_stream(self, ctx: SessionContext, *, result_holder: list) -> AsyncIterator[Any]
```

Run the planning stage and capture results.

---

### function openjiuwen.auto_harness.pipelines.meta_evolve_pipeline.meta_evolve_task_pipeline.prepare_task_runtime

```
async prepare_task_runtime(orchestrator: 'AutoHarnessOrchestrator', task: OptimizationTask) -> TaskRuntime
```

Prepare worktree, agents, and rails for a single task. Creates an isolated worktree, initializes EditSafetyRail, creates task_agent, fix_agent, and commit_agent, and records the existing dirty file list.

**Parameters**:
* **orchestrator**(`AutoHarnessOrchestrator`): Orchestrator instance
* **task**(`OptimizationTask`): Task object

**Returns**: `TaskRuntime` instance

---

### class openjiuwen.auto_harness.pipelines.meta_evolve_pipeline.PRTaskPipeline

```
class openjiuwen.auto_harness.pipelines.meta_evolve_pipeline.PRTaskPipeline(BasePipeline)
```

Task-level pipeline for the meta-evolution pipeline. Executes the full flow for a single task: implement -> verify -> commit -> publish PR. Stops subsequent stages on failure.

**stage_map**: `{IMPLEMENT: MetaImplementStage, VERIFY: MetaVerifyStage, COMMIT: CommitStage, PUBLISH: PublishPRStage}`

#### stream(ctx: TaskContext) -> AsyncIterator[Any]

```
async stream(self, ctx: TaskContext) -> AsyncIterator[Any]
```

Execute the task-level pipeline. Runs implement, verify, commit, and publish PR stages in sequence.

#### run_implement_stage_stream(ctx: TaskContext) -> AsyncIterator[Any]

```
async run_implement_stage_stream(self, ctx: TaskContext) -> AsyncIterator[Any]
```

Run the implement stage. Throws internal stop exception on failure.

#### run_verify_stage_stream(ctx: TaskContext) -> AsyncIterator[Any]

```
async run_verify_stage_stream(self, ctx: TaskContext) -> AsyncIterator[Any]
```

Run the verify stage. Throws internal stop exception on failure.

#### run_commit_stage_stream(ctx: TaskContext) -> AsyncIterator[Any]

```
async run_commit_stage_stream(self, ctx: TaskContext) -> AsyncIterator[Any]
```

Run the commit stage. Throws internal stop exception on failure.

#### run_publish_pr_stage_stream(ctx: TaskContext) -> AsyncIterator[Any]

```
async run_publish_pr_stage_stream(self, ctx: TaskContext) -> AsyncIterator[Any]
```

Run the publish PR stage. Throws internal stop exception on failure.

#### run_isolated_stream(orchestrator, task) -> AsyncIterator[Any]

```
@classmethod async run_isolated_stream(cls, orchestrator: 'AutoHarnessOrchestrator', task: OptimizationTask) -> AsyncIterator[Any]
```

Run a single task within timeout protection. Creates worktree and runtime, executes the full pipeline, and cleans up the worktree on completion. Records failure experience on timeout or exception.

**Parameters**:
* **orchestrator**(`AutoHarnessOrchestrator`): Orchestrator instance
* **task**(`OptimizationTask`): Task object

**Returns**: Async event iterator

---

### class openjiuwen.auto_harness.pipelines.extended_evolve_pipeline.ExtendedEvolvePipeline

```
class openjiuwen.auto_harness.pipelines.extended_evolve_pipeline.ExtendedEvolvePipeline(BasePipeline)
```

Session-level pipeline for isolated extension evolution. Executes competitor gap assessment -> extension design -> dependency-wave parallel implementation/verification -> activation (single extension activates directly, multiple extensions merge first then activate).

**Class attributes**:
* **name**: `"extended_evolve_pipeline"`
* **description**: `"Extended evolve generation pipeline."`
* **expected_outputs**: `["extension_design", "session_results"]`
* **stage_order**: `[("assess", "Assess extension gaps"), ("plan", "Design extension plans"), ("build_verify", "Implement/verify extensions"), ("activate", "Activate extensions")]`
* **stage_map**: `{ASSESS: ExtendAssessStage, PLAN: ExtendPlanStage}`

#### stream(ctx: SessionContext) -> AsyncIterator[Any]

```
async stream(self, ctx: SessionContext) -> AsyncIterator[Any]
```

Execute the extended evolution pipeline. First ensures community skill source repos are cloned, then runs assess, plan, dependency-wave build/verify, and finally activates (single extension activates directly, multiple extensions merge then activate).

---

### function openjiuwen.auto_harness.pipelines.extended_evolve_pipeline.extension_task_pipeline.build_extension_task

```
build_extension_task(design: ExtensionDesign) -> OptimizationTask
```

Build task wrapper to reuse task context for extension execution. Generated topic format: `runtime-extension:<extension_name>`.

**Parameters**:
* **design**(`ExtensionDesign`): Extension design plan

**Returns**: `OptimizationTask` instance

---

### function openjiuwen.auto_harness.pipelines.extended_evolve_pipeline.extension_task_pipeline.prepare_extension_task_runtime

```
async prepare_extension_task_runtime(orchestrator: 'AutoHarnessOrchestrator', design: ExtensionDesign, *, configure_shared_workspace: bool = True) -> TaskRuntime
```

Prepare clean worktree for single extension build. Creates isolated worktree, configures shared workspace (optional), creates task_agent and commit_agent.

**Parameters**:
* **orchestrator**(`AutoHarnessOrchestrator`): Orchestrator instance
* **design**(`ExtensionDesign`): Extension design plan
* **configure_shared_workspace**(`bool`): Whether to configure shared workspace, default `True`

**Returns**: `TaskRuntime` instance

---

### class openjiuwen.auto_harness.pipelines.extended_evolve_pipeline.extension_task_pipeline.VerifiedExtensionTask

```
@dataclass
class openjiuwen.auto_harness.pipelines.extended_evolve_pipeline.extension_task_pipeline.VerifiedExtensionTask
```

A verified, serially activatable extension task.

**Fields**:
* **design**(`ExtensionDesign`): Extension design plan
* **task**(`OptimizationTask`): Task object
* **ctx**(`TaskContext`): Task context

---

### class openjiuwen.auto_harness.pipelines.extended_evolve_pipeline.ExtensionTaskPipeline

```
class openjiuwen.auto_harness.pipelines.extended_evolve_pipeline.ExtensionTaskPipeline(BasePipeline)
```

Task-level pipeline to build, verify, commit, and publish PR for a single generated runtime extension.

**stage_map**: `{IMPLEMENT: ExtendImplementStage, VERIFY: ExtendVerifyStage, ACTIVATE: ExtendActivateStage}`

#### stream(ctx: TaskContext) -> AsyncIterator[Any]

```
async stream(self, ctx: TaskContext) -> AsyncIterator[Any]
```

Execute the full extension task pipeline: implement -> verify -> activate.

#### run_build_verify_stream(ctx: TaskContext) -> AsyncIterator[Any]

```
async run_build_verify_stream(self, ctx: TaskContext) -> AsyncIterator[Any]
```

Run only the implement and verify stages, used for parallel waves.

#### run_isolated_stream(orchestrator, design) -> AsyncIterator[Any]

```
@classmethod async run_isolated_stream(cls, orchestrator: 'AutoHarnessOrchestrator', design: ExtensionDesign) -> AsyncIterator[Any]
```

Run a full extension task (implement + verify + activate) within timeout protection.

**Parameters**:
* **orchestrator**(`AutoHarnessOrchestrator`): Orchestrator instance
* **design**(`ExtensionDesign`): Extension design plan

#### run_build_verify_isolated_stream(orchestrator, design, *, verified_tasks=None) -> AsyncIterator[Any]

```
@classmethod async run_build_verify_isolated_stream(cls, orchestrator: 'AutoHarnessOrchestrator', design: ExtensionDesign, *, verified_tasks: list[VerifiedExtensionTask] | None = None) -> AsyncIterator[Any]
```

Run implement and verify stages in an isolated worktree. Appends `VerifiedExtensionTask` to the `verified_tasks` list on success.

**Parameters**:
* **orchestrator**(`AutoHarnessOrchestrator`): Orchestrator instance
* **design**(`ExtensionDesign`): Extension design plan
* **verified_tasks**(`list[VerifiedExtensionTask] | None`): Optional verified task collection list

#### run_activate_stream(orchestrator, verified) -> AsyncIterator[Any]

```
@classmethod async run_activate_stream(cls, orchestrator: 'AutoHarnessOrchestrator', verified: VerifiedExtensionTask) -> AsyncIterator[Any]
```

Activate a verified extension and record the final result.

**Parameters**:
* **orchestrator**(`AutoHarnessOrchestrator`): Orchestrator instance
* **verified**(`VerifiedExtensionTask`): Verified extension task
