# Codex Python SDK 成员运行时

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-07-21 |
| 范围 | `external/cli_agent/codex/`、backend registry、spawn、Codex 可选依赖 |
| 测试基线 | external CLI 单测 123 passed |
| Refs | S_18，F_58 |

## 背景

F_58 首先让 Codex 成员从“每轮调用一次 `codex exec`”迁移到“每成员一个
app-server 和一个 thread”，但 Jiuwen 仍自行处理 stdio JSON-RPC、request id、notification
以及子进程关闭。`openai-codex` 已对这些细节提供类型化 Python SDK，继续保留
raw transport 会造成两套协议实现，也无法直接使用 SDK 的 thread resume、steer 和
interrupt 契约。

## 决策

1. **`codex` 是真正的 SDK backend**
   - `CodexSdkRuntime` 继承 `CliRuntimeBase`。
   - 运行时持有一个 `AsyncCodex` client、一个 Codex thread 和最多一个活跃
     `AsyncTurnHandle`。
   - Jiuwen 不再自行实现 app-server JSON-RPC；SDK 内部如何启动 app-server
     属于 SDK 实现细节。

2. **一个成员复用一个 thread**
   - 首次启动调用 `thread_start()` 并保畘 `thread.id`。
   - 后续每轮调用同一 `thread.turn(prompt)`，通过 `handle.stream()` 消费输出。
   - runtime 收到已保存 `thread_id` 时调用 `thread_resume(thread_id)`。
   - 每个 `spawn_external_cli` 都创建独立 runtime，不同角色不共享 thread。

3. **交互语义直接映射 SDK handle**
   - 当前 turn 的紧急消息调用 `handle.steer()`。
   - 普通在途消息排队，当前 turn 结束后在同一 thread 上开新 turn。
   - abort / shutdown 调用 `handle.interrupt()`；关闭调用 `AsyncCodex.close()`，并保证幂等。

4. **SDK 事件只在 Codex runtime 内转换**
   - agent message delta → `llm_output`。
   - reasoning delta → `llm_reasoning`。
   - MCP / dynamic / command / file-change item → `tool_call` / `tool_result`。
   - 非重试错误和 failed turn 上抛为 runtime 错误。

5. **配置、MCP 与权限边界**
   - `openai-codex>=0.144.4` 是可选 extra，只在构建 Codex 成员时延迟导入。
   - 团队 MCP 以 `CodexConfig.config_overrides` 的 `mcp_servers.<id>.*` 注入。
   - 角色提示词经 `developer_instructions` 传入 thread 启动/恢复选项。
   - 不硬编码 `approval_mode`，不设置 `sandbox`，也不在 Jiuwen 中自动批准
     app-server 请求。

## 拒绝的方案

- **保留 raw app-server 并只改名为 SDK**：分类变更不等于 SDK 接入，无法消除自维护协议的风险。
- **每轮新建 `AsyncCodex`**：会丢失成员跨任务上下文，也无法精确 steer 活跃 turn。
- **多个成员共享一个 thread**：会泄露私有角色提示词与工作上下文。
- **默认 `danger-full-access` 或无审批模式**：超出 Jiuwen external backend 应自动获得的权限范围。

## 验证

- 可选依赖延迟导入，未安装时返回 Codex 专属配置错误。
- fake SDK 验证 thread start/resume、多 turn 复用、事件转换、steer、interrupt 和幂等关闭。
- MCP config override 验证 command / args / join env / startup timeout / required。
- external CLI 单测：123 passed。

## 已知遗留

- `CodexSdkRuntime` 已支持使用传入的 `thread_id` 执行 `thread_resume()`，但
  spawn/TeamAgent checkpoint 尚未持久化和回填该 id；冷重建仍会建新 thread。
- 尚需真实 Codex 环境 E2E 验收 MCP 可见性、团队任务工具调用和 pause/resume。
- Codex SDK 当前是本地 app-server subprocess 模式，没有实现 SSH transport。
