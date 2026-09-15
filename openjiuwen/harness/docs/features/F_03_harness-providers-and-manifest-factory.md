# Harness Providers、DeepAgent IO Adapter 与 Manifest 工厂

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-09-09 |
| 范围 | `openjiuwen/harness_providers/`（`base` / `stream` / `io_adapter` / `factory` / `native` / `claudecode` / `codex` / `dsh`）、`openjiuwen/harness/resources/extension_resolver.py`（`render_agent_template_system_prompt`） |
| 测试基线 | `tests/unit_tests/harness_providers`（46 通过）；`tests/system_tests/harness_providers`：Claude Code 8/8、Codex 7/7、DSH 5/5、native 7/7（`API_BASE/API_KEY/MODEL_NAME` 指向 DeepSeek `deepseek-v4-flash`）通过 |
| Refs | #751 |

## 背景

`openjiuwen.harness_protocol` 是 provider-neutral 的三方 Harness SPI，但 harness 侧没有：
（1）把协议输出翻译回 DeepAgent 输入输出契约的适配层；（2）以 DeepAgent 自身作为协议实现；
（3）从 harness 专家定义的 AgentTemplate manifest 创建协议 harness 的入口。manifest 面向
DeepAgent 设计，`tools` / `rails` / `subagents` / `skills` 依赖框架，三方 CLI 无法承载。

## 决策

1. **`SerializedTurnHarness` 骨架**（`base.py`）：所有内置 provider 共享一套串行 Turn 状态机、
   有界 BLOCK 事件流（`stream.py`）、interaction 记账与 checkpoint 发布；provider 只译 SDK。
2. **`DeepAgentHarness`**（`native/`）：一个外部 Turn = 一次 `attach_output` + `send_input` 到输出流
   结束；`_ObservationRail` 把工具生命周期写成 `tool_call` / `tool_result` chunk 以产出
   `ItemLifecycleEvent`；ask-user 中断保持 Turn 打开，经 `UserInputRequest` 取得宿主答案后用
   `InteractiveInput` 继续；无宿主 handler 时以 `stop_reason="interrupt"` 结束并在 `provider_data`
   带 `pending_interrupt_ids`，宿主可用 `metadata.kind=interactive_input` 的输入恢复。声明
   STEER（`send_input(mode=STEER)`）与 FORCE_ABORT（`cancel_round`）。`_open_session` 在 `agent.start`
   前显式 `ensure_initialized()`：交互循环不会自行初始化 agent，否则 pending rails 不注册、cwd
   ContextVar 不按 `cwd` 初始化（native e2e 发现）。文件/shell 工具由 manifest 的 `core.sys_operation`
   rail 提供，spec build 不默认挂载。
3. **`HarnessIOAdapter`**（`io_adapter.py`）：协议 ⇄ DeepAgent I/O；自身实现
   `HarnessInteractionHandler`，`UserInputRequest` → `__interaction__` chunk → `InteractiveInput`
   应答；工具审批默认自动放行。
4. **`create_harness` / `build_harness_context`**（`factory.py`）：`provider` 参数取
   `native | claudecode | codex | dsh`；`native` 热加载整份 `AgentTemplateSpec`（card / model
   缺省从 template 补），三方 provider 只取模型端点并拒绝 DeepAgent-only 段；context 构建把
   persona sections 按 priority 渲染为 `system_prompt`（新增
   `render_agent_template_system_prompt`），manifest MCP → `McpServerConfig`。
5. **Claude Code / Codex / DSH provider** 的映射与限制见 agent_teams `F_96`；DSH 配置字段更新为当前
   安装 SDK 的 `DeepSeekHarnessConfig`（`dsh_home` / `profile` / `dsh_bin` / `patches` 等）。

## 拒绝的方案

- **让 `NativeHarness`（agent_teams）充当 native provider**：它是 team 包内的 DeepAgent 子类，harness
  侧不能依赖 agent_teams；DeepAgent 自身的 session-scoped 交互循环已足以承载协议。
- **用轮询 `agent.phase` 判定 Turn 结束**：输出流结束（`_close_idle_output_if_finished`）是 DeepAgent
  的权威边界，且 DeepAgent 内部的任务循环续轮本就属于同一外部 Turn。
- **为三方 provider 静默裁剪 manifest**：见 `F_96`。

## 验证

`tests/unit_tests/harness_providers/`（假 SDK）与 `tests/system_tests/harness_providers/`（真实
CLI，共享 `_contract.py`）。native e2e 使用环境变量配置的真实模型。

## 已知遗留

- `DeepAgentHarness` 未声明 GRACEFUL_ABORT / PAUSE_RESUME（DeepAgent 交互 API 只有 `cancel_round`）。
- provider entry point discovery 未做；`create_harness` 只认四个内置名字。
