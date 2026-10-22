# 能力真值表：每一项都要有证据

卡片（`HarnessCard`）是静态类属性，宿主据此决定发什么命令。**只声明真实运行端到端证明过的能力**；
未声明的命令由基类抛 `UnsupportedHarnessCapabilityError`。声明了却做不到，比不声明危险得多：
团队层会据此发 abort / steer / resume，失败要么静默要么卡死。

## Harness 能力（`capabilities`）

| 能力 | 要证明什么 | 实现钩子 | 证明它的测试 |
|---|---|---|---|
| `STEER` | 运行中追加的输入进入**当前** Turn 并被模型看到 | `_steer(turn, content, message_id=...)`；SDK handle 未就绪时排队（见 Codex `_pending_steers`） | `contract.run_steer_turn` |
| `GRACEFUL_ABORT` | 发出取消后 Turn 以 ABORTED 收口，会话仍可继续下一轮 | `_interrupt_turn`（ACP `session/cancel`、SDK `interrupt()`） | `run_abort_turn`（中止后再跑一轮 FINISHED） |
| `FORCE_ABORT` | 立即终止（杀进程也算），会话仍可继续 | `_interrupt_turn` 终止进程 | 同上 |
| `PERSISTENT_SESSION` | 同一 cycle 内后一轮记得前一轮 | 常驻进程，或每轮带会话 id | e2e 记忆题：第一轮让它记住随机暗号，第二轮问回 |
| `CHECKPOINT` | `export_checkpoint` 的数据能在**新进程**里恢复会话 | `_publish_checkpoint` + `_open_session` 读 `_restored_checkpoint_data` | `run_resume_turns` |
| `MCP_TOOLS` | `HarnessContext.mcp_servers` 被挂载且工具可调用 | 会话参数 / CLI 参数 / 配置文件 | e2e 调用 stdio FastMCP fixture 工具（见 testing.md） |
| `NATIVE_TOOLS` | 宿主工具网关（`context.tools`）的工具能被厂商调用 | 动态工具（Codex `item/tool/call`） | 专门 e2e |
| `MODEL_SELECTION` | `set_model` 在 idle 时立即生效、运行中延后到下一轮生效，跨重连保持 | `_apply_model_selection` | 单测 + e2e |
| `MODEL_DISCOVERY` | `list_models` 返回真实目录 | `_list_models` | 单测 + e2e |
| `ENDPOINT_MODELS` | `model_config` 能把宿主端点（`ModelEndpoint` 的 `api_base` / `api_key`）映射进配置 | provider 的 `model_config` | 单测（模板注册测试按此分支） |
| `AUTH_FALLBACK` | 回合以 `auth_required` 失败时，在 `model_config(..., fallback=)` 给的回退模型上恢复同一会话并重放；流程在骨架里，宿主先确认 | `_auth_fallback_payload` + `_reconnect_for_auth` (+ `_on_auth_endpoint_changed`) | 单测 + 真实 CLI（无效密钥主端点 → 回退端点） |
| `PAUSE_RESUME` | 暂停后原地续跑 | 基类默认不支持 | — |
| `HOOKS` | 宿主 hook 改写/拒绝工具调用 | — | — |

模板默认值：

- **ACP**：`GRACEFUL_ABORT`、`MCP_TOOLS`（ACP 规范强制）、`PERSISTENT_SESSION` + `CHECKPOINT`
  （要求 `agentCapabilities.loadSession`，否则删掉这两项）。填了 `ENDPOINT_ENV` 时自动加 `ENDPOINT_MODELS`，
  且仅在同时保留 `PERSISTENT_SESSION` 时加 `AUTH_FALLBACK`（`_with_auth_fallback`：回退要把会话 load 进重启的
  agent）。
- **一次性 CLI**：`FORCE_ABORT`（adapter 自己持有进程，构造上成立）。会话能靠 `SESSION_ARGS`
  延续时再加 `PERSISTENT_SESSION` + `CHECKPOINT`。
- **SDK**：空集。钩子已接好，逐项证明后再加。

## 宿主能力（`optional_host_capabilities` / `required_host_capabilities`）

`validate_host` 只检查 **required** 是否满足；optional 是声明性的，宿主多给不报错。
但**行为必须随宿主声明切换**：

| 宿主能力 | provider 的义务 |
|---|---|
| `TOOL_APPROVAL` | 有声明且有 `interactions` 才把审批路由给宿主；否则按配置自动批准/拒绝。Claude 需把 `permission_mode` 切回 `default` 才会触发回调。 |
| `USER_INPUT` | 厂商的"向用户提问"变成 `UserInputRequest`，Turn 保持 RUNNING；无宿主时拒绝或回空答案，不能卡死。 |
| `CHECKPOINT_SINK` | 基类 `_publish_checkpoint` 自动推送；provider 只管在会话激活 / id 变化时调用。 |
| `MCP_SERVERS` | 挂载 `context.mcp_servers`；不支持的传输（`IN_PROCESS`、无 http 能力时的 HTTP）在 `start` 抛 Unsupported。 |
| `PROVIDER_INTERACTION` | 改变 provider 持久身份的切换（认证 fallback）先 `_confirm_provider_extension` 再提交。 |
| `MODEL_REQUEST_OBSERVATION` | 产出 `ModelRequestEvent`，轨迹的 Model / Input 泳道全靠它：ACP 模板默认从流推断（只有输出侧），有厂商请求记录时在 `_observe_request` 补全输入。规则见 [observation.md](observation.md)。 |

团队成员运行时（`ExternalHarnessMemberRuntime` + `HarnessIOAdapter`）总会声明 `USER_INPUT`，并在
有成员 session 时声明 `CHECKPOINT_SINK`；新 provider 必须在这些宿主能力存在时仍能正常 `start`。

## 判定流程

1. 列出候选能力（来自 recon 的必答问题）。
2. 对每项写 e2e（`tests/contract.py` 里已有的直接复用），在真实 CLI / SDK 上跑。
3. 通过的留在卡片，失败或无法证明的删掉，并在包内 `AGENTS.md`"验证过的厂商事实"里写明原因。
4. 生成的单测已按卡片自动切换（`skipif(card.supports(...))` 成对出现：声明时测行为，未声明时测拒绝）。
