# browser_agent（浏览器子智能体）

浏览器子智能体是一个预配置的 [`DeepAgent`](../deep_agent.md)，通过官方 Playwright MCP 服务器（`npx @playwright/mcp`）直接控制真实浏览器：导航页面、点击、输入、填写表单、提取数据。在此之上，它提供一套确定性辅助层（紧凑页面探测、批量交互、自定义动作），以及一个让浏览器会话可恢复、可判定完成状态的运行时 Rail。

通过 [`create_browser_agent`](#function-create_browser_agent) 创建，或通过 [`build_browser_agent_config`](#function-build_browser_agent_config) 声明式注册。父智能体通常通过 `TaskTool` 以 `subagent_type="browser_agent"` 派生它。

子智能体可用的工具（Playwright MCP 原语工具和注入的辅助工具）单独记录在 [`browser_tools`](../tools/browser_tools.md) 中。

## 功能概览

| 功能 | 说明 |
|---|---|
| [Playwright MCP 工具](../tools/browser_tools.md#playwright-mcp-工具) | 原语级 `browser_*` 工具（导航、点击、输入、快照、截图、执行代码等）由官方 Playwright MCP 服务器提供，创建智能体时自动注册。 |
| [能力白名单](#浏览器能力capabilities) | Playwright 工具按命名能力分组（`core`、`pdf`、`vision`、`devtools`、`config`、`network`、`storage`、`testing`）。调用方按任务选择能力；模型只能看到展开后的工具白名单。 |
| [运行时辅助工具](../tools/browser_tools.md#运行时辅助工具) | 页面探测、批量交互、自定义动作、取消和健康检查，与 Playwright 原语工具一起注入。 |
| [浏览器选项](#浏览器选项) | 无头（headless）/有头（headed）运行、浏览器引擎选择、会话持久化、视口/设备模拟、代理、输出目录。 |
| [浏览器实例隔离](#class-browserinstanceconfig) | `BrowserInstanceConfig` 为每个智能体分配独立浏览器（独立 MCP 服务器、Chrome 配置目录和 CDP 端口）；使用相同 key 的智能体有意共享同一个浏览器。 |
| [驱动模式](#驱动模式) | `managed`（启动专属本地 Chrome）、`remote`（连接已有 CDP 端点）、`extension`（通过 Playwright MCP 扩展桥驱动正在运行的浏览器）。 |
| [进度与可恢复性](#class-browserruntimerail) | `BrowserRuntimeRail` 将任务进度持久化到会话中，在下次调用时作为延续上下文注入，并将未完成的运行转换为结构化失败摘要。 |
| [Guardrails](#class-browserrunguardrails) | 步数、失败次数和超时限制，支持自动重试，以及在达到最大迭代后可选的恢复执行。 |
| [可观测性](#可观测性) | 专用日志文件 `logs/browser_agent.log` 和经过脱敏的结构化状态遥测（`[BROWSER_SUBAGENT]` 记录）。 |

---

## function create_browser_agent

```python
create_browser_agent(
    model: Model,
    *,
    card: AgentCard | None = None,
    system_prompt: str | None = None,
    tools: list[Tool | ToolCard] | None = None,
    mcps: list[McpServerConfig] | None = None,
    subagents: list[SubAgentConfig | DeepAgent] | None = None,
    rails: list[AgentRail] | None = None,
    enable_task_loop: bool = False,
    max_iterations: int = 25,
    workspace: Workspace | str | None = None,
    skills: list[str] | None = None,
    backend: Any | None = None,
    sys_operation: SysOperation | None = None,
    language: str | None = None,
    prompt_mode: str | None = None,
    settings: RuntimeSettings | None = None,
    browser_key: str | None = None,
    browser_instance: BrowserInstanceConfig | dict | None = None,
    browser_capabilities: list[str] | None = None,
    **config_kwargs,
) -> DeepAgent
```

创建浏览器子智能体。相比普通 deep agent，该工厂额外完成：

1. 将请求的 `browser_capabilities` 与可信能力目录进行校验（未知名称抛出 `ValueError` 并列出可用能力）。
2. 根据 `settings`（缺省时从模型客户端配置或环境变量解析）构建 [`BrowserAgentRuntime`](#class-browseragentruntime)。
3. 将[运行时辅助工具](../tools/browser_tools.md#运行时辅助工具)追加到调用方提供的 `tools` 之后。
4. 将 [`BrowserRuntimeRail`](#class-browserruntimerail) 追加到调用方提供的 `rails` 之后。

默认系统提示词（区分 `cn`/`en` 语言）要求智能体优先使用紧凑探测而非页面快照，只有在已知 selector 时才使用 `browser_run_code`，并且只有当页面上有证据证明任务完成时才声明完成。

**参数**:

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `model` | `Model` | *(必填)* | LLM 模型实例。当模型带有客户端配置时，其 provider / API key / base URL / 模型名同时用于浏览器运行时 |
| `card` | `Optional[AgentCard]` | `None` | 智能体身份卡。为 None 时创建 `name="browser_agent"` 的默认卡（描述随语言而定） |
| `system_prompt` | `Optional[str]` | `None` | 系统提示词。为 None 时使用对应语言的内置浏览器提示词 |
| `tools` | `Optional[List[Tool \| ToolCard]]` | `None` | 额外工具，位于注入的运行时辅助工具之前 |
| `mcps` | `Optional[List[McpServerConfig]]` | `None` | 额外 MCP 服务器配置。Playwright MCP 服务器由运行时自动注册，无需在此列出 |
| `subagents` | `Optional[List[SubAgentConfig \| DeepAgent]]` | `None` | 嵌套子智能体 |
| `rails` | `Optional[List[AgentRail]]` | `None` | 额外 Rails，位于注入的 `BrowserRuntimeRail` 之前 |
| `enable_task_loop` | `bool` | `False` | 是否启用任务循环 |
| `max_iterations` | `int` | `25` | 最大智能体迭代次数 |
| `workspace` | `Optional[str \| Workspace]` | `None` | 工作区 |
| `skills` | `Optional[List[str]]` | `None` | 技能 |
| `backend` | `Optional[Any]` | `None` | LLM 后端 |
| `sys_operation` | `Optional[SysOperation]` | `None` | 系统操作 |
| `language` | `Optional[str]` | `None` | `"cn"` 或 `"en"`；其他值回退到 `"cn"` |
| `prompt_mode` | `Optional[str]` | `None` | 提示词模式 |
| `settings` | `Optional[RuntimeSettings]` | `None` | 浏览器运行时完整覆盖（provider、API key/base、模型、MCP 配置、guardrails、实例）。缺省时从 `model` 或环境变量解析 |
| `browser_key` | `Optional[str]` | `None` | `browser_instance=BrowserInstanceConfig(key=...)` 的简写 |
| `browser_instance` | `Optional[BrowserInstanceConfig \| dict]` | `None` | 每实例浏览器身份。接受普通 dict，以便身份能以可序列化的 `factory_kwargs` 跨进程传递。缺省为旧版共享、环境驱动的浏览器 |
| `browser_capabilities` | `Optional[List[str]]` | `None` | 任务级能力名称，取自[能力目录](#浏览器能力capabilities)。`core` 始终包含。`None` 表示不做白名单限制 |
| `**config_kwargs` | `Any` | — | 转发到 `create_deep_agent()` |

**返回值**: `DeepAgent` — 已配置的浏览器子智能体。

**异常**:

- **ValueError**: `browser_capabilities` 不是字符串列表，或包含能力目录中不存在的名称。

**示例**:

```python
from openjiuwen.harness.subagents import create_browser_agent

agent = create_browser_agent(
    model,
    language="cn",
    browser_key="agent-a",              # 该智能体独享一个 managed Chrome
    browser_capabilities=["storage"],   # 只暴露 core + storage 工具
)
result = await agent.invoke({
    "query": "打开 https://books.toscrape.com，列出 Travel 分类中最便宜的 3 本书。",
    "conversation_id": "session-1",
})
```

---

## function build_browser_agent_config

```python
build_browser_agent_config(
    model: Model,
    *,
    card: AgentCard | None = None,
    system_prompt: str | None = None,
    tools: list[Tool | ToolCard] | None = None,
    mcps: list[McpServerConfig] | None = None,
    rails: list[AgentRail] | None = None,
    enable_task_loop: bool = False,
    max_iterations: int = 25,
    workspace: Workspace | str | None = None,
    skills: list[str] | None = None,
    backend: Any | None = None,
    sys_operation: SysOperation | None = None,
    language: str | None = None,
    prompt_mode: str | None = None,
    settings: RuntimeSettings | None = None,
    browser_key: str | None = None,
    browser_instance: BrowserInstanceConfig | dict | None = None,
) -> SubAgentConfig
```

构建一个 [`SubAgentConfig`](../schema/config.md)，将浏览器智能体注册为父 `DeepAgent` 的子智能体。该配置携带 `factory_name="browser_agent"`，并把解析后的 `RuntimeSettings` 存入 `factory_kwargs`；父智能体通过 `DeepAgent.create_subagent` 延迟实例化，最终回到 [`create_browser_agent`](#function-create_browser_agent)（工厂名 `"browser_runtime"` 为别名）。

参数与 `create_browser_agent` 一致，但不包含 `subagents` 和 `browser_capabilities` —— 能力在派生任务时选择，而非注册时。

**返回值**: `SubAgentConfig` — 声明式浏览器子智能体注册。

**示例**:

```python
from openjiuwen.harness.factory import create_deep_agent
from openjiuwen.harness.subagents import build_browser_agent_config

browser_cfg = build_browser_agent_config(model, browser_key="agent-a", language="cn")
main_agent = create_deep_agent(model=model, subagents=[browser_cfg])
```

运行时，主智能体通过 `TaskTool` 派生它：

```jsonc
// tool: task
{
  "subagent_type": "browser_agent",
  "task": "登录管理后台，导出本周报表为 PDF。",
  "browser_capabilities": ["pdf"]   // 可选；"core" 始终包含
}
```

`browser_agent` 的子会话 ID 是确定性的（`{parent_session}_sub_browser_agent`），因此「失败 → 修复 → 重新验证」的循环会恢复到同一个浏览器会话，已存储的进度继续生效。

---

## 浏览器能力（Capabilities）

模块：`openjiuwen.harness.tools.browser_move.playwright_runtime.browser_capabilities`

Playwright MCP 工具按可信的显式能力目录分组。匹配按工具名精确进行 —— 从不使用前缀匹配 —— 因此新引入的 Playwright 工具在其策略审查之前不会被暴露。主智能体按任务选择能力名称；解析器本身不做任何任务语义解释。

| 能力 | 工具数 | 说明 |
|---|---|---|
| `core` | 24 | 始终包含。导航、点击、输入、填写表单、选择选项、悬停、拖放、按键、标签页管理、快照与截图、执行代码、查看控制台与网络请求、处理对话框、上传文件、等待、调整窗口、关闭页面。 |
| `pdf` | 1 | 将当前页面保存为 PDF 产物。 |
| `vision` | 6 | 基于坐标的鼠标交互，用于需要视觉定位的任务。 |
| `devtools` | 11 | 标注、高亮、tracing、视频录制。 |
| `config` | 1 | 查看解析后的 Playwright MCP 配置。 |
| `network` | 4 | 修改网络状态；添加、查看或移除请求 mock。 |
| `storage` | 17 | 查看或修改 Cookie、localStorage、sessionStorage 以及保存的 storage state。 |
| `testing` | 5 | 生成 Playwright locator，验证元素、列表、文本或值的可见性。 |

各能力包含的具体工具名列在 [`browser_tools`](../tools/browser_tools.md#playwright-mcp-工具) 中。

### function resolve_browser_capabilities

```python
resolve_browser_capabilities(
    requested_names: Iterable[str] | None,
    available_capabilities: Iterable[BrowserCapability] = DEFAULT_BROWSER_CAPABILITIES,
) -> ResolvedBrowserCapabilities
```

校验能力选择并展开为确定性的工具白名单。始终前置 `core`，保留首次出现顺序，将未知名称收集到 `rejected_names`（工厂在存在任何 rejected 名称时抛出异常）。

**返回值**: `ResolvedBrowserCapabilities` — 冻结 dataclass，包含 `requested_names`、`selected_names`、`rejected_names` 和展开后的 `allowed_tool_names`。

白名单在两处生效：`BrowserRuntimeRail` 每次调用时将其应用到智能体的 MCP 工具注册（`ability_manager.set_mcp_tool_allowlist`）；浏览器服务将同一列表传给其嵌套的 worker 智能体。

---

## 浏览器选项

运行浏览器子智能体前，开发者应了解的关键概念。哪个配置项生效取决于谁启动浏览器 —— 参见[驱动模式](#驱动模式)：

- **Playwright 自启动**（默认）：未配置 CDP 端点时，Playwright MCP 服务器自行启动浏览器。通过 `PLAYWRIGHT_MCP_ARGS`（`@playwright/mcp` 的命令行参数，按 shell 分词字符串或 JSON 数组原样传入）配置。
- **Managed（托管）**：运行时启动自己的 Chrome，Playwright 通过 CDP 连接。通过 `BROWSER_MANAGED_*` 环境变量配置 Chrome。
- **Remote / extension**：浏览器已经存在；其特性（有头/无头、引擎、配置目录）取决于它启动时的参数。

### 无头（headless）与有头（headed）

不存在独立的 headless 配置项 —— headless 是启动浏览器的进程的启动参数：

| 驱动 | 无头运行方式 |
|---|---|
| Playwright 自启动 | 在 `PLAYWRIGHT_MCP_ARGS` 中加 `--headless`，例如 `PLAYWRIGHT_MCP_ARGS="-y @playwright/mcp@latest --headless"`。默认有头。 |
| Managed | 在 `BROWSER_MANAGED_ARGS` 中加 Chrome 参数，例如 `BROWSER_MANAGED_ARGS="--headless=new"`。默认有头。 |
| Remote | 取决于远端 Chrome 的启动方式。 |
| Extension | 始终有头 —— 它是用户的真实浏览器。 |

Managed 模式注意事项：重连时，运行时会拒绝复用启动参数与当前 `BROWSER_MANAGED_ARGS` 不一致的已运行 Chrome（例如上次运行遗留的有头 Chrome，而当前要求无头），而是按当前配置启动新的浏览器。

对于有反爬检测的站点和调试场景（可以看着智能体操作），有头模式是更稳妥的默认选择；无头模式适合 CI 和无显示器的服务器环境。

### 浏览器引擎与设备模拟

- `PLAYWRIGHT_MCP_BROWSER` —— Playwright 自启动浏览器的引擎（如 `chrome`、`firefox`、`webkit`、`msedge`），转发给 MCP 服务器。基于 CDP 的模式（managed/remote）仅支持 Chromium；配置了 CDP 端点时引擎强制为 `chrome`。
- `PLAYWRIGHT_MCP_DEVICE` —— Playwright 设备描述符（如 `"iPhone 15"`），用于设备模拟。与 CDP 端点不兼容，同时配置会抛出异常。
- 视口：智能体可在运行时用 `browser_resize` 工具调整；初始尺寸可通过 `PLAYWRIGHT_MCP_ARGS` 传入（如 `--viewport-size=1280,720`）。

### 会话、配置目录与登录持久化

- **Managed 模式**为每个 profile 使用持久化的 Chrome user-data 目录 —— 默认 `{working_dir}/.browser-profiles/{profile_name}` —— Cookie 和登录状态在重启后保留。不同的 `BrowserInstanceConfig` key 对应不同 profile；相同 key 复用同一 profile（若浏览器仍存活则复用同一个运行中的 Chrome）。
- **Playwright 自启动模式**维护自己的 profile；可通过 `PLAYWRIGHT_MCP_ARGS` 传入 `--user-data-dir=...`，或用 `--isolated`（每会话全新内存 profile，可选用 `--storage-state=path` 预置登录态）。
- `storage` 能力提供运行时读写 Cookie 和 storage state 的工具 —— 可注入已保存的登录态，或捕获登录态供后续复用。由于涉及敏感会话状态，它不属于 `core`，必须显式申请。

### 网络与代理

- `HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY` 会从环境转发给 Playwright MCP 服务器进程。
- 浏览器级代理可通过 `PLAYWRIGHT_MCP_ARGS` 中的 `--proxy-server=...` 设置。
- `network` 能力提供运行时请求 mock 和网络状态控制。

### 超时

- **单次工具调用**：`PLAYWRIGHT_MCP_TIMEOUT_S` / `BROWSER_TIMEOUT_S`（默认 `180`）限制每次 MCP 工具调用。
- **单次任务尝试**：`BrowserRunGuardrails.timeout_s`（默认 `180`）限制一次完整的委派浏览器任务；参见 [Guardrails](#class-browserrunguardrails)。
- **批量步骤**：`browser_batch_interact` 将单步超时钳制在 250–30000 ms，整批上限 90 s。

### 输出目录

运行时解析工作目录（`PLAYWRIGHT_RUNTIME_MCP_CWD`，默认当前工作目录），并在其下创建两个子目录：`screenshots/` 存放截图，`artifacts/` 存放智能体产出的文件（下载、导出、提取的数据）。Managed 模式的浏览器 profile 位于同一根目录下的 `.browser-profiles/`。

---

## class BrowserInstanceConfig

模块：`openjiuwen.harness.tools.browser_move.playwright_runtime.config`

```python
@dataclass(frozen=True)
class BrowserInstanceConfig
```

每实例浏览器身份，用于让每个智能体拥有独立浏览器。所有字段默认为空/`0`，等同于旧版进程全局（环境驱动）行为。当 `key` 非空时浏览器被隔离：Playwright MCP `server_id` 追加 key 后缀（`playwright_official_stdio__<key>`），managed profile、端口和 user-data 目录均从 key 派生而非共享环境配置。使用相同 `key` 的智能体有意共享同一个浏览器；带 key 的运行时绝不会回退到旧版无 key 的 MCP 客户端。

**属性**:

| 属性 | 类型 | 说明 |
|---|---|---|
| `key` | `str` | 浏览器身份 key。用于 server ID 和 profile 名时净化为 `[A-Za-z0-9_-]` |
| `driver_mode` | `str` | `"managed"`、`"remote"` 或 `"extension"`；空字符串时遵循环境变量 `BROWSER_DRIVER`（默认 `remote`） |
| `managed_port` | `int` | Managed 模式的 CDP 调试端口。`0` 时带 key 实例自动分配空闲端口（旧版实例使用 `BROWSER_MANAGED_PORT`，默认 `9333`） |
| `user_data_dir` | `str` | Chrome user-data 目录。为空时派生为 `{working_dir}/.browser-profiles/{profile_name}` |
| `profile_name` | `str` | Managed profile 名。为空时回退到 key，再回退到 `BROWSER_PROFILE_NAME` |
| `cdp_url` | `str` | Remote 模式的显式 CDP 端点；优先于共享环境端点 |
| `browser_binary` | `str` | 可选的 Chrome 可执行文件路径覆盖 |

### 驱动模式

- **managed** —— 运行时启动并持有一个专属本地 **Chrome**（设计上仅支持 Chrome），带 `--remote-debugging-port` 和 per-profile user-data 目录，等待 CDP 端点就绪（最长 20 s），然后将 Playwright MCP 服务器指向它。已运行且 profile 匹配的 Chrome 会被「收养」但不被持有（关闭时不会杀掉）；`BROWSER_MANAGED_KILL_EXISTING` 会先杀掉冲突实例。
- **remote** —— 连接已有 CDP 端点（`cdp_url`，或 `PLAYWRIGHT_MCP_CDP_ENDPOINT` / `PLAYWRIGHT_CDP_URL`）。CDP 模式仅支持 Chromium；与设备模拟同时配置会抛出异常。完全未配置 CDP 端点时，Playwright MCP 服务器自行启动浏览器（即[浏览器选项](#浏览器选项)中的「Playwright 自启动」形态）。
- **extension** —— 通过 Playwright MCP 扩展桥驱动已运行的浏览器（`PLAYWRIGHT_MCP_EXTENSION`，可选 `PLAYWRIGHT_MCP_EXTENSION_TOKEN`）。

---

## class RuntimeSettings

模块：`openjiuwen.harness.tools.browser_move.playwright_runtime.config`

```python
@dataclass(frozen=True)
class RuntimeSettings
```

解析后的浏览器运行时设置，存放在 `SubAgentConfig.factory_kwargs` 中，由 `create_browser_agent` 消费。

**属性**:

| 属性 | 类型 | 说明 |
|---|---|---|
| `provider` | `str` | 模型 provider（`openai`、`openrouter`、`siliconflow`、`dashscope`） |
| `api_key` | `str` | 模型 API key |
| `api_base` | `str` | 模型 API base URL |
| `model_name` | `str` | 嵌套 browser worker 使用的模型名 |
| `mcp_cfg` | `McpServerConfig` | Playwright MCP 服务器配置（stdio；命令默认 `npx -y @playwright/mcp@latest`，通过 `--caps=` 启用完整能力列表） |
| `guardrails` | `BrowserRunGuardrails` | 运行 guardrails |
| `instance` | `Optional[BrowserInstanceConfig]` | 每实例浏览器身份 |

使用 `build_runtime_settings(instance)` 从环境变量解析全部字段，或让 `create_browser_agent` 从 `Model` 对象的客户端配置推导 provider/key/base/model。

---

## class BrowserRunGuardrails

模块：`openjiuwen.harness.tools.browser_move.playwright_runtime.config`

```python
@dataclass
class BrowserRunGuardrails
```

浏览器任务运行的限制。由 `build_browser_guardrails()` 从环境变量构建。

**属性**:

| 属性 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `max_steps` | `int` | `20` | 每任务最大 worker 迭代数（即嵌套 worker 的 ReAct 迭代上限）。env: `BROWSER_GUARDRAIL_MAX_STEPS` |
| `max_failures` | `int` | `2` | 传达给 worker 的失败预算。env: `BROWSER_GUARDRAIL_MAX_FAILURES` |
| `timeout_s` | `int` | `180` | 每次任务尝试的墙钟超时。env: `BROWSER_TIMEOUT_S` / `PLAYWRIGHT_TOOL_TIMEOUT_S` |
| `retry_once` | `bool` | `True` | 失败后携带失败上下文提示词重试一次。env: `BROWSER_GUARDRAIL_RETRY_ONCE` |
| `resume_on_max_iterations` | `bool` | `False` | 达到最大迭代后，额外执行一次从已记录进度恢复的尝试（而非重新开始任务）。env: `BROWSER_GUARDRAIL_RESUME_ON_MAX_ITERATIONS` |

可重试的传输层故障（frame detached、target closed、页面崩溃、`net::ERR_*`）会在重试前额外触发浏览器运行时重启。

---

## class BrowserAgentRuntime

模块：`openjiuwen.harness.tools.browser_move.playwright_runtime.runtime`

```python
class BrowserAgentRuntime(
    provider: str,
    api_key: str,
    api_base: str,
    model_name: str,
    mcp_cfg: McpServerConfig,
    guardrails: BrowserRunGuardrails,
    instance: BrowserInstanceConfig | None = None,
    allowed_tool_names: Iterable[str] | None = None,
)
```

所有浏览器辅助工具共享的运行时内核。持有底层的 `BrowserService`（浏览器生命周期、心跳、取消、guardrail 执行）和 `ActionController`（自定义动作），并对外暴露辅助工具所调用的操作。

**关键方法**:

- **`ensure_runtime_ready()`** —— 启动浏览器驱动、注册 Playwright MCP 服务器、绑定直接代码执行器。要求 `PATH` 上有 `npx`。
- **`probe_interactives(...)` / `probe_cards(...)`** —— 运行页面探测并解析其 JSON 结果（卡片探测还会写入 selector 缓存）。
- **`batch_interact(...)`** —— 通过 action controller 执行 `browser_batch_interact` 步骤列表。
- **`run_custom_action(action, ...)` / `list_actions()`** —— 分发/枚举自定义动作。
- **`run_browser_task(task, ...)`** —— 将完整任务委派给嵌套 browser worker，带 guardrails、重试和进度跟踪（用于 `browser_task` 动作和独立 MCP 服务器，不用于子智能体的直接路径）。
- **`cancel_run(...)` / `clear_cancel(...)`** —— 取消控制。
- **`runtime_health()`** —— 连接健康度、心跳时间戳、provider/模型信息。服务每 30 s ping 一次 CDP 端点和 MCP 子进程；心跳失败会将连接标记为不健康，但重启推迟到下一个任务执行时，避免空闲期间复活被手动关闭的浏览器。
- **`shutdown()`** —— 停止心跳、runner 以及运行时持有的 managed 浏览器。

---

## class BrowserRuntimeRail

模块：`openjiuwen.harness.tools.browser_move.playwright_runtime.runtime`

```python
class BrowserRuntimeRail(runtime: BrowserAgentRuntime)
```

让直接浏览器会话可恢复、可判定完成状态的 Rail。由 `create_browser_agent` 自动注入。

**行为**:

- **before_invoke** —— 确保运行时就绪，在智能体的 ability manager 上注册 Playwright MCP 服务器，应用能力白名单，并从会话恢复已存储的进度状态。
- **before_model_call** —— 添加提示词 section，要求模型在停止且不再调用浏览器工具时，追加且仅追加一个 `<browser_progress>{...}</browser_progress>` JSON 块（字段：`status`、`completed_steps`、`remaining_steps`、`next_step`、`completion_evidence`、`missing_requirements`），且仅当结果有证据时才使用 `status=completed`。若存在已存储进度，则作为延续上下文附件注入（「避免重复已完成的操作」）。
- **after_tool_call** —— 将每次 `browser_*` 工具结果折叠进会话的进度状态（最近工具步骤上限 8 条、最后页面 URL/标题、最后截图）。
- **after_invoke** —— 从输出中提取并剥离 `<browser_progress>` 块，然后：
  - `status=completed`（无缺失需求且有证据）→ `result_type="answer"`，清除进度；
  - 否则 → `result_type="error"`，附带结构化**失败摘要**（任务摘录、错误、最后页面、截图引用、进度块、部分输出），供父智能体携带上下文重试；
  - 达到最大迭代的结果同样生成失败摘要。

由于 `TaskTool` 为 `browser_agent` 派生确定性的子会话 ID，持久化的进度在同一父会话的多次派生之间保留 —— 失败的浏览器任务可以重试并从中断处继续。

---

## 可观测性

- **专用日志文件** —— 浏览器活动通过 `openjiuwen.browser_agent` logger 写入 `./logs/browser_agent.log`，与通用应用日志分离。路径用 `OPENJIUWEN_BROWSER_AGENT_LOG_FILE` 覆盖（falsy 值如 `0`/`off` 表示禁用文件），级别用 `OPENJIUWEN_BROWSER_AGENT_LOG_LEVEL`（默认 `INFO`），镜像到通用日志用 `OPENJIUWEN_BROWSER_AGENT_LOG_MIRROR_COMMON`（默认关闭）。
- **状态遥测** —— `BROWSER_SUBAGENT_STATUS_LOG` 启用时（默认开启），为任务/模型/工具的开始、结束和异常输出带 `[BROWSER_SUBAGENT]` 标记的紧凑 JSON 记录，包含各工具计数、批量步骤聚合，以及从失败批量到原语工具的回退检测。敏感值会被脱敏，URL 的 query 部分会被剥离。

---

## 配置参考

模型和 provider 设置优先从携带客户端配置的 `Model` 对象解析；否则从环境变量解析（安装了 `python-dotenv` 时会加载仓库根目录的 `.env`）：`MODEL_NAME`（默认 `anthropic/claude-sonnet-4.5`）、`MODEL_PROVIDER`、`API_KEY`、`API_BASE`，或 provider 专属变量（`OPENROUTER_API_KEY`、`DASHSCOPE_API_KEY` 等）。

| 分组 | 环境变量（默认值） |
|---|---|
| Playwright MCP | `PLAYWRIGHT_MCP_COMMAND`（`npx`）、`PLAYWRIGHT_MCP_ARGS`（`-y @playwright/mcp@latest`；在此添加 `--headless`、`--viewport-size`、`--proxy-server`、`--isolated` 等参数）、`PLAYWRIGHT_MCP_TIMEOUT_S`、`PLAYWRIGHT_MCP_ENV_JSON`、`PLAYWRIGHT_MCP_BROWSER`、`PLAYWRIGHT_MCP_DEVICE`、`PLAYWRIGHT_BROWSERS_PATH`、`HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY` |
| 工作目录 | `PLAYWRIGHT_RUNTIME_MCP_CWD` / `BROWSER_RUNTIME_MCP_CWD`（默认当前工作目录；`screenshots/`、`artifacts/`、`.browser-profiles/` 位于其下） |
| 驱动选择 | `BROWSER_DRIVER`（`remote` \| `managed` \| `extension`；默认 `remote`）、`PLAYWRIGHT_MCP_CDP_ENDPOINT` / `PLAYWRIGHT_CDP_URL`、`PLAYWRIGHT_MCP_CDP_HEADERS`、`PLAYWRIGHT_MCP_CDP_TIMEOUT`、`PLAYWRIGHT_MCP_EXTENSION`（+ `_TOKEN`） |
| Managed Chrome | `BROWSER_MANAGED_HOST`（`127.0.0.1`）、`BROWSER_MANAGED_PORT`（`9333`）、`BROWSER_MANAGED_USER_DATA_DIR`、`BROWSER_MANAGED_BINARY`、`BROWSER_MANAGED_ARGS`（在此添加 `--headless=new` 实现无头 managed Chrome）、`BROWSER_MANAGED_KILL_EXISTING`、`BROWSER_PROFILE_NAME`、`BROWSER_PROFILE_STORE_PATH` |
| Guardrails | `BROWSER_GUARDRAIL_MAX_STEPS`（`20`）、`BROWSER_GUARDRAIL_MAX_FAILURES`（`2`）、`BROWSER_GUARDRAIL_RETRY_ONCE`（`true`）、`BROWSER_GUARDRAIL_RESUME_ON_MAX_ITERATIONS`（`false`）、`BROWSER_TIMEOUT_S` / `PLAYWRIGHT_TOOL_TIMEOUT_S`（`180`） |
| Worker 采样 | `BROWSER_WORKER_TEMPERATURE`（`0.2`）、`BROWSER_WORKER_TOP_P`（`0.1`） |
| 探测 selector 缓存 | `OPENJIUWEN_BROWSER_SELECTOR_CACHE`（`~/.openjiuwen/browser_selector_cache.json`） |
| 文件上传 | `BROWSER_UPLOAD_ROOT`（未设置时 `list_upload_files` 返回错误） |
| 日志 | `BROWSER_SUBAGENT_STATUS_LOG`（开启）、`OPENJIUWEN_BROWSER_AGENT_LOG_FILE`（`./logs/browser_agent.log`）、`OPENJIUWEN_BROWSER_AGENT_LOG_LEVEL`（`INFO`）、`OPENJIUWEN_BROWSER_AGENT_LOG_MIRROR_COMMON`（关闭） |
