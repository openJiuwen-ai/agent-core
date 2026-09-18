# S_05 工具契约

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | `openjiuwen/harness/tools/`（130 文件）、`openjiuwen/harness/schema/task.py`、`openjiuwen/core/foundation/tool/base.py`（`Tool.render_for_llm`） |
| 最近一次修订日期 | 2026-09-14 |
| 关联 feature | `F_04_tool-result-llm-rendering.md` |

## 范围 / 边界

本规约定义 harness 的工具（tools）子系统契约：工具形态、注册/发现、分组工具、描述与
i18n、工具生命周期。`tools/` 是 harness 最大的子模块（130 文件），但每类只钉**契约**，
具体工具的 docstring / 实现细节不在此展开。

具体覆盖：

- 工具返回形态 `ToolOutput`（定义于 `core/foundation/tool/schema.py`，经 `tools/base_tool.py` 再导出）
  与面向模型的结果渲染 `Tool.render_for_llm`。
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

1. **`ToolOutput` 是工具的统一返回形态**（定义于 `core/foundation/tool/schema.py`，
   `tools/base_tool.py` 再导出）：`success: bool`、`data`、`error`、`extracted_content`、
   `include_extracted_content_only_once`、`long_term_memory`。工具失败必须 `success=False` +
   `error`，不抛裸异常上抛宿主。
2. **工具注册走 `DeepAgent` / rail 的卡片机制**：工具以 `Tool | ToolCard` 形态存在，
   `card.name` 是身份（`_tool_identity` / `ability_manager.get(name)` 强校验）；新增工具
   不得复用已有 card.name。卸载先校验 card 身份（见 `S_04` 不变量 7）。
3. **工具发现**：`tool_discovery/` 提供 `ToolSearchTool` + bm25 检索
   （`tool_discovery/bm25.py`）以及固定的 `ToolCallTool` 包装器。模型先搜索并授权 deferred
   工具，再经 `tool_call` 交给原 `AbilityManager` 生命周期执行；`ListSkillTool` /
   `SkillTool`（`tools/skills/`）负责技能类工具。
4. **工具分组簇**：
   - web：`create_web_tools()`（fetch / free_search / paid_search）+ `WebFreeSearchTool` /
     `WebFetchWebpageTool`；`is_free_search_enabled()` / `is_paid_search_enabled()` 门控。
     付费搜索卡片的描述和 `provider` 枚举仅包含 `auto` 与当前配置了非空 Key 的供应商。
     调用时重新读取配置：已移除的供应商和失效的环境变量覆盖回退到当前可用供应商，
     不进入未配置供应商的 runner；全部 Key 移除时不注册工具，也不发出付费请求。
     热重载不能因卡片 ID 相同而保留旧的付费搜索描述或参数枚举。
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
   - shell：`BashTool` / `PowerShellTool` / `CodeTool`（`tools/shell/` + `code.py`）；
     大输出（> `max_output_chars`，默认 20000）落盘并在 `<persisted-output>` 中以 head+tail
     预览回显（`truncate_output`，`head_ratio` 默认 0.6），保证尾部错误/结束状态可见。
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
9. **Browser 默认工具面保持紧凑**：默认只暴露常用 Playwright primitive、两类 Probe、
   Batch 和受限 offload recall。诊断、取消、custom-action discovery、拖放及其他低频能力
   通过显式 capability 启用；runtime 内部 transport 工具不进入模型工具面。
10. **Browser 可恢复错误不消耗模型回合**：generation 刷新、单步骤 Batch primitive 改写、
    primary link 导航、Probe JSON 一次重试和新标签页 URL 等待由 runtime 确定性处理；只有
    无法唯一解析目标或页面语义确实不充分时才把紧凑错误返回模型。
11. **模型读到的工具结果文本只来自 `Tool.render_for_llm`**：`AbilityManager` 构造工具结果
    `ToolMessage` 时对工具实例调用 `render_for_llm(result)`；结构化结果原样留在
    `ToolCallInputs.tool_result` 给 rail / 事件 / 日志。默认实现（`render_tool_output`）：成功取
    `data["content"]`（`data` 为字符串直接用，无 `content` 的其它载荷序列化为 JSON），失败取
    `error`（为空回落载荷），空结果给占位文本；非 `ToolOutput` 结果为 `str(output)`。
    - `data` 没有 `content` 的工具**必须覆写** `render_for_llm` 给出纯文本，JSON 兜底只是最后防线；
      浏览器 runtime 工具例外——`BrowserRuntimeRail` 按 JSON 解析其消息，保持默认 JSON。
    - 返回裸 dict 的工具（todo / goal）不改返回形态（CLI todo 视图解析其 `str()`），只覆写渲染。
    - 函数式工具经 `LocalFunction(..., render=...)` 定制，无需子类化。
    - 自定义渲染抛异常时记录日志并回落默认渲染：工具已执行（可能有副作用），不能因渲染失败
      转成执行错误并触发重试。
    - **渲染只作用于发给模型的 `ToolMessage.content`，绝不改写结构化结果**：流式出口
      （`ToolTrackingRail` 的 `str(tool_result)`、native harness `_ObservationRail` 的
      `to_json_safe(tool_result)`）与 observability 读的都是 `tool_result`，上层服务依赖其结构做
      判断与展示。因此 `McpToolResult` 保持独立模型（不继承 `ToolOutput`，序列化不多字段）。
    - **流式工具结果同时带结构化字段与渲染文本（独立字段）**：`tool_result` 流式块在原字段之外
      增加 `rendered_result`——模型实际读到的文本，由 `resolve_tool_result_text(inputs, exception)`
      取值（`core/single_agent/ability_manager.py`，与 `AbilityManager.execute` 共用
      `resolve_tool_message` 这一条规则：正常 / 跳过取 `inputs.tool_msg`，工具抛异常取
      `AbilityExecutionError.tool_message`）。展示侧（CLI 渲染器、subagent activity / transcript）
      优先读 `rendered_result`，缺失才回落旧字段；不得再用 `str(tool_result)` 生成展示文本。
    - 产出 `tool_result` 流式块的 rail（`ToolTrackingRail` / `_ObservationRail`，priority 5）必须
      晚于所有改写 `tool_msg` 的 rail，`rendered_result` 才是最终文本。
    - `structured_result` 暂不输出；`ToolTrackingRail` 的字符串 `tool_result` 是兼容字段。待 UI 迁移
      到结构化数据后，该兼容字段与基于其字符串的解析逻辑全链路移除。
    - `tool_call` 包装器不重新渲染目标结果：结构化结果保持 `{"name", "result"}` / 原 `error`，
      目标工具已渲染（含 AFTER_TOOL_CALL 改写）的消息文本放在 `RelayedToolOutput` 的私有属性里，
      不进 `model_dump` / `str()`，只由 `ToolCallTool.render_for_llm` 读取。
    - 外部协议出口（team MCP server / Claude SDK MCP / skill CLI / 被动成员执行器）同样调用
      `render_for_llm`，保证与进程内模型看到的文本一致。
12. **Shell 大输出以 head+tail 回显**：`BashTool` / `PowerShellTool` 对超过
    `max_output_chars`（默认 20000）的输出落盘并在 `<persisted-output>` 块中回显带缺口标记的
    head+tail 预览（`truncate_output`，`head_ratio` 默认 0.6），使尾部错误/结束状态可见；
    小输出内联、不落盘。

## 接口契约

```python
# core/foundation/tool/schema.py（harness 经 tools/base_tool.py 再导出）
class ToolOutput(BaseModel):
    success: bool
    data: Any | None = None
    error: str | None = None
    extracted_content: str | None = None
    include_extracted_content_only_once: bool = False
    long_term_memory: str | None = None

# core/single_agent/ability_manager.py
def resolve_tool_message(inputs: ToolCallInputs, exception: BaseException | None) -> ToolMessage | None: ...
def resolve_tool_result_text(inputs: ToolCallInputs, exception: BaseException | None) -> str | None: ...

# core/foundation/tool/base.py
class Tool:
    def render_for_llm(self, output: Any) -> str: ...     # 子类覆写以定制模型文本
def render_tool_output(output: ToolOutput) -> str: ...   # 默认渲染规则
def render_payload_text(data: Any) -> str: ...           # data["content"] / 字符串 / JSON 兜底

# harness/tools/base_tool.py
def render_fields(fields: Mapping[str, Any], *, separator: str = "\n") -> str: ...  # 扁平记录 → key: value

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

- 可恢复的工具错误一律以 `ToolOutput(success=False, error=...)` 返回，不抛裸异常。
  `ToolInterruptException` 属于用户交互控制流，所有工具包装层必须原样传播，具体契约见
  `S_04`。经包装层进入中断状态的 deferred 工具在 resume 时重新执行原 wrapper call，
  由 wrapper 在保留的搜索授权下再次分发 target；审批请求仍使用 target call ID。
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


## 任务级 Web 配置

WebFreeSearchTool、WebPaidSearchTool、WebFetchWebpageTool 可在构造时接收
`proxy_url`；该值优先于 WEB_PROXY_URL / FREE_SEARCH_PROXY_URL，不修改进程环境。
未提供时保留既有环境代理与 NO_PROXY 行为。代理认证由 HTTP transport 处理。

FreeSearch 和 Fetch 可接收 `allowed_domains`，按主机名与子域匹配过滤来源；
FreeSearch 另接受 `enabled_engines`，用于单个任务选择后端，避免修改全局开关。
国内学术域范围下使用百度学术、百度网页、知网、万方入口，并过滤返回来源。
Fetch 在受限来源模式下禁用 jina reader 回退。

域名过滤检查请求入口和返回结果；HTTP 自动重定向仍可能访问域外主机，
因此该设置不是网络访问隔离边界。严格访问隔离应由网络层实施。

决策与限制见 `../features/F_01_task-scoped-web-research.md`。
