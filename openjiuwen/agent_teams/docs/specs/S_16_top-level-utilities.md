# Top-level Utilities

`agent_teams/` 顶层的几块基础设施：路径、上下文、运行时国际化串、时间渲染、保留名常量、
跨机器 worktree 后端、DeepAgent 适配器。这些文件不属于任何子目录，但子目录全在用它们；
它们是 team 子系统的"地基铸件"。本规约把每块铸件的边界、不变量和契约钉死，避免被各子目录
当成可以随手再造一份的便利工具。

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | `openjiuwen/agent_teams/paths.py` · `context.py` · `i18n.py` · `timefmt.py` · `constants.py` · `worktree_remote.py` · `harness.py` |
| 最近一次修订日期 | 2026-05-27 |
| 关联 feature | F_24_agent-time-awareness.md |

## 范围 / 边界

**管：**

- 文件系统布局唯一真相源（`paths.py`）。
- 跨成员、跨 spawn 模式的运行时上下文变量（`context.py` 的 `session_id`）。
- 进程级运行时硬编码字符串的中英切换（`i18n.py`）。
- 毫秒 epoch → "绝对本地时间 + 相对差" 的人类可读渲染（`timefmt.py`）。
- 保留成员名集合及其唯一定义点（`constants.py`）。
- 跨机器 worktree 的 team 端后端 + 远程节点处理器（`worktree_remote.py`）。
- TeamAgent 与 DeepAgent 之间的唯一适配器（`harness.py` 的 `TeamHarness`）。

**不管：**

- prompt 正文与工具描述长文：归 `prompts/` 与 `tools/locales/descs/`，按 `lang` 入参传递，
  不进 `i18n.py`。
- 通用 worktree 实现（`WorktreeManager` / `WorktreeBackend` 协议 / 本地 backend / git
  helpers）：归 `openjiuwen.harness.tools.worktree`，本规约只描述 team 一侧的远程后端如何
  注入它。
- DeepAgent 自身的运行时与 rail 实现：本规约只描述 `TeamHarness` 暴露给 team 业务代码的
  契约，DeepAgent 内部行为是它自己的事。
- session checkpoint 的 team-namespace 读写：归 `runtime/metadata.py`。
- 静态团队蓝图、运行时上下文模型、状态机枚举：归 `schema/`。

## 不变量

> 这些是任意时刻必须为真的事实。违反任意一条就是设计债，不接受"暂时绕一下"。

### paths.py

1. **任何 team-owned 文件路径都必须经 `paths.py` 构造**，禁止散落 `Path("…")` /
   `os.path.join(...)` 硬编码 team 目录。下游需要新子目录就在 `paths.py` 加新函数。
2. **路径根可被运行时重写一次**：`configure_openjiuwen_home(path)` 是合法重写入口；运行时
   一旦写入，所有派生路径（`get_agent_teams_home` / `team_home` / `team_memory_dir` /
   `independent_member_workspace` / `worktree_remote` 的 remote_repos 缓存目录）都必须看
   到新值。`reset_openjiuwen_home()` 是唯一回退路径，不允许直接清 `_configured_openjiuwen_home`。
3. **独立 DeepAgent 工作区落在 `OPENJIUWEN_HOME` 下，不进入 team 子目录**。`independent_member_workspace`
   产物在加入 / 离开 team 时必须存活，因此 path = `~/.openjiuwen/{member}_workspace`，
   而不是 `~/.openjiuwen/.agent_teams/{team}/...`。
4. **目录创建是调用方的责任**，`paths.py` 只算路径，不 `mkdir`。`team_home(...)` 不存在
   不是错误，调用方需要自己保证写入前父目录存在。
5. **`OPENJIUWEN_HOME` / `AGENT_TEAMS_HOME` 模块属性是兼容遗物**，新代码一律调用函数版本
   （`get_openjiuwen_home()` / `get_agent_teams_home()`）。`__getattr__` 只为旧 import
   不挂掉。

### context.py

6. **`session_id` 是唯一的跨成员 contextvar**。其它跨成员上下文（`member_id` 等）走
   `core.common.logging` 自带的 contextvars，不在本模块复制一份。
7. **未设置时读到 `""`，不是 `None`**。`get_session_id()` 对未初始化的 caller 返回空串，
   保证下游字符串拼接不需要再判 None。`Optional[str]` 类型签名是历史遗留，当前实现的有效
   返回域是 `str`。
8. **跨 spawn 模式共享同一份变量**：`spawn_mode="inprocess"` 在同 event loop 起协程，依赖
   contextvars 自然继承；`spawn_mode="process"` 起子进程，由 spawn 路径在子进程入口显式
   `set_session_id()`。两种模式都读这一份变量，不允许各自再起一个。
9. **`set_session_id` 必须配对 `reset_session_id(token)`**。Token 是 `Token[str]`，不允许
   丢弃；嵌套调用按栈式回退，调用方负责。

### i18n.py

10. **进 `i18n.py` 的字符串必须满足两个条件**：
    - 在运行时代码路径中硬编码（dispatcher 通知、backend 默认 persona、HITT 默认描述等）；
    - 短串，不是模板长正文。
    长正文（system_prompt / 工具描述）走 `prompts/<lang>/*.md` 与
    `tools/locales/descs/<lang>/*.md`，由各自模块按 `lang` 入参加载。
11. **键不存在直接抛 `KeyError`**，不做静默回退。语言注册新键时必须中英对齐——这条由
    `t(key)` 的 fail-fast 行为保障，缺译会在运行时就被打出来。
12. **支持的语言集合 = `STRINGS` 的顶层键**。`set_language(lang)` 用未注册语言会抛
    `ValueError`，列出当前合法集合。新增语言 = 新增 `STRINGS["<lang>"]` 字典 + 给现有键
    全译，禁止半搭。
13. **进程级单例**：`_current_language` 是模块级变量，不走 contextvars。这是有意的——
    运行时硬编码串切语言是 team 启动时的一次性配置，不需要按 session 切。

### constants.py

14. **保留名集合 = `RESERVED_MEMBER_NAMES`，唯一定义点**。`TeamAgentSpec.build()` 在校验
    时必须用这个 frozenset，不允许各处再写一份字符串字面量比对。
15. **`human_agent` 的特例**：保留名只在 `enable_hitt=True` 由运行时注入；用户在
    `agents={...}` 里手动声明 `human_agent` 必须被拒绝。校验逻辑不在本模块，本模块只
    定义"哪些名字是保留的"。
16. **新增保留名 = 在本模块加常量并加进 frozenset，且更新 `RESERVED_MEMBER_NAMES`**。漏
    加 frozenset 会让校验失效——这是 frozenset 集中保留名的首要价值。

### worktree_remote.py

17. **远程 repo 缓存路径走 `paths.get_agent_teams_home()`**：`{home}/remote_repos/<sha256[:12]>`。
    这一路径是远程 worktree 的 shallow-clone 缓存，所以 `worktree_remote.py` 强依赖
    `paths.py`——它本身就是 team 端文件布局的一部分，不可能放到 `harness/tools/worktree`
    下面（generic backend 不知道 team 的目录约定）。
18. **不进 backend registry**。通用 worktree manager 在 `harness.tools.worktree` 下有
    一套 backend registry（`local` / 其他），但 `RemoteWorktreeBackend` 构造参数不止
    `WorktreeConfig`（还要 `messager`、`node_id`），无法走 registry 的字符串型构造协议。
    调用方一律 `WorktreeManager(backend=RemoteWorktreeBackend(config, messager, node_id))`
    显式注入。
19. **leader-side 与 node-side 严格分离**：`RemoteWorktreeBackend` 是 leader 端，发请求
    并 await 响应；`WorktreeRemoteHandler` 是 node 端，被 messager 注册为 direct-message
    handler。两端通过 `WorktreeRemoteRequest` / `WorktreeRemoteResponse` 通信，不允许
    handler 反过来发请求、也不允许 backend 写盘。
20. **Request/Response 是 pydantic 模型**：跨进程序列化只走 `model_dump` / `model_validate`，
    不允许塞自定义对象、文件句柄、callable。

### harness.py

21. **TeamAgent 不直接持 DeepAgent 引用**。DeepAgent 的所有调用面（`add_rail` / `steer` /
    `follow_up` / `abort` / `Runner.run_agent_streaming`）必须经 `TeamHarness`。`TeamHarness.inner_agent`
    是给测试与少数迁移 helper 的逃生口，生产业务代码禁用。
22. **Rail 挂载顺序在 `TeamHarness.build` 内固化**：`team_tool_rail` 必须先挂且即时初始化
    （`init(deep_agent)`），保证 `TeamPolicyRail` 取系统提示词 snapshot 时 team tools 已注册。
    后续 rail 顺序：`team_policy_rail` → `first_iter_gate?` → `team_workspace_rail?` →
    `tool_approval_rail?`。
23. **`run_agent_customizer` 隔离用户回调失败**：customizer 抛异常只 log warning，不让整个
    team 装配挂掉。这是有意的容错——customizer 是 user-facing 扩展点，不能让用户的 bug 杀
    掉 team 启动；但失败必须可见。
24. **`run_streaming` 的入参形态是序列化友好的**：`inputs: dict[str, Any]` + `session_id: str`。
    这条不变量是为了让远端 / 分布式调度 backend（替换 DeepAgent 的设想）能走同一份签名。

## 接口契约

### paths.py

```python
def configure_openjiuwen_home(path: str | Path) -> None
def reset_openjiuwen_home() -> None
def get_openjiuwen_home() -> Path
def get_agent_teams_home() -> Path
def team_home(team_name: str) -> Path
def independent_member_workspace(member_name: str) -> Path
def team_memory_dir(team_name: str) -> Path
```

派生关系（重写后必须自洽）：

- `get_agent_teams_home() == get_openjiuwen_home() / ".agent_teams"`
- `team_home(t) == get_agent_teams_home() / t`
- `team_memory_dir(t) == team_home(t) / "team-workspace" / "team-memory"`
- `independent_member_workspace(m) == get_openjiuwen_home() / f"{m}_workspace"`

### context.py

```python
def set_session_id(session_id: str) -> Token[str]
def get_session_id() -> str           # 实际返回 str；Optional[str] 是签名遗留
def reset_session_id(token: Token[str]) -> None
```

### i18n.py

```python
Language = Literal["cn", "en"]

def set_language(lang: Language) -> None       # 非法 lang → ValueError
def get_language() -> Language
def t(key: str, **kwargs: object) -> str       # 缺 key → KeyError
```

错误语义：

- `set_language("xx")` → `ValueError: Unsupported language 'xx'. Supported: cn, en`
- `t("missing.key")` → `KeyError: Missing i18n key 'missing.key' for language 'cn'`
- `t(key, **kwargs)` 用 `str.format_map`，模板里多写的占位符 → 抛 `KeyError`（来自
  `format_map`），少传的 kwarg 不报错（因为 `format_map` 只拿用到的）。

### timefmt.py

```python
def format_time_context(timestamp_ms: int | None, now_ms: int) -> str
```

- 输出形如 `2026-05-27 14:30:05 +08:00 (3 分钟前)`；`timestamp_ms is None` → `t("time.unknown")`。
- 相对桶：`< 10s` / 未来 → `time.just_now`；`< 60s` → `time.seconds_ago`；`< 60min` →
  `time.minutes_ago`；`< 24h` → `time.hours_ago`；否则 `time.days_ago`。
- 复用点：`external/format.py`（`render_message` / `render_task_line`）、`agent/coordination/
  handlers/`（message / task_board / stale_task）、`tools/team_tools.py`（`view_task`
  map_result）、`mcp/server.py`、`skill/cli.py`。这些是 `now_ms` 的注入方，各自取
  `get_current_time()`。

### constants.py

```python
HUMAN_AGENT_MEMBER_NAME: str = "human_agent"
USER_PSEUDO_MEMBER_NAME: str = "user"
DEFAULT_LEADER_MEMBER_NAME: str = "team_leader"
RESERVED_MEMBER_NAMES: frozenset[str] = frozenset({...})
```

校验语义（在 `TeamAgentSpec.build()`）：用户成员名 ∈ `RESERVED_MEMBER_NAMES` →
`ValueError`；唯一例外是 runtime 内部为 `enable_hitt=True` 注入的 `human_agent`。

### worktree_remote.py

```python
class WorktreeRemoteRequest(BaseModel):
    action: str                           # "create" | "remove" | "exists"
    slug: str | None = None
    repo_url: str | None = None
    base_branch: str | None = None
    worktree_path: str | None = None
    config: dict[str, Any] | None = None

class WorktreeRemoteResponse(BaseModel):
    success: bool = True
    worktree_path: str | None = None
    worktree_branch: str | None = None
    head_commit: str | None = None
    existed: bool = False
    exists: bool = False
    error: str | None = None

class RemoteWorktreeBackend:
    def __init__(self, config: WorktreeConfig, messager: Any, node_id: str)
    async def create(self, slug: str, repo_root: str, target_path: str) -> WorktreeCreateResult
    async def remove(self, worktree_path: str, repo_root: str) -> bool
    async def exists(self, worktree_path: str) -> bool

class WorktreeRemoteHandler:
    def __init__(self, manager: Any)              # WorktreeManager
    async def handle(self, request: WorktreeRemoteRequest) -> WorktreeRemoteResponse
```

错误语义：

- `RemoteWorktreeBackend.create` 收到 `success=False` → `RuntimeError("Remote worktree
  creation failed: <error>")`。
- 远程 repo 无法解析 origin URL → `_get_repo_url` 抛 `RuntimeError("Cannot determine
  remote URL")`。
- `WorktreeRemoteHandler.handle` 收到未知 action → `WorktreeRemoteResponse(success=False,
  error="Unknown action: <action>")`，**不抛**。
- handler 内部任何异常都被 `try/except Exception` 包住，返回 `success=False, error=str(exc)`，
  保证远端不会因为本端业务异常而 hang。

注入路径：

```python
backend = RemoteWorktreeBackend(config=worktree_config, messager=messager, node_id=node_id)
manager = WorktreeManager(config=worktree_config, backend=backend)
```

### harness.py

```python
AgentCustomizer = Callable[[DeepAgent, Optional[str], str], None]

@dataclass
class _MountedRails:
    team_tool: TeamToolRail
    team_policy: TeamPolicyRail
    first_iter_gate: Optional[FirstIterationGate] = None
    team_workspace: Optional[TeamWorkspaceRail] = None
    tool_approval: Optional[TeamToolApprovalRail] = None

class TeamHarness:
    @classmethod
    def build(
        cls,
        *,
        agent_spec: DeepAgentSpec,
        role: TeamRole,
        member_name: Optional[str],
        team_tool_rail: TeamToolRail,
        team_policy_rail: TeamPolicyRail,
        first_iter_gate: Optional[FirstIterationGate] = None,
        team_workspace_rail: Optional[TeamWorkspaceRail] = None,
        tool_approval_rail: Optional[TeamToolApprovalRail] = None,
    ) -> "TeamHarness"

    def run_agent_customizer(self, customizer: AgentCustomizer) -> None

    # snapshots
    @property
    def deep_config(self) -> DeepAgentConfig
    @property
    def workspace(self) -> Optional[Any]
    @property
    def sys_operation(self) -> Optional[Any]
    @property
    def model(self) -> Any
    def has_pending_interrupt(self) -> bool
    def is_pending_interrupt_resume_valid(self, user_input: Any) -> bool
    def init_cwd_for_round(self) -> None

    # runtime
    async def steer(self, content: str) -> None
    async def follow_up(self, content: str) -> None
    async def abort(self) -> None
    def run_streaming(
        self,
        inputs: dict[str, Any],
        *,
        session_id: Optional[str],
    ) -> AsyncIterator[Any]

    # rail / tool registration
    async def register_rail(self, rail: AgentRail) -> None
    async def unregister_rail(self, rail: AgentRail) -> None
    def register_member_tools(self, memory_manager: Any) -> None
    async def inject_member_memory(self, memory_manager: Any, query: str) -> None

    # escape hatch (test-only)
    @property
    def inner_agent(self) -> DeepAgent
    @property
    def rails(self) -> _MountedRails
```

`abort()` 语义：cooperative，标志位 + `on_abort` hook，不抛 `CancelledError`；硬截止由
调用方在外层用 `asyncio.wait_for + Task.cancel` 兜底。

### timefmt.py

> 编号续在 harness 之后（25+），不打乱前段；时序上 timefmt 属于 i18n 之后的渲染铸件。

25. **存储不渲染、渲染不存储**。DB 一律存毫秒 UTC epoch（`get_current_time()`）；
    `format_time_context(timestamp_ms, now_ms)` 只在喂给 agent / 观测的文本里把 epoch 翻译成
    `<绝对本地时间> (<相对差>)`。不允许为"可读性"把存储类型改成 datetime / 字符串——那是把
    展示层关注点污染进存储层。
26. **`now_ms` 永远是入参**。`format_time_context` 不自取当前时间，保持纯函数、可测；
    external / MCP / CLI 路径各自注入自己的 now。
27. **相对分桶与语言解耦**。`_relative_key_and_value(delta_ms)` 纯数值，只输出
    `(i18n_key, value)`，文案全在 `i18n.py` 的 `time.*` 键（占位符统一 `{value}`）。新增语言
    不碰 `timefmt.py`。
28. **边界归一**：`now < timestamp`（时钟漂移）与 `< 10s` 都归 `time.just_now`，绝不渲染负数
    或 "0 秒前"；`timestamp_ms is None` → `time.unknown`。
29. **绝对时间用运行时本地时区 + 数字偏移**（`±HH:MM`）；相对差是 epoch 减法，与时区无关。

## 数据结构

### 文件系统布局（`paths.py` 决定）

```
{OPENJIUWEN_HOME}/                                  # 默认 ~/.openjiuwen
├── {member_name}_workspace/                        # independent DeepAgent
└── .agent_teams/                                   # AGENT_TEAMS_HOME
    ├── remote_repos/                               # worktree_remote shallow clones
    │   └── <sha256(repo_url)[:12]>/
    └── {team_name}/
        ├── team-workspace/
        │   └── team-memory/                        # team_memory_dir
        ├── workspaces/
        │   └── {member}_workspace/                 # stable_base 成员工作区
        └── team.db                                 # 默认 sqlite
```

### `_MountedRails`（`harness.py`）

挂载顺序与字段顺序一致：team_tool → team_policy → first_iter_gate? → team_workspace? →
tool_approval?。Optional 字段缺省 = 该 rail 当前装配未启用，不是错误。

### Worktree request/response（`worktree_remote.py`）

`action` 字符串型分发，不用枚举的原因：跨 messager 序列化时字符串最稳；handler 端用
`if/elif` 分发，未知 action 走显式错误响应。

## 与其它 spec 的关系

- **`paths.py` 的下游消费者**：
  - `team_workspace/`：用 `team_home(...)/team-workspace` 作为共享工作区根。
  - `agent/team_agent.py` / `schema/blueprint.py`：用 `team_home` 作为团队物化根。
  - `runtime/`（`TeamRuntimeManager.delete_team` / `clean_team`）：清理走 `team_home`。
  - `worktree_remote.py`：`get_agent_teams_home() / "remote_repos"` 作为远端 clone 缓存。
  - 默认 sqlite 路径 `team_home(t) / "team.db"`：见 `messager/` 与 storage spec。
- **`context.py` 的下游消费者**：
  - `spawn/`（process / inprocess）：spawn 入口写入子进程 / 子任务的 `session_id`。
  - `interaction/`（UserInbox / HumanAgentInbox）：路由消息时读取当前 session。
  - `runtime/InteractGate`：把 session 当作并发分桶 key。
- **`i18n.py` 的下游消费者**：
  - `agent/dispatcher.py`：成员事件、催促、idle 通知文案。
  - `tools/team.py`：shutdown / cancel 默认请求文案。
  - `schema/blueprint.py`：默认 persona。
  - HITT 默认 display name / persona / spawn 通知。
  - 长正文（`prompts/<lang>/*.md`、`tools/locales/descs/<lang>/*.md`）**不走** 本模块，由
    各自模块按 `lang` 参数加载——这两条路径与 i18n.py 严格分隔，互不污染。
- **`constants.py` 的下游消费者**：
  - `schema/blueprint.py` 的 `TeamAgentSpec.build()`：保留名校验。
  - `interaction/`：用 `USER_PSEUDO_MEMBER_NAME` 标识外部 caller。
  - HITT 子系统：用 `HUMAN_AGENT_MEMBER_NAME` 作为人类成员注入名。
  - `agent/team_agent.py`：用 `DEFAULT_LEADER_MEMBER_NAME` 作为默认 leader。
- **`worktree_remote.py` 的关系**：依赖 `paths.py`、依赖 `messager/`（P2P 通信）、依赖
  `harness.tools.worktree`（通用 manager / config / git helper / 创建结果模型）。team 一侧
  不再持有 worktree manager 实现，只贡献"远程后端 + 远程 handler + 团队目录布局"这三块。
- **`harness.py` 的关系**：是 team 子系统对 DeepAgent 的唯一入口。`agent/` 下的所有 manager
  （spawn / coordination / stream / recovery / session）通过 `TeamHarness` 操作 DeepAgent；
  `rails/` 下的 team rail 由 `TeamHarness.build` 在挂载时按固定顺序串起来。`runtime/`
  调度 spec 直接拿 `TeamHarness.run_streaming` 当远端可移植入口。
