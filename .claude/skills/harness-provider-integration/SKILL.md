---
name: harness-provider-integration
description: Turn a locally installed third-party agent harness (an agent CLI or an agent Python SDK) into a pip-installable openjiuwen harness provider package that openjiuwen discovers through the `openjiuwen.harness_providers` entry point. Covers, in order of preference, vendor Python SDKs (least adapter code), a vendor's native full-duplex protocol driven directly (e.g. `codex app-server` JSON-RPC), native one-shot CLIs with structured stdout and session resume, and CLIs speaking the Agent Client Protocol (ACP, e.g. `opencode acp`, `kimi acp`, gemini-cli/qwen-code/goose). Ships a scaffold script whose generated package, fake-runtime unit tests, registration test and e2e suite pass immediately, and a build script producing the wheel plus a sha256 runtime manifest for frozen hosts that download, extract and append it to sys.path, optionally bundling the vendor CLI into platform wheels as a last-resort fallback behind the CLI on PATH (like claude-agent-sdk / openai-codex-cli-bin), plus recon, capability, mapping, testing and packaging rules distilled from openjiuwen's claudecode/codex/dsh providers. Works with openjiuwen installed from PyPI or from a clone of the agent-core develop branch (source dependency on the provider SPI). Use whenever someone wants to plug an agent CLI or agent SDK into openjiuwen as a harness provider (接入/扩展三方 Agent Harness、CLI agent、agent SDK 到 openjiuwen), or asks how to write such a provider.
---

# 三方 Agent Harness 接入 openjiuwen（provider 包）

把用户本机的三方编码 agent（CLI 或 Python SDK）接成 openjiuwen 的 `HarnessProtocol` provider，
产物是一个**独立的 pip 包** `openjiuwen-harness-<name>`：

- 包内实现 provider（config / provider / harness / mapping / failure_classifier [+ process / rpc / bundled]）；
- `pyproject.toml` 声明 entry point `openjiuwen.harness_providers`；
- 构建 wheel 并产出 `runtime-manifest.json`，供冻结版宿主（jiuwenswarm 桌面应用）按清单下载、校验、
  解压、挂 `sys.path` 动态加载；CLI 模式**可选**把厂商 CLI 打进按平台区分的 wheel（`_bundled/bin/<cli>`，
  与 claude-agent-sdk、openai-codex-cli-bin 同一做法），运行时 PATH 上的 CLI 优先，自带的只作兜底；
- 安装后 openjiuwen 无需改代码即可解析：`create_harness(manifest, provider="<name>")`、
  `available_providers()` 里能看到它；团队、DeepAgent IO adapter 等宿主像驱动内置的
  Claude Code / Codex / DSH 一样驱动它。

本 skill 自包含：openjiuwen 可以从 PyPI 安装，也可以克隆源码仓库安装，两种都不需要改 openjiuwen。
下文 `SKILL_DIR` 指本 skill 所在目录。

## 0. 准备环境

openjiuwen 二选一：

```bash
python -m venv .venv && . .venv/bin/activate        # Windows: .venv\Scripts\activate

# A. 已发布版本
python -m pip install -U openjiuwen pytest pytest-asyncio

# B. 源码：最新 develop 分支的 provider 接口定义（尚未发布的 SPI 改动也能用）
git clone -b develop https://gitcode.com/openJiuwen/agent-core.git
python -m pip install -e ./agent-core pytest pytest-asyncio

python -c "from openjiuwen.harness_providers import HARNESS_PROVIDER_ENTRY_POINT_GROUP"   # 失败说明 openjiuwen 不支持 entry point 发现
<agent-cli> --version                                 # 目标 CLI 已安装、已登录；SDK 模式则 pip install 厂商 SDK
```

脚手架按 openjiuwen 的来源写生成包的依赖：A 写 `openjiuwen>=<已装版本>`；B 写
`openjiuwen @ git+https://gitcode.com/openJiuwen/agent-core.git@develop`（从 git URL 装的则写 pip 记录的
URL 与 commit）。B 下安装生成包要加 `--no-deps`（测试依赖第 0 步已装），否则 pip 会按 git 依赖重新从 gitcode
拉 openjiuwen、覆盖你的源码安装。发布到 PyPI 前用 `--openjiuwen-requirement 'openjiuwen>=<首个支持的正式版>'`
重新生成或手改 `pyproject.toml`：PyPI 不接受 URL 依赖。

需要读 openjiuwen 实现时：A 读已安装的源码（pip 包不带 md 文档）；B 直接读克隆目录，
`openjiuwen/harness_providers/AGENTS.md` 与 `openjiuwen/harness/docs/specs/S_19_harness-providers.md`
是 provider 接口的设计说明。

```bash
python -c "import openjiuwen.harness_providers as p, os; print(os.path.dirname(p.__file__))"
```

该目录下 `base.py`（`SerializedTurnHarness` 骨架）、`factory.py`（provider 解析与 manifest 映射）、
`skills.py`、`mcp_naming.py`，以及内置 provider `codex/`（异步 SDK）、`dsh/`（同步 SDK + 回调）、
`claudecode/`（审批交互与 Turn 边界）是最好的先例。

## 1. 取证（不可跳过）

按 [references/recon.md](references/recon.md) 回答 15 个必答问题，把真实运行记录落盘到临时目录。
没有原始记录就没有映射：映射之后的数据只能证明映射本身。

## 2. 选接入模式

按优先级从高到低：

| 优先级 | 厂商提供 | 模式 | 模板现状 |
|---|---|---|---|
| 优先级 | 厂商提供 | 模式 | 模板现状 |
|---|---|---|---|
| 1 | Python SDK（流式 API、中断、会话、服务端请求回调） | `sdk` | 调用集中在 `_sdk_*`，按真实 API 重写；内置 claudecode / codex / dsh 都走这条 |
| 2 | 原生常驻全双工协议（`codex app-server` 这类 stdio JSON-RPC：运行中追加输入、中断、审批与提问都在一条连接上） | 原生：`--mode acp` 生成骨架，按 [mode-native.md](references/mode-native.md) 重写协议层 | codex app-server 实例通过 6 项契约 + 8 项厂商 e2e |
| 3 | 原生一次性模式：结构化 stdout + 会话 id 续接（`run --format json`、`exec --json` + `resume`） | `oneshot` | 映射与 argv 需按厂商记录重写 |
| 4 | `<cli> acp` / `--acp` 等 ACP stdio 入口 | `acp` | 标准协议，常量 + 夹具即可；opencode、kimi-code 实测通过 |

为什么这样排：

- **SDK 最优先**：进程管理、帧格式、请求 / 响应配对、类型化的通知与错误都由厂商维护，provider 只写映射
  与少量调用，底层代码最少，厂商协议升级也跟着 SDK 走。代价是多一个依赖：SDK 自带的 CLI 可能落后于本机
  （配置要允许指定 CLI 路径），生成类型可能缺某些请求（Codex 的 `request_user_input` 要从原始参数里取）。
  厂商没有 SDK、SDK 缺关键能力、或宿主不能接受这个依赖时，再往下选。
- **原生协议优先于 ACP**：原生协议给出厂商的全部能力与结构化数据（运行中追加输入、审批、向用户提问、
  结构化错误码、模型列表），只是传输层要自己写；ACP 是各厂商的公分母，实现参差不齐（opencode 1.17 拒绝
  会话内切模型，kimi-code 的额度耗尽与认证失败同一个错误码、无法区分）。
- **全双工常驻优先于单次 Turn**：单次 Turn 每轮冷启动，失去运行中追加输入、审批、向用户提问和温和中断，
  但仍是厂商原生的结构化输出，并靠会话 id 续接，所以排在 ACP 前面。

细节：[mode-sdk.md](references/mode-sdk.md) · [mode-native.md](references/mode-native.md) ·
[mode-oneshot.md](references/mode-oneshot.md) · [mode-acp.md](references/mode-acp.md)。

## 3. 生成包并立即跑绿

用装了 openjiuwen 的那个 Python 运行脚手架（它据此写 openjiuwen 依赖下限）：

```bash
python $SKILL_DIR/scripts/scaffold.py --name opencode --mode acp --display OpenCode --binary opencode --command "opencode acp" --out .
python $SKILL_DIR/scripts/scaffold.py --name kimi --mode oneshot --binary kimi --command "kimi --output-format stream-json" --out .
python $SKILL_DIR/scripts/scaffold.py --name acme --mode sdk --sdk-module acme_agent --sdk-dist acme-agent-sdk --out .

cd openjiuwen-harness-<name>
python -m pip install -e ".[test]"          # entry point 是包元数据，必须安装后 openjiuwen 才看得见
                                             # openjiuwen 来自源码克隆时改用：python -m pip install -e . --no-deps
python -m pytest -q                          # 单测 + 注册测试必须全绿；运行时缺失时 e2e 自动跳过
```

`--dry-run` 先看计划；脚本拒绝覆盖已有目录。`<name>` 是 `create_harness` 用的 provider 名
（snake_case，不能与内置的 `native / native_v2 / claudecode / codex / dsh` 重名），包名
`openjiuwen-harness-<name>`、模块 `openjiuwen_harness_<name>`。生成后测试不全绿就先查环境再往下走。

## 4. 厂商化：关闭所有 `SCAFFOLD(<topic>)` 标记

`python $SKILL_DIR/scripts/scaffold.py --check openjiuwen-harness-<name>` 列出剩余标记，结束时必须为 0。

| 主题 | 做什么 | 参考 |
|---|---|---|
| `capabilities` | 卡片只留 e2e 证明过的能力 | [capabilities.md](references/capabilities.md) |
| `mapping` / `turn-end` | 用真实记录重写 `consume`；确认结束信号 | [mapping.md](references/mapping.md) |
| `sdk-calls` / `config` / `mcp-config` | 按真实 SDK API 改写调用与配置 | mode-sdk.md |
| `argv` / `prompt-delivery` / `system-prompt` | 一次性 CLI 的命令行形态 | mode-oneshot.md |
| `model` / `mcp-naming` | 模型启动参数或会话内切换；模型侧 MCP 工具名规则 | mode-acp.md、recon.md |
| `skills-dir` | CLI 的项目级技能发现目录（没有就保持 `None`，配置技能会被拒绝） | recon.md |
| `failure-codes` / `failure-exceptions` | 厂商错误码 / 异常类型到共享类别 | mapping.md |
| `fixtures` | 假运行时改成真实记录的形状 | [testing.md](references/testing.md) |
| `observation` | 模型请求上报：流推断之外，用厂商请求记录补全输入（轨迹 Input / Model 泳道） | [observation.md](references/observation.md) |
| `agents-md` | 填包内 `AGENTS.md`"验证过的厂商事实" | — |

## 5. e2e 证明能力

`tests/test_e2e.py` 复用包内自带的 `tests/contract.py`（与 openjiuwen 内置 provider 的 e2e 契约同源），
按卡片能力自动选用例，并验证"装好的包能经 entry point 驱动 manifest 工厂"。真实运行时上全部通过，
卡片才算定稿。见 [testing.md](references/testing.md)。

## 6. 构建 wheel 与运行时清单（是否自带 CLI 由用户决定）

CLI 模式在这一步**询问用户是否把厂商 CLI 打进 wheel**，不要替用户决定：

- **不打包**：用户自己安装 CLI（开发者、服务器部署）。构建一个纯 Python wheel，覆盖所有目标平台：

  ```bash
  python $SKILL_DIR/scripts/build_runtime.py --project ./openjiuwen-harness-<name> \
      --platform macos-arm64 --platform windows-x86_64 --base-url <发布地址前缀>
  ```

- **打包**：宿主的终端用户可能没装 CLI（桌面应用）。每个目标平台一次，用户提供该平台的 CLI 可执行：

  ```bash
  python $SKILL_DIR/scripts/build_runtime.py --project ./openjiuwen-harness-<name> \
      --platform macos-arm64 --binary <该平台的 CLI 可执行> [--helper <辅助可执行>] --base-url <发布地址前缀>
  ```

两种情况下运行时的选用顺序都是：配置的 `cli_path` → PATH 上的 CLI → 包内自带 CLI（兜底）。产物与
`runtime-manifest.json` 累积在 `dist/`；打包时脚本校验二进制确属目标平台、打平台标签，并在本机平台上
模拟冻结宿主安装做冒烟。SDK 模式用 `--extra-wheel` 带上厂商 SDK 的平台 wheel。布局、清单格式、与
jiuwenswarm 的对接、版本规则：[packaging.md](references/packaging.md)。

## 硬约束（违反即返工）

1. **能力真实**：卡片声明 = e2e 证明。做不到的命令让基类抛 Unsupported，不要用 stop/restart 模拟。
2. **厂商依赖惰性**：config / provider / 包 import 不 import SDK、不解析或启动 CLI；缺失在 `start` 报
   `HarnessError` 或 `ProviderStartupError(process_start_failed)`，且不留半开 cycle。openjiuwen 枚举
   provider 时会 import 你的 `provider.py`，这里多一个重依赖就拖慢所有宿主。
3. **失败只用共享类别**（`auth_required / quota_exceeded / rate_limited / request_rejected /
   server_unavailable / network_timeout / process_start_failed / sdk_error / unknown`），按结构化字段
   分类；stderr、凭据、环境变量、原始异常正文不进事件 / 结果 / checkpoint / 日志。
4. **一个 Turn 恰好一个 STARTED 一个终态**，由基类发；provider 只返回 `(TurnEventKind, TurnResult)`。
   宿主中止用 `interrupted_result`，自动区分 `USER_ABORT` / `HARNESS_STOP`。
5. **未知记录保留为 `ProviderEvent`**；厂商对象先 `to_json_safe`。Turn 之外的通知不是 Turn 事件。
6. **背压不绕开**：通知按序 inline await 进有界缓冲区；不加无界队列，不 fire-and-forget。
   等宿主应答（审批 / 提问）不能阻塞读通知的那条路径。
7. **MCP 工具命名声明**：挂了 MCP 就在系统提示词前置 `mcp_tool_naming_preamble`，规则取自真实记录。
8. **跨平台**：子进程用 `asyncio.create_subprocess_exec` + `shutil.which`（Windows 走 PATHEXT），
   终止走 terminate → kill；不用 `shell=True`、信号组或 POSIX 专有调用，除非两个平台都覆盖。
9. **只用 openjiuwen 的公开扩展面**：`openjiuwen.harness_protocol`，`openjiuwen.harness_providers`
   的 `base` / `jsonsafe` / `inputs` / `mcp_naming` / `skills` 与工厂注册接口。不要 import 内置
   provider 的私有模块，它们随版本变化。

## 验收清单

- [ ] `scaffold.py --check` 输出 0 个标记
- [ ] `python -m pytest` 全绿（单测、注册测试、e2e），e2e 在真实运行时上运行而非跳过
- [ ] 卡片能力与 e2e 一一对应；未声明能力有"拒绝"单测
- [ ] 宿主观测请求时轨迹有 Model 与 Tools 泳道、工具都挂在请求下；CLI 有本地请求记录时 Input 泳道也有内容
- [ ] 包内 `AGENTS.md` 写明厂商版本与每条关键事实的出处
- [ ] `pyproject.toml` 的 openjiuwen 下限不低于提供 entry point 发现的版本；SDK 模式写明厂商 SDK 依赖
- [ ] 已和用户确认是否自带 CLI；自带时每个目标平台都用 `build_runtime.py` 构建并通过冒烟，非本机平台在该平台上实测过
- [ ] `runtime-manifest.json` 的 url / mirror_url 已填为发布地址，且每个平台的产物是完整闭包

## 常见坑（来自 openjiuwen 内置 provider 的真实教训）

- SDK 自带 CLI 可能落后于本机配置的模型：配置允许指定 CLI 路径，e2e 用 PATH 上的 CLI。
- 厂商的实验特性默认关闭（Codex `request_user_input` 需 `features.default_mode_request_user_input`）：
  宿主声明对应能力时由 provider 自动打开，不要让宿主自己配。
- 模型没拿到某工具时会把问题当文本输出再反复 `sleep`：e2e 卡住先用带硬超时的探针打印每个通知。
- 提示词要点名厂商自己的工具名，模型才会稳定调用。
- 已有一个 ACP 包再接第二个 ACP 厂商时，把 `process.py` / `rpc.py` / ACP 映射抽成共享基础包，
  厂商包只留差异；不要复制第二份 ACP 客户端。
