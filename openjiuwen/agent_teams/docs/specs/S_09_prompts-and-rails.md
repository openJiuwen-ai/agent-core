# Prompts 与 Team Rails

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | `openjiuwen/agent_teams/prompts/`, `openjiuwen/agent_teams/rails/` |
| 最近一次修订日期 | 2026-05-16 |
| 关联 feature | `F_18_hide-human-agent-role-from-teammate.md` |

## 范围 / 边界

**管：**

- `agent_teams/prompts/` 下系统提示词的全部产出路径：模板加载、占位符装配、`PromptSection` 构造、动态 section 的 mtime 缓存。
- `agent_teams/rails/` 下三个团队级 Rail（`TeamPolicyRail` / `FirstIterationGate` / `TeamToolApprovalRail`）的契约、注入时机、与 DeepAgent rail registry 的交互。
- prompts 子模块的 `cn/` `en/` 双语模板布局，以及与 `agent_teams/i18n.py`（运行时硬编码字符串）的边界。

**不管：**

- `TeamToolRail` 的工具注册行为（属于 `tools/` 子系统的 spec）。
- 模板正文写作风格（Markdown 内容由模板自身维护，spec 只规定装配契约）。
- DeepAgent 内置的非团队 rail（safety / sys_operation / context-engineering 等）的实现，本 spec 只描述它们与团队 rail 的协作位面。
- `SystemPromptBuilder` 与 `PromptSection` 自身的实现（属于 core 的 spec）。

## 不变量

### 装配路径

1. **两条装配路径并存且都消费同一批 `.md`**：`policy.build_system_prompt`（老路径，把所有内容塞进 `system_prompt.md` 一次成壳）与 `sections.build_team_*_section`（主力路径，每片内容独立产出 `PromptSection`）读的是同一个 `prompts/<lang>/*.md` 目录。模板正文修改自动同时生效。
2. **结构性变更必须明确归属一条路径**：占位符增减只影响 `policy.py`（`system_prompt.md` 是它的私产）；section 名 / priority 调整只影响 `sections.py` + `TeamPolicyRail`。两条路径的结构演化彼此独立。
3. **生产路径是 `sections.py` + `TeamPolicyRail`**：`TeamHarness.build` 走的是 rail 注入。`policy.build_system_prompt` 仅供未走 rail 的旧调用者保留兼容。

### Section / 文件落位

4. **`TeamPolicyRail` 是团队 section 名的唯一发行方**：所有团队相关 section 名集中在 `TeamSectionName` 类常量里，priority 取值集中在该 rail 的注释表里。其他模块不得 hardcode `"team_*"` section 名。
5. **section name 全局唯一**：`SystemPromptBuilder._sections` 是 `dict[str, PromptSection]`，同名 add 直接覆盖。团队 section 与 harness 内置 section（safety / capabilities / runtime / ...）必须不冲突。
6. **section priority 单调约定**：团队静态 section 占 11–16，团队动态 section 占 65–66；harness 内置 section 排在 0–10、20–60、70–99。priority 升序拼接，相同 priority 顺序由 `dict` 插入序决定。
7. **role-specific section 在不应出现的角色下返回 `None`**：`build_team_workflow_section` / `build_team_lifecycle_section` 在 `role != LEADER` 时返回 `None`；`build_team_hitt_section` 在没有 human_agent 注册时返回 `None`。**禁止用空字符串占位**——返回 `None` 等价于不挂 section。
   - **TEAMMATE 默认走 anonymous 变体**：`build_team_hitt_section` 对 `role == TEAMMATE` 默认渲染一段**无名单、无 "real humans" 标签**的 role-neutral section（只承载"跨成员一律 `send_message`、容忍延迟、不要推断 peer 身份"等通用协作守则），peer role 不会泄漏到其它成员的 system prompt。开关 `TeamAgentSpec.expose_human_agents_to_teammates=True` 切换回旧版 roster-exposing section（列出所有 human_agent `member_name` + "real humans" 标签）。LEADER / HUMAN_AGENT 分支不受开关影响，始终拿完整 roster section。
8. **`.md` 模板里只能用 `{{double_brace}}` 占位符**：单花括号会被 `PromptTemplate.format` 当字面量。当前只有 `system_prompt.md` 使用占位符；`cn/` `en/` 下的模板均为纯文本。

### 双语 / i18n

9. **`cn/` `en/` 双语模板必须成对存在**：每个文件名两边都要有；`load_template` 按 `(name, language)` 分别 `@cache`。
10. **`prompts/` 与 `agent_teams/i18n.py` 严格分离**：长文本（角色策略 / 工作流 / 生命周期 / HITT）一律走 `prompts/`，运行时硬编码字符串（dispatcher 通知、default persona 等）走 `i18n.py`。新增字符串前必须先决定归属，不得混用。
11. **`load_template` 默认语言 `"cn"`**：缺省 `language` 参数等价于 `"cn"`，与 `core.single_agent.prompts.builder.DEFAULT_LANGUAGE` 一致。

### 缓存

12. **`@cache` 永不失效**：`loader._load(name, language)` 用 `functools.cache` 终身缓存解析后的 `PromptTemplate`。运行进程不会感知文件改动；调试热更需重启进程或清 `_load.cache_clear()`。
13. **`MtimeSectionCache` 仅用于动态 section**：当前仅 `team_info` / `team_members` 两片走该缓存。新增 dynamic section 必须提供单调递增的 `probe`（数据库 `updated_at` 列或聚合 `MAX(updated_at)`）；缺少 probe 的内容不应走 dynamic 路径。
14. **首次调用必 miss**：`MtimeSectionCache._initialized = False` 保证第一次 `refresh()` 一定执行 `fetch_and_build`，无视 probe 是否变化。

### Rail 注入契约

15. **Rail 通过 DeepAgent 的 rail registry 注入，不直接修改 `SystemPromptBuilder`**：`TeamPolicyRail` 在 `init(agent)` 里捕获 `agent.system_prompt_builder` 引用，于 `before_model_call` 写入 section；在 `uninit(agent)` 里按名移除。**禁止**绕过 rail 把 section 直接 `add_section` 到 builder 上。
16. **Mount order load-bearing**：`TeamHarness.build` 必须先挂 `TeamToolRail` 并 eager `init`，再挂 `TeamPolicyRail`。原因：policy 输出引用 ability 快照，能力必须先就位。Rail 顺序的修改必须同步检视 mount path。
17. **`uninit` 必须把自己写入的 section 全部清掉**：`TeamPolicyRail.uninit` 删除 `_static_sections` 里的每个 section + 两个 dynamic section 名；rail 卸载后 builder 不得残留团队 section。
18. **dynamic section 在 `team_backend is None` 时整体跳过**：单测可只关心 static 内容；`_info_cache` / `_members_cache` 在缺 backend 时不构造，rail 行为退化成纯静态。

### `FirstIterationGate`

19. **打开仅一次性，可 reset**：`asyncio.Event.set()` 等价开锁；新一轮要先 `reset()` 再 `wait()`。Gate 没有自动复位机制，由 `start_agent` 路径显式调用。
20. **HUMAN_AGENT 不挂 gate**：`agent_configurator` 仅对非 `HUMAN_AGENT` 角色构造 `FirstIterationGate`。human-agent 没有自主 task loop，挂上等不到 trigger。

### `TeamToolApprovalRail`

21. **审批是中断驱动 + 消息驱动的复合协议**：teammate 端挂 rail，触发时 (a) 通过 `TeamMessageManager.send_message` 把审批请求送给 leader，(b) 调 `self.interrupt(InterruptRequest(...))` 阻塞当前工具调用。leader 通过 `approve_tool` 工具回填 `ConfirmPayload`，rail 在 resume 时根据 payload 决定 approve / reject。
22. **`auto_confirm_config` 是 user input 通道，不持久化**：每轮构造一份；`_get_auto_confirm_key` 从 `tool_call` 派生 key。同一 key 的后续审批请求若命中 config 直接 `approve()`，无需消息。
23. **未配置 `approval_required_tools` 不挂 rail**：`agent_configurator` 仅在 teammate + `agent_spec.approval_required_tools` 非空时构造该 rail。leader 与 human_agent 不挂。
24. **消息发送失败 = 直接 reject**：`send_message` 返回 falsy 时 rail `reject(tool_result="Failed to send approval request to leader")`，**不重试**——避免对 messager 的重试压力反向放大故障。

## 接口契约

### `prompts/loader.py`

```python
def load_template(name: str, language: str = "cn") -> PromptTemplate:
    """Load <prompts_dir>/<language>/<name>.md, terminal-cached by (name, language)."""
    ...

def load_shared_template(name: str) -> PromptTemplate:
    """Load <prompts_dir>/<name>.md, terminal-cached by name."""
    ...
```

- `name` 不带扩展名；`language` 取 `"cn"` / `"en"`，未来扩展只需新增子目录。
- 返回 `core.foundation.prompt.PromptTemplate`，`.content` 为原始 markdown，`.format(...)` 渲染 `{{placeholder}}`。
- 文件不存在直接抛 `FileNotFoundError`（`Path.read_text` 默认行为），不做兜底。

### `prompts/policy.py`

```python
def role_policy(role: TeamRole, language: str = "cn") -> str:
    """Return the leader_policy or teammate_policy markdown text."""
    ...

def build_system_prompt(
    *,
    role: TeamRole,
    persona: str,
    base_prompt: str | None = None,
    team_info: dict[str, Any] | None = None,
    team_members: list[dict[str, str]] | None = None,
    member_name: str | None = None,
    lifecycle: str = "temporary",
    language: str = "cn",
    team_mode: str = "default",
) -> str:
    """Compose the full system prompt by stuffing system_prompt.md placeholders."""
    ...
```

- `team_mode` 取值 `{"default", "predefined", "hybrid"}`，由 `_WORKFLOW_TEMPLATES` 映射到 `leader_workflow*.md`；非法值走 `"default"`。
- `lifecycle` 取值 `{"persistent", "temporary"}`，非 `"persistent"` 走 `lifecycle_temporary`。
- 输出字符串供 `DeepAgentSpec.system_prompt` 消费，进入 `SystemPromptBuilder` 的 IDENTITY 槽位。

### `prompts/sections.py`

每个 builder 返回 `PromptSection | None`，`None` 表示该角色下不应出现该 section。

```python
class TeamSectionName:
    ROLE = "team_role"        # P:11
    HITT = "team_hitt"        # P:12
    WORKFLOW = "team_workflow"   # P:13
    LIFECYCLE = "team_lifecycle" # P:14
    PERSONA = "team_persona"  # P:15
    EXTRA = "team_extra"      # P:16
    INFO = "team_info"        # P:65
    MEMBERS = "team_members"  # P:66

def build_team_role_section(
    *,
    role: TeamRole,
    member_name: str | None,
    teammate_mode: str = "build_mode",
    language: str = "cn",
) -> PromptSection: ...

def build_team_workflow_section(
    *,
    role: TeamRole,
    team_mode: str = "default",
    language: str = "cn",
) -> PromptSection | None: ...    # None when role != LEADER

def build_team_lifecycle_section(
    *,
    role: TeamRole,
    lifecycle: str,
    language: str = "cn",
) -> PromptSection | None: ...    # None when role != LEADER

def build_team_persona_section(
    *,
    persona: str | None,
    language: str = "cn",
) -> PromptSection | None: ...    # None when persona is empty

def build_team_extra_section(
    *,
    base_prompt: str | None,
    language: str = "cn",
) -> PromptSection | None: ...    # None when base_prompt is empty/whitespace

def build_team_info_section(
    *,
    team_info: dict[str, Any] | None,
    team_workspace_mount: str | None = None,
    team_workspace_path: str | None = None,
    language: str = "cn",
) -> PromptSection | None: ...    # None when no usable fields

def build_team_members_section(
    *,
    team_members: list[dict[str, str]] | None,
    self_member_name: str | None,
    language: str = "cn",
) -> PromptSection | None: ...    # None when list empty after self exclusion

def build_team_hitt_section(
    *,
    role: TeamRole,
    human_agent_names: Iterable[str] | None = None,
    language: str = "cn",
    self_member_name: str | None = None,
) -> PromptSection | None: ...    # None when no human members
```

- `team_info` 字段仅识别 `team_name` / `display_name` / `desc`。多余 key 静默丢弃。
- `team_members` 元素至少含 `member_name` / `display_name`；`desc` 可选。
- `self_member_name` 在 `members` 与 `hitt` 中都用作自身排除 / 自身标注，调用方应保证一致。
- `language` 未在 `_LABELS` 中时回退到 `"cn"`，**不抛异常**。

### `prompts/section_cache.py`

```python
class MtimeSectionCache:
    def __init__(
        self,
        probe: Callable[[], Awaitable[int]],
        fetch_and_build: Callable[[], Awaitable[PromptSection | None]],
    ) -> None: ...

    async def refresh(self) -> PromptSection | None:
        """Cheap probe + lazy rebuild; returns last cached section."""

    def invalidate(self) -> None:
        """Force next refresh to refetch regardless of probe."""
```

- `probe` 必须返回单调递增整数；返回相同值视为无变化。
- `fetch_and_build` 可返回 `None`（数据为空时）；`None` 也会被缓存，下次 probe 不变时直接复用。
- 不持有锁；外部并发调用 `refresh()` 由调用方串行化（实际由 `before_model_call` 单线程保证）。

### `rails/team_policy_rail.py`

```python
class TeamPolicyRail(DeepAgentRail):
    priority = 12

    def __init__(
        self,
        *,
        role: TeamRole,
        persona: str,
        member_name: str | None = None,
        lifecycle: str = "temporary",
        teammate_mode: str = "build_mode",
        language: str = "cn",
        team_mode: str = "default",
        base_prompt: str | None = None,
        team_workspace_mount: str | None = None,
        team_workspace_path: str | None = None,
        team_backend: TeamBackend | None = None,
    ) -> None: ...

    def init(self, agent: Any) -> None: ...
    def uninit(self, agent: Any) -> None: ...
    async def before_model_call(self, ctx: AgentCallbackContext) -> None: ...
```

- `__init__` 中一次性 build static section；HITT 段消费 `team_backend.human_agent_names()` 的快照。运行时新增 human-agent 不重建静态段，需要等 rail 重新构造（`build_team` 路径触发）。
- `before_model_call` 是该 rail 的唯一写入点：先把所有 static section 写回 builder，再依次 `await` 两个 dynamic cache 的 `refresh`。dynamic cache 缺席（`team_backend is None`）时跳过。
- `uninit` 必须把 `_DYNAMIC_SECTION_NAMES = (TeamSectionName.INFO, TeamSectionName.MEMBERS)` 也一起删——dynamic section 不在 `_static_sections` 里。

### `rails/first_iteration_gate.py`

```python
class FirstIterationGate(AgentRail):
    async def wait(self) -> None: ...
    @property
    def is_ready(self) -> bool: ...
    async def before_task_iteration(self, ctx: AgentCallbackContext) -> None: ...
    def reset(self) -> None: ...
```

- `before_task_iteration` 是 `core.single_agent.rail.base.AgentRail` 的钩子；agent 进 task loop 时被调用。
- `wait()` 不超时；caller 自行 `asyncio.wait_for` 包外层。
- `reset()` 只清状态，不取消已 `await wait()` 的协程；正常路径是先 `reset()` 再触发新一轮，等待者会被本轮的下一次 `set()` 唤醒。

### `rails/tool_approval_rail.py`

```python
class TeamToolApprovalRail(ConfirmInterruptRail):
    def __init__(
        self,
        team_name: str,
        member_name: str,
        db: TeamDatabase,
        messager: Messager,
        leader_member_name: str,
        tool_names: Iterable[str] | None = None,
    ) -> None: ...

    async def resolve_interrupt(
        self,
        ctx: AgentCallbackContext,
        tool_call: ToolCall | None,
        user_input: Any | None,
        auto_confirm_config: dict | None = None,
    ) -> InterruptDecision: ...
```

- `tool_names` 限定该 rail 拦截的工具集合；`None` 表示全部（继承自 `ConfirmInterruptRail`）。
- `resolve_interrupt` 两阶段：
  - `user_input is None`：第一次进入 → 命中 auto_confirm 则 `approve()`；否则 `send_message` + `interrupt(...)`。
  - `user_input` 非空：解析为 `ConfirmPayload` → `approved=True` `approve()`，否则 `reject(tool_result=feedback)`。
- 解析失败重新 `interrupt(...)`，**不丢错误成 approve**。

## 数据结构

### `PromptSection`（消费的 core 类型）

| 字段 | 类型 | 含义 |
|---|---|---|
| `name` | `str` | section 唯一名，团队侧来自 `TeamSectionName` 常量 |
| `content` | `dict[str, str]` | language → 渲染好的正文；当前所有团队 section 在构造时只填一种语言 |
| `priority` | `int` | 拼接顺序，团队 section 取 11–16 + 65–66 |

### `MtimeSectionCache`（rail 内部状态）

| 字段 | 类型 | 生命周期 |
|---|---|---|
| `_probe` | `Callable[[], Awaitable[int]]` | 构造时注入，rail 生命周期内不变 |
| `_fetch_and_build` | `Callable[[], Awaitable[PromptSection \| None]]` | 同上 |
| `_cached_section` | `PromptSection \| None` | 跨 `refresh` 持有，`invalidate` 清空 |
| `_cached_mtime` | `int` | 最后一次成功 fetch 时的 probe 值；初值 `0` |
| `_initialized` | `bool` | 首次调用必 miss 的标志，`invalidate` 复位 |

### `TeamPolicyRail` 状态字段

| 字段 | 类型 | 含义 |
|---|---|---|
| `_language` | `str` | 整个 rail 渲染语言；与 `SystemPromptBuilder.language` 应保持一致 |
| `_member_name` | `str \| None` | 用作 dynamic members section 的自身排除 |
| `_team_backend` | `TeamBackend \| None` | dynamic cache probe / fetch 的来源；`None` 退化为纯静态 |
| `_team_workspace_mount` / `_team_workspace_path` | `str \| None` | info section 的可选附加 |
| `_static_sections` | `list[PromptSection]` | 构造期产出的不变内容（已剔除 `None`） |
| `_info_cache` / `_members_cache` | `MtimeSectionCache \| None` | 仅在 `team_backend` 存在时构造 |
| `system_prompt_builder` | `SystemPromptBuilder \| None` | `init` 时绑定，`uninit` 时解绑 |

### `FirstIterationGate` 状态字段

| 字段 | 类型 | 含义 |
|---|---|---|
| `_event` | `asyncio.Event` | 单次开锁原语；`reset()` 调 `clear()` |

### `TeamToolApprovalRail` 状态字段

| 字段 | 类型 | 含义 |
|---|---|---|
| `team_name` / `member_name` / `leader_member_name` | `str` | 消息路由所需的团队 + 成员标识 |
| `message_manager` | `TeamMessageManager` | 包 `db + messager` 的发送器；rail 自身不直接持有底层句柄 |
| `tool_names`（继承） | `Iterable[str] \| None` | 拦截范围 |

### Rail 装配状态机（来自 `TeamHarness`）

`agent_configurator` 决定每条 rail 是否构造，`TeamHarness.build` 决定挂载顺序：

```
              role=LEADER     role=TEAMMATE   role=HUMAN_AGENT
TeamToolRail        ✓               ✓               ✓
TeamPolicyRail      ✓               ✓               ✓
FirstIterationGate  ✓               ✓               ✗
TeamWorkspaceRail   conditional on workspace_manager
TeamToolApprovalRail ✗  conditional ✓ when team-coordinated
                        and approval_required_tools non-empty
                                                    ✗
```

## 与其它 spec 的关系

- **S_03 schema**：`TeamRole` 枚举、`TeamAgentSpec.lifecycle / team_mode / teammate_mode / approval_required_tools` 字段定义在 schema 层，本 spec 的 builder / rail 仅消费这些字段。
- **S_05 agent / TeamHarness**：rail 的实际挂载点（`TeamHarness.build`）、`agent_configurator` 决定挂哪些 rail 的逻辑由 agent spec 负责；本 spec 只规定 rail 各自的契约。
- **S_07 tools**：`TeamToolRail` 与团队工具集合属于 tools spec；本 spec 仅指出 mount order（tool rail 必须先于 policy rail eager init）。`TeamToolApprovalRail` 调 `approve_tool` 工具的契约由 tools spec 定义。
- **S_10 team_workspace**：`TeamWorkspaceRail` 与本 spec 平级，但本 spec 的 `team_info` section 携带 `team_workspace_mount` 信息——workspace 子系统对 prompt 的可见面只通过这两个参数。
- **S_11 i18n（如有）**：`prompts/cn/` `prompts/en/` 与 `agent_teams/i18n.py` 的边界由本 spec 的不变量 10 落地；新增语言要求 `prompts/<lang>/*.md` 全套对齐 + `_LABELS` / `_I18N_LABELS` 增加映射。
- **core S_x prompts**：`PromptSection` / `SystemPromptBuilder` / `PromptTemplate` 的契约属于 core；本 spec 假定它们的行为不变（priority 升序拼接 / `add_section` 同名覆盖 / `{{placeholder}}` 渲染）。
