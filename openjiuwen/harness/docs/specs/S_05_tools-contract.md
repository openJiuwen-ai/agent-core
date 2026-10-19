# S_05 工具契约

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | `openjiuwen/harness/tools/`（130 文件）、`openjiuwen/harness/schema/task.py`、`openjiuwen/core/foundation/tool/base.py`（`Tool.render_for_llm`） |
| 最近一次修订日期 | 2026-10-15 |
| 关联 feature | `F_04_tool-result-llm-rendering.md`、`F_05_browser-task-integrity.md`、F_12_browser-jev-shared-observation、F_13_browser-local-decision-loop、F_21_cua-desktop-subagent |

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
  mobile_gui / cua（`tools/cua/`）/ tool_discovery。
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
     `audio_transcription` 对 `gpt-4o-transcribe`、`gpt-4o-mini-transcribe`、`whisper-1`
     和 `FunAudioLLM/SenseVoiceSmall` 忽略大小写精确匹配，使用 `/audio/transcriptions`
     的 multipart 文件上传；请求保留原始模型名。其他模型（如 MiMo、Gemini）继续使用
     `/chat/completions` 的 `input_audio`。SiliconFlow 的 `base_url` 应为
     `https://api.siliconflow.cn/v1`，不含接口后缀。本路由不改变 `audio_question_answering`，
     不代表 SenseVoice 支持音频问答。
     Chat 音频编码将 `audio/wav`、`audio/wave` 和 `audio/x-wav` 统一为 `wav`，
     避免不同平台的 MIME 数据库把同一 WAV 文件误标为 `mp3`。
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
9. **Browser 默认工具面保持紧凑**：`browser_capabilities=None` / `[]` 只选择 `core`，
   加上两类 Probe、Batch、可选意图/验证入口 browser_phase 和受限 offload recall。其他 MCP 工具通过显式 capability 启用；
   `unsafe_dev` 与 `advanced_code` 同选时以前者替换后者。不支持图像输入时过滤截图工具。
   取消、健康检查、custom-action discovery 是可显式装配的 Python Tool，不是 capability
   自动注入的工具。runtime 内部 transport 的代码执行器不等于模型可见工具面。
   完整清单和当前行为统一见[浏览器子智能体](../../../../docs/zh/2.开发指南/API文档/openjiuwen.harness/subagents/浏览器子智能体.md)。
10. **Browser 可恢复错误不消耗模型回合**：generation 刷新、单步骤 Batch primitive 改写、
    真实详情链接导航、无歧义参数别名和新标签页 URL 等待由 runtime 确定性处理；只有
    无法唯一解析目标或页面语义确实不充分时才把紧凑错误返回模型。
    Probe 确定性脚本异常保留原错且不重跑；当前页 fragment、JavaScript 链接和状态控件保留 click。
    Batch 接受当前 generation 的原生 AX ref 或 PageState target；排序等待消费已解析目标的实时状态。
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
      不进 `model_dump` / `str()`，只由 `ToolCallTool.render_for_llm` 读取。目标结果带
      `data["multimodal"]` 时，包装器把这份图片列表移到自身 `data["multimodal"]`（嵌套的
      `result` 不再带它），图片照常送达模型，且只序列化一次。
    - 工具图片走 `data["multimodal"]`（`type="image"`、`data_url`）：代理在该批工具消息之后追加
      一条用户消息交给模型。图片项带 `"keep_in_context": False` 时，只在模型对它作出回答之前出现
      在发给模型的窗口里；之后窗口中该图片替换为一句说明文字。存储的上下文不改，失败重试、暂停
      恢复时图片仍在。不带该键的图片每次都发送。
    - 外部协议出口（team MCP server / Claude SDK MCP / skill CLI / 被动成员执行器）同样调用
      `render_for_llm`，保证与进程内模型看到的文本一致。
12. **Browser Batch 按顺序验证执行目标**：完整 schema 在执行前检查；DOM 唯一性、可见性和
    可操作性在每个步骤执行前检查，允许前序等待或操作使后续目标出现。generation 绑定目标
    不得跨导航复用。未执行的校验失败带 `executed=false/state_changed=false`；已执行前序
    步骤后的失败保留 `partial`。只有明确 `optional` 的步骤失败才继续。
13. **可执行数据不是展示文本**：URL、selector、target/generation 标识完整保留；紧凑投影
    超过容量时删除整项或省略字段，不产生貌似可执行的截断链接。滚动 transport 最长等待
    15 秒（仍受更短的任务期限约束），点击和脚本等副作用操作超时不能自动重放。

14. **Browser 管理与执行分离**：browser_phase 只更新意图/读取核验，不占页面动作预算；
    逐步骤执行回执保留部分完成与未派发事实。业务验证在 llm/hybrid 中一致，见 S_18 / F_10。
15. **Browser 输入与回执可恢复**：阶段条件按 kind 声明字段，set/verify 分开校验；错误包含
    字段路径和修正方向，绑定目标来自同一当前 guarded registry。执行器确认的反馈文本保留
    在 journal，但不能替代业务读数。原生工具和 Batch 共用执行安全边界，见 F_17。
    `browser_phase verify` 的 `inspect_cart:true` 仅执行固定 DOM 读取，返回 reader 线索；不认证
    完整性、不修改页面或关闭基线。cart_delta 的 requirement_id 允许重绑 reader，不允许改写原基线/增量。

## Browser 运行时减法（F_17，当前契约）

阶段计数、自然语言验收和可选条件只提供诊断，不阻断一般动作。删除阶段次数上限、
三次核验读取配额、重规划试探/拒绝终止和价格区间特判。总截止时间、迭代数、单次等待、
权限、串行派发、精确目标校验及真实未知效果保护保持有效。

cart_delta 仅是可选工具读数，不再要求动作先建立基线，不因成功点击强制进入核验协议，
普通观察不自动附加购物车 RPC。当前意图替换旧条件；journal 未知效果不会随之消失。
authoritative_browser_result 保留兼容名，但权威范围限于执行/观察；业务完成由 LLM
结合原始目标与本任务观察判断。acceptance、missing_conditions 和自动字段缺口是提示，
不能推导“任务已认证”或“页面被锁住”。工具日志证明动作未发送时记录未派发；真实未知
效果仍禁止盲重放。该行为同时适用于 llm/hybrid，不再由 strict/lean 切换执行真值。

此前 F_10/F_12/F_13/F_16 中与本节冲突的预算、自动证明和试验开关约定由本节取代。
详见 [F_17](../features/F_17_browser-runtime-reduction.md)。

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
def resolve_subagent_task_timeout_s() -> float   # tools/subagent/task_tool.py
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

- `SubagentResumeTool` 恢复实例但不投递任务；返回 `idle` 时在 `message` 中明确说明
  本次调用未执行新任务。用户只要求恢复时保持待命，有后续输入才调用 `send_input` + `wait`，
  不因 `idle` / `turn_outcome=completed` 立即关闭。`restored=false` 表示实例已存活，
  不应通过 `close` + `resume` 重试；运行中实例不附加空闲待命提示。
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

## Browser 页面与动作协作

- 定向 Interactive Probe 从同一个 registry 返回本次匹配的有效 target，保持匹配顺序；
  不能拿全页前 20 个控件替代。全页摘要继续优先搜索框；无有效命中返回诊断并允许原生局部 AX 回退。
- Browser Rail 的 snapshot 请求始终返回可读内容（忽略 filename 落盘选项）；大结果沿用任务内
  recall，不添加任意文件读取能力。非 Browser Rail 的 MCP 调用不受此归一化影响。
- 当前 tab 的 URL/title 成对解析，URL 变化而 title 未知时清空旧 title。目的页证据必须关联本次
  结果选择/导航，不因“不是搜索页”就认证首页为目标详情，也不把目的页推荐卡标题当页面标题。
- MCP Result 区先统一解码，再投影字段；支持数组/字符串和嵌套包装，不从执行脚本中挖 JSON。
  成功导航记录 requested/landed 关系；同页 URL 规范化不清标题，路由参数与 fragment 仍保留。
- Batch URL 等待限定动作来源页或动作后新开的关联页，不扫描任意旧 tab。切换后同步 MCP 活动页；
  歧义不猜选，不重放成功动作。保留 Chrome/Profile/Cookie。
- Card 质量分只用于挑选容器；主区域确定后按渲染/列表顺序输出。区域歧义、关键词过滤或
  滚动视口不确认全局序号时返回 `order_known=false`，不伪造 `result_index`。
- 作者排除操作按钮，计数只读取局部字段，日期不填入评论数；分段整数/小数价格需拼接。
  可执行 URL、selector 不按展示文本截断；同实体详情纠错沿用现有 evidence/provenance。
- 离屏可滚动控件与禁用/遮挡分别诊断；不使用强制点击绕过真实不可操作状态。无有效 target 时返回
  有限局部片段。价格读取保留 DOM 文本节点边界，折扣不并入金额。导航/筛选组不占结果卡片窗口。
- `observed_count` 是本次 Probe 观察到的自然条目数，不以排名是否已知归零；诊断的 ranked_count
  单独表达可排序条目。工具返回本次注册卡片，PageState 仍保持原有小窗口，投影原文进入任务内 recall。
- 原生 Playwright 负责单动作与 AX；Probe 补充结构化区域/字段；Batch 负责确定多步骤。
  有明确前置排序 target 的缺参等待可一次修复；不猜歧义目标，不重放已成功点击。
- 定向 evaluate 可用简短 `fields: {author: {value, selector, raw_text}}`，不强制每轮进度 JSON。
  简单问答可直接搜索 URL 并读顶部答案卡，标注来源，不把 AI 答案伪装成自然结果排名。
- 导航、广告、AI 答案与主结果分开分类；分类原因保留在紧凑卡片中，站点规则在 site profile。
  `is_ad=true/ad_status=ad` 表示有广告标记；未识别到时 `ad_status=unknown`，不是非广告证明。
  时长 scope 区分 current_part/collection/unknown，酒店星级不填住客评分，既有报价口径仍保留。
- Cards/native/evaluate 共用已有 sourced observation 路径。evaluate 的有序列表按实际有效记录计数，
  不采信单独 count 值。空查找标记 observation_status=not_observed，不等于证明字段不存在；
  带依据的 missing/unknown 保留，后续同实体正证据可纠正早先空结果。
  `value=null`、空数组和仅有 selector 的字段包装也属于未命中，字典的字符串表示不是缺失依据。
  未命中的详情查找不能覆盖已有值，也不能阻止后续真实观察修正；短的非空原生文本不以长度判无效。

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

## Jev 分段交接补充（F_08，2026-09-23）

同一任务的评估总预算、回退作用域、可执行状态指纹和执行记录保存在 session phase state；
focused resume / 模型重建不重置预算或 deadline。软回退只在意图或可执行状态实质变化后
重新准入，时间、capture_id、target_id 更新本身不构成恢复条件。FINISH 留在现有 LLM
收尾协议；任何失败（含认证、计费、配置与协议错误）只回退当前可执行状态段，状态变化后重新询问 Jev（F_15）。
browser_page_action 是参数封闭的运行时辅助工具，提供显式 URL 导航、返回和有界滚动；
与 Batch 一样先经过权限钩子，再检查实际参数、任务、页面与 DOM document，且验证底层
capability。工具成功回执与执行后观察分开记录；不能自动重放结果不明的操作。


### September 24 shared observation and Jev coverage (F_12)

Control capability facts belong to the shared Runtime regardless of model mode.
Exact AX targets can be enriched with bounded fixed DOM reads before journal preparation.
Cross-read sort/card proofs require the same query, source, page, generation and
interaction revision; potentially mutating actions invalidate that association.
Reads and explicit verification share the task deadline, without a separate recovery
allowance (F_17). Resolving one effect cannot erase another effect's uncertainty.
Jev may select registered fixed probes and source-grounded first-result navigation,
with the same task, permission, argument and late page/node guards as other actions.
Separately quoted sort labels do not make an otherwise unique search literal ambiguous.
No alternate executor, model-generated proof or new browser lock is introduced.


### September 25 local decision loop (F_13)

Observed labels have one normalization. Action-result and automatic observation
share an interaction revision; independent later changes invalidate it. Navigation
proof binds a selected result to the actual landing page, including popup batches.
Browser tools execute serially through the existing scheduler. Semantic progress is
advisory; recovery does not need a runtime-approved trial or semantic proof (F_17).
Client first/idle chunk waits and tool timeouts are bounded by the shared task deadline; hybrid adds no whole-stream cap (F_25).

Jev selects operation and its scoped target in a single multi-head request. Closed
fixed reads and known local UI remain available during unknown-effect recovery;
unverified business effects never certify completion or authorize replay.
Bindings, receipts, milestones and observations reuse the existing phase, journal
and PageState. No additional workflow manager or alternate executor is introduced.
### September 29 Jev decision context (F_15)

A target that failed twice (Jev, LLM or no observable progress) is retired for the parent
query across subagent runs, url, intent and re-render changes, and re-admitted when it later
succeeds. Jev's request includes the LLM's last six browser steps with per-step outcomes and
navigation destinations (host and path only), covered controls with the element covering them
(information only, never targets), structured target options with observed control state, and
flags for truncated page text and unread result cards. Search terms and quoted values come from
the user's unwrapped request (either host envelope prefix), limited to those the current intent
names. FINISH always hands back to the LLM, including when adapter fields are missing (F_17). Any Jev failure hands back
only the current observed state. Payload and pick logs are opt-in because they carry task text.

### September 29 lean guard trial (F_16)

The strict/lean trial has been superseded by F_17; the environment switch no longer changes
execution truth. Cart actions need no mandatory baseline. A primitive whose executor log
proves it timed out before performing the action is recorded as not dispatched. Explicit
executed=true, performed actions, absent/truncated logs and arbitrary scripts remain uncertain
on failure. Task typing still recognises buying and booking wording.


## Browser 执行回执与局部连续性（F_18，2026-10-08）

目标 preflight 通过只表示可尝试，不能写 executed=true。执行调用中未获 ACK 为 unknown；
完整 Playwright Call log（先移除 ANSI 样式）证明未发送时，compact/primitive 共用 journal
归类为未派发。复合输入已有动作的执行事实不能被后续等待错误抹掉；外层不再重放未知点击。

本地 offload recall 不推进页面版本。同页、同版本、同范围卡片读取可在短观察窗口复用，
导航、潜在写入、等待或新观察变化使缓存失效。任意脚本保持潜在写入边界。进展指纹只比较
内容与可执行状态，不把调用历史、观察元数据和卡片重新有效算成进展；新读取记 new_observation。

Jev 候选避免重交页面已经观察到的同一搜索，默认把回到已访问标签页留给 LLM 恢复；新标签仍
可选。同对象已 ACK 的业务动作由共享 journal 排除自动重放，不新增业务成功证明。新对象、
已执行的新输入/选择、显式新局部目标可表达后续工作；固定读取和其他局部动作继续开放。
LLM/Jev 共享 local_context，复杂目标复用可选 objective/绑定；普通查询无需额外阶段调用。

详见 [F_18](../features/F_18_browser-receipts-and-local-continuity.md)。

## Browser 默认观察与结果交接（F_19，当前契约）

默认任务不再从自然语言生成字段、数量、比较槽，不维护对应 acceptance/coverage 认证路径。
已保存的 inferred 状态在加载/续跑时退出该路径，保留原始观察、journal、局部目标与期限。
外部明确提供 requirements_source=explicit 的输出契约继续按既有字段形态检查，不能扩张为业务认证。
当前搜索 URL、未映射字段和推断缺失不自动改写完成状态，也不自动创建修复调用。
父层可显式用原 resume_task_id 纠正遗漏，受原期限和一次续跑限制；不带续跑请求的重复调用返回已有结果。

统一终态携带 worker 判断、执行回执、来源观察和 summary；字段诊断不替代原文。阻断列表不是完备枚举，
为空不能证明没有页面障碍。父层与 resume 保留同一结果；模型渲染只展示一次结果，避免三份重复投影。
常用等待使用已有目标/文本/URL 条件；缺目标仍拒绝，旧目标校验、串行派发、权限和真实未知效果保护保留。
本节取代 F_17 中继续默认维护 inferred 诊断和语义反证的部分，详见 [F_19](../features/F_19_browser-observation-first-results.md)。

### October 9 decision observation handoff (F_21)

Fixed-reader optional observation uses executor-supported Page APIs, not assumed Node VM
globals. Capture failure preserves the primary tool receipt and carries a specific diagnostic.
Missing compact decision data permits one bounded existing metadata reconciliation, without
full AX capture or primary-action replay. Valid cached observations remain shared.

Jev operation criteria summarize legal targets/values directly; independent target questions
do not depend on another head's criteria or result. Both heads remain one request. The
optional phase objective/bindings are the LLM's local handoff, never mandatory per action.
Initial tabs and scoped reader content use existing task/cache state to avoid stale destinations
and repeat reads caused only by unrelated control observations. Same-query ACK search
submits are retired locally; LLM recovery, new bindings and changed scope remain possible.

See [F_21](../features/F_21_browser-decision-observation-handoff.md) for compatibility and tests.


### Browser receipt and current binding consistency (2026-10-09)

Failed read/wait steps retain their errors and conditions but aggregate as partial with
previous ACKs intact. Only uncertain mutations propagate dispatched_unknown; a failed
text wait is not observed feedback. Probe source checks prefer the current shared page
binding to historical phase URLs and synchronize rejection body/metadata before policy
or evidence processing. Navigation-only target errors preserve the target capability and
observed URL; multi-step popup intent is never silently rewritten. Common model-facing
waits are reduced while controller compatibility and required target checks remain. A
read binding lost after a batch action permits one bounded existing observation refresh,
without action replay or guessed rebinding. See F_22.


### Browser reader admission and stale observation recovery (F_23, 2026-10-09)

A successful fixed read is reusable work, not an invitation to sample again. The existing
policy receipts retain its scope: page/document, query/range, object and local intent/bindings (F_25). Text-content changes alone do not re-admit that same read or reset
its retry key. ACKed concrete interactions, changed relevant state, document/range or explicit local intent permit new reads.
Unrelated revisions, arbitrary scripts and failed dispatches do not erase read history (F_25). Arbitrary evaluate remains potentially mutating.
Raw observations stay available to LLM; historical text never becomes a fresh target.

A definite stale page/node/target rejection invalidates the existing decision snapshot
and post-action cache. Its unexecuted receipt triggers one bounded compact metadata/probe
repair via the normal context lifecycle. No action is replayed or rebound. Failed repair
returns to LLM with an empty decision observation and concrete diagnostic; an ordinary
button failure does not invalidate the whole observation.

FINISH hands judgment to LLM without requiring phase conditions. Optional set requires
op/objective; verify-only fields and noncurrent targets remain rejected with correction
guidance. Batch and page-action examples use their respective schemas. Completion means
the user's authorized endpoint, not subsequent work the user reserved for themselves.
Parent delegation preserves the endpoint and constraints without inventing comparisons
or repeat verification. Recommendation regions and nearby control text are source facts,
not business proof or new action gates.

### Browser dispatch facts and local recovery (F_24, 2026-10-12)

Complete executor strict-resolution errors before dispatch share the journal's undispatched
classification; explicit execution and incomplete logs never lose unknown-effect protection.
PageState stale errors carry a stable code and category. Unexecuted stale steps, including
those after an ACK in a partial batch, invalidate the shared observation and request the
existing bounded repair without undoing the ACK or replaying an action.

Late guards retain their predicates and expose only mismatch categories. Existing policy
receipts scope repeated stale-binding recovery to the operation/object; after two rejections
one LLM recovery turn is requested. Scoped LLM recovery, successful object action or a new binding re-admits it,
with guards still mandatory. Other controls and readers remain available. Navigation
repetition feedback uses recent actions and observed page identity, without blocking tools.


### Executor facts and model deadline ownership (F_25, 2026-10-12)

The native targeting executor's exact selector-zero-match envelope is undispatched;
script errors and explicit execution remain uncertain. Client first/idle waits keep their
diagnostic stages. Hybrid wraps only the shared task deadline, not an extra total/idle
limit. Near deadline, the existing tool-disabled synthesis/force-finish path preserves
facts; it does not manufacture a 0.1-second provider request. Error receipts distinguish
runtime task exhaustion, client waits, local limits, unknown timeouts and upstream errors.


### Scoped reader work and local capabilities (F_25, 2026-10-12)

Observed navigation has full-URL identity scoped to its source document and local objective.
Late link/order checks rerun the original bounded probe recipe and selector provenance on
live DOM. Repeated invalidations use existing receipts and a single local LLM handoff.
Historical card/text content remains interpretable after target invalidation; stale content
never grants a fresh executable binding. Automatic read identity excludes generic revision
and content hashes. Concrete ACK interactions can reopen a range; repeated no-progress
backoff survives unrelated captures/evaluate. LLM direct reads need no justification schema.

The same one-request action/target menu offers capped full-page or current-viewport cards,
bound extract_text/extract_value, observed links, number/date inputs and purpose-bound keys.
Readonly current-value reads retain document/node/signature checks but ignore old values and
unrelated form state; mutations retain full guards. Row text helps discover named controls,
never infers an unlabeled icon's business meaning. Observed links cannot evade local object
constraints. Both providers use the existing client schema and ordinary serial tool rails.

Use action receipts/current observations before conditional waits. Retain per-step ACKs
after failed waits and never replay to repair a read. Parent and worker preserve user
budget, dates, object and stopping point; optional phase fields are handoff, not proof.

## TaskTool cua 委派与续跑（F_21，当前契约）

- **委派上限可配**：task_tool 卡片的 `resilience.timeout_s` 取 `resolve_subagent_task_timeout_s()`：
  环境变量 `OPENJIUWEN_SUBAGENT_TASK_TIMEOUT_S` 为正数时用它，否则用 `DEFAULT_SUBAGENT_TASK_TIMEOUT_S`
  （720 秒）；非法值记 warning 后回退默认。
- **显式续跑类型**：`browser_agent` 与 `cua_agent` 都非 sticky（`S_16`），只有它们接受
  `resume_task_id`；值必须以 `{parent_session_id}_sub_{subagent_type}_` 开头，否则拒绝。其他类型
  传入 `resume_task_id` 报 `TOOL_TASK_TOOL_INVOKED`。
- **结果映射**：cua_agent 的返回 data 总带 `resume_task_id`。子代理结果中存在
  `cua_result`（`CuaProgressRail` 写入，`S_18`）时，另带 `cua_result`、`retryable`
  （`status != "completed"`）和 `resume_context`（`status` / `blockers` / `current_window` /
  `recommended_recovery` / `resume_count`）。`resume_context` 按 sub_session_id 存入父 session
  state `__cua_delegation_state__`。
- **超时可续跑**：cua 委派在 `max(上限 - 20, 上限 × 0.5)` 秒时由 TaskTool 自行取消，先于
  AbilityManager 的硬超时。取消后返回 `success=True` 的 ToolOutput：`status="timeout"`、
  `retryable=True`、`resume_context.last_action_confirmed=False`，并说明取消时正在执行的动作可能
  已生效但未记录。不抛超时错误，已完成的步骤不丢失。
- **聚焦续跑**：带 `resume_task_id` 的 cua 调用，若父 session 中有该子会话的记录，任务描述改写为
  「上次状态 / 最后窗口 / blockers / 恢复提示 + Remaining task」。上次为 `timeout` 时要求先
  重新快照并确认中断的动作是否已生效，再决定是否重做；无记录时描述原样传递。续跑时子代理输入带
  `run.context.extra = {"cua_resume": True, "resume_task_id": ...}`。
- `SubagentRail` 给 cua_agent 的工具摘要为 `list_apps, list_windows, get_window_state, click,
  type_text, press_key, hotkey, scroll, launch_app`；task_tool 提示词说明 cua 超时后用同一
  `resume_task_id` 续跑、只描述剩余部分。

## cua 桌面工具（`tools/cua/`，F_21，当前契约）

- **工具集**：`desktop_tools.build_cua_desktop_tools(desktop, names)` 构建 cua 桌面工具，名字为裸名
  （`list_windows`、`list_apps`、`get_window_state`、`zoom`、`get_screen_size`、
  `get_cursor_position`、`click`、`double_click`、`right_click`、`move_cursor`、`drag`、`scroll`、
  `type_text`、`press_key`、`hotkey`、`launch_app`、`bring_to_front`、`kill_app`），卡片 id 为
  `tool.cua.<name>`；`names` 之外的工具不构建。同一个 `CuaDesktop` 在这些工具间共享后端与 zoom 原点。
  工具实例按代理 card id 限定注册（`<name>_<owner_id>`），不同代理的同名工具互不覆盖。
- **感知与坐标**：只有截图。`get_window_state(pid, window_id)` 先把窗口置前，再截取该窗口边界，
  以 `data["multimodal"]` 图像项返回（`type="image"`、PNG data URL）。带 `pid` 的指针类工具，其
  `x,y` 是该窗口最新截图中的像素（左上角为原点），先把窗口置前再换算为屏幕坐标；无法置前时返回 ❌
  且不发送任何输入。不带 `pid` 时 `x,y` 为屏幕绝对坐标。`from_zoom=true` 时 `x,y` 是该 pid 最近一次
  `zoom` 图像中的像素。
- **结果文本**：动作成功的回复以 `✅` 开头；不能执行时返回 `ToolOutput(success=False)`，`error`
  以 `❌` 开头。后端抛出的任何异常都转成 ❌ 回复。cua rails 只读这段文本（`S_18`）。
- **后端**：`backend.create_desktop_backend()` 在 Windows 返回 `WindowsBackend`（ctypes Win32 +
  pyautogui），在 Linux 返回 `X11Backend`（python-xlib + EWMH + pyautogui，需要 `DISPLAY`），其他平台
  抛 `COMPONENT_TOOL_INIT_FAILED`。后端在第一次工具调用时才构建，pyautogui 在构建时才导入，所以构建
  工具与导入模块都不触碰显示器。输入始终是前台真实输入；所有阻塞调用经 `asyncio.to_thread` 执行。
- **依赖**：`cua` extra（`pyautogui`、`psutil`；Windows 上 `pygetwindow`，Linux 上 `python-xlib`）。
