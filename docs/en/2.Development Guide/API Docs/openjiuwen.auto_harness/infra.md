# openjiuwen.auto_harness.infra

Auto Harness infrastructure subpackage, providing CI gate execution, Git operations, worktree management, runtime extension manifest parsing and merging, agent output parsing, pipeline selection, PR template fetching, community skill management, and edit scope constraints. These modules provide underlying support for the auto-harness orchestrator and pipelines, without containing business logic themselves.

Submodules:
- `ci_gate_runner`: CI gate runner, executing lint / test / type-check and parsing results
- `fix_loop`: Two-phase CI fix loop controller
- `session_budget`: Session budget controller, managing clock and API cost
- `git_operations`: Git branch, push, and PR creation operations
- `worktree_manager`: Creating isolated git worktrees for each task
- `git_auth`: Task-level GitCode authentication environment construction
- `runtime_manifest`: Runtime `harness_config.yaml` manifest schema and loader
- `runtime_extension_loader`: Loading runtime extensions from session-local runtime directory
- `runtime_extension_merger`: Merging multiple verified runtime extensions into a single extension
- `runtime_extension_static_checks`: Shared static analysis tools for runtime extensions
- `parsers`: Agent output parsing utilities
- `pipeline_selector`: Session-level pipeline selection helpers
- `gitcode_pr_template`: Fetching GitCode PR templates
- `github_cli`: GitHub CLI pre-check helpers
- `skill_source_manager`: Community skill source management (clone, scan, copy)
- `commit_scope`: Commit scope helper functions
- `edit_scope`: Shared edit scope rules for auto-harness planning and implementation

---

## Runtime and Gating

### class openjiuwen.auto_harness.infra.ci_gate_runner.CIGateRunner

```
class openjiuwen.auto_harness.infra.ci_gate_runner.CIGateRunner(workspace: str, config_path: str = '', python_executable: str = '', install_command: str = '')
```

Execute CI gate checks and return structured results. Loads gate definitions from YAML configuration files, supporting lint (ruff / codespell / pylint), type-check (mypy), and test (pytest) gate types, with change-line-range filtering for lint and type-check gates to only report violations on actually modified lines.

**Parameters**:
* **workspace**(`str`): Working directory for make commands
* **config_path**(`str`): Path to ci_gate.yaml, uses default configuration when empty
* **python_executable**(`str`): Python interpreter path
* **install_command**(`str`): Environment install command

#### set_workspace(workspace: str) -> None

Update the command working directory.

**Parameters**:
* **workspace**(`str`): New working directory

#### run(action: str = 'all') -> Dict[str, Any]

Execute CI gate checks. Filters gates to execute based on the `action` parameter (`"all"` executes all, `"check"` / `"lint"` executes lint gates, `"test"` executes test gates, `"type-check"` executes type-check gates).

**Parameters**:
* **action**(`str`): Gate type to execute, default `"all"`

**Returns**: Structured result dictionary containing `passed` (bool), `gates` (list of gate results), and `errors` (merged error messages from failed gates).

---

### function openjiuwen.auto_harness.infra.ci_gate_runner.decode_stdout

```
decode_stdout(stdout: bytes) -> str
```

Decode subprocess stdout using cross-platform encoding handling. On Windows, tries UTF-8, system console encoding (GBK/CP936), and latin-1 in sequence; on Unix, tries UTF-8 and latin-1.

**Parameters**:
* **stdout**(`bytes`): Raw subprocess output bytes

**Returns**: Decoded string

---

### class openjiuwen.auto_harness.infra.fix_loop.FixLoopResult

```
@dataclass
class openjiuwen.auto_harness.infra.fix_loop.FixLoopResult
```

Fix loop execution result.

**Fields**:
* **success**(`bool`): Whether successful, default `False`
* **attempts**(`int`): Total attempt count, default `0`
* **phase**(`int`): Final phase (1 or 2), default `1`
* **error_log**(`List[str]`): Error log list, default empty list

---

### class openjiuwen.auto_harness.infra.fix_loop.FixLoopController

```
class openjiuwen.auto_harness.infra.fix_loop.FixLoopController(phase1_max_retries: int = 10, phase2_max_retries: int = 9, timeout_per_attempt: float = 600.0)
```

Two-phase CI fix loop. Phase 1 — Direct fix: run CI -> parse errors -> agent fixes -> retry. Phase 2 — Review fix: evaluator reviews quality, continues fixing if not approved.

**Parameters**:
* **phase1_max_retries**(`int`): Phase 1 max retries, default 10
* **phase2_max_retries**(`int`): Phase 2 max retries, default 9
* **timeout_per_attempt**(`float`): Timeout per attempt in seconds, default 600.0

#### run(ci_runner, agent_fixer, evaluator=None) -> FixLoopResult

```
async run(ci_runner: Callable[[], Coroutine[Any, Any, Any]], agent_fixer: Callable[[str], Coroutine[Any, Any, Any]], evaluator: Optional[Callable[[], Coroutine[Any, Any, Any]]] = None) -> FixLoopResult
```

Execute the two-phase fix loop. In Phase 1, repeatedly runs CI and passes errors to the agent for fixing on failure; in Phase 2, the evaluator reviews fix quality.

**Parameters**:
* **ci_runner**(`Callable[[], Coroutine[Any, Any, Any]]`): Async callable that runs CI, returns object with `.passed` and `.errors`
* **agent_fixer**(`Callable[[str], Coroutine[Any, Any, Any]]`): Async callable that receives error messages and attempts fixes
* **evaluator**(`Optional[Callable[[], Coroutine[Any, Any, Any]]]`): Optional, reviews fix quality, returns object with `.approved`

**Returns**: `FixLoopResult`, containing success status, attempt count, and error log

---

### class openjiuwen.auto_harness.infra.session_budget.SessionBudgetController

```
class openjiuwen.auto_harness.infra.session_budget.SessionBudgetController(wall_clock_secs: float = 3600.0, cost_limit_usd: float = 10.0, task_timeout_secs: float = 1200.0)
```

Manage clock budget and API cost budget for a single session. Automatically emits warning logs when consumption reaches the 80% threshold.

**Parameters**:
* **wall_clock_secs**(`float`): Maximum session duration (seconds), default 3600.0
* **cost_limit_usd**(`float`): API cost limit (USD), default 10.0
* **task_timeout_secs**(`float`): Single task default timeout (seconds), default 1200.0

**Example**:

```python
budget = SessionBudgetController(wall_clock_secs=1800, cost_limit_usd=5.0)
budget.start()

if budget.should_stop:
    break
if not budget.check_task_budget():
    break

budget.add_cost(0.03)
print(f"Remaining time: {budget.remaining_secs:.0f}s")
```

#### start() -> None

Record the session start time.

#### add_cost(amount_usd: float) -> None

Accumulate API cost and emit warning when threshold (80%) is reached.

**Parameters**:
* **amount_usd**(`float`): Current API call cost (USD)

#### elapsed_secs() -> float

**Property**. Elapsed time (seconds).

#### remaining_secs() -> float

**Property**. Remaining clock budget (seconds).

#### remaining_cost_usd() -> float

**Property**. Remaining cost budget (USD).

#### should_stop() -> bool

**Property**. Returns `True` when clock or cost budget is exhausted.

#### check_task_budget(task_timeout_secs: float | None = None) -> bool

Check if there is enough time to start the next task.

**Parameters**:
* **task_timeout_secs**(`float | None`): Task timeout in seconds, defaults to constructor parameter

**Returns**: `True` if budget is sufficient to start a task

---

## Git and Worktree

### class openjiuwen.auto_harness.infra.git_operations.GitOperations

```
class openjiuwen.auto_harness.infra.git_operations.GitOperations(workspace: str, remote: str = '', base_branch: str = 'develop', fork_owner: str = '', upstream_owner: str = 'openJiuwen', upstream_repo: str = 'agent-core', gitcode_username: str = '', gitcode_token: str = '', user_name: str = '', user_email: str = '')
```

Git operations (orchestrator infrastructure). Encapsulates branch creation, status collection, file staging, committing, pushing, and PR creation via GitCode API. All git command outputs are automatically sanitized of credentials.

**Parameters**:
* **workspace**(`str`): Git repository working directory
* **remote**(`str`): Fork remote name
* **base_branch**(`str`): Target branch, default `"develop"`
* **fork_owner**(`str`): Fork owner
* **upstream_owner**(`str`): Upstream repository owner, default `"openJiuwen"`
* **upstream_repo**(`str`): Upstream repository name, default `"agent-core"`
* **gitcode_username**(`str`): GitCode username
* **gitcode_token**(`str`): GitCode API token
* **user_name**(`str`): Git commit username
* **user_email**(`str`): Git commit email

**Example**:

```python
git = GitOperations(workspace="/path/to/repo", remote="fork", gitcode_token="your-token")
await git.create_branch("fix/issue-42")
await git.add_paths(["openjiuwen/core/foo.py"])
await git.commit("fix: resolve foo issue")
result = await git.push("fix/issue-42")
pr = await git.create_pr("Fix issue #42", "Body", "fix/issue-42")
```

#### set_workspace(workspace: str) -> None

Update the git command working directory.

#### create_branch(branch_name: str) -> Dict[str, Any]

```
async create_branch(branch_name: str) -> Dict[str, Any]
```

Create and switch to a new branch.

**Returns**: Dictionary containing `success`, `branch`, and `output`

#### collect_status() -> Dict[str, List[str]]

```
async collect_status() -> Dict[str, List[str]]
```

Collect current git status in structured form, containing `dirty_files`, `tracked_modified_files`, `untracked_files`, and `renamed_files` lists.

#### list_dirty_files() -> List[str]

```
async list_dirty_files() -> List[str]
```

Return the current dirty file list.

#### current_branch() -> str

```
async current_branch() -> str
```

Return the current branch name.

#### current_head() -> str

```
async current_head() -> str
```

Return the current HEAD SHA.

#### diff_stat(paths: List[str] | None = None) -> str

```
async diff_stat(paths: List[str] | None = None) -> str
```

Return `git diff --stat` summary.

#### diff_stat_against_base() -> str

```
async diff_stat_against_base() -> str
```

Return the diff stat of the current branch against the base branch.

#### add_paths(paths: List[str]) -> Dict[str, Any]

```
async add_paths(paths: List[str]) -> Dict[str, Any]
```

Stage specified repo-relative paths in the current working directory.

**Returns**: Dictionary containing `success` and `output`

#### commit(message: str) -> Dict[str, Any]

```
async commit(message: str) -> Dict[str, Any]
```

Create a commit in the current working directory.

**Returns**: Dictionary containing `success` and `output`

#### diff_name_only(revision: str = 'HEAD') -> List[str]

```
async diff_name_only(revision: str = 'HEAD') -> List[str]
```

Return normalized path list from `git diff --name-only <revision>`.

#### diff_name_only_against_base() -> List[str]

```
async diff_name_only_against_base() -> List[str]
```

Return the changed file list of the current branch commits against the base branch.

#### has_commits_against_base() -> bool

```
async has_commits_against_base() -> bool
```

Return whether HEAD contains commits not in origin/base.

#### find_existing_issue_fix_ref(issue_number: str, *, allowed_files: List[str] | None = None) -> Dict[str, Any]

```
async find_existing_issue_fix_ref(issue_number: str, *, allowed_files: List[str] | None = None) -> Dict[str, Any]
```

Find existing local/remote branches carrying issue fixes. Scores and sorts by branch name matching, commit count, and file overlap.

**Returns**: Dictionary containing `success`, `ref`, `commit_count`, `files`, `score`

#### cherry_pick_ref_commits(ref: str) -> Dict[str, Any]

```
async cherry_pick_ref_commits(ref: str) -> Dict[str, Any]
```

Cherry-pick commits in the origin/base..ref range one by one onto the current branch.

**Returns**: Dictionary containing `success`, `output`, and `commits`

#### status_porcelain() -> str

```
async status_porcelain() -> str
```

Return raw `git status --porcelain` output.

#### show_last_commit_stat() -> str

```
async show_last_commit_stat() -> str
```

Return a compact summary of the latest commit.

#### discard_worktree_changes() -> bool

```
async discard_worktree_changes() -> bool
```

Discard changes in the current worktree via `git checkout .`.

#### diff_against(revision: str) -> str

```
async diff_against(revision: str) -> str
```

Return `git diff <revision>` output.

#### diff_against_base() -> str

```
async diff_against_base() -> str
```

Return the full diff of the current branch commits against the base branch.

#### push(branch_name: str) -> Dict[str, Any]

```
async push(branch_name: str) -> Dict[str, Any]
```

Push to the fork remote. Tries force-with-lease push, regular push, and HEAD refspec push in sequence, collecting diagnostics on total failure.

**Returns**: Dictionary containing `success` and `output`

#### build_pr_web_url(head_branch: str) -> str

Return the GitCode Web URL for manually creating a PR/MR.

#### create_pr(title: str, body: str, head_branch: str) -> Dict[str, Any]

```
async create_pr(title: str, body: str, head_branch: str) -> Dict[str, Any]
```

Asynchronously create a PR via GitCode API.

**Returns**: Dictionary containing `success`, `pr_url` (or `error`), `manual_url`, and `diagnostics`

---

### class openjiuwen.auto_harness.infra.worktree_manager.WorktreeManager

```
class openjiuwen.auto_harness.infra.worktree_manager.WorktreeManager(config: 'AutoHarnessConfig')
```

Create and clean up git worktrees for each task. Workflow: with `local_repo`, directly fetch + worktree add; without `local_repo`, first ensure clone cache then fetch + worktree add.

**Parameters**:
* **config**(`AutoHarnessConfig`): Auto Harness configuration

#### prepare(topic: str) -> str

```
async prepare(topic: str) -> str
```

Create an isolated worktree for a task. Generates a filesystem-safe slug and branch name (`auto-harness/<slug>`) from the topic, cleans up old branches with the same name, creates a new worktree, and configures git user and fork remote.

**Returns**: Worktree absolute path

#### prepare_readonly_snapshot(*, label: str = 'assess') -> str

```
async prepare_readonly_snapshot(*, label: str = 'assess') -> str
```

Create a detached read-only snapshot worktree from origin/base. Used for assess/plan stages to ensure analysis is always based on the latest fetched remote base branch.

**Returns**: Worktree absolute path

#### cleanup(worktree_path: str) -> None

```
async cleanup(worktree_path: str) -> None
```

Clean up a worktree. Removes via `git worktree remove --force`.

---

### function openjiuwen.auto_harness.infra.git_auth.build_git_auth_env

```
build_git_auth_env(*, username: str = '', token: str = '') -> Dict[str, str]
```

Build subprocess environment variables for non-interactive GitCode authentication. Disables the global credential helper and only injects Basic Authorization headers for `https://gitcode.com` requests.

**Parameters**:
* **username**(`str`): GitCode username
* **token**(`str`): GitCode API token

**Returns**: Subprocess environment variable dictionary configured with authentication info

---

## Runtime Extensions

### class openjiuwen.auto_harness.infra.runtime_manifest.MetaSchema

```
class openjiuwen.auto_harness.infra.runtime_manifest.MetaSchema(BaseModel)
```

Governance metadata — for display and permission management.

**Fields**:
* **owner**(`str`): Owner, default `""`
* **tags**(`List[str]`): Tag list, default empty list
* **visibility**(`str`): Visibility, default `"internal"`

---

### class openjiuwen.auto_harness.infra.runtime_manifest.SectionSchema

```
class openjiuwen.auto_harness.infra.runtime_manifest.SectionSchema(BaseModel)
```

Single prompt section entry.

**Fields**:
* **name**(`str`): Section name
* **priority**(`Optional[int]`): Priority, default `None`
* **file**(`Optional[str]`): File path, default `None`
* **content**(`Optional[Union[Dict[str, str], str]]`): Content, default `None`

---

### class openjiuwen.auto_harness.infra.runtime_manifest.ToolResourceSchema

```
class openjiuwen.auto_harness.infra.runtime_manifest.ToolResourceSchema(BaseModel)
```

Tool resource specification.

**Fields**:
* **type**(`str`): Resource type (`builtin` / `package` / `entry_point`)
* **names**(`Optional[List[str]]`): Name list, default `None`
* **name**(`Optional[str]`): Name, default `None`
* **package**(`Optional[str]`): Package name, default `None`
* **module**(`Optional[str]`): Module name, default `None`
* **class_name**(`Optional[str]`): Class name (aliased as `class` in YAML), default `None`
* **kwargs**(`Dict[str, Any]`): Keyword arguments, default empty dict
* **params**(`Dict[str, Any]`): Parameters, default empty dict

---

### class openjiuwen.auto_harness.infra.runtime_manifest.RailResourceSchema

```
class openjiuwen.auto_harness.infra.runtime_manifest.RailResourceSchema(BaseModel)
```

Rail resource specification.

**Fields**:
* **type**(`str`): Resource type (`builtin` / `package` / `entry_point`)
* **name**(`Optional[str]`): Name, default `None`
* **package**(`Optional[str]`): Package name, default `None`
* **module**(`Optional[str]`): Module name, default `None`
* **class_name**(`Optional[str]`): Class name (aliased as `class` in YAML), default `None`
* **kwargs**(`Dict[str, Any]`): Keyword arguments, default empty dict
* **params**(`Dict[str, Any]`): Parameters, default empty dict

---

### class openjiuwen.auto_harness.infra.runtime_manifest.SkillsSchema

```
class openjiuwen.auto_harness.infra.runtime_manifest.SkillsSchema(BaseModel)
```

Skills configuration.

**Fields**:
* **dirs**(`List[str]`): Skill directory list, default empty list
* **mode**(`str`): Mode (`all` / `auto_list`), default `"all"`

---

### class openjiuwen.auto_harness.infra.runtime_manifest.McpResourceSchema

```
class openjiuwen.auto_harness.infra.runtime_manifest.McpResourceSchema(BaseModel)
```

MCP server specification.

**Fields**:
* **type**(`str`): Transport type, default `"stdio"`
* **name**(`Optional[str]`): Name, default `None`
* **server_name**(`Optional[str]`): Server name, default `None`
* **server_id**(`Optional[str]`): Server ID, default `None`
* **url**(`Optional[str]`): URL, default `None`
* **command**(`str`): Startup command, default `""`
* **args**(`List[str]`): Command arguments, default empty list
* **env**(`Dict[str, str]`): Environment variables, default empty dict
* **cwd**(`Optional[str]`): Working directory, default `None`
* **params**(`Dict[str, Any]`): Parameters, default empty dict
* **auth_headers**(`Dict[str, str]`): Authentication headers, default empty dict
* **auth_query_params**(`Dict[str, str]`): Authentication query parameters, default empty dict

---

### class openjiuwen.auto_harness.infra.runtime_manifest.ResourcesSchema

```
class openjiuwen.auto_harness.infra.runtime_manifest.ResourcesSchema(BaseModel)
```

All runtime resources: tools, rails, skills, MCPs.

**Fields**:
* **tools**(`List[ToolResourceSchema]`): Tool list, default empty list
* **rails**(`List[RailResourceSchema]`): Rail list, default empty list
* **skills**(`Optional[SkillsSchema]`): Skills configuration, default `None`
* **mcps**(`List[McpResourceSchema]`): MCP server list, default empty list

---

### class openjiuwen.auto_harness.infra.runtime_manifest.PromptsSchema

```
class openjiuwen.auto_harness.infra.runtime_manifest.PromptsSchema(BaseModel)
```

Prompt section declarations.

**Fields**:
* **sections**(`List[SectionSchema]`): Section list, default empty list

---

### class openjiuwen.auto_harness.infra.runtime_manifest.WorkspaceSchema

```
class openjiuwen.auto_harness.infra.runtime_manifest.WorkspaceSchema(BaseModel)
```

Workspace (file operation root directory).

**Fields**:
* **root_path**(`str`): Root path, default `"./"`

---

### class openjiuwen.auto_harness.infra.runtime_manifest.RuntimeHarnessManifest

```
class openjiuwen.auto_harness.infra.runtime_manifest.RuntimeHarnessManifest(BaseModel)
```

Top-level `harness_config.yaml` schema for runtime extensions. Defines the complete configuration structure for runtime extensions, including metadata, workspace, prompts, resources, language, iteration limits, task loop, sub-agents, permissions, and more.

**Fields**:
* **schema_version**(`str`): Schema version, default `"harness_config.v0.1"`
* **meta**(`Optional[MetaSchema]`): Governance metadata, default `None`
* **id**(`Optional[str]`): Extension ID, default `None`
* **name**(`Optional[str]`): Extension name, default `None`
* **description**(`Optional[str]`): Description, default `None`
* **workspace**(`Optional[WorkspaceSchema]`): Workspace configuration, default `None`
* **prompts**(`Optional[PromptsSchema]`): Prompt configuration, default `None`
* **resources**(`Optional[ResourcesSchema]`): Resource configuration, default `None`
* **language**(`str`): Language, default `"cn"`
* **max_iterations**(`Optional[int]`): Maximum iteration count, default `None`
* **completion_timeout**(`Optional[float]`): Completion timeout, default `None`
* **enable_task_loop**(`Optional[bool]`): Whether to enable task loop, default `None`
* **enable_async_subagent**(`Optional[bool]`): Whether to enable async sub-agent, default `None`
* **add_general_purpose_agent**(`Optional[bool]`): Whether to add general-purpose agent, default `None`
* **enable_task_planning**(`Optional[bool]`): Whether to enable task planning, default `None`
* **restrict_to_work_dir**(`Optional[bool]`): Whether to restrict to work directory, default `None`
* **prompt_mode**(`Optional[str]`): Prompt mode, default `None`
* **default_mode**(`Optional[str]`): Default mode, default `None`
* **permissions**(`Optional[Dict[str, Any]]`): Permission configuration, default `None`
* **progressive_tool_enabled**(`Optional[bool]`): Whether to enable progressive tools, default `None`
* **subagents**(`Optional[List[Dict[str, Any]]]`): Sub-agent configuration, default `None`
* **context**(`Optional[Dict[str, Any]]`): Context configuration, default `None`
* **stop_eval_conditions**(`Optional[Dict[str, Any]]`): Stop evaluation conditions, default `None`

---

### function openjiuwen.auto_harness.infra.runtime_manifest.load_runtime_manifest

```
load_runtime_manifest(path: Union[str, Path]) -> RuntimeHarnessManifest
```

Parse and validate a single `harness_config.yaml` file. Only reads the declared manifest (no sidecar merging), validated against the runtime manifest schema.

**Parameters**:
* **path**(`Union[str, Path]`): Manifest file path

**Returns**: Validated `RuntimeHarnessManifest` instance

**Raises**:
* `FileNotFoundError`: File does not exist
* `ValueError`: Schema validation fails

---

### function openjiuwen.auto_harness.infra.runtime_extension_loader.load_runtime_rails

```
load_runtime_rails(runtime_ext: RuntimeExtensionArtifact, *, session_id: str) -> list[type[Any]]
```

Load rail classes declared in the runtime extension manifest. Only processes entries with `type == "package"` that specify both `module` and `class_name`.

**Parameters**:
* **runtime_ext**(`RuntimeExtensionArtifact`): Runtime extension artifact
* **session_id**(`str`): Session ID

**Returns**: List of rail classes

---

### function openjiuwen.auto_harness.infra.runtime_extension_loader.load_runtime_tools

```
load_runtime_tools(runtime_ext: RuntimeExtensionArtifact, *, session_id: str) -> list[type[Any]]
```

Load tool classes declared in the runtime extension manifest. Only processes entries with `type == "package"` that specify both `module` and `class_name`.

**Parameters**:
* **runtime_ext**(`RuntimeExtensionArtifact`): Runtime extension artifact
* **session_id**(`str`): Session ID

**Returns**: List of tool classes

---

### function openjiuwen.auto_harness.infra.runtime_extension_loader.load_runtime_skill_dirs

```
load_runtime_skill_dirs(runtime_ext: RuntimeExtensionArtifact) -> list[str]
```

Return absolute paths of skill directories declared by the runtime extension. Resolves relative paths in `resources.skills.dirs` to absolute paths relative to the extension root directory.

**Parameters**:
* **runtime_ext**(`RuntimeExtensionArtifact`): Runtime extension artifact

**Returns**: List of skill directory absolute paths

---

### class openjiuwen.auto_harness.infra.runtime_extension_merger.MergedExtensionError

```
class openjiuwen.auto_harness.infra.runtime_extension_merger.MergedExtensionError(Exception)
```

Raised when merging runtime extensions encounters a fatal error.

---

### class openjiuwen.auto_harness.infra.runtime_extension_merger.MergeRuntimeExtensionsResult

```
@dataclass
class openjiuwen.auto_harness.infra.runtime_extension_merger.MergeRuntimeExtensionsResult
```

Output of `merge_runtime_extensions`.

**Fields**:
* **runtime_ext**(`RuntimeExtensionArtifact`): Merged runtime extension artifact
* **rename_map**(`dict[tuple[str, str], str]`): File rename mapping, key is `(source extension name, original relative path)`
* **skill_rename_map**(`dict[tuple[str, str], str]`): Skill rename mapping, key is `(source extension name, original skill name)`
* **source_exts_summary**(`list[dict[str, str]]`): Source extension summary list

---

### function openjiuwen.auto_harness.infra.runtime_extension_merger.merge_runtime_extensions

```
merge_runtime_extensions(artifacts: list['RuntimeExtensionArtifact'], session_root: Path, merged_name: str = 'merged_extensions') -> MergeRuntimeExtensionsResult
```

Deterministically merge multiple verified runtime extensions. Conflict detection uses full relative paths; conflicting files get a `__<source extension name>` suffix, non-conflicting files retain their original names. All AST import rewrites and manifest rewrites share the same rename_map. Automatically cleans up partial merge directories on failure.

**Parameters**:
* **artifacts**(`list[RuntimeExtensionArtifact]`): Source runtime extension artifacts to merge
* **session_root**(`Path`): Session runtime directory
* **merged_name**(`str`): Merged extension name, default `"merged_extensions"`

**Returns**: `MergeRuntimeExtensionsResult`

**Raises**:
* `MergedExtensionError`: Source manifest is invalid, syntax errors, or M1 self-check fails

---

### class openjiuwen.auto_harness.infra.runtime_extension_static_checks.ExtStaticCheckResult

```
@dataclass
class openjiuwen.auto_harness.infra.runtime_extension_static_checks.ExtStaticCheckResult
```

Static check counts and errors for an extension.

**Fields**:
* **errors**(`list[str] | None`): Error list, default `None` (initialized to empty list in `__post_init__`)
* **rails_count**(`int`): Rail count, default `0`
* **tools_count**(`int`): Tool count, default `0`
* **skills_count**(`int`): Skill count, default `0`
* **skill_dirs_count**(`int`): Skill directory count, default `0`

---

### function openjiuwen.auto_harness.infra.runtime_extension_static_checks.check_ruff

```
async check_ruff(extension_root: Path) -> list[str]
```

Auto-fix formatting then run lint checks on the extension root directory (`ruff format` + `ruff check --fix`), then check for remaining lint errors.

**Parameters**:
* **extension_root**(`Path`): Extension root directory

**Returns**: List of lint error descriptions

---

### function openjiuwen.auto_harness.infra.runtime_extension_static_checks.run_static_checks_against_runtime

```
async run_static_checks_against_runtime(*, runtime_ext: RuntimeExtensionArtifact, session_id_prefix: str) -> ExtStaticCheckResult
```

Comprehensive static checks: manifest schema validation -> load_runtime_rails/tools instantiation -> skill_dirs loading -> SKILL.md frontmatter validation -> ruff lint checks.

**Parameters**:
* **runtime_ext**(`RuntimeExtensionArtifact`): Runtime extension artifact
* **session_id_prefix**(`str`): Session ID prefix

**Returns**: `ExtStaticCheckResult`

---

## Parsing and Selection

### function openjiuwen.auto_harness.infra.parsers.parse_tasks

```
parse_tasks(raw: str) -> List[OptimizationTask]
```

Parse JSON task list from agent output. Supports code fence wrapping and bare JSON arrays.

**Parameters**:
* **raw**(`str`): Raw agent output text

**Returns**: Parsed `OptimizationTask` list

---

### function openjiuwen.auto_harness.infra.parsers.parse_learnings

```
parse_learnings(raw: str) -> List[dict]
```

Parse JSON experience list from learnings agent output.

**Parameters**:
* **raw**(`str`): Raw agent output text

**Returns**: List of dictionaries, each containing type/topic/summary/details

---

### function openjiuwen.auto_harness.infra.parsers.parse_pr_draft_with_error

```
parse_pr_draft_with_error(raw: str) -> tuple[PullRequestDraft | None, str]
```

Parse PR draft JSON response and return detailed error information. Supports Markdown code fence wrapped JSON and loosely formatted JSON-like text.

**Parameters**:
* **raw**(`str`): Raw agent output text

**Returns**: `(PullRequestDraft | None, error message)` tuple

---

### function openjiuwen.auto_harness.infra.parsers.parse_pr_draft

```
parse_pr_draft(raw: str) -> PullRequestDraft | None
```

Parse PR draft JSON response.

**Parameters**:
* **raw**(`str`): Raw agent output text

**Returns**: `PullRequestDraft` or `None`

---

### function openjiuwen.auto_harness.infra.parsers.parse_pipeline_selection

```
parse_pipeline_selection(raw: str) -> PipelineSelectionArtifact | None
```

Parse the selector agent's JSON response.

**Parameters**:
* **raw**(`str`): Raw agent output text

**Returns**: `PipelineSelectionArtifact` or `None`

---

### function openjiuwen.auto_harness.infra.parsers.extract_text

```
extract_text(chunk: Any) -> str
```

Extract text content from an OutputSchema chunk. Checks the `content` or `output` field in the `payload` attribute.

**Parameters**:
* **chunk**(`Any`): OutputSchema instance

**Returns**: Extracted text, or empty string if no content

---

### function openjiuwen.auto_harness.infra.parsers.parse_gaps

```
parse_gaps(raw_text: str) -> List[Gap]
```

Parse structured text into `Gap` objects. Accepts Markdown tables with columns: `competitor | feature | current_state | gap_description | impact | feasibility | suggested_approach | target_files`.

**Parameters**:
* **raw_text**(`str`): Markdown table or similar text

**Returns**: `Gap` list sorted by priority in descending order

---

### function openjiuwen.auto_harness.infra.parsers.parse_extension_designs

```
parse_extension_designs(raw: str) -> tuple[str | None, List[ExtensionDesign]]
```

Parse ExtensionDesign JSON list from agent output. Supports two formats (backward compatible): new format `{"package_name": "...", "designs": [...]}` and old format bare array or single design object.

**Parameters**:
* **raw**(`str`): Raw agent output text

**Returns**: `(package_name, designs)` tuple. Old format returns `(None, designs)`

---

### function openjiuwen.auto_harness.infra.pipeline_selector.detect_pipeline_signal

```
detect_pipeline_signal(tasks: Iterable[OptimizationTask] | None, config: AutoHarnessConfig) -> str | None
```

Detect whether the session should route to the extended evolve pipeline. Matches task topics, descriptions, expected effects, and optimization goals via keywords and regex patterns.

**Parameters**:
* **tasks**(`Iterable[OptimizationTask] | None`): Task list
* **config**(`AutoHarnessConfig`): Configuration

**Returns**: Matched pipeline name, or `None`

---

### function openjiuwen.auto_harness.infra.pipeline_selector.choose_session_pipeline

```
choose_session_pipeline(*, tasks: list[OptimizationTask] | None, config: AutoHarnessConfig, available_pipelines: list[str]) -> PipelineSelectionArtifact
```

Select session pipeline based on config preference and signal. Priority: explicit config preference > auto signal detection > default meta_evolve.

**Parameters**:
* **tasks**(`list[OptimizationTask] | None`): Task list
* **config**(`AutoHarnessConfig`): Configuration
* **available_pipelines**(`list[str]`): Available pipeline name list

**Returns**: `PipelineSelectionArtifact`

---

### function openjiuwen.auto_harness.pipelines.normalize_pipeline_name

```
normalize_pipeline_name(name: str) -> str
```

Normalize legacy pipeline names to current built-in names. For example, `"pr_pipeline"` -> `"meta_evolve_pipeline"`, `"extended_harness_pipeline"` -> `"extended_evolve_pipeline"`.

**Parameters**:
* **name**(`str`): Pipeline name

**Returns**: Normalized pipeline name

---

## PR Templates and Community Skills

### function openjiuwen.auto_harness.infra.gitcode_pr_template.load_pr_template_fallback

```
load_pr_template_fallback() -> str
```

Return the built-in PR template when the GitCode API is unavailable. Results are cached.

**Returns**: PR template text

---

### function openjiuwen.auto_harness.infra.gitcode_pr_template.template_suffix_for_language

```
template_suffix_for_language(language: str) -> str
```

Map auto-harness language config to GitCode template filename suffix. For example, `"en"` -> `".en"`, `"cn"` / `"zh"` -> `".zh-CN"`.

**Parameters**:
* **language**(`str`): Language config

**Returns**: Template filename suffix

---

### function openjiuwen.auto_harness.infra.gitcode_pr_template.pick_pr_template_entry

```
pick_pr_template_entry(templates: Sequence[RepositoryGitCodeTemplate], preferred_suffix: str) -> Optional[RepositoryGitCodeTemplate]
```

Pick best template metadata object from GitCode list response. Matches by preferred suffix, then fallback suffix.

**Parameters**:
* **templates**(`Sequence[RepositoryGitCodeTemplate]`): Template list
* **preferred_suffix**(`str`): Preferred suffix

**Returns**: Best template or `None`

---

### function openjiuwen.auto_harness.infra.gitcode_pr_template.fetch_pr_template

```
async fetch_pr_template(config: 'AutoHarnessConfig') -> str
```

Fetch upstream PR template text for the configured repository. Uses `AsyncGitCode.pulls.list_templates` and `get_template`. Falls back to built-in template when token is missing or API calls fail.

**Parameters**:
* **config**(`AutoHarnessConfig`): Auto Harness configuration

**Returns**: PR template text

---

### class openjiuwen.auto_harness.infra.github_cli.GitHubCliStatus

```
@dataclass
class openjiuwen.auto_harness.infra.github_cli.GitHubCliStatus
```

GitHub CLI pre-check result.

**Fields**:
* **available**(`bool`): Whether `gh` is available
* **authenticated**(`bool`): Whether authenticated
* **installed_now**(`bool`): Whether installed during this run, default `False`
* **path**(`str`): `gh` executable path, default `""`

---

### function openjiuwen.auto_harness.infra.github_cli.ensure_github_cli_ready

```
ensure_github_cli_ready(emit: Callable[[str], None]) -> GitHubCliStatus
```

Ensure `gh` exists and output login guidance if needed. If `gh` is missing, attempts installation via detected package manager. If installed but not authenticated, does not block execution (public repos can still be cloned).

**Parameters**:
* **emit**(`Callable[[str], None]`): Status message output callback

**Returns**: `GitHubCliStatus`

---

### class openjiuwen.auto_harness.infra.skill_source_manager.SkillMatch

```
@dataclass
class openjiuwen.auto_harness.infra.skill_source_manager.SkillMatch
```

A discovered community skill with metadata.

**Fields**:
* **name**(`str`): Skill name
* **description**(`str`): Skill description
* **repo_url**(`str`): Source repository URL
* **skill_dir**(`Path`): Skill directory path

---

### function openjiuwen.auto_harness.infra.skill_source_manager.ensure_skill_sources

```
async ensure_skill_sources(config: 'AutoHarnessConfig', *, emit: Any = None) -> List[str]
```

Download or update community skill source repositories. Uses zip direct download for known public GitHub repos; uses git clone with optional authentication for other repos (e.g., gitcode.com). Repos downloaded within 7 days skip update.

**Parameters**:
* **config**(`AutoHarnessConfig`): Auto Harness configuration
* **emit**(`Any`): Optional status message output callback

**Returns**: List of cached repository directory paths

---

### function openjiuwen.auto_harness.infra.skill_source_manager.scan_skills

```
scan_skills(config: 'AutoHarnessConfig') -> Dict[str, SkillMatch]
```

Scan all cached skill source repositories for available skills. Handles different repository structures (e.g., `skills/<name>/SKILL.md` or flat structure).

**Parameters**:
* **config**(`AutoHarnessConfig`): Auto Harness configuration

**Returns**: `skill_name -> SkillMatch` mapping dictionary

---

### function openjiuwen.auto_harness.infra.skill_source_manager.copy_skill_to_extension

```
copy_skill_to_extension(skill_name: str, extension_root: Path, config: 'AutoHarnessConfig') -> Optional[Path]
```

Copy a community skill to the extension's skills/ directory. Includes automatic frontmatter patching: if the source SKILL.md is missing `name` or `description` fields, they are automatically added.

**Parameters**:
* **skill_name**(`str`): Skill name
* **extension_root**(`Path`): Extension root directory
* **config**(`AutoHarnessConfig`): Auto Harness configuration

**Returns**: Destination skill directory path, or `None` (skill not found in cache)

---

### function openjiuwen.auto_harness.infra.skill_source_manager.community_skill_cache_skill_dirs

```
community_skill_cache_skill_dirs(config: 'AutoHarnessConfig') -> List[str]
```

Return skill root directory paths within each cached repository. Handles different repository structures: returns the `skills/` subdirectory if present, otherwise the repository root.

**Parameters**:
* **config**(`AutoHarnessConfig`): Auto Harness configuration

**Returns**: List of skill directory paths

---

### function openjiuwen.auto_harness.infra.skill_source_manager.format_community_skill_list

```
format_community_skill_list(config: 'AutoHarnessConfig') -> str
```

Format available community skills into a prompt-friendly list. Returns a multi-line string, each line containing skill name and truncated description.

**Parameters**:
* **config**(`AutoHarnessConfig`): Auto Harness configuration

**Returns**: Formatted skill list text

---

## Edit Scope

### function openjiuwen.auto_harness.infra.commit_scope.is_documentation_file

```
is_documentation_file(path: str) -> bool
```

Check if path points to a markdown file under docs/.

**Parameters**:
* **path**(`str`): File path

**Returns**: `True` if it is a .md file under docs/

---

### function openjiuwen.auto_harness.infra.commit_scope.is_allowed_documentation_file

```
is_allowed_documentation_file(path: str) -> bool
```

Check if documentation file is within allowed docs layout.

**Parameters**:
* **path**(`str`): File path

**Returns**: `True` if it is an allowed documentation file

---

### function openjiuwen.auto_harness.infra.commit_scope.derive_test_files

```
derive_test_files(task_files: list[str]) -> list[str]
```

Derive candidate test files from declared source files. For each non-test Python source file, generates `tests/unit_tests/**/test_<stem>.py` and `tests/system_tests/**/test_<stem>.py` patterns.

**Parameters**:
* **task_files**(`list[str]`): Source file path list

**Returns**: Candidate test file pattern list

---

### function openjiuwen.auto_harness.infra.commit_scope.is_derived_test_file

```
is_derived_test_file(source_files: list[str], candidate: str) -> bool
```

Check if candidate matches source-to-test mapping rules.

**Parameters**:
* **source_files**(`list[str]`): Source file path list
* **candidate**(`str`): Candidate test file path

**Returns**: `True` if matches mapping rules

---

### function openjiuwen.auto_harness.infra.commit_scope.extract_verify_related_files

```
extract_verify_related_files(ci_result: dict | None, fix_logs: str | None = None) -> list[str]
```

Extract explicitly mentioned test file paths from verification output. Scans CI result error messages and fix logs for `tests/` paths.

**Parameters**:
* **ci_result**(`dict | None`): CI result dictionary
* **fix_logs**(`str | None`): Fix log text

**Returns**: Deduplicated test file path list

---

### function openjiuwen.auto_harness.infra.commit_scope.derive_legacy_related_test_files

```
derive_legacy_related_test_files(edited_files: list[str], verify_related_files: list[str]) -> list[str]
```

Allow only when adapted legacy tests are both edited and directly referenced.

**Parameters**:
* **edited_files**(`list[str]`): Edited file list
* **verify_related_files**(`list[str]`): Verify related file list

**Returns**: Allowed legacy test file list

---

### function openjiuwen.auto_harness.infra.edit_scope.normalize_repo_path

```
normalize_repo_path(path: str) -> str
```

Normalize tool paths to repo-relative POSIX paths where possible.

**Parameters**:
* **path**(`str`): Original path

**Returns**: Normalized POSIX path

---

### function openjiuwen.auto_harness.infra.edit_scope.is_allowed_repo_edit_path

```
is_allowed_repo_edit_path(path: str) -> bool
```

Check if path is within allowed auto-harness edit scope. Allowed prefixes include `jiuwenswarm/`, `openjiuwen/dev_tools/`, `openjiuwen/harness/`, `openjiuwen/core/`, `tests/`, `examples/`, `docs/en/`, `docs/zh/`.

**Parameters**:
* **path**(`str`): File path

**Returns**: `True` if within allowed scope

---

### function openjiuwen.auto_harness.infra.edit_scope.render_edit_scope

```
render_edit_scope(header: str = 'Allowed change scope for this round') -> str
```

Render stable edit scope block for prompts. Output includes source path allowed scope, companion file allowed scope, documentation write restrictions, and out-of-scope handling rules.

**Parameters**:
* **header**(`str`): Header, default `"Allowed change scope for this round"`

**Returns**: Formatted edit scope text block
