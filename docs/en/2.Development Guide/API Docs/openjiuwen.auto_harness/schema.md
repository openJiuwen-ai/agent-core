# openjiuwen.auto_harness.schema

Auto Harness Agent data model module, defining all data structures used during orchestrator execution, including optimization tasks, experience records, stage artifacts, configuration, runtime state, and more. All core data classes use the `@dataclass` decorator, and enum classes inherit from `str, Enum`.

Submodules:
- `schema`: Data model definitions, including task status, experience types, stage slots, various artifact data classes, configuration classes, and helper functions.

---

## openjiuwen.auto_harness.schema.normalize_pipeline_preference

```
normalize_pipeline_preference(value: Any) -> str
```

Normalize user-facing pipeline preference values. Supports alias mapping (e.g., `"meta"` → `META_EVOLVE_PIPELINE`); unrecognized values fall back to `"auto"`.

**Parameters**:
* **value**(`Any`): User-input pipeline preference value.

**Returns**: `str` — The normalized pipeline name.

---

## class openjiuwen.auto_harness.schema.TaskStatus

```
class openjiuwen.auto_harness.schema.TaskStatus(str, Enum)
```

Optimization task status.

**Enum values**:

| Member | Value |
|------|------|
| `PENDING` | `"pending"` |
| `RUNNING` | `"running"` |
| `SUCCESS` | `"success"` |
| `FAILED` | `"failed"` |
| `TIMEOUT` | `"timeout"` |
| `REVERTED` | `"reverted"` |

---

## class openjiuwen.auto_harness.schema.ExperienceType

```
class openjiuwen.auto_harness.schema.ExperienceType(str, Enum)
```

Experience record type.

**Enum values**:

| Member | Value |
|------|------|
| `OPTIMIZATION` | `"optimization"` |
| `FAILURE` | `"failure"` |
| `INSIGHT` | `"insight"` |

---

## class openjiuwen.auto_harness.schema.StageSlot

```
class openjiuwen.auto_harness.schema.StageSlot(str, Enum)
```

Canonical stage phase names shared across pipelines.

**Enum values**:

| Member | Value |
|------|------|
| `ASSESS` | `"assess"` |
| `PLAN` | `"plan"` |
| `IMPLEMENT` | `"implement"` |
| `VERIFY` | `"verify"` |
| `ACTIVATE` | `"activate"` |
| `COMMIT` | `"commit"` |
| `PUBLISH` | `"publish"` |
| `LEARNINGS` | `"learnings"` |

---

## class openjiuwen.auto_harness.schema.Gap

```
@dataclass
class openjiuwen.auto_harness.schema.Gap
```

Competitor gap.

**Fields**:

* **id**(`str`): Gap unique identifier. Default `""`.
* **competitor**(`str`): Competitor name. Default `""`.
* **feature**(`str`): Feature name. Default `""`.
* **current_state**(`str`): Current state description. Default `""`.
* **gap_description**(`str`): Gap description. Default `""`.
* **impact**(`float`): Impact severity score. Default `0.0`.
* **feasibility**(`float`): Feasibility score. Default `0.0`.
* **suggested_approach**(`str`): Suggested solution approach. Default `""`.
* **target_files**(`List[str]`): Target file list. Default `[]`.

### priority

```
@property
priority -> float
```

impact x feasibility.

**Returns**: `float` — Priority score (impact × feasibility).

---

## class openjiuwen.auto_harness.schema.OptimizationTask

```
@dataclass
class openjiuwen.auto_harness.schema.OptimizationTask
```

Single optimization task.

**Fields**:

* **topic**(`str`): Task topic. (Required)
* **description**(`str`): Task description. Default `""`.
* **files**(`List[str]`): Related file list. Default `[]`.
* **issue_ref**(`Optional[str]`): Associated issue reference. Default `None`.
* **expected_effect**(`str`): Expected effect. Default `""`.
* **pipeline_name**(`str`): Specified pipeline name. Default `""`.
* **status**(`TaskStatus`): Task status. Default `TaskStatus.PENDING`.

---

## class openjiuwen.auto_harness.schema.Experience

```
@dataclass
class openjiuwen.auto_harness.schema.Experience
```

Experience store record.

**Fields**:

* **type**(`ExperienceType`): Experience type. Default `ExperienceType.OPTIMIZATION`.
* **topic**(`str`): Topic. Default `""`.
* **summary**(`str`): Summary. Default `""`.
* **outcome**(`str`): Outcome. Default `""`.
* **details**(`str`): Details. Default `""`.
* **pr_url**(`str`): Associated PR URL. Default `""`.
* **files_changed**(`List[str]`): Changed file list. Default `[]`.
* **signal**(`str`): Signal description. Default `""`.
* **strategy**(`str`): Strategy description. Default `""`.
* **causal_chain**(`str`): Causal chain description. Default `""`.
* **signal_frequency**(`int`): Signal frequency. Default `0`.
* **id**(`str`): Record unique identifier, auto-generated 12-character hex string. Default `uuid.uuid4().hex[:12]`.
* **timestamp**(`float`): Timestamp, automatically set to current time. Default `time.time`.

---

## class openjiuwen.auto_harness.schema.ResearchContext

```
@dataclass
class openjiuwen.auto_harness.schema.ResearchContext
```

Context collected during the Research stage.

**Fields**:

* **experiences**(`List[Experience]`): Related experience record list. Default `[]`.
* **source_files**(`dict[str, str]`): Source file path to content mapping. Default `{}`.
* **gap_report**(`Optional[str]`): Gap analysis report. Default `None`.

---

## class openjiuwen.auto_harness.schema.CycleResult

```
@dataclass
class openjiuwen.auto_harness.schema.CycleResult
```

Result of a single task execution.

**Fields**:

* **success**(`bool`): Whether successful. Default `False`.
* **summary**(`str`): Result summary. Default `""`.
* **pr_url**(`str`): Created PR URL. Default `""`.
* **error**(`str`): Error message. Default `""`.
* **reverted**(`bool`): Whether reverted. Default `False`.
* **error_log**(`str`): Error log. Default `""`.

---

## class openjiuwen.auto_harness.schema.AssessmentArtifact

```
@dataclass
class openjiuwen.auto_harness.schema.AssessmentArtifact
```

Structured output from the Assess stage.

**Fields**:

* **report**(`str`): Assessment report content. Default `""`.

---

## class openjiuwen.auto_harness.schema.TaskPlanArtifact

```
@dataclass
class openjiuwen.auto_harness.schema.TaskPlanArtifact
```

Structured output from the Plan stage.

**Fields**:

* **tasks**(`List[OptimizationTask]`): Planned optimization task list. Default `[]`.
* **raw_plan**(`str`): Raw plan text. Default `""`.

---

## class openjiuwen.auto_harness.schema.PipelineSelectionArtifact

```
@dataclass
class openjiuwen.auto_harness.schema.PipelineSelectionArtifact
```

Structured output from the select_pipeline stage.

**Fields**:

* **pipeline_name**(`str`): Selected pipeline name. Default `META_EVOLVE_PIPELINE`.
* **reason**(`str`): Selection reason. Default `""`.
* **alternatives**(`List[str]`): Alternative pipeline list. Default `[]`.
* **confidence**(`float`): Selection confidence. Default `0.0`.
* **risk_level**(`str`): Risk level. Default `""`.
* **required_inputs**(`List[str]`): Required input list. Default `[]`.
* **fallback_pipeline**(`str`): Fallback pipeline name. Default `""`.

---

## class openjiuwen.auto_harness.schema.GapAnalysisArtifact

```
@dataclass
class openjiuwen.auto_harness.schema.GapAnalysisArtifact
```

Gap analysis output from the extended evolve pipeline.

**Fields**:

* **gaps**(`List[Gap]`): Gap list. Default `[]`.
* **competitor_summary**(`str`): Competitor analysis summary. Default `""`.
* **raw_analysis**(`str`): Raw analysis text. Default `""`.

---

## class openjiuwen.auto_harness.schema.ExtensionDesign

```
@dataclass
class openjiuwen.auto_harness.schema.ExtensionDesign
```

Single extension design candidate.

**Fields**:

* **gap_id**(`str`): Associated gap ID. Default `""`.
* **extension_name**(`str`): Extension name. Default `""`.
* **kind**(`str`): Extension kind. Default `"capability"`.
* **depends_on**(`List[str]`): Dependency extension list. Default `[]`.
* **applies_to**(`List[str]`): Applicability list. Default `[]`.
* **components**(`List[str]`): Component list. Default `[]`.
* **file_plan**(`Dict[str, str]`): File plan mapping. Default `{}`.
* **harness_config_patch**(`Dict[str, Any]`): Harness config patch. Default `{}`.
* **skill_source**(`str`): Skill source. Default `""`.

---

## class openjiuwen.auto_harness.schema.ExtensionDesignArtifact

```
@dataclass
class openjiuwen.auto_harness.schema.ExtensionDesignArtifact
```

Design artifact generated by runtime extension.

**Fields**:

* **designs**(`List[ExtensionDesign]`): Extension design list. Default `[]`.
* **package_name**(`str`): Final package name. Default `""`.

---

## class openjiuwen.auto_harness.schema.ExtensionBuildArtifact

```
@dataclass
class openjiuwen.auto_harness.schema.ExtensionBuildArtifact
```

Verified extension build artifact in task worktree.

**Fields**:

* **extension_name**(`str`): Extension name. Default `""`.
* **extension_root**(`str`): Extension root directory path. Default `""`.
* **config_path**(`str`): Configuration file path. Default `""`.

---

## class openjiuwen.auto_harness.schema.RuntimeExtensionArtifact

```
@dataclass
class openjiuwen.auto_harness.schema.RuntimeExtensionArtifact
```

Runtime extension promoted locally in session.

**Fields**:

* **extension_name**(`str`): Extension name. Default `""`.
* **runtime_path**(`str`): Runtime path. Default `""`.
* **config_path**(`str`): Configuration file path. Default `""`.

---

## class openjiuwen.auto_harness.schema.SessionResultsArtifact

```
@dataclass
class openjiuwen.auto_harness.schema.SessionResultsArtifact
```

Session aggregated results.

**Fields**:

* **results**(`List[CycleResult]`): All cycle result list. Default `[]`.

---

## class openjiuwen.auto_harness.schema.CodeChangeArtifact

```
@dataclass
class openjiuwen.auto_harness.schema.CodeChangeArtifact
```

Output from the Implement stage.

**Fields**:

* **related**(`List[Experience]`): Related experience record list. Default `[]`.
* **edited_files**(`List[str]`): Edited file list. Default `[]`.

---

## class openjiuwen.auto_harness.schema.VerifyReportArtifact

```
@dataclass
class openjiuwen.auto_harness.schema.VerifyReportArtifact
```

Output from the Verify stage.

**Fields**:

* **ci_result**(`Dict[str, Any]`): CI execution result. Default `{}`.
* **fix_errors**(`str`): Fix error message. Default `""`.
* **reverted**(`bool`): Whether reverted. Default `False`.
* **error**(`str`): Error message. Default `""`.

---

## class openjiuwen.auto_harness.schema.CommitArtifact

```
@dataclass
class openjiuwen.auto_harness.schema.CommitArtifact
```

Output from the Commit stage.

**Fields**:

* **facts**(`CommitFacts | None`): Commit facts snapshot. Default `None`.
* **status_text**(`str`): Status text. Default `""`.
* **last_commit_stat**(`str`): Latest commit statistics. Default `""`.
* **branch_name**(`str`): Branch name. Default `""`.
* **committed**(`bool`): Whether successfully committed. Default `False`.
* **error**(`str`): Error message. Default `""`.

---

## class openjiuwen.auto_harness.schema.PullRequestArtifact

```
@dataclass
class openjiuwen.auto_harness.schema.PullRequestArtifact
```

Output from the Publish stage.

**Fields**:

* **pr_url**(`str`): PR URL. Default `""`.
* **summary**(`str`): PR summary. Default `""`.

---

## class openjiuwen.auto_harness.schema.PullRequestDraft

```
@dataclass
class openjiuwen.auto_harness.schema.PullRequestDraft
```

Structured PR draft generated by communicate agent.

**Fields**:

* **title**(`str`): PR title. Default `""`.
* **body**(`str`): PR body. Default `""`.
* **kind**(`str`): PR type. Default `""`.

---

## class openjiuwen.auto_harness.schema.CommitFacts

```
@dataclass
class openjiuwen.auto_harness.schema.CommitFacts
```

Fact snapshot for the commit stage.

**Fields**:

* **branch_name**(`str`): Branch name. Default `""`.
* **task_declared_files**(`List[str]`): Task declared file list. Default `[]`.
* **preexisting_dirty_files**(`List[str]`): Pre-existing dirty file list. Default `[]`.
* **current_dirty_files**(`List[str]`): Current dirty file list. Default `[]`.
* **tracked_modified_files**(`List[str]`): Tracked and modified file list. Default `[]`.
* **untracked_files**(`List[str]`): Untracked file list. Default `[]`.
* **edited_files**(`List[str]`): Edited file list. Default `[]`.
* **allowed_files**(`List[str]`): Allowed commit file list. Default `[]`.
* **derived_test_files**(`List[str]`): Derived test file list. Default `[]`.
* **legacy_related_test_files**(`List[str]`): Legacy related test file list. Default `[]`.
* **verify_related_files**(`List[str]`): Verify related file list. Default `[]`.
* **diff_stat**(`str`): Diff statistics. Default `""`.

---

## class openjiuwen.auto_harness.schema.ProjectProfile

```
@dataclass
class openjiuwen.auto_harness.schema.ProjectProfile
```

Project profile carrying repo-specific defaults.

**Fields**:

* **name**(`str`): Project name. Default `"agent-core"`.
* **repo_url**(`str`): Repository URL. Default `"https://gitcode.com/openJiuwen/agent-core.git"`.
* **repo_slug**(`str`): Repository slug. Default `"openJiuwen/agent-core"`.
* **platform**(`str`): Code hosting platform. Default `"gitcode"`.
* **immutable_files**(`List[str]`): Immutable file list. Defaults to 3 built-in files (identity.md, ci_gate.yaml, prompt_security_rail.py).
* **high_impact_prefixes**(`List[str]`): High-impact path prefix list. Default `["openjiuwen/core/"]`.
* **default_base_branch**(`str`): Default base branch. Default `"develop"`.
* **default_ci_profile**(`str`): Default CI configuration. Default `"default"`.

---

## class openjiuwen.auto_harness.schema.AutoHarnessPaths

```
@dataclass
class openjiuwen.auto_harness.schema.AutoHarnessPaths
```

Path derivation results required for runtime.

**Fields**:

* **data_dir**(`str`): Data root directory. Default `""`.
* **experience_dir**(`str`): Experience store directory. Default `""`.
* **worktrees_dir**(`str`): Worktree root directory. Default `""`.
* **runs_dir**(`str`): Run record directory. Default `""`.
* **cache_repo_dir**(`str`): Clone cache directory. Default `""`.
* **runtime_extensions_dir**(`str`): Runtime extension directory. Default `""`.

---

## class openjiuwen.auto_harness.schema.AutoHarnessRuntimeState

```
@dataclass
class openjiuwen.auto_harness.schema.AutoHarnessRuntimeState
```

Runtime state.

**Fields**:

* **current_workspace**(`str`): Current workspace path. Default `""`.
* **selected_pipeline**(`str`): Selected pipeline name. Default `""`.
* **config_bootstrapped**(`bool`): Whether config was auto-bootstrapped. Default `False`.
* **suggested_local_repo**(`str`): Suggested local repository path. Default `""`.
* **session_id**(`str`): Session unique identifier, auto-generated 12-character hex string. Default `uuid.uuid4().hex[:12]`.

---

## class openjiuwen.auto_harness.schema.StageResult

```
@dataclass
class openjiuwen.auto_harness.schema.StageResult
```

Unified stage execution result.

**Fields**:

* **status**(`str`): Execution status. Default `"success"`.
* **artifacts**(`Dict[str, Any]`): Artifact dictionary. Default `{}`.
* **messages**(`List[str]`): Message list. Default `[]`.
* **metrics**(`Dict[str, Any]`): Metrics dictionary. Default `{}`.
* **error**(`str`): Error message. Default `""`.

---

## class openjiuwen.auto_harness.schema.StageSpec

```
@dataclass
class openjiuwen.auto_harness.schema.StageSpec
```

Declarative metadata for a stage.

**Fields**:

* **name**(`str`): Stage name. (Required)
* **stage_cls**(`type[Any]`): Stage implementation class. (Required)
* **scope**(`str`): Scope. Default `"session"`.
* **consumes**(`List[str]`): Consumed artifact name list. Default `[]`.
* **produces**(`List[str]`): Produced artifact name list. Default `[]`.
* **description**(`str`): Stage description. Default `""`.
* **slot**(`str`): Corresponding stage slot. Default `""`.

---

## class openjiuwen.auto_harness.schema.PipelineSpec

```
@dataclass
class openjiuwen.auto_harness.schema.PipelineSpec
```

Declarative template for a pipeline.

**Fields**:

* **name**(`str`): Pipeline name. (Required)
* **pipeline_cls**(`type[Any]`): Pipeline implementation class. (Required)
* **description**(`str`): Pipeline description. Default `""`.
* **expected_outputs**(`List[str]`): Expected output artifact list. Default `[]`.

---

## class openjiuwen.auto_harness.schema.AutoHarnessConfig

```
@dataclass
class openjiuwen.auto_harness.schema.AutoHarnessConfig
```

Auto Harness Agent configuration.

`data_dir` is passed by the host CLI; all artifacts (experience store, run records, clone cache, worktrees) are stored under this directory.

`local_repo` is optional, pointing to a local agent-core repository path to accelerate worktree creation. When not configured, it auto-clones to `{data_dir}/repo/agent-core`.

**Example**:
```python
>>> from openjiuwen.auto_harness.schema import AutoHarnessConfig
>>> config = AutoHarnessConfig(
...     data_dir="/tmp/auto_harness",
...     local_repo="/path/to/agent-core",
...     language="cn",
... )
>>> config.resolved_experience_dir
'/tmp/auto_harness/experience'
```

**Fields**:

* **model**(`Optional[Model]`): Default LLM model. Default `None`.
* **plan_model**(`Optional[Model]`): LLM model for planning stages. Default `None`.
* **data_dir**(`str`): Data root directory. Default `""`.
* **local_repo**(`str`): Local agent-core repository path. Default `""`.
* **repo_url**(`str`): Remote repository URL. Default `"https://gitcode.com/openJiuwen/agent-core.git"`.
* **skills_dirs**(`List[str]`): Skill directory list. Default `[]`.
* **community_skill_repos**(`List[str]`): Community skill repository list. Default `["https://github.com/anthropics/skills.git", "https://github.com/JimLiu/baoyu-skills.git"]`.
* **community_skill_cache_dir**(`str`): Community skill cache directory. Default `""`.
* **stage_registrars**(`List[str]`): Stage registrar module path list. Default `[]`.
* **pipeline_registrars**(`List[str]`): Pipeline registrar module path list. Default `[]`.
* **language**(`str`): Output language. Default `"cn"`.
* **optimization_goal**(`str`): Optimization goal description. Default `""`.
* **pipeline_preference**(`str`): Pipeline preference. Default `"auto"`.
* **session_budget_secs**(`float`): Session total budget (seconds). Default `900000.0`.
* **cost_limit_usd**(`float`): Cost limit (USD). Default `10.0`.
* **task_timeout_secs**(`float`): Single task timeout (seconds). Default `300000.0`.
* **model_timeout_secs**(`float`): Model call timeout (seconds). Default `300000.0`.
* **max_tasks_per_session**(`int`): Maximum tasks per session. Default `10`.
* **self_driven_slots**(`int`): Self-driven slot count. Default `1`.
* **extension_verify_concurrency**(`int`): Extension verification concurrency. Default `4`.
* **git_remote**(`str`): Git remote name. Default `""`.
* **git_base_branch**(`str`): Base branch. Default `"develop"`.
* **git_user_name**(`str`): Git username. Default `""`.
* **git_user_email**(`str`): Git user email. Default `""`.
* **fork_owner**(`str`): Fork owner. Default `""`.
* **upstream_owner**(`str`): Upstream repository owner. Default `"openJiuwen"`.
* **upstream_repo**(`str`): Upstream repository name. Default `"agent-core"`.
* **gitcode_username**(`str`): GitCode username. Default `""`.
* **gitcode_token**(`str`): GitCode access token. Default `""`.
* **gitcode_token_env**(`str`): GitCode token environment variable name. Default `"GITCODE_ACCESS_TOKEN"`.
* **ci_gate_config**(`str`): CI gate configuration file path. Default `""`.
* **ci_gate_python_executable**(`str`): CI gate Python executable path. Default `""`.
* **ci_gate_install_command**(`str`): CI gate install command. Default `""`.
* **fix_phase1_max_retries**(`int`): Fix loop phase 1 max retries. Default `10`.
* **fix_phase2_max_retries**(`int`): Fix loop phase 2 max retries. Default `9`.
* **immutable_files**(`List[str]`): Immutable file list. Default `[]`.
* **high_impact_prefixes**(`List[str]`): High-impact path prefix list. Default `["openjiuwen/core/"]`.
* **agent_iterations**(`Dict[str, int]`): Maximum iteration count mapping per agent stage. Default `{"implement": 30, "assess": 30, "plan": 15, "select_pipeline": 10, "eval": 10, "pr_draft": 5, "learnings": 5, "explore_subagent": 20, "browser_subagent": 20, "merge_ext": 8}`.
* **workspace**(`str`): Workspace path (deprecated, kept for compatibility). Default `""`.
* **config_path**(`str`): Configuration file path. Default `""`.
* **config_bootstrapped**(`bool`): Whether config was auto-bootstrapped. Default `False`.
* **suggested_local_repo**(`str`): Suggested local repository path. Default `""`.
* **experience_dir**(`str`): Experience store directory (used when explicitly specified). Default `""`.

### resolved_experience_dir

```
@property
resolved_experience_dir -> str
```

Experience store directory, derived from data_dir.

**Returns**: `str` — Experience store directory path.

### worktrees_dir

```
@property
worktrees_dir -> str
```

Worktree root directory, derived from data_dir.

**Returns**: `str` — Worktree root directory path.

### runs_dir

```
@property
runs_dir -> str
```

Run record directory, derived from data_dir.

**Returns**: `str` — Run record directory path.

### cache_repo_dir

```
@property
cache_repo_dir -> str
```

Clone cache directory, derived from data_dir.

**Returns**: `str` — Clone cache directory path.

### runtime_extensions_dir

```
@property
runtime_extensions_dir -> str
```

Session-local runtime extensions root.

**Returns**: `str` — Session-level runtime extension root directory path.

### resolved_community_skill_cache_dir

```
@property
resolved_community_skill_cache_dir -> str
```

Community skill repo cache directory.

**Returns**: `str` — Community skill repository cache directory path.

### resolve_repo_name(self) -> str

Resolve the repository directory name used for local cache path.

**Returns**: `str` — Repository directory name.

### resolve_gitcode_token(self) -> str

Resolve the GitCode token. Prefers `gitcode_token`; otherwise reads from the environment variable specified by `gitcode_token_env`.

**Returns**: `str` — Token string, or empty string if not configured.

### resolve_gitcode_username(self) -> str

Resolve the GitCode login username for git HTTPS authentication.

**Returns**: `str` — GitCode username.

### resolve_ci_gate_python_executable(self) -> str

Resolve the Python executable path used by the CI gate command.

**Returns**: `str` — Python executable path.

### resolve_agent_iterations(self, stage_name: str, default: int) -> int

Resolve the maximum iteration count for an agent stage.

**Parameters**:
* **stage_name**(`str`): Stage name.
* **default**(`int`): Default iteration count.

**Returns**: `int` — Maximum iteration count for the stage.

### resolve_immutable_files(self) -> List[str]

Returns the configured immutable file list, or built-in defaults if not configured.

**Returns**: `List[str]` — Immutable file path list.

### build_project_profile(self) -> ProjectProfile

Build a repo-specific project profile.

**Returns**: `ProjectProfile` — Project profile instance.

### build_paths(self) -> AutoHarnessPaths

Build derived runtime paths.

**Returns**: `AutoHarnessPaths` — Runtime paths instance.

### load_from_dict(data: Dict[str, Any]) -> 'AutoHarnessConfig'

`@staticmethod`

Build configuration from a dictionary, supporting nested YAML structures.

**Parameters**:
* **data**(`Dict[str, Any]`): Configuration dictionary, supporting top-level and nested keys (e.g., `git`, `gitcode`, `budget`, `ci_gate`, `fix_loop`, `agent`, `extensions`).

**Returns**: `AutoHarnessConfig` — Populated configuration instance.

---

## openjiuwen.auto_harness.schema.load_auto_harness_config

```
load_auto_harness_config(config_path: str, workspace_hint: str = '') -> AutoHarnessConfig
```

Load AutoHarnessConfig from a YAML file. Auto-generates a template and returns default configuration when the file does not exist.

**Parameters**:
* **config_path**(`str`): YAML configuration file path.
* **workspace_hint**(`str`): Current CLI working directory, used to infer the suggested `local_repo`. Default `""`.

**Returns**: `AutoHarnessConfig` — Populated configuration instance.

---

## openjiuwen.auto_harness.schema.is_placeholder_local_repo

```
is_placeholder_local_repo(path: str) -> bool
```

Check if `path` is a template/example value.

**Parameters**:
* **path**(`str`): Path string to check.

**Returns**: `bool` — `True` if the path is a known template/example value.

---

## class openjiuwen.auto_harness.schema.ActivateDecision

```
@dataclass
class openjiuwen.auto_harness.schema.ActivateDecision
```

User decision result from the activate stage.

**Fields**:

* **action**(`str`): User decision action. Default `"accept"`.
* **feedback**(`str`): User feedback content. Default `""`.
