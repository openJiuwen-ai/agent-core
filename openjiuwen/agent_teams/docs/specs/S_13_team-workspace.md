# Team Workspace

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | `openjiuwen/agent_teams/team_workspace/` |
| 最近一次修订 commit | `18823271` |
| 关联 feature | — |

## 范围 / 边界

本 spec 描述团队成员之间的**共享工作空间**：跨成员可见的产物目录 + 可选 git 版本控制 + 可选文件锁。
入口是 `TeamWorkspaceManager`，对应配置是 `TeamWorkspaceConfig`。

### 管什么

- 每个 team 一份共享磁盘目录（默认 `team_home(team_name)/team-workspace/`）。
- 该目录下的产物（文档、代码、报告、轨迹）被**所有成员通过 `.team/{team_name}/` 挂载点**读写。
- 该目录的版本（git auto-commit、history 查询、distributed pull / push）。
- 该目录下文件的写锁（in-memory，按文件路径粒度，带 timeout）。
- 通过事件总线发布 workspace 级事件（artifact 更新、冲突、lock 请求/响应）。

### 不管什么

- **不管代码隔离**。每个成员各自的 git worktree 由 `WorktreeManager`（`openjiuwen.harness.tools.worktree`）独立负责；workspace 是产物协同区，worktree 是源码工作树，二者磁盘路径不相交。
- **不管文件 I/O 实现**。`read_file` / `write_file` / `glob` 等读写动作走标准 SysOperation 工具，命中 `.team/` 前缀时被 `TeamWorkspaceRail` 拦截、加上锁与版本策略。manager 自身不做内容读写。
- **不管成员私有 workspace**。成员私有目录是 `team_home(team_name)/workspaces/{member}_workspace/` 或 `independent_member_workspace(member)`，由 `agent_configurator.setup_agent` 处理；workspace manager 只把团队共享区**挂载**进成员 workspace。
- **不管多 team 全局协调**。一份 `TeamWorkspaceManager` 只服务一个 team_name；跨 team 的状态在 runtime pool 层。

## 不变量

1. **Workspace 与 worktree 路径不相交**。Workspace 路径由 `paths.team_home(team_name) / "team-workspace"` 派生（或 config 显式指定 `root_path`）；worktree 路径由 `WorktreeManager.config` 决定。两者不能互相覆盖也不能互为子目录。`mount_into_worktree` 在 worktree 内只放 `.team` 符号链接 + 把 `.agent/` `.team/` 写入 `.gitignore`，不落产物文件。
2. **`paths.py` 是路径布局唯一真相源**。Workspace 的默认根、artifact 子目录、挂载点都从 `team_home(team_name)` 推出；`agent_configurator.create_workspace_manager` 不绕开 `team_home`。Config 的 `root_path` 是用户覆盖入口，不是新增散落硬编码的理由。
3. **挂载点统一为 `.team/{team_name}/`**。成员 workspace 通过 `mount_into_workspace(workspace_root)` 在 `workspace_root/.team/{team_name}` 上建符号链接。`TeamWorkspaceRail` 与 prompts 中宣告的挂载路径必须严格一致；rail 解析 `.team/` 前缀时既兼容 hub 布局（`.team/{team_name}/...`）也兼容 legacy 布局（`.team/...`），但**新代码只生成 hub 布局**。
4. **Windows 兜底用 junction，不静默忽略**。`os.symlink` 在 Windows 因权限失败时退到 `mklink /J`；junction 创建失败抛 `OSError`，不允许 catch-all 当成功处理。
5. **文件锁是单一权威，按文件路径粒度**。LOCAL 模式：`TeamWorkspaceManager._locks` 是唯一权威。DISTRIBUTED 模式：leader 节点是唯一权威，远端通过 `WorkspaceLockRequestEvent` 走 messager 请求 leader 决议。同一文件不存在两个并发持锁人。
6. **过期锁自动回收，可重入刷新**。`WorkspaceFileLock.is_expired()` 由 `acquired_at + timeout_seconds` 与当前时间判定。`acquire_lock` 遇到他人过期锁直接覆盖；遇到自己持的锁刷新时间，不算冲突。
7. **锁失败必须显式表达**。`acquire_lock` 返回 `False` 而非抛异常；`release_lock` 释放他人锁返回 `False`。`TeamWorkspaceRail.before_tool_call` 把 lock 拒绝写到 `ctx.extra["workspace_lock_rejected"]`，下游决定是否阻断写入——manager 层不静默吞写请求。
8. **`version_control=False` 时所有 git 路径全是 no-op**。`auto_commit` 返回 `None`、`get_history` 返回 `[]`、`pull` 返回 `False`、`push` 返回 `True`（无事可做即成功）。disabled 状态下 manager 仍要保证目录与 artifact_dirs 创建到位。
9. **`mode` 与 `messager` 是两个维度，不允许耦合误判**。`WorkspaceMode.DISTRIBUTED` 由 config 显式声明（或 blueprint 根据 `remote_url` 推断后注入）；leader/follower 关系由 `leader_id == node_id` 决定。这两个维度解耦——LOCAL + 单机多进程也合法（leader 持锁，远端经 messager 请求）。
10. **冲突策略不变更已写文件的语义**。`ConflictStrategy` 决定**写之前**怎么裁决，不决定写之后怎么修复——`LOCK` 拒写、`MERGE` 让 git rebase 处理、`LAST_WRITE_WINS` 不检查。事件 `WORKSPACE_CONFLICT` 是观察口，不是回滚手段。

## 接口契约

### 配置层

```python
class WorkspaceMode(str, Enum):
    LOCAL = "local"
    DISTRIBUTED = "distributed"

class ConflictStrategy(str, Enum):
    LOCK = "lock"
    MERGE = "merge"
    LAST_WRITE_WINS = "last_write_wins"

class TeamWorkspaceConfig(BaseModel):
    enabled: bool = False
    root_path: str | None = None                # None → team_home(team_name)/team-workspace
    artifact_dirs: list[str] = ["artifacts/code", "artifacts/docs",
                                "artifacts/reports", "trajectories"]
    version_control: bool = True
    conflict_strategy: ConflictStrategy = ConflictStrategy.LOCK
    remote_url: str | None = None               # 设了就走 DISTRIBUTED

class WorkspaceFileLock(BaseModel):
    file_path: str
    holder_id: str
    holder_name: str
    acquired_at: str            # ISO 8601 UTC
    timeout_seconds: int = 300
    def is_expired(self) -> bool: ...
```

### Manager

`TeamWorkspaceManager` 是本子系统**唯一**对外类。构造：

```python
TeamWorkspaceManager(
    config: TeamWorkspaceConfig,
    workspace_path: str,
    team_name: str,
    *,
    mode: WorkspaceMode = WorkspaceMode.LOCAL,
    messager: Any | None = None,
    leader_id: str | None = None,
    node_id: str | None = None,
    publish_event: Callable[[str, BaseEventMessage], Awaitable[None]] | None = None,
)
```

#### 生命周期 / 挂载

```python
async def initialize(self, *, remote_url: str | None = None) -> None
def mount_into_workspace(self, workspace_root: str) -> None
def mount_into_worktree(self, worktree_path: str) -> None
```

- `initialize`：建目录、建 artifact 子目录、建 `skills/`；`version_control=True` 时按 mode 决定 `git init` 或 `git clone`，已存在 `.git` 时跳过。
- `mount_into_workspace`：在成员 workspace 下建 `.team/{team_name}` 符号链接。
- `mount_into_worktree`：在 worktree 下建 `.team` 符号链接，并把 `.agent/` `.team/` 追加到 `.gitignore`。

#### 锁

```python
def get_lock(self, file_path: str) -> WorkspaceFileLock | None
async def acquire_lock(self, file_path: str, member_name: str, display_name: str,
                       *, timeout_seconds: int = 300) -> bool
async def release_lock(self, file_path: str, member_name: str) -> bool
async def list_locks(self) -> list[WorkspaceFileLock]
```

- `get_lock` 不走 IO、不走网络，只看本地 cache。过期项顺手清掉。
- `acquire_lock`：LOCAL 或 leader 节点直接走 in-memory mutex；DISTRIBUTED 远端委托 `_remote_acquire_lock`（Phase 3，当前抛 `NotImplementedError`）。re-entrant：同 holder 重新 acquire 等价于刷新。
- `release_lock`：只释放自己持的锁；释放他人锁返回 `False`，不抛错。
- `list_locks` 顺手清过期项。

#### 版本控制

```python
async def pull(self) -> bool                # DISTRIBUTED only；其它返回 False
async def push(self) -> bool                # DISTRIBUTED only；其它返回 True
async def auto_commit(self, relative_path: str, member_name: str) -> str | None
async def get_history(self, relative_path: str, limit: int = 10) -> list[dict]
```

- `auto_commit`：`git add` → 检查 staged diff → `git commit -m "[member] Update path"`；DISTRIBUTED 模式 commit 后 push，push 失败 pull 一次再重试一次，仍失败记 error 不抛。
- `get_history` DISTRIBUTED 模式查询前先 pull；返回的 dict 字段 `commit / author / date / message`。
- `version_control=False` 时上述四个方法全是 no-op，按各自的 "无事可做即成功" 语义返回。

#### 分布式 leader 端

```python
async def handle_lock_request(self, request: WorkspaceLockRequestEvent) -> WorkspaceLockResponseEvent
def handle_lock_response(self, response: WorkspaceLockResponseEvent) -> None
```

- `handle_lock_request` 只在 leader 节点上调；它复用 `acquire_lock` / `release_lock`，把当前持锁人塞进响应的 `holder` 字段（如果拒绝）。
- `handle_lock_response` 只在远端节点上调，唤醒 `_pending_lock_requests` 里 `{action}:{file_path}` 的 future。

### Rail

```python
class TeamWorkspaceRail(DeepAgentRail):
    TEAM_PREFIX = ".team/"
    WRITE_TOOLS = {"write_file", "edit_file"}
    READ_TOOLS = {"read_file", "glob", "grep", "list_files"}

    def init(self, agent) -> None
    async def before_tool_call(self, ctx: AgentCallbackContext) -> None
    async def after_tool_call(self, ctx: AgentCallbackContext) -> None
```

- `init`：把 `workspace_path` 写进当前 agent 的 `CwdState.team_workspace`。**必须在 owning agent 的 asyncio Task 里跑**——`set_team_workspace` 用 ContextVar，串错 task 就丢值。
- `before_tool_call`：路径不带 `.team/` 前缀直接放行；READ 工具按 `_pull_interval` 节流 pull；WRITE 工具节流 pull + `LOCK` 策略下查锁，被他人锁住时把拒绝原因写到 `ctx.extra["workspace_lock_rejected"]`。
- `after_tool_call`：WRITE 工具命中 `.team/` 时调 `auto_commit` + 通过 `publish_event` 发 `WORKSPACE_ARTIFACT_UPDATED`。
- 路径规范化：`_resolve_workspace_relative` 同时支持 hub（`.team/{team_name}/...`）与 legacy（`.team/...`），新代码用 hub。

### Tool

```python
class WorkspaceMetaTool(TeamTool):
    # ToolCard.id = "team.workspace_meta"
    # action ∈ {"lock", "unlock", "locks", "history"}
    async def invoke(self, inputs: dict, **kwargs) -> ToolOutput
```

- 不做文件 I/O——读写继续走标准 `read_file` / `write_file`，由 rail 透明加策略。
- 只暴露**没有文件系统等价物**的元数据动作：取锁、放锁、列锁、查历史。
- `kwargs` 里的 `member_name` / `display_name` 来自 caller context，不允许从 `inputs` 接管（caller 身份不能被 LLM 自报）。

### 事件契约

| 事件 | 时机 | Payload |
|---|---|---|
| `WORKSPACE_ARTIFACT_UPDATED` | rail `after_tool_call` 写文件后 | `WorkspaceArtifactEvent(team_name, member_name, artifact_path, commit_sha?)` |
| `WORKSPACE_CONFLICT` | merge / push 冲突时 | `WorkspaceConflictEvent(file_path, conflicting_commit?)` |
| `WORKSPACE_LOCK_REQUEST` | 远端节点要锁 | `WorkspaceLockRequestEvent(action, file_path, holder_name?, timeout_seconds?)` |
| `WORKSPACE_LOCK_RESPONSE` | leader 回锁请求 | `WorkspaceLockResponseEvent(file_path, granted, holder?)` |

发布通道：构造 manager 时注入 `publish_event` 回调；事件类型在 `agent_teams/schema/events.py` 中由 `_EVENT_TYPE_MAP` 登记，不允许在本子系统私自再起 enum。

## 数据结构

### 在哪建、什么时候建、什么时候销

| 字段 | 持有者 | 创建时机 | 释放时机 |
|---|---|---|---|
| `workspace_path` 目录 + artifact_dirs | 文件系统 | `agent_configurator.create_workspace_manager` 阶段 `os.makedirs`，`initialize()` 再补 artifact_dirs | 由 `TeamBackend.clean_team` 走 `paths` 统一路径清理 |
| `_locks: dict[str, WorkspaceFileLock]` | manager 实例 | 构造时空 dict | 进程结束 / manager GC；按 timeout 自动过期 |
| `_lock_mutex: asyncio.Lock` | manager 实例 | 构造时 | 同上 |
| `_pending_lock_requests` | manager 实例（远端节点） | `_remote_acquire/release_lock` 发请求时填 future | 收到对应 `WorkspaceLockResponseEvent` 时 set_result |
| `.team/{team_name}` 符号链接 | 成员 workspace | `setup_agent` 调 `mount_into_workspace` | 成员 workspace 清理时随之删除 |
| `.team` 符号链接 + `.gitignore` 注入 | worktree | `mount_into_worktree` | worktree 清理时随之删除 |
| 共享 `.git` 仓库 | `workspace_path` 内 | `initialize()` 在 `version_control=True` 时建 | 不主动删，复用旧仓库（已有 `.git` 跳过 init） |

### Spec 形态

`TeamWorkspaceConfig` 是 `TeamAgentSpec.workspace` 的字段类型，纯装配数据。**不**持运行时引用（messager / runner / 文件句柄）。`mode` / `leader_id` / `node_id` / `messager` 是 manager 构造参数，不在 config 上——它们是 runtime 上下文，归 `TeamRuntimeContext` 管。

### 与 paths.py 的对应

```
team_home(team_name)/                     # paths.team_home
├── team-workspace/                       # ← 默认 workspace_path
│   ├── .git/                             # version_control=True 时
│   ├── artifacts/{code,docs,reports}/    # config.artifact_dirs
│   ├── trajectories/
│   ├── skills/
│   └── team-memory/                      # paths.team_memory_dir
└── workspaces/                           # 成员 workspace 容器（不归本子系统）
    └── {member}_workspace/
        └── .team/{team_name} → ../../team-workspace
```

成员若在独立 workspace 模式（`stable_base=True`），`independent_member_workspace(member)` 通过符号链接被纳入 `team_home(team_name)/workspaces/{member}_workspace`；`.team` 挂载点不变。

## 与其它 spec 的关系

- **S_01 public-api-and-spec-flow**：`TeamWorkspaceConfig` 通过 `TeamAgentSpec.workspace` 进入装配蓝图，遵循 Spec → build → Runtime 单向流；workspace 不回写 spec。
- **S_02 team-agent-architecture**：`TeamWorkspaceManager` 落在 `agent/infra.py` 的 `TeamInfra` 上（per-process 共享）；`TeamWorkspaceRail` 由 `agent/agent_configurator.py` 装到 DeepAgent；prompts/sections 通过 `team_workspace_mount` / `team_workspace_path` 把挂载点注入 system prompt——agent 侧不重复声明挂载格式。
- **S_06 runtime-pool-dispatch**：workspace 的物理目录创建发生在 manager `activate` 路径（spec → blueprint → `create_workspace_manager`）；pool entry 复活时复用既有目录，不重新 init。
- **S_08 team-tools-contract**：`WorkspaceMetaTool` 是团队工具的一员，ToolCard id `team.workspace_meta` 遵循 `team.{name}` 前缀约定；其描述文本走 `tools/locales/descs/<lang>/workspace_meta.md`，不在代码里写长文案。
- **Worktree 子系统（`harness.tools.worktree`）**：路径不相交是不变量 1。`mount_into_worktree` 是 worktree → workspace 的单向挂载入口，不存在反向 mount；worktree 改动不通过 workspace 的 git 历史走，二者各自独立。
- **事件总线（`schema/events.py`）**：所有 workspace 事件类型在统一 `TeamEvent` 枚举与 `_EVENT_TYPE_MAP` 中登记。新增 workspace 事件必须先扩 schema，再扩 manager / rail——不允许本子系统私造事件。
