# openjiuwen.auto_harness.infra

Auto Harness 基础设施子包，提供 CI 门控执行、Git 操作、worktree 管理、运行时扩展清单解析与合并、agent 输出解析、流水线选择、PR 模板获取、社区 skill 管理以及编辑范围约束等能力。这些模块为 auto-harness 编排器（orchestrator）和各级流水线提供底层支撑，不包含业务逻辑本身。

子模块：
- `ci_gate_runner`：CI 门控运行器，执行 lint / test / type-check 并解析结果
- `fix_loop`：两阶段 CI 修复循环控制器
- `session_budget`：会话预算控制器，管理时钟与 API 成本
- `git_operations`：Git 分支、推送与 PR 创建操作
- `worktree_manager`：为每个 task 创建隔离的 git worktree
- `git_auth`：任务级 GitCode 认证环境构建
- `runtime_manifest`：运行时 `harness_config.yaml` 清单 schema 与加载器
- `runtime_extension_loader`：从会话本地运行时目录加载运行时扩展
- `runtime_extension_merger`：将多个已验证运行时扩展合并为单一扩展
- `runtime_extension_static_checks`：运行时扩展的共享静态分析工具
- `parsers`：agent 输出解析工具集
- `pipeline_selector`：会话级流水线选择辅助
- `gitcode_pr_template`：获取 GitCode PR 模板
- `github_cli`：GitHub CLI 预检辅助
- `skill_source_manager`：社区 skill 源管理（克隆、扫描、复制）
- `commit_scope`：提交范围辅助函数
- `edit_scope`：auto-harness 规划与实现的共享编辑范围规则

---

## 运行时与门控

### class openjiuwen.auto_harness.infra.ci_gate_runner.CIGateRunner

```
class openjiuwen.auto_harness.infra.ci_gate_runner.CIGateRunner(workspace: str, config_path: str = '', python_executable: str = '', install_command: str = '')
```

执行 CI 门控检查并返回结构化结果。从 YAML 配置文件加载门控定义，支持 lint（ruff / codespell / pylint）、type-check（mypy）和 test（pytest）等门控类型，并对 lint 和 type-check 门控执行基于变更行范围的过滤，仅报告实际修改行上的违规。

**参数**：
* **workspace**(`str`)：make 命令的工作目录
* **config_path**(`str`)：ci_gate.yaml 路径，为空则使用默认配置
* **python_executable**(`str`)：Python 解释器路径
* **install_command**(`str`)：环境安装命令

#### set_workspace(workspace: str) -> None

更新命令工作目录。

**参数**：
* **workspace**(`str`)：新的工作目录

#### run(action: str = 'all') -> Dict[str, Any]

执行 CI 门控检查。根据 `action` 参数筛选要执行的门控（`"all"` 执行全部，`"check"` / `"lint"` 执行 lint 门控，`"test"` 执行测试门控，`"type-check"` 执行类型检查门控）。

**参数**：
* **action**(`str`)：要执行的门控类型，默认 `"all"`

**返回**：结构化结果字典，包含 `passed`（bool）、`gates`（各门控结果列表）和 `errors`（失败门控的合并错误信息）。

---

### function openjiuwen.auto_harness.infra.ci_gate_runner.decode_stdout

```
decode_stdout(stdout: bytes) -> str
```

使用跨平台编码处理解码子进程 stdout。在 Windows 上依次尝试 UTF-8、系统控制台编码（GBK/CP936）、latin-1；在 Unix 上尝试 UTF-8 和 latin-1。

**参数**：
* **stdout**(`bytes`)：子进程原始输出字节

**返回**：解码后的字符串

---

### class openjiuwen.auto_harness.infra.fix_loop.FixLoopResult

```
@dataclass
class openjiuwen.auto_harness.infra.fix_loop.FixLoopResult
```

修复循环执行结果。

**字段**：
* **success**(`bool`)：是否成功，默认 `False`
* **attempts**(`int`)：总尝试次数，默认 `0`
* **phase**(`int`)：最终所处阶段（1 或 2），默认 `1`
* **error_log**(`List[str]`)：错误日志列表，默认为空列表

---

### class openjiuwen.auto_harness.infra.fix_loop.FixLoopController

```
class openjiuwen.auto_harness.infra.fix_loop.FixLoopController(phase1_max_retries: int = 10, phase2_max_retries: int = 9, timeout_per_attempt: float = 600.0)
```

两阶段 CI 修复循环。Phase 1 — 直接修复：运行 CI -> 解析错误 -> agent 修复 -> 重试。Phase 2 — 评审修复：evaluator 审查质量，不通过则继续修复。

**参数**：
* **phase1_max_retries**(`int`)：Phase 1 最大重试次数，默认 10
* **phase2_max_retries**(`int`)：Phase 2 最大重试次数，默认 9
* **timeout_per_attempt**(`float`)：每次尝试的超时秒数，默认 600.0

#### run(ci_runner, agent_fixer, evaluator=None) -> FixLoopResult

```
async run(ci_runner: Callable[[], Coroutine[Any, Any, Any]], agent_fixer: Callable[[str], Coroutine[Any, Any, Any]], evaluator: Optional[Callable[[], Coroutine[Any, Any, Any]]] = None) -> FixLoopResult
```

执行两阶段修复循环。Phase 1 中反复运行 CI 并在失败时将错误交给 agent 修复；Phase 2 中由 evaluator 审查修复质量。

**参数**：
* **ci_runner**(`Callable[[], Coroutine[Any, Any, Any]]`)：运行 CI 的异步可调用对象，返回含 `.passed` 和 `.errors` 的对象
* **agent_fixer**(`Callable[[str], Coroutine[Any, Any, Any]]`)：接收错误信息并尝试修复的异步可调用对象
* **evaluator**(`Optional[Callable[[], Coroutine[Any, Any, Any]]]`)：可选，审查修复质量，返回含 `.approved` 的对象

**返回**：`FixLoopResult`，包含成功状态、尝试次数和错误日志

---

### class openjiuwen.auto_harness.infra.session_budget.SessionBudgetController

```
class openjiuwen.auto_harness.infra.session_budget.SessionBudgetController(wall_clock_secs: float = 3600.0, cost_limit_usd: float = 10.0, task_timeout_secs: float = 1200.0)
```

管理单次会话的时钟预算与 API 成本预算。当消耗达到 80% 阈值时自动发出警告日志。

**参数**：
* **wall_clock_secs**(`float`)：会话最大时长（秒），默认 3600.0
* **cost_limit_usd**(`float`)：API 成本上限（美元），默认 10.0
* **task_timeout_secs**(`float`)：单任务默认超时（秒），默认 1200.0

**示例**：

```python
budget = SessionBudgetController(wall_clock_secs=1800, cost_limit_usd=5.0)
budget.start()

if budget.should_stop:
    break
if not budget.check_task_budget():
    break

budget.add_cost(0.03)
print(f"剩余时间: {budget.remaining_secs:.0f}s")
```

#### start() -> None

记录会话起始时间。

#### add_cost(amount_usd: float) -> None

累加 API 成本并在达到阈值（80%）时发出警告。

**参数**：
* **amount_usd**(`float`)：本次 API 调用成本（美元）

#### elapsed_secs() -> float

**属性**。已用时长（秒）。

#### remaining_secs() -> float

**属性**。剩余时钟预算（秒）。

#### remaining_cost_usd() -> float

**属性**。剩余成本预算（美元）。

#### should_stop() -> bool

**属性**。时钟或成本预算耗尽时返回 `True`。

#### check_task_budget(task_timeout_secs: float | None = None) -> bool

检查是否有足够时间启动下一个任务。

**参数**：
* **task_timeout_secs**(`float | None`)：任务超时秒数，默认使用构造参数

**返回**：`True` 表示预算充足，可以启动任务

---

## Git 与 Worktree

### class openjiuwen.auto_harness.infra.git_operations.GitOperations

```
class openjiuwen.auto_harness.infra.git_operations.GitOperations(workspace: str, remote: str = '', base_branch: str = 'develop', fork_owner: str = '', upstream_owner: str = 'openJiuwen', upstream_repo: str = 'agent-core', gitcode_username: str = '', gitcode_token: str = '', user_name: str = '', user_email: str = '')
```

Git 操作（orchestrator 基础设施）。封装分支创建、状态收集、文件暂存、提交、推送和通过 GitCode API 创建 PR 等操作。所有 git 命令输出均自动清除凭据信息。

**参数**：
* **workspace**(`str`)：git 仓库工作目录
* **remote**(`str`)：fork 远程名称
* **base_branch**(`str`)：目标分支，默认 `"develop"`
* **fork_owner**(`str`)：fork 所有者
* **upstream_owner**(`str`)：上游仓库所有者，默认 `"openJiuwen"`
* **upstream_repo**(`str`)：上游仓库名称，默认 `"agent-core"`
* **gitcode_username**(`str`)：GitCode 用户名
* **gitcode_token**(`str`)：GitCode API token
* **user_name**(`str`)：git commit 用户名
* **user_email**(`str`)：git commit 邮箱

**示例**：

```python
git = GitOperations(workspace="/path/to/repo", remote="fork", gitcode_token="your-token")
await git.create_branch("fix/issue-42")
await git.add_paths(["openjiuwen/core/foo.py"])
await git.commit("fix: resolve foo issue")
result = await git.push("fix/issue-42")
pr = await git.create_pr("Fix issue #42", "Body", "fix/issue-42")
```

#### set_workspace(workspace: str) -> None

更新 git 命令工作目录。

#### create_branch(branch_name: str) -> Dict[str, Any]

```
async create_branch(branch_name: str) -> Dict[str, Any]
```

创建并切换到新分支。

**返回**：包含 `success`、`branch` 和 `output` 的字典

#### collect_status() -> Dict[str, List[str]]

```
async collect_status() -> Dict[str, List[str]]
```

收集当前 git 状态的结构化形式，包含 `dirty_files`、`tracked_modified_files`、`untracked_files` 和 `renamed_files` 四个列表。

#### list_dirty_files() -> List[str]

```
async list_dirty_files() -> List[str]
```

返回当前脏文件列表。

#### current_branch() -> str

```
async current_branch() -> str
```

返回当前分支名称。

#### current_head() -> str

```
async current_head() -> str
```

返回当前 HEAD SHA。

#### diff_stat(paths: List[str] | None = None) -> str

```
async diff_stat(paths: List[str] | None = None) -> str
```

返回 `git diff --stat` 摘要。

#### diff_stat_against_base() -> str

```
async diff_stat_against_base() -> str
```

返回当前分支相对于 base 分支的 diff stat。

#### add_paths(paths: List[str]) -> Dict[str, Any]

```
async add_paths(paths: List[str]) -> Dict[str, Any]
```

在当前工作区暂存指定的仓库相对路径。

**返回**：包含 `success` 和 `output` 的字典

#### commit(message: str) -> Dict[str, Any]

```
async commit(message: str) -> Dict[str, Any]
```

在当前工作区创建提交。

**返回**：包含 `success` 和 `output` 的字典

#### diff_name_only(revision: str = 'HEAD') -> List[str]

```
async diff_name_only(revision: str = 'HEAD') -> List[str]
```

返回 `git diff --name-only <revision>` 的规范化路径列表。

#### diff_name_only_against_base() -> List[str]

```
async diff_name_only_against_base() -> List[str]
```

返回当前分支提交相对于 base 分支的变更文件列表。

#### has_commits_against_base() -> bool

```
async has_commits_against_base() -> bool
```

返回 HEAD 是否包含不在 origin/base 中的提交。

#### find_existing_issue_fix_ref(issue_number: str, *, allowed_files: List[str] | None = None) -> Dict[str, Any]

```
async find_existing_issue_fix_ref(issue_number: str, *, allowed_files: List[str] | None = None) -> Dict[str, Any]
```

查找已有的携带 issue 修复的本地/远程分支。根据分支命名匹配、提交数量、文件重叠度进行评分排序。

**返回**：包含 `success`、`ref`、`commit_count`、`files`、`score` 的字典

#### cherry_pick_ref_commits(ref: str) -> Dict[str, Any]

```
async cherry_pick_ref_commits(ref: str) -> Dict[str, Any]
```

将 origin/base..ref 范围内的提交逐个 cherry-pick 到当前分支。

**返回**：包含 `success`、`output` 和 `commits` 的字典

#### status_porcelain() -> str

```
async status_porcelain() -> str
```

返回原始 `git status --porcelain` 输出。

#### show_last_commit_stat() -> str

```
async show_last_commit_stat() -> str
```

返回最新提交的紧凑摘要。

#### discard_worktree_changes() -> bool

```
async discard_worktree_changes() -> bool
```

通过 `git checkout .` 丢弃当前 worktree 的变更。

#### diff_against(revision: str) -> str

```
async diff_against(revision: str) -> str
```

返回 `git diff <revision>` 输出。

#### diff_against_base() -> str

```
async diff_against_base() -> str
```

返回当前分支提交相对于 base 分支的完整 diff。

#### push(branch_name: str) -> Dict[str, Any]

```
async push(branch_name: str) -> Dict[str, Any]
```

推送到 fork 远程。依次尝试 force-with-lease 推送、普通推送和 HEAD refspec 推送，并在全部失败时收集诊断信息。

**返回**：包含 `success` 和 `output` 的字典

#### build_pr_web_url(head_branch: str) -> str

返回用于手动创建 PR/MR 的 GitCode Web URL。

#### create_pr(title: str, body: str, head_branch: str) -> Dict[str, Any]

```
async create_pr(title: str, body: str, head_branch: str) -> Dict[str, Any]
```

通过 GitCode API 异步创建 PR。

**返回**：包含 `success`、`pr_url`（或 `error`）、`manual_url` 和 `diagnostics` 的字典

---

### class openjiuwen.auto_harness.infra.worktree_manager.WorktreeManager

```
class openjiuwen.auto_harness.infra.worktree_manager.WorktreeManager(config: 'AutoHarnessConfig')
```

为每个 task 创建和清理 git worktree。工作流：有 `local_repo` 时直接 fetch + worktree add；无 `local_repo` 时先确保 clone 缓存再 fetch + worktree add。

**参数**：
* **config**(`AutoHarnessConfig`)：Auto Harness 配置

#### prepare(topic: str) -> str

```
async prepare(topic: str) -> str
```

为 task 创建隔离的 worktree。根据 topic 生成文件系统安全的 slug 和分支名（`auto-harness/<slug>`），清理同名旧分支后创建新 worktree，并配置 git user 和 fork remote。

**返回**：worktree 绝对路径

#### prepare_readonly_snapshot(*, label: str = 'assess') -> str

```
async prepare_readonly_snapshot(*, label: str = 'assess') -> str
```

从 origin/base 创建分离的只读快照 worktree。用于 assess/plan 阶段，确保分析始终基于最新拉取的远程 base 分支。

**返回**：worktree 绝对路径

#### cleanup(worktree_path: str) -> None

```
async cleanup(worktree_path: str) -> None
```

清理 worktree。通过 `git worktree remove --force` 移除。

---

### function openjiuwen.auto_harness.infra.git_auth.build_git_auth_env

```
build_git_auth_env(*, username: str = '', token: str = '') -> Dict[str, str]
```

构建用于非交互式 GitCode 认证的子进程环境变量。禁用全局凭据助手，仅在 `https://gitcode.com` 请求中注入 Basic Authorization 头。

**参数**：
* **username**(`str`)：GitCode 用户名
* **token**(`str`)：GitCode API token

**返回**：配置好认证信息的子进程环境变量字典

---

## 运行时扩展

### class openjiuwen.auto_harness.infra.runtime_manifest.MetaSchema

```
class openjiuwen.auto_harness.infra.runtime_manifest.MetaSchema(BaseModel)
```

治理元数据 — 用于显示和权限管理。

**字段**：
* **owner**(`str`)：所有者，默认 `""`
* **tags**(`List[str]`)：标签列表，默认为空列表
* **visibility**(`str`)：可见性，默认 `"internal"`

---

### class openjiuwen.auto_harness.infra.runtime_manifest.SectionSchema

```
class openjiuwen.auto_harness.infra.runtime_manifest.SectionSchema(BaseModel)
```

单个 prompt 区段条目。

**字段**：
* **name**(`str`)：区段名称
* **priority**(`Optional[int]`)：优先级，默认 `None`
* **file**(`Optional[str]`)：文件路径，默认 `None`
* **content**(`Optional[Union[Dict[str, str], str]]`)：内容，默认 `None`

---

### class openjiuwen.auto_harness.infra.runtime_manifest.ToolResourceSchema

```
class openjiuwen.auto_harness.infra.runtime_manifest.ToolResourceSchema(BaseModel)
```

工具资源规格。

**字段**：
* **type**(`str`)：资源类型（`builtin` / `package` / `entry_point`）
* **names**(`Optional[List[str]]`)：名称列表，默认 `None`
* **name**(`Optional[str]`)：名称，默认 `None`
* **package**(`Optional[str]`)：包名，默认 `None`
* **module**(`Optional[str]`)：模块名，默认 `None`
* **class_name**(`Optional[str]`)：类名（YAML 中别名为 `class`），默认 `None`
* **kwargs**(`Dict[str, Any]`)：关键字参数，默认为空字典
* **params**(`Dict[str, Any]`)：参数，默认为空字典

---

### class openjiuwen.auto_harness.infra.runtime_manifest.RailResourceSchema

```
class openjiuwen.auto_harness.infra.runtime_manifest.RailResourceSchema(BaseModel)
```

Rail 资源规格。

**字段**：
* **type**(`str`)：资源类型（`builtin` / `package` / `entry_point`）
* **name**(`Optional[str]`)：名称，默认 `None`
* **package**(`Optional[str]`)：包名，默认 `None`
* **module**(`Optional[str]`)：模块名，默认 `None`
* **class_name**(`Optional[str]`)：类名（YAML 中别名为 `class`），默认 `None`
* **kwargs**(`Dict[str, Any]`)：关键字参数，默认为空字典
* **params**(`Dict[str, Any]`)：参数，默认为空字典

---

### class openjiuwen.auto_harness.infra.runtime_manifest.SkillsSchema

```
class openjiuwen.auto_harness.infra.runtime_manifest.SkillsSchema(BaseModel)
```

Skills 配置。

**字段**：
* **dirs**(`List[str]`)：skill 目录列表，默认为空列表
* **mode**(`str`)：模式（`all` / `auto_list`），默认 `"all"`

---

### class openjiuwen.auto_harness.infra.runtime_manifest.McpResourceSchema

```
class openjiuwen.auto_harness.infra.runtime_manifest.McpResourceSchema(BaseModel)
```

MCP 服务器规格。

**字段**：
* **type**(`str`)：传输类型，默认 `"stdio"`
* **name**(`Optional[str]`)：名称，默认 `None`
* **server_name**(`Optional[str]`)：服务器名称，默认 `None`
* **server_id**(`Optional[str]`)：服务器 ID，默认 `None`
* **url**(`Optional[str]`)：URL，默认 `None`
* **command**(`str`)：启动命令，默认 `""`
* **args**(`List[str]`)：命令参数，默认为空列表
* **env**(`Dict[str, str]`)：环境变量，默认为空字典
* **cwd**(`Optional[str]`)：工作目录，默认 `None`
* **params**(`Dict[str, Any]`)：参数，默认为空字典
* **auth_headers**(`Dict[str, str]`)：认证头，默认为空字典
* **auth_query_params**(`Dict[str, str]`)：认证查询参数，默认为空字典

---

### class openjiuwen.auto_harness.infra.runtime_manifest.ResourcesSchema

```
class openjiuwen.auto_harness.infra.runtime_manifest.ResourcesSchema(BaseModel)
```

所有运行时资源：tools、rails、skills、MCPs。

**字段**：
* **tools**(`List[ToolResourceSchema]`)：工具列表，默认为空列表
* **rails**(`List[RailResourceSchema]`)：rail 列表，默认为空列表
* **skills**(`Optional[SkillsSchema]`)：skills 配置，默认 `None`
* **mcps**(`List[McpResourceSchema]`)：MCP 服务器列表，默认为空列表

---

### class openjiuwen.auto_harness.infra.runtime_manifest.PromptsSchema

```
class openjiuwen.auto_harness.infra.runtime_manifest.PromptsSchema(BaseModel)
```

Prompt 区段声明。

**字段**：
* **sections**(`List[SectionSchema]`)：区段列表，默认为空列表

---

### class openjiuwen.auto_harness.infra.runtime_manifest.WorkspaceSchema

```
class openjiuwen.auto_harness.infra.runtime_manifest.WorkspaceSchema(BaseModel)
```

工作区（文件操作根目录）。

**字段**：
* **root_path**(`str`)：根路径，默认 `"./"`

---

### class openjiuwen.auto_harness.infra.runtime_manifest.RuntimeHarnessManifest

```
class openjiuwen.auto_harness.infra.runtime_manifest.RuntimeHarnessManifest(BaseModel)
```

运行时扩展的顶层 `harness_config.yaml` schema。定义了运行时扩展的完整配置结构，包括元数据、工作区、prompt、资源、语言、迭代限制、任务循环、子 agent、权限等。

**字段**：
* **schema_version**(`str`)：schema 版本，默认 `"harness_config.v0.1"`
* **meta**(`Optional[MetaSchema]`)：治理元数据，默认 `None`
* **id**(`Optional[str]`)：扩展 ID，默认 `None`
* **name**(`Optional[str]`)：扩展名称，默认 `None`
* **description**(`Optional[str]`)：描述，默认 `None`
* **workspace**(`Optional[WorkspaceSchema]`)：工作区配置，默认 `None`
* **prompts**(`Optional[PromptsSchema]`)：prompt 配置，默认 `None`
* **resources**(`Optional[ResourcesSchema]`)：资源配置，默认 `None`
* **language**(`str`)：语言，默认 `"cn"`
* **max_iterations**(`Optional[int]`)：最大迭代次数，默认 `None`
* **completion_timeout**(`Optional[float]`)：完成超时，默认 `None`
* **enable_task_loop**(`Optional[bool]`)：是否启用任务循环，默认 `None`
* **enable_async_subagent**(`Optional[bool]`)：是否启用异步子 agent，默认 `None`
* **add_general_purpose_agent**(`Optional[bool]`)：是否添加通用 agent，默认 `None`
* **enable_task_planning**(`Optional[bool]`)：是否启用任务规划，默认 `None`
* **restrict_to_work_dir**(`Optional[bool]`)：是否限制在工作目录，默认 `None`
* **prompt_mode**(`Optional[str]`)：prompt 模式，默认 `None`
* **default_mode**(`Optional[str]`)：默认模式，默认 `None`
* **permissions**(`Optional[Dict[str, Any]]`)：权限配置，默认 `None`
* **progressive_tool_enabled**(`Optional[bool]`)：是否启用渐进式工具，默认 `None`
* **progressive_tool_always_visible_tools**(`List[str]`)：始终可见的工具列表，默认为空列表
* **progressive_tool_default_visible_tools**(`List[str]`)：默认可见工具列表，默认为空列表
* **progressive_tool_max_loaded_tools**(`Optional[int]`)：最大加载工具数，默认 `None`
* **subagents**(`Optional[List[Dict[str, Any]]]`)：子 agent 配置，默认 `None`
* **context**(`Optional[Dict[str, Any]]`)：上下文配置，默认 `None`
* **stop_eval_conditions**(`Optional[Dict[str, Any]]`)：停止评估条件，默认 `None`

---

### function openjiuwen.auto_harness.infra.runtime_manifest.load_runtime_manifest

```
load_runtime_manifest(path: Union[str, Path]) -> RuntimeHarnessManifest
```

解析并验证单个 `harness_config.yaml` 文件。仅读取声明的清单（不含 sidecar 合并），按运行时清单 schema 进行验证。

**参数**：
* **path**(`Union[str, Path]`)：清单文件路径

**返回**：验证后的 `RuntimeHarnessManifest` 实例

**异常**：
* `FileNotFoundError`：文件不存在
* `ValueError`：schema 验证失败

---

### function openjiuwen.auto_harness.infra.runtime_extension_loader.load_runtime_rails

```
load_runtime_rails(runtime_ext: RuntimeExtensionArtifact, *, session_id: str) -> list[type[Any]]
```

加载运行时扩展清单中声明的 rail 类。仅处理 `type == "package"` 且同时指定了 `module` 和 `class_name` 的条目。

**参数**：
* **runtime_ext**(`RuntimeExtensionArtifact`)：运行时扩展产物
* **session_id**(`str`)：会话 ID

**返回**：rail 类列表

---

### function openjiuwen.auto_harness.infra.runtime_extension_loader.load_runtime_tools

```
load_runtime_tools(runtime_ext: RuntimeExtensionArtifact, *, session_id: str) -> list[type[Any]]
```

加载运行时扩展清单中声明的工具类。仅处理 `type == "package"` 且同时指定了 `module` 和 `class_name` 的条目。

**参数**：
* **runtime_ext**(`RuntimeExtensionArtifact`)：运行时扩展产物
* **session_id**(`str`)：会话 ID

**返回**：工具类列表

---

### function openjiuwen.auto_harness.infra.runtime_extension_loader.load_runtime_skill_dirs

```
load_runtime_skill_dirs(runtime_ext: RuntimeExtensionArtifact) -> list[str]
```

返回运行时扩展声明的 skill 目录绝对路径列表。将 `resources.skills.dirs` 中的相对路径解析为相对于扩展根目录的绝对路径。

**参数**：
* **runtime_ext**(`RuntimeExtensionArtifact`)：运行时扩展产物

**返回**：skill 目录绝对路径列表

---

### class openjiuwen.auto_harness.infra.runtime_extension_merger.MergedExtensionError

```
class openjiuwen.auto_harness.infra.runtime_extension_merger.MergedExtensionError(Exception)
```

当合并运行时扩展发生致命错误时抛出。

---

### class openjiuwen.auto_harness.infra.runtime_extension_merger.MergeRuntimeExtensionsResult

```
@dataclass
class openjiuwen.auto_harness.infra.runtime_extension_merger.MergeRuntimeExtensionsResult
```

`merge_runtime_extensions` 的输出。

**字段**：
* **runtime_ext**(`RuntimeExtensionArtifact`)：合并后的运行时扩展产物
* **rename_map**(`dict[tuple[str, str], str]`)：文件重命名映射，键为 `(源扩展名, 原始相对路径)`
* **skill_rename_map**(`dict[tuple[str, str], str]`)：skill 重命名映射，键为 `(源扩展名, 原始 skill 名)`
* **source_exts_summary**(`list[dict[str, str]]`)：源扩展摘要列表

---

### function openjiuwen.auto_harness.infra.runtime_extension_merger.merge_runtime_extensions

```
merge_runtime_extensions(artifacts: list['RuntimeExtensionArtifact'], session_root: Path, merged_name: str = 'merged_extensions') -> MergeRuntimeExtensionsResult
```

确定性地合并多个已验证的运行时扩展。冲突检测使用完整相对路径；冲突文件添加 `__<源扩展名>` 后缀，非冲突文件保留原名。所有 AST import 重写和清单重写共享同一个 rename_map。失败时自动清理部分合并目录。

**参数**：
* **artifacts**(`list[RuntimeExtensionArtifact]`)：要合并的源运行时扩展产物
* **session_root**(`Path`)：会话运行时目录
* **merged_name**(`str`)：合并后扩展名称，默认 `"merged_extensions"`

**返回**：`MergeRuntimeExtensionsResult`

**异常**：
* `MergedExtensionError`：源清单无效、语法错误或 M1 自检失败

---

### class openjiuwen.auto_harness.infra.runtime_extension_static_checks.ExtStaticCheckResult

```
@dataclass
class openjiuwen.auto_harness.infra.runtime_extension_static_checks.ExtStaticCheckResult
```

扩展的静态验证计数和错误。

**字段**：
* **errors**(`list[str] | None`)：错误列表，默认 `None`（`__post_init__` 中初始化为空列表）
* **rails_count**(`int`)：rail 数量，默认 `0`
* **tools_count**(`int`)：工具数量，默认 `0`
* **skills_count**(`int`)：skill 数量，默认 `0`
* **skill_dirs_count**(`int`)：skill 目录数量，默认 `0`

---

### function openjiuwen.auto_harness.infra.runtime_extension_static_checks.check_ruff

```
async check_ruff(extension_root: Path) -> list[str]
```

在 extension_root 上先自动修复格式化和 lint 问题（`ruff format` + `ruff check --fix`），然后检查剩余 lint 错误。

**参数**：
* **extension_root**(`Path`)：扩展根目录

**返回**：lint 错误描述列表

---

### function openjiuwen.auto_harness.infra.runtime_extension_static_checks.run_static_checks_against_runtime

```
async run_static_checks_against_runtime(*, runtime_ext: RuntimeExtensionArtifact, session_id_prefix: str) -> ExtStaticCheckResult
```

执行完整的静态检查流水线：清单 schema 验证 -> load_runtime_rails/tools 实例化 -> skill_dirs 加载 -> SKILL.md frontmatter 验证 -> ruff lint 检查。

**参数**：
* **runtime_ext**(`RuntimeExtensionArtifact`)：运行时扩展产物
* **session_id_prefix**(`str`)：会话 ID 前缀

**返回**：`ExtStaticCheckResult`

---

## 解析与选择

### function openjiuwen.auto_harness.infra.parsers.parse_tasks

```
parse_tasks(raw: str) -> List[OptimizationTask]
```

从 agent 输出中解析 JSON 任务列表。支持代码围栏包裹和裸 JSON 数组。

**参数**：
* **raw**(`str`)：agent 输出的原始文本

**返回**：解析后的 `OptimizationTask` 列表

---

### function openjiuwen.auto_harness.infra.parsers.parse_learnings

```
parse_learnings(raw: str) -> List[dict]
```

从 learnings agent 输出中解析 JSON 经验列表。

**参数**：
* **raw**(`str`)：agent 输出的原始文本

**返回**：字典列表，每个包含 type/topic/summary/details

---

### function openjiuwen.auto_harness.infra.parsers.parse_pr_draft_with_error

```
parse_pr_draft_with_error(raw: str) -> tuple[PullRequestDraft | None, str]
```

解析 PR draft JSON 响应并返回详细错误信息。支持 Markdown 代码围栏包裹的 JSON 和格式松散的 JSON-like 文本。

**参数**：
* **raw**(`str`)：agent 输出的原始文本

**返回**：`(PullRequestDraft | None, 错误信息)` 元组

---

### function openjiuwen.auto_harness.infra.parsers.parse_pr_draft

```
parse_pr_draft(raw: str) -> PullRequestDraft | None
```

解析 PR draft JSON 响应。

**参数**：
* **raw**(`str`)：agent 输出的原始文本

**返回**：`PullRequestDraft` 或 `None`

---

### function openjiuwen.auto_harness.infra.parsers.parse_pipeline_selection

```
parse_pipeline_selection(raw: str) -> PipelineSelectionArtifact | None
```

解析选择器 agent 的 JSON 响应。

**参数**：
* **raw**(`str`)：agent 输出的原始文本

**返回**：`PipelineSelectionArtifact` 或 `None`

---

### function openjiuwen.auto_harness.infra.parsers.extract_text

```
extract_text(chunk: Any) -> str
```

从 OutputSchema chunk 中提取文本内容。检查 `payload` 属性中的 `content` 或 `output` 字段。

**参数**：
* **chunk**(`Any`)：OutputSchema 实例

**返回**：提取的文本，无内容时返回空字符串

---

### function openjiuwen.auto_harness.infra.parsers.parse_gaps

```
parse_gaps(raw_text: str) -> List[Gap]
```

将结构化文本解析为 `Gap` 对象。接受 Markdown 表格，每行包含列：`competitor | feature | current_state | gap_description | impact | feasibility | suggested_approach | target_files`。

**参数**：
* **raw_text**(`str`)：Markdown 表格或类似文本

**返回**：按优先级降序排列的 `Gap` 列表

---

### function openjiuwen.auto_harness.infra.parsers.parse_extension_designs

```
parse_extension_designs(raw: str) -> tuple[str | None, List[ExtensionDesign]]
```

从 agent 输出中解析 ExtensionDesign JSON 列表。支持两种格式（向后兼容）：新格式 `{"package_name": "...", "designs": [...]}` 和旧格式裸数组或单个 design 对象。

**参数**：
* **raw**(`str`)：agent 输出的原始文本

**返回**：`(package_name, designs)` 元组。旧格式返回 `(None, designs)`

---

### function openjiuwen.auto_harness.infra.pipeline_selector.detect_pipeline_signal

```
detect_pipeline_signal(tasks: Iterable[OptimizationTask] | None, config: AutoHarnessConfig) -> str | None
```

检测会话是否应路由到 extended evolve 流水线。通过关键词和正则模式匹配任务主题、描述、预期效果和优化目标。

**参数**：
* **tasks**(`Iterable[OptimizationTask] | None`)：任务列表
* **config**(`AutoHarnessConfig`)：配置

**返回**：匹配的流水线名称，或 `None`

---

### function openjiuwen.auto_harness.infra.pipeline_selector.choose_session_pipeline

```
choose_session_pipeline(*, tasks: list[OptimizationTask] | None, config: AutoHarnessConfig, available_pipelines: list[str]) -> PipelineSelectionArtifact
```

根据配置偏好和信号选择会话流水线。优先级：配置显式偏好 > 自动信号检测 > 默认 meta_evolve。

**参数**：
* **tasks**(`list[OptimizationTask] | None`)：任务列表
* **config**(`AutoHarnessConfig`)：配置
* **available_pipelines**(`list[str]`)：可用流水线名称列表

**返回**：`PipelineSelectionArtifact`

---

### function openjiuwen.auto_harness.pipelines.normalize_pipeline_name

```
normalize_pipeline_name(name: str) -> str
```

将遗留流水线名称规范化为当前内置名称。例如 `"pr_pipeline"` -> `"meta_evolve_pipeline"`，`"extended_harness_pipeline"` -> `"extended_evolve_pipeline"`。

**参数**：
* **name**(`str`)：流水线名称

**返回**：规范化后的流水线名称

---

## PR 模板与社区 skills

### function openjiuwen.auto_harness.infra.gitcode_pr_template.load_pr_template_fallback

```
load_pr_template_fallback() -> str
```

返回 GitCode API 不可用时使用的内置 PR 模板。结果会被缓存。

**返回**：PR 模板文本

---

### function openjiuwen.auto_harness.infra.gitcode_pr_template.template_suffix_for_language

```
template_suffix_for_language(language: str) -> str
```

将 auto-harness 语言配置映射为 GitCode 模板文件名后缀。例如 `"en"` -> `".en"`，`"cn"` / `"zh"` -> `".zh-CN"`。

**参数**：
* **language**(`str`)：语言配置

**返回**：模板文件名后缀

---

### function openjiuwen.auto_harness.infra.gitcode_pr_template.pick_pr_template_entry

```
pick_pr_template_entry(templates: Sequence[RepositoryGitCodeTemplate], preferred_suffix: str) -> Optional[RepositoryGitCodeTemplate]
```

从 GitCode 列表响应中选择最佳模板元数据对象。按首选后缀、回退后缀顺序匹配。

**参数**：
* **templates**(`Sequence[RepositoryGitCodeTemplate]`)：模板列表
* **preferred_suffix**(`str`)：首选后缀

**返回**：最佳模板或 `None`

---

### function openjiuwen.auto_harness.infra.gitcode_pr_template.fetch_pr_template

```
async fetch_pr_template(config: 'AutoHarnessConfig') -> str
```

获取已配置仓库的上游 PR 模板文本。使用 `AsyncGitCode.pulls.list_templates` 和 `get_template`。当 token 缺失或 API 调用失败时回退到内置模板。

**参数**：
* **config**(`AutoHarnessConfig`)：Auto Harness 配置

**返回**：PR 模板文本

---

### class openjiuwen.auto_harness.infra.github_cli.GitHubCliStatus

```
@dataclass
class openjiuwen.auto_harness.infra.github_cli.GitHubCliStatus
```

GitHub CLI 预检结果。

**字段**：
* **available**(`bool`)：`gh` 是否可用
* **authenticated**(`bool`)：是否已认证
* **installed_now**(`bool`)：是否在本次运行中安装，默认 `False`
* **path**(`str`)：`gh` 可执行文件路径，默认 `""`

---

### function openjiuwen.auto_harness.infra.github_cli.ensure_github_cli_ready

```
ensure_github_cli_ready(emit: Callable[[str], None]) -> GitHubCliStatus
```

确保 `gh` 存在并在需要时打印登录指引。如果 `gh` 缺失，尝试通过检测到的包管理器安装。如果已安装但未认证，不阻止运行（公开仓库仍可 clone）。

**参数**：
* **emit**(`Callable[[str], None]`)：状态消息输出回调

**返回**：`GitHubCliStatus`

---

### class openjiuwen.auto_harness.infra.skill_source_manager.SkillMatch

```
@dataclass
class openjiuwen.auto_harness.infra.skill_source_manager.SkillMatch
```

发现的社区 skill 及其元数据。

**字段**：
* **name**(`str`)：skill 名称
* **description**(`str`)：skill 描述
* **repo_url**(`str`)：源仓库 URL
* **skill_dir**(`Path`)：skill 目录路径

---

### function openjiuwen.auto_harness.infra.skill_source_manager.ensure_skill_sources

```
async ensure_skill_sources(config: 'AutoHarnessConfig', *, emit: Any = None) -> List[str]
```

下载或更新社区 skill 源仓库。对已知 GitHub 公开仓库使用 zip 直接下载，对其他仓库（如 gitcode.com）使用 git clone 并可选认证。7 天内已下载的仓库跳过更新。

**参数**：
* **config**(`AutoHarnessConfig`)：Auto Harness 配置
* **emit**(`Any`)：可选的状态消息输出回调

**返回**：缓存的仓库目录路径列表

---

### function openjiuwen.auto_harness.infra.skill_source_manager.scan_skills

```
scan_skills(config: 'AutoHarnessConfig') -> Dict[str, SkillMatch]
```

扫描所有缓存的 skill 源仓库，查找可用 skill。处理不同的仓库结构（如 `skills/<name>/SKILL.md` 或扁平结构）。

**参数**：
* **config**(`AutoHarnessConfig`)：Auto Harness 配置

**返回**：`skill_name -> SkillMatch` 映射字典

---

### function openjiuwen.auto_harness.infra.skill_source_manager.copy_skill_to_extension

```
copy_skill_to_extension(skill_name: str, extension_root: Path, config: 'AutoHarnessConfig') -> Optional[Path]
```

将社区 skill 复制到扩展的 skills/ 目录。包含自动 frontmatter 修补：如果源 SKILL.md 缺少 `name` 或 `description` 字段，会自动添加。

**参数**：
* **skill_name**(`str`)：skill 名称
* **extension_root**(`Path`)：扩展根目录
* **config**(`AutoHarnessConfig`)：Auto Harness 配置

**返回**：目标 skill 目录路径，或 `None`（skill 未在缓存中找到）

---

### function openjiuwen.auto_harness.infra.skill_source_manager.community_skill_cache_skill_dirs

```
community_skill_cache_skill_dirs(config: 'AutoHarnessConfig') -> List[str]
```

返回每个缓存仓库内的 skill 根目录路径。处理不同的仓库结构：有 `skills/` 子目录时返回该子目录，否则返回仓库根目录。

**参数**：
* **config**(`AutoHarnessConfig`)：Auto Harness 配置

**返回**：skill 目录路径列表

---

### function openjiuwen.auto_harness.infra.skill_source_manager.format_community_skill_list

```
format_community_skill_list(config: 'AutoHarnessConfig') -> str
```

将可用社区 skill 格式化为 prompt 友好的列表。返回多行字符串，每行包含 skill 名称和截断后的描述。

**参数**：
* **config**(`AutoHarnessConfig`)：Auto Harness 配置

**返回**：格式化的 skill 列表文本

---

## 编辑范围

### function openjiuwen.auto_harness.infra.commit_scope.is_documentation_file

```
is_documentation_file(path: str) -> bool
```

返回路径是否指向 `docs/` 下的 Markdown 文件。

**参数**：
* **path**(`str`)：文件路径

**返回**：`True` 如果是 docs/ 下的 .md 文件

---

### function openjiuwen.auto_harness.infra.commit_scope.is_allowed_documentation_file

```
is_allowed_documentation_file(path: str) -> bool
```

返回文档文件是否在允许的 docs 布局内。

**参数**：
* **path**(`str`)：文件路径

**返回**：`True` 如果是允许的文档文件

---

### function openjiuwen.auto_harness.infra.commit_scope.derive_test_files

```
derive_test_files(task_files: list[str]) -> list[str]
```

从声明的源文件派生候选测试文件。对每个非测试 Python 源文件生成 `tests/unit_tests/**/test_<stem>.py` 和 `tests/system_tests/**/test_<stem>.py` 模式。

**参数**：
* **task_files**(`list[str]`)：源文件路径列表

**返回**：候选测试文件模式列表

---

### function openjiuwen.auto_harness.infra.commit_scope.is_derived_test_file

```
is_derived_test_file(source_files: list[str], candidate: str) -> bool
```

检查候选文件是否匹配源文件到测试文件的映射规则。

**参数**：
* **source_files**(`list[str]`)：源文件路径列表
* **candidate**(`str`)：候选测试文件路径

**返回**：`True` 如果匹配映射规则

---

### function openjiuwen.auto_harness.infra.commit_scope.extract_verify_related_files

```
extract_verify_related_files(ci_result: dict | None, fix_logs: str | None = None) -> list[str]
```

从验证输出中提取明确提及的测试文件路径。扫描 CI 结果错误信息和修复日志中的 `tests/` 路径。

**参数**：
* **ci_result**(`dict | None`)：CI 结果字典
* **fix_logs**(`str | None`)：修复日志文本

**返回**：去重后的测试文件路径列表

---

### function openjiuwen.auto_harness.infra.commit_scope.derive_legacy_related_test_files

```
derive_legacy_related_test_files(edited_files: list[str], verify_related_files: list[str]) -> list[str]
```

仅在编辑文件和直接引用的验证文件同时匹配时允许适配的遗留测试。

**参数**：
* **edited_files**(`list[str]`)：已编辑文件列表
* **verify_related_files**(`list[str]`)：验证相关文件列表

**返回**：允许的遗留测试文件列表

---

### function openjiuwen.auto_harness.infra.edit_scope.normalize_repo_path

```
normalize_repo_path(path: str) -> str
```

将工具路径规范化为仓库相对的 POSIX 路径（尽可能）。

**参数**：
* **path**(`str`)：原始路径

**返回**：规范化后的 POSIX 路径

---

### function openjiuwen.auto_harness.infra.edit_scope.is_allowed_repo_edit_path

```
is_allowed_repo_edit_path(path: str) -> bool
```

返回路径是否在允许的 auto-harness 编辑范围内。允许的前缀包括 `jiuwenswarm/`、`openjiuwen/dev_tools/`、`openjiuwen/harness/`、`openjiuwen/core/`、`tests/`、`examples/`、`docs/en/`、`docs/zh/`。

**参数**：
* **path**(`str`)：文件路径

**返回**：`True` 如果在允许范围内

---

### function openjiuwen.auto_harness.infra.edit_scope.render_edit_scope

```
render_edit_scope(header: str = '本轮允许变更范围') -> str
```

为 prompt 渲染稳定的编辑范围块。输出包含源码路径允许范围、配套文件允许范围、文档写入限制和越界处理规则。

**参数**：
* **header**(`str`)：标题，默认 `"本轮允许变更范围"`

**返回**：格式化的编辑范围文本块
