# openjiuwen.auto_harness.stages

Built-in auto-harness stage module. Each stage corresponds to an execution step in the pipeline, implementing streaming output through the unified `BaseStage` interface. Stages are divided into session-level (cross-task) and task-level (single-task) scopes, handled by `SessionStage` and `TaskStage` respectively.

Submodules:
- `base`: Stage base classes and scope utility functions
- `assess`: Assess current state and competitor gap analysis
- `plan`: Task planning and extension design
- `implement`: Code changes and extension implementation
- `verify`: CI gate checks and extension verification
- `commit`: Git commit
- `publish_pr`: Push branch and create PR
- `learnings`: Post-session reflection and experience recording
- `activate`: Extension hot-loading and activation
- `merge`: Multi-extension merging
- `select_pipeline`: Pipeline selection

---

## Base

### class openjiuwen.auto_harness.stages.base.BaseStage

```
class openjiuwen.auto_harness.stages.base.BaseStage

def __init__(self) -> None
```

Base class for all stages. Defines stage metadata attributes and the streaming execution interface.

**Class attributes**:
* **name**(`str`): Stage name, default `""`
* **display_name**(`str`): Stage display name, default `""`
* **description**(`str`): Stage description, default `""`
* **slot**(`str`): Stage slot, default `""`
* **consumes**(`list[str]`): Consumed upstream artifact list, default `[]`
* **produces**(`list[str]`): Produced downstream artifact list, default `[]`
* **scope**(`str`): Scope (`"session"` or `"task"`), default `"session"`

#### classmethod spec()

```
classmethod spec(cls) -> StageSpec
```

Return stage metadata `StageSpec`, including name, stage_cls, description, consumes, produces, scope, slot.

#### async stream(ctx: SessionContext | TaskContext) -> AsyncIterator[StageEvent]

```
async stream(self, ctx: "SessionContext | TaskContext") -> AsyncIterator[StageEvent]
```

Execute the stage in streaming mode. Subclasses must override this method.

**Parameters**:
* **ctx**(`SessionContext | TaskContext`): Execution context

**Returns**: `AsyncIterator[StageEvent]`, where `StageEvent = OutputSchema | StageResult`

---

### class openjiuwen.auto_harness.stages.base.SessionStage

```
class openjiuwen.auto_harness.stages.base.SessionStage(BaseStage)
```

Base class for session-level stages. `scope` is fixed to `"session"`.

#### async stream(ctx: SessionContext) -> AsyncIterator[StageEvent]

```
async stream(self, ctx: "SessionContext") -> AsyncIterator[StageEvent]
```

Execute session-level stage in streaming mode. Subclasses must override this method.

**Parameters**:
* **ctx**(`SessionContext`): Session-level execution context

---

### class openjiuwen.auto_harness.stages.base.TaskStage

```
class openjiuwen.auto_harness.stages.base.TaskStage(BaseStage)
```

Base class for task-level stages. `scope` is fixed to `"task"`.

#### async stream(ctx: TaskContext) -> AsyncIterator[StageEvent]

```
async stream(self, ctx: "TaskContext") -> AsyncIterator[StageEvent]
```

Execute task-level stage in streaming mode. Subclasses must override this method.

**Parameters**:
* **ctx**(`TaskContext`): Task-level execution context

---

### function openjiuwen.auto_harness.stages.base.scope_output_event_stage

```
openjiuwen.auto_harness.stages.base.scope_output_event_stage(event: Any, stage: str) -> Any
```

Scope nested agent progress events to the outer stage. If the event is an `OutputSchema` of type `"message"` or `"stage_result"`, sets the `stage` field in the payload.

**Parameters**:
* **event**(`Any`): Event to scope
* **stage**(`str`): Outer stage name

**Returns**: Scoped event object

---

## Assess

### class openjiuwen.auto_harness.stages.assess.AssessStage

```
class openjiuwen.auto_harness.stages.assess.AssessStage(SessionStage)
```

Abstract base class for all assess family stages.

**Class attributes**:
* **name** = `"assess"`
* **slot** = `"assess"`
* **display_name** = `"Assess current state"`
* **description** = `"Assess current repository state."`
* **produces** = `["assessment"]`

#### async stream(ctx: SessionContext) -> AsyncIterator[Any]

```
async stream(self, ctx: SessionContext) -> AsyncIterator[Any]
```

Abstract method, subclasses must override.

---

### class openjiuwen.auto_harness.stages.assess.MetaAssessStage

```
class openjiuwen.auto_harness.stages.assess.MetaAssessStage(AssessStage)
```

Assess current session's repository state. Calls `run_assess_stream` to stream-generate the assessment report, producing `AssessmentArtifact` on completion.

#### async stream(ctx: SessionContext) -> AsyncIterator[Any]

```
async stream(self, ctx: SessionContext) -> AsyncIterator[Any]
```

Stream-execute assessment. Gets `input_tasks` from context, calls assess agent to generate report, finally yields `StageResult` containing the `assessment` artifact.

**Parameters**:
* **ctx**(`SessionContext`): Session-level execution context

**Returns**: `AsyncIterator[Any]` — Streaming OutputSchema events + final StageResult

---

### class openjiuwen.auto_harness.stages.assess.ExtendAssessStage

```
class openjiuwen.auto_harness.stages.assess.ExtendAssessStage(AssessStage)
```

Analyze runtime extension capability gaps with assess agent. Produces `GapAnalysisArtifact`.

**Class attributes**:
* **name** = `"assess_ext"`
* **display_name** = `"Assess extension gaps"`
* **description** = `"Analyze runtime extension capability gaps."`
* **produces** = `["gap_analysis"]`

#### async stream(ctx: SessionContext) -> AsyncIterator[Any]

```
async stream(self, ctx: SessionContext) -> AsyncIterator[Any]
```

Stream-execute gap analysis. Prefers agent analysis, falls back to heuristic on failure. Finally yields `StageResult` containing the `gap_analysis` artifact.

**Parameters**:
* **ctx**(`SessionContext`): Session-level execution context

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

Streaming assessment, yields OutputSchema event chunks.

**Parameters**:
* **config**(`AutoHarnessConfig`): Auto Harness configuration
* **experience_store**(`ExperienceStore`): ExperienceStore instance
* **input_tasks**(`list[OptimizationTask] | None`): Optional input task list
* **extra_rails**(`list | None`): Caller-injected extra rails

**Returns**: `AsyncIterator[Any]` — DeepAgent output streaming events

---

### function openjiuwen.auto_harness.stages.assess.run_gap_analysis

```
openjiuwen.auto_harness.stages.assess.run_gap_analysis(
    config: AutoHarnessConfig,
    harness_state: str,
) -> List[Gap]
```

Analyze gaps with competitors using DeepAgent.

**Parameters**:
* **config**(`AutoHarnessConfig`): Auto Harness configuration
* **harness_state**(`str`): Current harness assessment text

**Returns**: `List[Gap]` — Priority-sorted gap list; returns empty list on agent failure

---

## Plan

### class openjiuwen.auto_harness.stages.plan.PlanStage

```
class openjiuwen.auto_harness.stages.plan.PlanStage(SessionStage)
```

Abstract base class for all plan family stages.

**Class attributes**:
* **name** = `"plan"`
* **slot** = `"plan"`
* **display_name** = `"Create optimization plan"`
* **description** = `"Plan optimization tasks."`
* **produces** = `["task_plan"]`

#### async stream(ctx: SessionContext) -> AsyncIterator[Any]

```
async stream(self, ctx: SessionContext) -> AsyncIterator[Any]
```

Abstract method, subclasses must override.

---

### class openjiuwen.auto_harness.stages.plan.MetaPlanStage

```
class openjiuwen.auto_harness.stages.plan.MetaPlanStage(PlanStage)
```

Generate task plan for current session. Calls `run_plan_stream` to stream-generate plan text, parses into task list, produces `TaskPlanArtifact`. The planning stage retains only the single highest-priority task.

**Class attributes**:
* **consumes** = `["assessment"]`

#### async stream(ctx: SessionContext) -> AsyncIterator[Any]

```
async stream(self, ctx: SessionContext) -> AsyncIterator[Any]
```

Stream-execute planning. Gets `assessment` and `input_tasks` from context, calls plan agent, parses tasks, finally yields `StageResult` containing the `task_plan` artifact.

**Parameters**:
* **ctx**(`SessionContext`): Session-level execution context

---

### class openjiuwen.auto_harness.stages.plan.ExtendPlanStage

```
class openjiuwen.auto_harness.stages.plan.ExtendPlanStage(PlanStage)
```

Convert capability gaps into concrete extension designs. Consumes `gap_analysis` artifact, produces `ExtensionDesignArtifact`.

**Class attributes**:
* **name** = `"plan_ext"`
* **display_name** = `"Design extension plans"`
* **description** = `"Design runtime extensions from analyzed gaps."`
* **consumes** = `["gap_analysis"]`
* **produces** = `["extension_design"]`

#### async stream(ctx: SessionContext) -> AsyncIterator[Any]

```
async stream(self, ctx: SessionContext) -> AsyncIterator[Any]
```

Stream-execute extension design. Prefers design_ext agent, falls back to heuristic on failure. Finally yields `StageResult` containing the `extension_design` artifact.

**Parameters**:
* **ctx**(`SessionContext`): Session-level execution context

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

Generate task list with DeepAgent (streaming).

**Parameters**:
* **config**(`AutoHarnessConfig`): Auto Harness configuration
* **assessment**(`str`): Assessment report text
* **experience_store**(`ExperienceStore`): ExperienceStore instance
* **input_tasks**(`list | None`): Optional input task list
* **extra_rails**(`list | None`): Caller-injected extra rails

**Returns**: `AsyncIterator[Any]` — Plan agent output streaming events

---

## Implement

### class openjiuwen.auto_harness.stages.implement.ImplementStage

```
class openjiuwen.auto_harness.stages.implement.ImplementStage(TaskStage)
```

Abstract base class for all implement slot stages.

**Class attributes**:
* **name** = `"implement"`
* **slot** = `"implement"`
* **display_name** = `"Execute code changes"`
* **description** = `"Implement code changes."`
* **produces** = `["code_change"]`

#### async stream(ctx: TaskContext) -> AsyncIterator[Any]

```
async stream(self, ctx: TaskContext) -> AsyncIterator[Any]
```

Abstract method, subclasses must override.

---

### class openjiuwen.auto_harness.stages.implement.MetaImplementStage

```
class openjiuwen.auto_harness.stages.implement.MetaImplementStage(ImplementStage)
```

Execute code changes for current task. Calls `run_implement_stream` to stream-drive the task agent for code changes, extracts actual modified file list via git status/diff, produces `CodeChangeArtifact`.

#### async stream(ctx: TaskContext) -> AsyncIterator[Any]

```
async stream(self, ctx: TaskContext) -> AsyncIterator[Any]
```

Stream-execute code changes. Builds implementation prompt, drives agent for code changes, detects workspace diff to extract edited files. If the agent fails or produces no diff, attempts to reuse existing branch commits or cherry-pick existing fixes. Finally yields `StageResult`.

**Parameters**:
* **ctx**(`TaskContext`): Task-level execution context

---

### class openjiuwen.auto_harness.stages.implement.ExtendImplementStage

```
class openjiuwen.auto_harness.stages.implement.ExtendImplementStage(ImplementStage)
```

Materialize an extension design into task worktree. Consumes `extension_target` (`ExtensionDesign`), produces `ExtensionBuildArtifact`.

**Class attributes**:
* **name** = `"implement_ext"`
* **display_name** = `"Implement extension"`
* **produces** = `["extension_build"]`
* **consumes** = `["extension_target"]`

#### async stream(ctx: TaskContext) -> AsyncIterator[Any]

```
async stream(self, ctx: TaskContext) -> AsyncIterator[Any]
```

Stream-implement extension. Parses extension root and manifest path from design artifact, builds implementation prompt, drives agent to generate code, finally yields `StageResult` containing the `extension_build` artifact.

**Parameters**:
* **ctx**(`TaskContext`): Task-level execution context

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

Execute task implementation via task agent (streaming).

**Parameters**:
* **agent**(`DeepAgent | None`): Task agent; skips if None
* **task**(`OptimizationTask`): Optimization task to implement
* **related**(`list[Experience]`): Related experience list
* **session**(`Session | None`): Optional session instance
* **prompt**(`str | None`): Optional custom prompt; auto-generated if None

**Returns**: `AsyncIterator[Any]` — Agent output streaming events

---

### function openjiuwen.auto_harness.stages.implement.promote_runtime

```
openjiuwen.auto_harness.stages.implement.promote_runtime(
    ctx: "TaskContext",
) -> "RuntimeExtensionArtifact"
```

Promote verified extension build to session runtime directory. Copies the extension directory to the session runtime root, returns `RuntimeExtensionArtifact`.

**Parameters**:
* **ctx**(`TaskContext`): Task-level execution context

**Returns**: `RuntimeExtensionArtifact` — Contains extension_name, runtime_path, config_path

---

## Verify

### class openjiuwen.auto_harness.stages.verify.VerifyStage

```
class openjiuwen.auto_harness.stages.verify.VerifyStage(TaskStage)
```

Abstract base class for all verify stages.

**Class attributes**:
* **name** = `"verify"`
* **slot** = `"verify"`
* **display_name** = `"CI gate checks"`
* **description** = `"Verify code changes."`
* **produces** = `["verify_report"]`

#### async stream(ctx: TaskContext) -> Any

```
async stream(self, ctx: TaskContext) -> Any
```

Abstract method, subclasses must override.

---

### class openjiuwen.auto_harness.stages.verify.MetaVerifyStage

```
class openjiuwen.auto_harness.stages.verify.MetaVerifyStage(VerifyStage)
```

Run CI and fix loops for current task. Consumes `code_change` artifact, produces `VerifyReportArtifact`.

**Class attributes**:
* **consumes** = `["code_change"]`

#### async stream(ctx: TaskContext) -> AsyncIterator[Any]

```
async stream(self, ctx: TaskContext) -> AsyncIterator[Any]
```

Stream-execute CI checks. First runs all CI gates, if not passed starts fix loop (fix loop), including agent fixes, CI re-runs, and review. Rolls back changes and records failure experience on fix failure. Finally yields `StageResult`.

**Parameters**:
* **ctx**(`TaskContext`): Task-level execution context

---

### class openjiuwen.auto_harness.stages.verify.ExtendVerifyStage

```
class openjiuwen.auto_harness.stages.verify.ExtendVerifyStage(VerifyStage)
```

Validate manifest, imports, lint, and constructors. Consumes `extension_build` artifact, produces `extension_build` and `verify_report`.

**Class attributes**:
* **name** = `"verify_ext"`
* **display_name** = `"Verify extension"`
* **consumes** = `["extension_build"]`
* **produces** = `["extension_build", "verify_report"]`

#### async stream(ctx: TaskContext) -> AsyncIterator[Any]

```
async stream(self, ctx: TaskContext) -> AsyncIterator[Any]
```

Stream-execute extension verification. Includes the following steps:
1. Install extension dependencies (requirements.txt)
2. Run structure/static checks (up to 3 fix rounds)
3. Run agent-generated acceptance tests (up to 3 fix rounds)
4. On pass, calls `promote_runtime` to promote to runtime directory

Finally yields `StageResult` containing `extension_build`, `verify_report`, `runtime_extension` artifacts.

**Parameters**:
* **ctx**(`TaskContext`): Task-level execution context

---

## Commit

### class openjiuwen.auto_harness.stages.commit.CommitRoundResult

```
class openjiuwen.auto_harness.stages.commit.CommitRoundResult
```

Structured result of a single commit attempt.

**Fields**:
* **ok**(`bool`): Whether the commit was successful
* **reason**(`str`): Failure reason (empty on success)
* **status_text**(`str`): Git status output after commit
* **last_commit_stat**(`str`): Latest commit statistics summary

---

### class openjiuwen.auto_harness.stages.commit.CommitStage

```
class openjiuwen.auto_harness.stages.commit.CommitStage(TaskStage)
```

Create git commit for current task. Consumes `verify_report` artifact, produces `commit_result`. Uses deterministic commit strategy: collects commit facts, validates issue target alignment, auto `git add` + `git commit`.

**Class attributes**:
* **name** = `"commit"`
* **slot** = `"commit"`
* **display_name** = `"Commit changes"`
* **description** = `"Create a git commit for the task."`
* **consumes** = `["verify_report"]`
* **produces** = `["commit_result"]`

#### async stream(ctx: TaskContext) -> AsyncIterator[Any]

```
async stream(self, ctx: TaskContext) -> AsyncIterator[Any]
```

Stream-execute commit flow. Collects commit facts (branch name, edited files, allowed files, etc.), validates issue target alignment, executes deterministic commit. Records failure experience on failure. Finally yields `StageResult` containing the `commit_result` artifact.

**Parameters**:
* **ctx**(`TaskContext`): Task-level execution context

---

## Publish

### class openjiuwen.auto_harness.stages.publish_pr.PublishPRStage

```
class openjiuwen.auto_harness.stages.publish_pr.PublishPRStage(TaskStage)
```

Push branch, open PR, and complete task result. Consumes `verify_report` and `commit_result`, produces `pull_request` and `task_result`.

**Class attributes**:
* **name** = `"publish_pr"`
* **slot** = `"publish"`
* **display_name** = `"Publish PR"`
* **description** = `"Push branch and create PR when configured."`
* **consumes** = `["verify_report", "commit_result"]`
* **produces** = `["pull_request", "task_result"]`

#### async stream(ctx: TaskContext) -> AsyncIterator[StageResult]

```
async stream(self, ctx: TaskContext) -> AsyncIterator[StageResult]
```

Stream-execute publish flow:
1. Validate whether commit_result has been committed
2. If remote repository is configured, generate PR draft (up to 2 attempts, uses deterministic fallback on failure)
3. Push branch
4. Create PR (requires `git_remote` and `fork_owner` configured)
5. Record success experience, yield `StageResult`

**Parameters**:
* **ctx**(`TaskContext`): Task-level execution context

**Returns**: `AsyncIterator[StageResult]`

---

## Learnings

### class openjiuwen.auto_harness.stages.learnings.LearningsStage

```
class openjiuwen.auto_harness.stages.learnings.LearningsStage(SessionStage)
```

Record experience reflection after session ends. Consumes `session_results` artifact, produces updated `session_results`.

**Class attributes**:
* **name** = `"learnings"`
* **slot** = `"learnings"`
* **display_name** = `"Summarize learnings"`
* **description** = `"Record learnings after a session."`
* **consumes** = `["session_results"]`
* **produces** = `["session_results"]`

#### async stream(ctx: SessionContext) -> AsyncIterator[Any]

```
async stream(self, ctx: SessionContext) -> AsyncIterator[Any]
```

Stream-execute experience summarization. Calls `run_learnings` to drive the learnings agent to reflect on session results, finally yields `StageResult`.

**Parameters**:
* **ctx**(`SessionContext`): Session-level execution context

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

Post-session reflection and experience recording (streaming).

**Parameters**:
* **config**(`AutoHarnessConfig`): Auto Harness configuration
* **results**(`List[CycleResult]`): Execution result list for this session
* **experience_store**(`ExperienceStore`): ExperienceStore instance
* **extra_rails**(`list | None`): Caller-injected extra rails

**Returns**: `AsyncIterator[Any]` — Learnings agent output streaming events

---

## Activate

### class openjiuwen.auto_harness.stages.activate.LoadedComponents

```
class openjiuwen.auto_harness.stages.activate.LoadedComponents
```

Hot-loaded extension components.

**Fields**:
* **rails**(`list[str]`): Loaded rail list, default `[]`
* **tools**(`list[str]`): Loaded tool list, default `[]`
* **skills**(`list[str]`): Loaded skill list, default `[]`

---

### class openjiuwen.auto_harness.stages.activate.ExtendActivateStage

```
class openjiuwen.auto_harness.stages.activate.ExtendActivateStage(TaskStage)
```

Activate stage: preview extension info, await user confirmation, execute hot-loading. Consumes `runtime_extension` and `verify_report`, produces `activate_decision`.

**Class attributes**:
* **name** = `"activate_ext"`
* **display_name** = `"Activate extension"`
* **slot** = `StageSlot.ACTIVATE`
* **consumes** = `["runtime_extension", "verify_report"]`
* **produces** = `["activate_decision"]`

#### async stream(ctx: TaskContext) -> AsyncIterator[Any]

```
async stream(self, ctx: "TaskContext") -> AsyncIterator[Any]
```

Stream-execute activation flow:
1. Produce `extension_ready` event, displaying extension info
2. Create interaction request (`activate_confirm`), await user accept/reject
3. If user rejects, clean up runtime directory and return failure
4. If user accepts, queue extension config for hot-loading
5. Stream-generate test guidance information
6. Yield `StageResult`

**Parameters**:
* **ctx**(`TaskContext`): Task-level execution context

---

### function openjiuwen.auto_harness.stages.activate.unload_extension

```
openjiuwen.auto_harness.stages.activate.unload_extension(
    runtime_ext: RuntimeExtensionArtifact,
    session_id: str,
) -> None
```

Unload hot-loaded extension: clean `sys.modules` of related modules and delete runtime directory.

**Parameters**:
* **runtime_ext**(`RuntimeExtensionArtifact`): Runtime extension to unload
* **session_id**(`str`): Session ID

---

## Merge

### class openjiuwen.auto_harness.stages.merge.MergeSuccessResult

```
class openjiuwen.auto_harness.stages.merge.MergeSuccessResult
```

Structured result yielded on successful merge, distinguished from status events.

**Fields**:
* **artifact**(`RuntimeExtensionArtifact`): Merged runtime extension artifact

---

### class openjiuwen.auto_harness.stages.merge.MergeActivationBlock

```
class openjiuwen.auto_harness.stages.merge.MergeActivationBlock
```

Merge multiple verified runtime extensions and run static checks. Creates merge agent for fixes on first static check failure, up to 3 retry rounds. Yields `MergeSuccessResult` and `"success"` status event on success; raises `MergedExtensionError` when retries are exhausted.

**Class attributes**:
* **name** = `"merge_ext"`

#### async stream(orchestrator: Any, verified_tasks: list[VerifiedExtensionTask], package_name: str = "") -> AsyncIterator[Any]

```
async stream(self, orchestrator: Any, verified_tasks: list[VerifiedExtensionTask], package_name: str = "") -> AsyncIterator[Any]
```

Stream-execute merge flow:
1. Merge multiple runtime extensions to session directory
2. Run static checks
3. If failed, use merge agent to fix (up to 3 rounds)
4. On success, clean up source extension directories, yield `MergeSuccessResult`

**Parameters**:
* **orchestrator**(`Any`): Orchestrator instance
* **verified_tasks**(`list[VerifiedExtensionTask]`): Verified extension task list
* **package_name**(`str`): Optional merged package name; generated by model or rules if empty

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

Run selector agent in streaming mode.

**Parameters**:
* **config**(`AutoHarnessConfig`): Auto Harness configuration
* **task**(`OptimizationTask`): Optimization task
* **assessment**(`str`): Assessment summary text, default `""`
* **available_pipelines**(`List[str] | None`): Optional pipeline name list; defaults to `[META_EVOLVE_PIPELINE]` when None

**Returns**: `AsyncIterator[Any]` — Selector agent output streaming events

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

Select configured or auto-detected pipeline for task. Directly calls `choose_session_pipeline` for deterministic selection.

**Parameters**:
* **config**(`AutoHarnessConfig`): Auto Harness configuration
* **task**(`OptimizationTask`): Optimization task
* **assessment**(`str`): Assessment summary text (currently unused), default `""`
* **available_pipelines**(`List[str] | None`): Optional pipeline name list; defaults to `[META_EVOLVE_PIPELINE, EXTENDED_EVOLVE_PIPELINE]` when None

**Returns**: `PipelineSelectionArtifact` — Pipeline selection result
