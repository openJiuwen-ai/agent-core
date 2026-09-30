# 工具结果面向模型的纯文本渲染

## 元信息

| 项 | 值 |
|---|---|
| 类型 | feature |
| 日期 | 2026-09-16 |
| 范围 | `openjiuwen/core/foundation/tool/`、`openjiuwen/core/single_agent/ability_manager.py`、`openjiuwen/harness/tools/`、`openjiuwen/harness/personal_context/file_tools.py`、`openjiuwen/harness/rails/progressive_tool_rail.py`、`openjiuwen/agent_teams/`（工具与外部协议出口）、`openjiuwen/agent_evolving/tools/` |
| 测试基线 | 见「验证」 |
| 关联 spec | `S_05_tools-contract.md`；`agent_teams/docs/specs/S_08_team-tools-contract.md` |
| Refs | #1565 |

## 背景

工具返回结构化的 `ToolOutput(success, data, error, ...)`，这个设计本身没问题，但模型读到的
文本长期由 `AbilityManager._build_tool_message_content` 鸭子类型拼出来：

- `data` 有 `content` 键才按原文输出；否则回落 `str(result)`——整个 `ToolOutput` 的 pydantic
  repr（`success=True data={...} error=None extracted_content=None ...`）直接进上下文。
- 绝大多数 harness 工具的 `data` 没有 `content`：`write_file` 把 `original_file`（旧文件全文）
  送给模型，`glob` / `list_files` / `grep(files_with_matches)` / `code` / memory / subagent /
  tool_search 全是 repr；`tool_call` 包装器把目标结果嵌套进 repr；MCP 工具是 `{'result': ...}`。
- agent_teams 另起一套：`TeamTool.map_result` + `MappedToolOutput.__str__` 的 hack，靠 ability
  层恰好走 `str(result)` 才生效；而失败带 `error`、或 `data` 恰有 `content`（`view_task` get）
  时又被前两条分支截走，映射文本根本到不了模型。外部 MCP / CLI 出口依赖 `str(invoke())`。

实现参差不齐，模型看到的往往是结构化噪声。

## 数据结构

```
Tool.invoke() ──► ToolOutput(success, data, error)          ← 结构化，留给 rail/事件/日志
                    │
AbilityManager      ▼
  _render_tool_result(tool, tool_call, result)
    = tool.render_for_llm(result)   (异常 → 记录并回落 Tool.render_for_llm 默认实现)
                    │
                    ▼
            ToolMessage.content                              ← 纯文本，只给模型
```

- `ToolOutput` 从 `harness/tools/base_tool.py` 下沉到 `core/foundation/tool/schema.py`（与
  `ToolTimeoutResult` 同处），harness 路径再导出。core 的默认渲染因此能按类型处理，不再
  `getattr` 鸭子类型。`McpToolResult` 保持独立模型，由 `MCPTool.render_for_llm` 自行渲染。
- `RelayedToolOutput`（`tools/tool_discovery/tool_call.py`）：`tool_call` 包装器的结果，目标工具
  已渲染的文本放私有属性，结构化字段与改动前一致。
- `core/foundation/tool/base.py`：`Tool.render_for_llm`（默认实现）、`render_tool_output`
  （ToolOutput 默认规则）、`render_payload_text`（`content` / 字符串 / JSON 兜底）。
- `harness/tools/base_tool.py`：`render_fields`（扁平记录 → `key: value`，跳过空值）。
- `LocalFunction(card, func, *, render=None)`：函数式工具不子类化即可定制渲染。

默认规则：成功取 `data["content"]`（字符串 `data` 直接用，其余无 `content` 载荷序列化为 JSON），
失败取 `error`（为空回落载荷），空结果给占位文本；非 `ToolOutput` 结果为 `str(output)`。

## 决策

1. **渲染方法挂在 core `Tool` 上**：`AbilityManager` 在 core，拿到的是 `Tool` 实例；方法放在
   harness 基类会逼 core 做 `hasattr` 判断。工具覆写即定制，结构化结果原样保留。
2. **下沉 `ToolOutput` 到 core**：默认实现要按类型判断；沿用 `ToolTimeoutResult` 的先例放在
   `core/foundation/tool/schema.py`。harness 的 `base_tool.py` 保留为再导出（`openjiuwen.harness.tools.ToolOutput`
   是公开 API）。
3. **逐工具补渲染**，把结构化载荷变成模型需要的文本，要点：
   - `write_file` / `edit_file` / `coding_memory_edit` 只报结果，不回显旧文件或整份新文件。
   - `glob` / `list_files` / `grep` 输出路径行；`grep` 失败（rg exit 2）保留部分匹配、截断时提示
     `offset`；个人上下文的受限 grep 复用同一个 `render_grep_output`。
   - `code` 按终端形态拼 stdout / stderr / 非零退出码；bash / powershell 后台启动报 pid。
   - `task_tool` 渲染子代理答案；browser 任务在答案后附 `browser_orchestration` JSON——工具描述
     要求模型据 `resume_task_id` / `retryable` / `browser_result` 决策，这些字段不能丢。
   - `tool_search` 的参数 schema 保持精确 JSON（模型要照它调用 `tool_call`）。
   - `browser_recall_offload` 在片段前加分页头，`next_offset` 可见。
   - todo / goal 返回裸 dict：只覆写渲染，不改返回形态（CLI todo 视图解析其 `str()`）。
   - MCP 工具渲染 `{"result": value}` 里的 value；`BaseEvolutionTool` 失败时在错误后附载荷
     （部分成功的状态、计数、重试 id 只在载荷里）。
4. **`tool_call` 包装器透传目标已渲染文本**：嵌套执行时目标工具已经过自己的
   `render_for_llm` 与 AFTER_TOOL_CALL rail 改写，包装器把该文本交给模型，不再把目标结构化
   结果二次渲染；文本走 `RelayedToolOutput` 私有属性，结构化结果（`{"name", "result"}`、原
   `error`）不变。
5. **渲染不得改变流式输出的结构化工具结果**：上层服务依据 `ToolTrackingRail` / native harness
   `_ObservationRail` 流出的 `tool_result` 做判断与展示，两者读结构化结果而非 `ToolMessage`。
   改动前后对同一输入逐字节比对两路 payload：普通工具、MCP 多模态结果、`tool_call` 成功 / 失败
   均一致。
6. **流式结果同时带结构化内容与渲染文本（独立字段）**：展示与会话恢复需要模型实际读到的文本，
   但原结构的解析方（大量代码按 repr 字符串解析 `result`）不能动。所以原字段一律不变，新增
   `rendered_result`：`ToolTrackingRail`、`_ObservationRail`（并带入 `ItemLifecycleEvent.data` /
   `ContentBlock.data`）、`HarnessIOAdapter` 的 `tool_result` 块都输出它。取值规则只有一条
   （`resolve_tool_message` / `resolve_tool_result_text`），`AbilityManager.execute` 的异常分支
   也复用它，异常路径（AFTER_TOOL_CALL 时 `tool_msg` 尚未生成）同样有文本。展示侧（CLI 渲染器、
   subagent activity / transcript）优先读 `rendered_result`，不再用 `str(tool_result)`。
7. **`structured_result` 暂不输出**：结构化数据迁移要等 web / TUI 等 UI 改为读结构化数据；届时
   字符串兼容字段（如 `ToolTrackingRail` 的 `tool_result`）与基于其解析的逻辑全链路移除。代码中
   在兼容字段处写明了这一计划。
8. **流式 rail 必须晚于改写 `tool_msg` 的 rail**：core 内所有改写方（security 90、personal context
   回调 100、browser runtime 50、mobile skill 35 等）优先级都高于流式 rail 的 5，`rendered_result`
   即最终文本；上层服务自己的流式 rail 需按同一顺序约束调整。
9. **agent_teams 统一到同一机制**：`map_result` 重命名为 `render_for_llm`，删除
   `MappedToolOutput` 与工厂包装器里的映射（包装器只剩日志）；`TeamTool` 不再有自带默认
   （唯一依赖它的 `WorkspaceMetaTool` 从未被包装，实际从未生效）。team MCP server、Claude SDK
   MCP、skill CLI、被动成员执行器改为调用 `tool.render_for_llm(result)`。
10. **渲染异常回落默认渲染并记录日志**：工具已执行、可能有副作用，渲染 bug 不能把完成的调用
   变成执行错误进而触发重试。
11. **浏览器 runtime 工具保持默认 JSON**：`BrowserRuntimeRail` 会 `json.loads` 其消息内容做
   计数改写与超长压缩，它们的消息本就是结构化观察。

## 拒绝的方案

1. **保留鸭子类型的 `_build_tool_message_content`，只给缺 `content` 的工具补 `data["content"]`**：
   把展示文本塞进数据载荷，数据与展示耦合；无法表达"失败时也要附部分结果"这类按工具定制的
   规则，也收编不了 agent_teams 的 `map_result`。
2. **渲染方法放 harness 基类（新增 `HarnessTool`）**：core 的 `AbilityManager` 只能 `hasattr`
   探测；约 70 个工具要改继承；LocalFunction / MCP 等 core 工具覆盖不到。
3. **保留 `MappedToolOutput.__str__`**：依赖 ability 层"恰好"走 `str(result)` 的实现细节，
   新默认渲染不再调用 `__str__`，留着就是死代码加误导。
4. **把 todo / goal 的返回改成 `ToolOutput`**：CLI todo 视图正则解析 `str(tool_result)`，改返回
   形态会破坏 UI，与本次目标无关。
5. **非 `ToolOutput` 结果也统一 JSON 化**：会改变 LocalFunction / RestfulApi / workflow 等所有
   非 harness 调用方的既有文本，范围失控；保持 `str(output)`。
6. **team 工具经工厂返回带 `__str__` 的子类以保持展示兼容**：展示应读专门的渲染文本字段，
   而不是借 `__str__` 把模型文本塞进结构化结果的字符串形式；历史恢复同理应存结构化结果与
   渲染文本。
7. **`structured_result` 放进 `raw_output` 或截断 JSON 输出**：`raw_output` 已有展示与计划标记
   语义，混用会改变消费方行为；截断的 JSON 破坏结构，不如暂不输出。
8. **`tool_call` 把渲染文本写进 `data["content"]` / 覆盖 `error`**：最初的实现如此，导致渲染
   文本泄漏进流式输出的结构化结果，改为私有属性透传。
9. **`McpToolResult` 继承 `ToolOutput`**：能省掉 `MCPTool` 的一个分支，但序列化多出
   `extracted_content` 等三个字段，改变流式 payload，已回退。
10. **个人上下文包装工具用私有 `LocalFunction` 子类**：违反 PersonalContext 封闭类清单约束
   （`test_closed_class_inventory`），改为给 `LocalFunction` 加显式 `render` 关键字参数。

## 验证

- 新增：`tests/unit_tests/core/foundation/tool/test_tool_render_for_llm.py`（默认规则、JSON 兜底、
  占位、McpToolResult、MCPTool、LocalFunction render）、
  `tests/unit_tests/core/single_agent/test_ability_manager_render.py`（ToolMessage 走工具渲染、
  结构化结果不变、渲染异常回落）、`tests/unit_tests/harness/tools/test_tool_render_for_llm.py`
  （逐工具渲染）、`tests/unit_tests/harness/personal_context/test_file_tools_render.py`、
  `tests/unit_tests/agent_teams/tools/test_tool_factory.py`、被动执行器 `map_output`、
  `tool_call` 渲染透传且结构化结果不含渲染文本、`resolve_tool_result_text`（正常 / 异常 / 非文本
  内容）、`ToolTrackingRail` / `_ObservationRail` / `_consume_chunk` / `HarnessIOAdapter` 输出
  `rendered_result`、CLI 渲染器与 subagent activity / transcript 优先展示 `rendered_result`。
- 回归：`tests/unit_tests/{core,harness,agent_teams,agent,agent_evolving,rsi,cli,multi_agent,harness_providers,extensions}`
  全量运行；剩余失败 `core/sys_operation/sandbox/mock/test_local_provider.py::test_local_and_aio_providers_coexist`
  、`harness/tools/test_grep_select_string.py` 两例（全量顺序下 WindowsPath）与
  `extensions/observability` 四例（组合运行顺序相关）在改动前的 HEAD 上同样失败，与本特性无关。
- 流式出口：对 `ToolTrackingRail` / native `_ObservationRail` 以改动前后代码分别跑同一组输入逐字节
  比对，见决策 5、6。

## 已知遗留

- `core/single_agent/legacy/react_agent.py`、`core/application/llm_agent`（LLMController）仍以
  `str(result)` / JSON dump 构造工具消息：属 legacy 兼容路径，未接入。
- cron 工具（`LocalFunction`）返回宿主后端的裸 dict / list，仍为 `str()`；`AskUserTool` 在未挂
  `AskUserRail` 时返回 `{}`。
- team 工具经工厂包装后 `tool_result` 由 `MappedToolOutput` 变为 `ToolOutput`：结构化序列化
  （`model_dump` / `to_json_safe`）不变，`str(tool_result)` 由原映射文本变为结构化 repr；展示与
  会话恢复需改读 `rendered_result`。jiuwenswarm 的 `JiuSwarmStreamEventRail`（priority 80，早于
  会追加审批提示的 `PlanApprovalRail` 76）、网关字段白名单、历史恢复与 web / TUI 展示尚未迁移，
  在 jiuwenswarm 侧跟进。
- `tool_call` 包装器把目标结果嵌套在 `data["result"]`，目标 `data["multimodal"]` 的图片不会送达
  模型（改动前即如此）。
- `LspTool` 的 formatter 对列表形态的 definition 结果抛异常后回落 repr（改动前即如此）。
