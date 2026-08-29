# S_05 工具契约

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | `openjiuwen/harness/tools/`（130 文件）、`openjiuwen/harness/schema/task.py` |
| 最近一次修订日期 | 2026-08-23 |
| 关联 feature | N/A |

## 范围 / 边界

本规约定义 harness 的工具（tools）子系统契约：工具形态、注册/发现、分组工具、描述与
i18n、工具生命周期。`tools/` 是 harness 最大的子模块（130 文件），但每类只钉**契约**，
具体工具的 docstring / 实现细节不在此展开。

具体覆盖：

- 工具返回形态 `ToolOutput`（`tools/base_tool.py`）。
- 工具组：shell（`tools/shell/`）、web（`tools/web/`）、multimodal
  （`tools/multimodal/`）、subagent（`tools/subagent/`）、skills（`tools/skills/`）、
  worktree（`tools/worktree/`）、lsp_tool（`tools/lsp_tool/`）、browser_move /
  mobile_gui / tool_discovery。
- 根级单片工具模块：`filesystem.py`（Read/Write/Edit/Glob/ListDir/Grep）、`code.py`、
  `todo.py`、`goal.py`、`ask_user.py`、`cron.py`、`mcp_tools.py`、`memory.py`、
  `coding_memory.py`、`compression_recall.py`、`agent_mode_tools.py`。
- 会话工具组 `SessionToolkit`（`tools/subagent/session_tools.py`）。

不在本规约范围内：
- 工具的权限生效（PermissionEngine / file guard）—— `S_08`。
- 具体 preset 子代理的构建（browser/code/research/verification）—— `S_18`。
- LSP 工具背后的 LSP 子系统—— `S_14`。
- prompt section 的具体文案—— `S_06`。

## 不变量

1. **`ToolOutput` 是工具的统一返回形态**（`tools/base_tool.py`）：
   `success: bool`、`data`、`error`、`extracted_content`、`include_extracted_content_only_once`、
   `long_term_memory`。工具失败必须 `success=False` + `error`，不抛裸异常上抛宿主。
2. **工具注册走 `DeepAgent` / rail 的卡片机制**：工具以 `Tool | ToolCard` 形态存在，
   `card.name` 是身份（`_tool_identity` / `ability_manager.get(name)` 强校验）；新增工具
   不得复用已有 card.name。卸载先校验 card 身份（见 `S_04` 不变量 7）。
3. **工具发现**：`tool_discovery/` 提供 `ToolSearchTool` + bm25 检索（`tool_discovery/bm25.py`），
   是工具搜索的唯一入口；`ListSkillTool` / `SkillTool`（`tools/skills/`）负责技能类工具。
4. **工具分组簇**：
   - web：`create_web_tools()`（fetch / free_search / paid_search）+ `WebFreeSearchTool` /
     `WebFetchWebpageTool`；`is_free_search_enabled()` / `is_paid_search_enabled()` 门控。
   - vision/audio：`create_vision_tools()` / `create_audio_tools()`；由 `VisionModelConfig`
     / `AudioModelConfig` 门控（`S_01` 不变量 8）。
   - todo：`create_todos_tool()`（`TodoCreateTool` / `TodoListTool` / `TodoGetTool` /
     `TodoModifyTool`）+ `TodoLockManager`（session 级锁）。
   - goal：`SubmitGoalReportTool` / `GetCurrentGoalTool` + `GoalReportSink`（接 `S_11`）。
   - 计划模式：`EnterPlanModeTool` / `ExitPlanModeTool` / `SwitchModeTool` +
     `resolve_plan_file_path` / `get_or_create_plan_slug` / `generate_word_slug`。
   - session：`build_session_tools()`（`SessionsListTool` / `SessionsSpawnTool` /
     `SessionsCancelTool`）+ `SessionTaskRow` + `SessionToolkit`；spawn 任务类型
     `SESSION_SPAWN_TASK_TYPE = "session_spawn_task"`（见 `S_03` 不变量 9）。
   - subagent：`SubagentSpawnTool` / `SubagentWaitTool` / `SubagentListTool` /
     `SubagentSendInputTool` / `SubagentCloseTool` / `SubagentResumeTool`
     （`tools/subagent/subagent_tools.py`），消费 `subagent_runtime` —— `S_10`。
   - worktree：`WorktreeManager` / `WorktreeConfig` / `WorktreeLifecyclePolicy` +
     `EnterWorktreeTool` / `ExitWorktreeTool`（`tools/worktree/`）。
   - shell：`BashTool` / `PowerShellTool` / `CodeTool`（`tools/shell/` + `code.py`）。
   - cron：`create_cron_tools()` + `CronToolContext` / `CronToolBackend`(Protocol)。
   - memory：`MemorySearchTool` / `MemoryGetTool` / `ReadMemoryTool` / `WriteMemoryTool` /
     `EditMemoryTool` + `CompressionRecallTool` + `CodingMemory{Read,Write,Edit}Tool`。
   - mcp：`ListMcpResourcesTool` / `ReadMcpResourceTool`（`tools/mcp_tools.py`）。
5. **会话工具与 session 强绑定**：`SessionToolkit` 是注入给 DeepAgent 的
   `set_session_toolkit(toolkit)`（`S_02`）；session 工具状态存 `SessionTaskRow`，
   消费方是 `task_loop/session_spawn_executor.py`。
6. **任务计划工具**：`create_task_tool()`（`TaskTool`，`tools/subagent/task_tool.py`）与
   todo 工具族共同构成任务计划面；task 卡的 schema 见本 spec 数据结构的 `TaskPlan` / `TodoItem`。
7. **工具描述与 i18n**：工具描述默认经 `prompts/tools/` 模板渲染；`LspToolMetadataProvider`
   （`tools/lsp_tool`）是 LSP 工具描述的唯一提供者 —— `S_14` / `S_06`。
8. **工具装载顺序**：`create_deep_agent` / `DeepAgentConfig.tools` 进 `ability_manager`；
   rail init 再动态加工具（`SysOperationRail` 100 先铺文件系统/shell 工具，见 `S_04`
   梯队 100）。工具分批装载的时序语义由 rail priority 保证。

## 接口契约

```python
class ToolOutput(BaseModel):
    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None
    extracted_content: Optional[str] = None
    include_extracted_content_only_once: bool = False
    long_term_memory: Optional[str] = None

def create_web_tools(...) -> list[Tool]
def create_vision_tools(...) -> list[Tool]
def create_audio_tools(...) -> list[Tool]
def create_todos_tool(...) -> list[Tool]
def create_cron_tools(...) -> list[Tool]
def create_task_tool(...) -> Tool
def build_session_tools(...) -> list[Tool]
def is_free_search_enabled() -> bool
def is_paid_search_enabled() -> bool

class SessionToolkit:
    # 会话列表 / spawn / cancel 的宿主能力面
    ...

class SessionsListTool(Tool): ...
class SessionsSpawnTool(Tool): ...
class SessionsCancelTool(Tool): ...

class SubagentSpawnTool(Tool): ...
class SubagentWaitTool(Tool): ...
class SubagentListTool(Tool): ...
class SubagentSendInputTool(Tool): ...
class SubagentCloseTool(Tool): ...
class SubagentResumeTool(Tool): ...

class EnterPlanModeTool(Tool): ...
class ExitPlanModeTool(Tool): ...
class SwitchModeTool(Tool): ...

def resolve_plan_file_path(workspace_root: str, plan_slug: str) -> Path
def get_or_create_plan_slug(workspace_root: str) -> str
def generate_word_slug() -> str

class WorktreeManager: ...
class WorktreeConfig(BaseModel): ...
class WorktreeLifecyclePolicy(str, Enum): ...
```

错误 / 返回语义：

- 工具错误一律以 `ToolOutput(success=False, error=...)` 返回，不抛异常。
- `get_or_create_plan_slug` 缺 workspace_root → 抛；plan 文件路径经 `resolve_plan_file_path`
  固定解析（`<workspace_root>/<slug>/plan.md` 形态，实际以 `agent_mode_tools.py` 为准）。
- `WorktreeManager` 操作失败抛 `GitError` / `WorktreeLockTimeout`（`tools/worktree/`）。

## 数据结构

### SessionTaskRow（session 工具行）

| 字段 | 语义 |
|---|---|
| `task_id` | controller 任务 id（`SESSION_SPAWN_TASK_TYPE`） |
| `status` | 行状态（进行中 / 完成 / 取消） |
| `session_id` / `command` | spawn 会话标识与命令载荷 |



### TaskPlan / TodoItem（`schema/task.py`）

| 字段 | 语义 |
|---|---|
| `id` / `content` / `activeForm` / `description` | 任务标识与描述 |
| `status: TodoStatus` | 四态（`PENDING` / `IN_PROGRESS` / `COMPLETED` / `CANCELLED`） |
| `depends_on: List[str]` | 前置任务 id（task 图） |
| `result_summary` / `meta_data` | 结果 / 附加 |
| `selected_model_id` | 单任务模型选择（model_selection 语义见 S_11 关联） |

`TodoStatus` 是任务状态的唯一枚举；`STATUS_ICONS` 提供展示图标。

### 工具分组 → 装载点

| 组 | 装载途径 | 门控 |
|---|---|---|
| web | `create_web_tools` | `is_free/paid_search_enabled()` |
| vision / audio | `create_vision_tools` / `create_audio_tools` | vision/audio config 完整 |
| todo / task | `create_todos_tool` / `create_task_tool` | — |
| goal | rail（`TaskCompletionRail` init 注册） | goal 启用 |
| session | `build_session_tools` | session 工具启用 |
| subagent | `SubagentRail` init 注册 | `enable_subagent_runtime` |
| worktree | `EnterWorktreeTool` / `ExitWorktreeTool` | worktree 配置 |

## 与其它 spec 的关系

- 工具进出 `ability_manager`、card 身份校验 —— `S_02` / `S_04`。
- 权限生效（`PermissionInterruptRail` / `PermissionEngine`）—— `S_08`。
- 子代理工具（spawn/wait/send_input/close/resume）消费 `subagent_runtime` —— `S_10`。
- 任务计划模型（`TaskPlan` / `TodoItem` / `TodoStatus`）—— 本 spec（`schema/task.py`）。
- goal 工具接 `GoalManager` —— `S_11`；LSP 工具接 `lsp/` —— `S_14`。
- 工具描述的文本归属 `prompts/tools/` —— `S_06`。
