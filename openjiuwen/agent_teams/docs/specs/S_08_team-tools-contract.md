# Team Tools Contract

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | `openjiuwen/agent_teams/tools/` |
| 最近一次修订 commit | 18823271 |
| 关联 feature | — |

## 范围 / 边界

本规约只管 **`agent_teams/tools/` 这一层暴露给 LLM 的"团队工具集合"**：
工厂入口、`ToolCard` 命名、描述文本如何分发到磁盘、按角色筛选的逻辑、
`teammate_mode` / `exclude_tools` / `lang` 这几个工厂参数的语义。

**不管**的事情：

- `TeamBackend` / `TeamTaskManager` / `TeamMessageManager` 的内部实现——
  那是后端层，工具只是它们的 LLM 适配器。工具不直接读写 `TeamDatabase`。
- `TeamToolRail` 怎么把工具挂到 DeepAgent——那是 `rails/team_tool_rail.py`
  的事，本规约只规定它必须经过 `create_team_tools` 这一道门。
- `WorkspaceMetaTool` / `EnterWorktreeTool` / `ExitWorktreeTool`——
  workspace 工具属于 `team_workspace/` 子系统，worktree 工具已经下沉到
  `harness/tools/worktree`。两者由 `TeamToolRail` 在工厂返回值之上 **追加**
  挂载，不在本工厂的内置目录里，也不能通过 `exclude_tools` 屏蔽。
- 系统提示词（`prompts/`）。提示词写"角色身份与决策原则"，工具描述写
  "操作过程"，两边内容禁止重复——这是分层契约。
- 运行时硬编码字符串（dispatcher 通知 / 默认 persona）走 `agent_teams/i18n.py`，
  本规约只管工具描述文本。

## 不变量

下列断言在任意时刻必须为真。违反任何一条都属于设计退化，不是"暂时绕一下"。

1. **唯一工厂**：从 `agent_teams.tools` 拿团队工具的合法路径只有
   `create_team_tools(...)`。其他模块不得直接 `import` 具体工具类
   （`BuildTeamTool` 等）来自行装配——绕开工厂就绕开了角色筛选、`teammate_mode`
   门禁、`exclude_tools` 屏蔽和 `_wrap_invoke_with_logging` 包装。
2. **ToolCard ID 全局前缀**：每个团队工具的 `ToolCard.id` 形如
   `team.{name}`。`name` 在团队工具集合内全局唯一，不允许跨角色重名。
   下游（rails、日志、UI 标签、Runner.resource_mgr）按 `team.` 前缀解析。
3. **ID 在 inprocess spawn 模式下需要再追加 team/member 后缀**：
   `Runner.resource_mgr` 是进程全局的；多成员共进程时 `TeamToolRail.init`
   的 `qualify_ids=True` 分支会把每个 ID 改写成
   `team.{name}.{team_name}.{member_name}`。这是 ID 命名约定的合法**扩展**
   而不是**例外**——在 ResourceManager 层看到的 ID 仍然以 `team.` 开头。
4. **描述文本是行为契约，不是 feature 摘要**。每条工具描述必须显式覆盖
   "什么时候调"、"什么时候不要调"、"昂贵操作的代价信号"。把工具说成
   "send a message" 是简介；说成"广播是 team-size 线性的，少用"才是契约。
5. **长描述住 Markdown，参数串住 dict**：超过几行的 `_desc` 一律落到
   `tools/locales/descs/<lang>/<tool_name>.md`；参数描述 / 短串留在
   `locales/<lang>.py` 的 `STRINGS`。Markdown 优先级高于 dict——同一
   `_desc` 同时存在两处时 Markdown 覆盖 dict（迁移完成后必须删 dict 项）。
6. **缺失即报错，不静默回退**：`Translator` 找不到 `_desc` 抛
   `FileNotFoundError`，找不到普通键抛 `KeyError`，构造期就炸；不允许
   返回空串、占位符、英文兜底等"善意"行为。
7. **每条 ToolCard 描述都过 Translator**。工具构造器拿到的是同一个
   `t: Translator` 闭包，`ToolCard.description` 必须由 `t(name)` 提供，
   不许在构造器里写硬编码字面量。
8. **`teammate_mode` 只挡 leader 的审批工具**：`approve_plan` /
   `approve_tool` 只在 `teammate_mode == "plan_mode"` 才进入 leader 工具集；
   `build_mode` 下 leader 工具集里没有这两个名字。`teammate_mode` 不影响
   teammate / human_agent 的工具集。
9. **`exclude_tools` 是减法，不是注册口**。它从角色集合里**移除**给定名字，
   不能通过它注册新工具。新工具靠的是工厂里的静态 `all_tools` 字典。
10. **角色集合互相对称**：
    - leader = `LEADER_ONLY_TOOLS ∪ SHARED_TOOLS`
    - teammate = `MEMBER_ONLY_TOOLS ∪ SHARED_TOOLS`
    - human_agent = 显式枚举的 `HUMAN_AGENT_TOOLS`，**不沿用** `SHARED_TOOLS`。
    Human agent 不得拿到 `send_message` / `claim_task`——前者人类用户经
    `HumanAgentInbox` 表达意图，不让 LLM 控；后者属于 teammate 自治路径，
    人类化身只能等 leader 通过 `update_task(assignee=...)` 显式指派。
11. **`view_task` 是唯一对 leader / teammate / human_agent 三方都开放的
    任务读取工具**。teammate 用 `claim_task`，human_agent 用
    `member_complete_task`，leader 用 `update_task`——三个写入路径
    不互相替代，也禁止用同一个工具按调用方角色分支。
12. **`spawn_member` 的 `role_type='human_agent'` 在 HITT 关闭时必须在
    工具边界拒绝**，配合给出明确指引（`enable_hitt=False`）；
    `model_name` / `prompt` 与 `human_agent` 互斥也在工具边界报错。
    后端的能力门是工具显式校验，不是隐藏断言。
13. **每个 `TeamTool.invoke` 必须返回 `ToolOutput`，永不抛**。工具内部
    `try / except` 捕获后端异常，落 `team_logger.error`，转成
    `ToolOutput(success=False, error=...)` 返回；不允许把 `Exception`
    透出到 ability 层。
14. **`map_result` 是 LLM 看到的唯一文本**：工厂的 `_wrap_invoke_with_logging`
    会在 `invoke` 返回后调用 `tool.map_result(output)`，把结果包成
    `MappedToolOutput`，其 `__str__` 返回该文本。`ToolOutput.data` 仍保留
    给事件 / 日志等程序消费者用。新工具如果不显式覆盖 `map_result`，
    就只会得到 `json.dumps(data)` 的兜底——意味着 token 浪费，应当显式覆盖。
15. **Card / Config 分层**：`ToolCard` 只承载可序列化的 `id` / `name` /
    `description` / `input_params`；`teammate_mode` / `model_config_allocator` /
    `on_teammate_created` 这类运行时句柄属于工具实例的私有字段，禁止下沉
    到 `ToolCard`。

## 接口契约

### `create_team_tools` — 唯一工厂入口

```python
def create_team_tools(
    *,
    role: str,
    agent_team: TeamBackend,
    teammate_mode: str = "build_mode",
    on_teammate_created: Callable[[str], Awaitable[None]] | None = None,
    model_config_allocator: Callable[[str | None], "Allocation" | None] | None = None,
    exclude_tools: set[str] | None = None,
    lang: str = "cn",
) -> list[Tool]:
    """Build the role-appropriate team tool list and return them wrapped."""
```

参数语义：

| 参数 | 取值 | 行为 |
|---|---|---|
| `role` | `"leader"` / `"teammate"` / `"human_agent"` | 决定基础工具集——分别为 `LEADER_TOOLS` / `MEMBER_TOOLS` / `HUMAN_AGENT_TOOLS`。其它字符串当作 teammate 走（落入 else 分支）。新增角色必须显式补一个集合常量，不要靠 fall-through。 |
| `agent_team` | `TeamBackend` | 后端句柄，所有写操作（`build_team` / `spawn_member` / 任务 / 消息）通过它走，不绕过去直接打数据库或 messager。 |
| `teammate_mode` | `"build_mode"` / `"plan_mode"` | 仅 leader 角色相关：非 plan_mode 时把 `approve_plan` / `approve_tool` 从 allowed 集合里减掉。 |
| `on_teammate_created` | `Callable[[str], Awaitable[None]]` | leader 用 `send_message` 时若发现成员未启动，自动 startup 的回调；不传则没有 auto-start 行为。teammate / human_agent 不消费这个回调。 |
| `model_config_allocator` | `Callable[[str \| None], Allocation \| None]` | leader 的 `spawn_member` 调它选 model；不传则 spawn 出来的 teammate 无 allocation，由后端兜底。teammate 不消费。 |
| `exclude_tools` | `set[str]` 或 `None` | **减法**——从该角色 allowed 集合里再减一遍。不存在于 allowed 的名字静默忽略（因为减法对空集是恒等）。 |
| `lang` | `"cn"` / `"en"` | 选语言加载 `_desc`，缺省 `"cn"`。其它字符串走 cn 兜底（`make_translator` 内部 if/else）。 |

返回值：

- 顺序按工厂内 `all_tools` 字典声明序遍历后过滤；调用方不应该依赖具体顺序。
- 每个返回 `Tool` 的 `invoke` 已被 `_wrap_invoke_with_logging` 包过，
  调用一次会经历：debug 日志 → 原 `invoke` → `map_result` → 包成
  `MappedToolOutput`。

错误语义：

- 构造期 `Translator` 缺 `_desc` 抛 `FileNotFoundError`，缺普通键抛
  `KeyError`——发生在 `t("name")` 这一步，调用方应当让它向上传播，
  不要捕获后退化成"工具不可用"。
- `role` 不是 `"leader"` / `"human_agent"` 时静默走 teammate 分支。
  调用方传 `"leader_x"` 这种拼错名字的字符串拿到的是 teammate 工具集——
  这是工厂当前行为，不是契约保护，调用前应自己校验。
- `agent_team` 必须在调用前 `build_team` 完成；工具实例化只读 `agent_team`
  的属性引用（`task_manager` / `message_manager`），运行期再调后端方法。

### `Translator` 协议

```python
Translator = Callable[..., str]
# t(tool: str, key: str = "_desc", **kwargs: str) -> str
```

- `t(tool)` 等价 `t(tool, "_desc")`——返回工具的描述文本。
- `t(tool, "param_name")` 返回该参数 schema 的描述串。
- `t(tool, "nested.sub")` 用点号表示嵌套 schema 的参数键。
- `**kwargs` 走 `PromptTemplate` 的 `{{placeholder}}` 插值或者
  `str.format_map`；只在描述里有占位符的工具上传。

### 描述文本路径约定

```
openjiuwen/agent_teams/tools/locales/
├── __init__.py                  # make_translator + _load_desc(@cache)
├── cn.py                        # STRINGS dict (cn)
├── en.py                        # STRINGS dict (en)
└── descs/
    ├── cn/<tool_name>.md        # 优先于 STRINGS["<tool>._desc"]
    └── en/<tool_name>.md
```

- 文件名 = 工具 `name`。`build_team` → `descs/cn/build_team.md`、
  `descs/en/build_team.md`。
- Markdown 文件存在即覆盖 dict 中同 key 的 `_desc`。迁移完一条描述后
  **必须**把 dict 里的 `_desc` 项删掉，避免出现两个 source-of-truth。
- 占位符使用 `{{name}}` 双大括号（`PromptTemplate`），不是 Python
  `str.format` 的单大括号。
- 短描述（参数 / 短 `_desc`）保留在 `cn.py` / `en.py` 的 `STRINGS` 字典，
  不强制迁出去；多行长文本一律落 Markdown。

### 角色级工具集合

| 集合常量 | 成员（commit 18823271 实测） |
|---|---|
| `LEADER_ONLY_TOOLS` | `build_team`, `clean_team`, `spawn_member`, `shutdown_member`, `approve_plan`, `approve_tool`, `create_task`, `update_task`, `list_members` |
| `MEMBER_ONLY_TOOLS` | `claim_task` |
| `SHARED_TOOLS` | `view_task`, `send_message` |
| `HUMAN_AGENT_TOOLS` | `view_task`, `member_complete_task` |
| `LEADER_TOOLS` | `LEADER_ONLY_TOOLS ∪ SHARED_TOOLS` |
| `MEMBER_TOOLS` | `MEMBER_ONLY_TOOLS ∪ SHARED_TOOLS` |

`workspace_meta` 不在以上任何集合里：它由 `TeamToolRail.init` 在
`workspace_manager is not None` 时 **追加**注册（leader / teammate / human_agent
都吃同一份）；同理 `enter_worktree` / `exit_worktree` 由
`worktree_manager is not None` 触发追加，且实现住 `harness/tools/worktree`。

### `teammate_mode` 的精确门禁

```python
if role == "leader" and teammate_mode != "plan_mode":
    allowed = allowed - {"approve_plan", "approve_tool"}
```

- 只对 `role == "leader"` 生效。
- `teammate_mode` 取 `"plan_mode"` 时 leader 拿到全套审批工具；其它取值
  （含默认 `"build_mode"`）一律剥离这两个工具。
- teammate / human_agent 不受此门禁影响——他们本来就不在 leader 集合里。

## 数据结构

### `TeamTool`（基类）

```python
class TeamTool(Tool, ABC):
    def map_result(self, output: ToolOutput) -> str: ...
    async def stream(self, inputs, **kwargs): raise NotImplementedError
```

- 抽象基类。所有团队工具继承它，`invoke` 由各子类自己实现。
- 不支持 streaming——`stream` 显式抛，避免被通用调用路径误调用。

### `MappedToolOutput`

```python
class MappedToolOutput(ToolOutput):
    _mapped_content: str = PrivateAttr(default="")

    @classmethod
    def from_output(cls, output: ToolOutput, mapped_content: str) -> "MappedToolOutput": ...

    def __str__(self) -> str: return self._mapped_content
```

- `_wrap_invoke_with_logging` 构造它包住 `invoke` 的返回值。
- ability 层最终把工具结果转成 `ToolMessage.content` 是通过 `str(result)`，
  这里覆盖 `__str__` 是该约定的入口。`data` / `success` / `error`
  从原 `ToolOutput` 拷过来，程序消费路径无变化。

### `ToolCard`（每个团队工具构造时填）

| 字段 | 取值 | 说明 |
|---|---|---|
| `id` | `f"team.{name}"` | 全局命名空间。inprocess 下进一步追加 `.{team_name}.{member_name}`。 |
| `name` | 工具短名（`build_team` / `view_task` …） | 跨角色全局唯一；与 Markdown 描述文件名一一对应。 |
| `description` | `t(name)` 返回值 | 不允许硬编码字面量。Markdown 文件优先。 |
| `input_params` | JSON Schema | 所有 property `description` 也走 `t(name, "<param>")`。 |

### 工具内部 → 后端的依赖

工具不直接持 `TeamDatabase`：

```
BuildTeamTool          → TeamBackend
CleanTeamTool          → TeamBackend
SpawnMemberTool        → TeamBackend (+ model_config_allocator)
ShutdownMemberTool     → TeamBackend
ApprovePlanTool        → TeamBackend
ApproveToolCallTool    → TeamBackend
ListMembersTool        → TeamBackend
TaskCreateTool         → TeamBackend.task_manager
UpdateTaskTool         → TeamBackend (+ task_manager 通过 backend 取)
ViewTaskToolV2         → TeamTaskManager
ClaimTaskTool          → TeamTaskManager
MemberCompleteTaskTool → TeamTaskManager
SendMessageTool        → TeamMessageManager (+ TeamBackend roster check + on_teammate_created)
```

事件发布与状态迁移由 manager 集中负责；工具只是把入参打包后转发给
manager / backend。新增工具如果发现"我得绕过 manager 直接写表"，
说明 manager 缺方法——补 manager，不要在工具里偷偷打数据库。

### 静态注册表是新工具的唯一注入路径

```python
all_tools = {
    "build_team": BuildTeamTool(agent_team, t),
    "clean_team": CleanTeamTool(agent_team, t),
    ...
}
```

新增工具的步骤是固定四件事：

1. 在 `team_tools.py` 写 `XxxTool(TeamTool)` 子类；`ToolCard.id="team.<name>"`。
2. 在 `create_team_tools` 内的 `all_tools` dict 加一条。
3. 把名字加到 `LEADER_ONLY_TOOLS` / `MEMBER_ONLY_TOOLS` / `SHARED_TOOLS` /
   `HUMAN_AGENT_TOOLS` 里需要它的那个集合（互斥，不重复加）。
4. 写 `_desc`：长描述放 `locales/descs/<lang>/<name>.md`，参数串放
   `locales/<lang>.py`。两边语言都补齐——不允许只补 cn。

没有"动态注册表"。`exclude_tools` 是减法，不是注入口；想新增能力请走
上面四步，不要走 `kwargs`、不要 monkey-patch、不要在 `TeamToolRail`
追加除 workspace / worktree 之外的工具（那两个有明确的子系统归属）。

## 与其它 spec 的关系

- **`prompts/` 子系统**：分层契约——工具描述写"操作过程 / 调用顺序 /
  反模式"，系统提示词写"角色身份 / 决策原则 / 状态迁移"。两边 i18n 走
  各自的 `locales/`，不互通。新增长文本前先判断归属，再选目录。
- **`rails/team_tool_rail.py`**：`TeamToolRail` 是工厂的唯一调用方，
  也是 workspace / worktree 工具的追加挂载点。`TeamToolRail.init`
  与本工厂的契约：必须用关键字参数透传 `role` / `teammate_mode` /
  `lang`，不允许 rail 自行重写工具集合。
- **`agent_teams/i18n.py`**：运行时硬编码字符串（dispatcher 通知、
  默认 persona 等）走 `t(key)` 这一组；本规约的描述文本是另一条路径。
  两者读不同的源，不要把工具描述塞进 `i18n.py` 的 `STRINGS`。
- **`schema/task.py` / `schema/team.py`**：工具返回的结构化 `data` 必须
  来自 schema 层的 Pydantic 模型（`TaskSummary` / `TaskDetail` /
  `TaskListResult` / `MemberOpResult` / `TaskCreateResult` /
  `TaskOpResult`），不允许在工具里现场拼裸 dict。
- **`runtime/`**：`Runner.resource_mgr` 是工具注册的下游消费者；
  `qualify_team_tool_ids` 在 inprocess 下扩展 ID 命名是为了不冲突，
  不要在 runtime 层另立解析规则——所有 `team.` 前缀的认知都在这条
  spec 里定义。
