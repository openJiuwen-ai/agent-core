# 原生协议模式：直接驱动厂商的常驻全双工协议

适用：厂商 CLI 自带一个常驻的 stdio 协议，IDE 插件和官方 SDK 都是它的客户端（`codex app-server`
是典型）。优先级排在 SDK 之后：有可用的 SDK 就走 SDK 模式，底层代码最少。下列情况才直接说协议——厂商没有
Python SDK、SDK 缺关键能力（某些请求没有生成类型、不暴露服务端请求）、或宿主不能接受 SDK 依赖。这时厂商的
全部能力（运行中追加输入、中断、审批、向用户提问、结构化错误、模型列表）仍都在协议里，不必退到 ACP 的公分母，
代价是传输层（进程、帧、请求配对、服务端请求应答）要自己写。

已验证实例：codex-cli 0.162.1 的 `codex app-server`，生成包 `openjiuwen-harness-codex-app-server`
（provider 名 `codex_app_server`；`codex` 是内置名，第三方包不能用）。卡片
STEER / GRACEFUL_ABORT / PERSISTENT_SESSION / CHECKPOINT / MCP_TOOLS / MODEL_DISCOVERY / MODEL_SELECTION，
宿主能力 TOOL_APPROVAL / USER_INPUT，6 项契约 e2e 与 8 项厂商 e2e 全部在真实 CLI 上通过。

## 生成骨架

没有单独的模板：原生协议各家不同，能复用的是传输层与外围。用 ACP 模式生成，再替换协议层：

```bash
python $SKILL_DIR/scripts/scaffold.py --name codex_app_server --mode acp --display "Codex App Server" \
    --binary codex --command "codex app-server" --out .
```

| 文件 | 处理 |
|---|---|
| `process.py`、`bundled.py` | 原样保留（子进程、行读写、自带 CLI 兜底与协议无关） |
| `rpc.py` | 保留单 reader / 通知按序 await / 服务端请求独立任务；先实测信封（app-server 不带 `jsonrpc` 但接受带它的），再加 `wait_closed()` 让等待 Turn 结束的协程感知进程退出 |
| `provider.py`、`tests/contract.py`、`tests/test_e2e.py`、`tests/test_registration.py` | 原样保留 |
| `config.py` | 换成厂商原生参数：审批策略、沙箱、推理强度、`-c` 配置覆盖；去掉 ACP 的"首条消息前置系统提示词"与自动批准 |
| `failure_classifier.py` | 去掉 ACP 专属的 `-32000 → auth_required`，加厂商结构化错误（Codex 的 `codexErrorInfo`） |
| `harness.py`、`mapping.py`、`tests/fake_agent.py`、`tests/test_harness.py` | 按协议重写；假运行时改名为 `fake_app_server.py` 之类，形状取自录到的真实记录 |

## 协议到 SerializedTurnHarness 的映射（以 app-server 为例）

| 协议动作 | 实现 |
|---|---|
| `start` | 启动进程（MCP 服务、特性开关作为 `-c` 覆盖项）→ `initialize` + `initialized` → `thread/resume`（checkpoint 里有 thread id 且策略允许）否则 `thread/start`；系统提示词与 MCP 命名前言放进 `developerInstructions` |
| 一个 Turn | `turn/start`（应答带原生 turn id）→ 把本 thread 的通知交给映射 → 同一 turn id 的 `turn/completed` 结束；同时等 `wait_closed()` 与超时 |
| `_steer` | `turn/steer`，`expectedTurnId` 为当前原生 turn id，`clientUserMessageId` 用回执的 `message_id` |
| `_interrupt_turn` | `turn/interrupt`；原生 turn id 还没回来时先等它 |
| `_list_models` | `model/list`；未启动时临时拉起一个进程查询，不开 thread |
| `_apply_model_selection` | 记下 `model` / `effort`，下一次 `turn/start` 带上；App Server 让它们对后续 Turn 一直生效，所以只发一次 |
| 服务端请求 | 命令 / 改文件审批 → `ToolApprovalRequest`；MCP 工具审批（elicitation）→ `ToolApprovalRequest`；`request_user_input` → `UserInputRequest`；宿主不接管时审批拒绝、提问空答案 |

## 已知坑

- **停止也会关进程**：`stop()` 先置中止再关闭会话，等待中的 Turn 会看到"连接已关闭"。先看
  `turn.abort_requested`，是就返回 ABORTED，否则才按进程退出判 FAILED。
- **覆盖项是粘性的**：Codex 的 turn 级 `model` / `effort` 对之后的 Turn 一直生效，每轮都发会掩盖宿主后来的
  切换语义；发一次即清空。是否真的切换，用厂商落盘记录（rollout 的 `turn_context.model`）判定。
- **审批默认值决定 MCP 能不能用**：`approvalPolicy: never` 时 Codex 拒绝未审批的 MCP 调用；宿主不接管审批就
  给挂载的服务设 `default_tools_approval_mode="approve"`，接管时改用 `on-request` 并把 elicitation 转给宿主。
- **elicitation 不带工具名**：MCP 审批只给服务名和参数；从本 Turn 最近一个同服务的 `mcpToolCall` item 取
  工具名与 item id，不解析提示文字。
- **代码模式改变 MCP 命名**：Codex 0.162.1 让模型在 `exec` 工具里写 `tools.mcp__<key>__<tool>(...)`；
  命名规则从 rollout 读，e2e 按 item 的 `server` / `tool` 断言，不按模型写出的代码字符串。
- **向用户提问默认关闭**：宿主声明 USER_INPUT 时才加 `features.default_mode_request_user_input=true`。
- **只把本 thread、本 Turn 的通知映射成事件**：`account/*`、`remoteControl/*` 等全局通知没有 `threadId`。
- **服务端请求必须回包**：不支持的请求回 JSON-RPC 错误（`MethodNotFound`），否则 Turn 一直等。

## e2e 必须覆盖

契约 6 项（文本、工具、多轮续接、中止、checkpoint 恢复、entry point 驱动 manifest 工厂）之外，原生模式的
卡片声明了 ACP 没有的能力，每项都要一个真实 e2e：运行中追加输入（回复里带上追加的指令）、向用户提问
（宿主答案出现在回复里）、命令审批的批准与拒绝（文件是否被创建）、MCP 在宿主不接管与接管审批两种情况下的
调用、模型列表与切换（以厂商记录判定）、技能目录被发现并使用。
