# openjiuwen.harness.subagents.browser_agent

The browser sub-agent is a pre-configured [`DeepAgent`](../deep_agent.md#class-openjiuwenharnessdeepagent) that directly controls a real browser through the official Playwright MCP server (`npx @playwright/mcp`). It navigates pages, clicks, types, fills forms, and extracts data, and adds a deterministic helper layer (compact page probes, batched interactions, custom actions) plus a runtime rail that makes browser sessions resumable and completion-aware.

It is created with [`create_browser_agent`](#function-openjiuwenharnesssubagentscreate_browser_agent) or registered declaratively with [`build_browser_agent_config`](#function-openjiuwenharnesssubagentsbuild_browser_agent_config). A parent agent typically spawns it through the `TaskTool` with `subagent_type="browser_agent"`.

The tools available to the sub-agent — both the Playwright MCP primitives and the injected helper tools — are documented separately in [`browser_tools`](../tools/browser_tools.md).

## Overview

| Feature | Description |
|---|---|
| [Playwright MCP tools](../tools/browser_tools.md#playwright-mcp-tools) | The primitive `browser_*` tools (navigate, click, type, snapshot, screenshot, evaluate, ...) come from the official Playwright MCP server, registered automatically at agent creation. |
| [Capability allowlist](#browser-capabilities) | Playwright tools are grouped into named capabilities (`core`, `pdf`, `vision`, `devtools`, `config`, `network`, `storage`, `testing`). The caller selects capabilities per task; only the expanded tool allowlist is exposed to the model. |
| [Runtime helper tools](../tools/browser_tools.md#runtime-helper-tools) | Page probes, batch interaction, custom actions, cancellation, and health checks, injected alongside the Playwright primitives. |
| [Browser options](#browser-options) | Headless vs. headed operation, browser engine choice, session persistence, viewport/device emulation, proxies, and output directories. |
| [Browser instance isolation](#class-openjiuwenharnesstoolsbrowser_moveplaywright_runtimeconfigbrowserinstanceconfig) | `BrowserInstanceConfig` gives each agent its own browser (own MCP server, Chrome profile, and CDP port); agents sharing a key intentionally share one browser. |
| [Driver modes](#driver-modes) | `managed` (launch a dedicated local Chrome), `remote` (attach to an existing CDP endpoint), or `extension` (drive a running browser through the Playwright MCP extension bridge). |
| [Progress and resumability](#class-openjiuwenharnesstoolsbrowser_moveplaywright_runtimeruntimebrowserruntimerail) | `BrowserRuntimeRail` persists task progress into the session, injects it back as continuation context on the next invocation, and converts incomplete runs into structured failure summaries. |
| [Guardrails](#class-openjiuwenharnesstoolsbrowser_moveplaywright_runtimeconfigbrowserrunguardrails) | Step, failure, and timeout limits with automatic retry and optional resume after a max-iterations stop. |
| [Observability](#observability) | A dedicated `logs/browser_agent.log` file and redacted structured status telemetry (`[BROWSER_SUBAGENT]` records). |

---

## function openjiuwen.harness.subagents.create_browser_agent

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

Create the browser sub-agent. On top of a plain deep agent, this factory:

1. Resolves the requested `browser_capabilities` against the trusted capability catalog (unknown names raise `ValueError` listing the available capabilities).
2. Builds a [`BrowserAgentRuntime`](#class-openjiuwenharnesstoolsbrowser_moveplaywright_runtimeruntimebrowseragentruntime) from `settings` (or from the model's client config / environment when `settings` is omitted).
3. Appends the [runtime helper tools](../tools/browser_tools.md#runtime-helper-tools) to any caller-provided `tools`.
4. Appends a [`BrowserRuntimeRail`](#class-openjiuwenharnesstoolsbrowser_moveplaywright_runtimeruntimebrowserruntimerail) to any caller-provided `rails`.

The default system prompt (locale-specific, `cn`/`en`) instructs the agent to prefer compact probes over snapshots, to use `browser_run_code` only with a known selector, and to claim completion only when the outcome is evidenced on the page.

**Parameters**:

- **model** (Model): LLM model instance. When the model carries a client config, its provider / API key / base URL / model name are also used for the browser runtime.
- **card** (AgentCard, optional): Agent identity card. Default: a card named `browser_agent` with a locale-specific description.
- **system_prompt** (str, optional): System prompt override. Default: built-in browser prompt for the resolved language.
- **tools** (list[Tool | ToolCard], optional): Additional tools, placed before the injected runtime helper tools. Default: `None`.
- **mcps** (list[McpServerConfig], optional): Additional MCP server configurations. The Playwright MCP server itself is registered by the runtime and does not need to be listed here. Default: `None`.
- **subagents** (list[[SubAgentConfig](../schema/config.md#class-openjiuwenharnessschemasubagentconfig) | DeepAgent], optional): Nested sub-agents. Default: `None`.
- **rails** (list[AgentRail], optional): Additional rails, placed before the injected `BrowserRuntimeRail`. Default: `None`.
- **enable_task_loop** (bool, optional): Enable the task loop. Default: `False`.
- **max_iterations** (int, optional): Maximum agent iterations. Default: `25`.
- **workspace** ([Workspace](../workspace/workspace.md#class-openjiuwenharnessworkspaceworkspace) | str, optional): Workspace. Default: `None`.
- **skills** (list[str], optional): Skills. Default: `None`.
- **backend** (Any, optional): LLM backend. Default: `None`.
- **sys_operation** (SysOperation, optional): System operation. Default: `None`.
- **language** (str, optional): `"cn"` or `"en"`; anything else falls back to `"cn"`. Default: `None`.
- **prompt_mode** (str, optional): Prompt mode. Default: `None`.
- **settings** ([RuntimeSettings](#class-openjiuwenharnesstoolsbrowser_moveplaywright_runtimeconfigruntimesettings), optional): Full browser runtime override (provider, API key/base, model, MCP config, guardrails, instance). Default: resolved from `model` or environment variables.
- **browser_key** (str, optional): Shorthand for `browser_instance=BrowserInstanceConfig(key=...)`. Default: `None`.
- **browser_instance** ([BrowserInstanceConfig](#class-openjiuwenharnesstoolsbrowser_moveplaywright_runtimeconfigbrowserinstanceconfig) | dict, optional): Per-instance browser identity. A plain dict is accepted so the identity can travel as serializable `factory_kwargs`. Default: `None` (legacy shared, environment-driven browser).
- **browser_capabilities** (list[str], optional): Task-scoped capability names from the [capability catalog](#browser-capabilities). `core` is always included. `None` means no allowlist restriction. Default: `None`.
- ****config_kwargs**: Additional configuration arguments forwarded to `create_deep_agent`.

**Returns**:

**[DeepAgent](../deep_agent.md#class-openjiuwenharnessdeepagent)**: A configured browser sub-agent.

**Raises**:

- **ValueError**: `browser_capabilities` is not a list of strings, or contains a name not in the capability catalog.

**Example**:

```python
from openjiuwen.harness.subagents import create_browser_agent

agent = create_browser_agent(
    model,
    language="en",
    browser_key="agent-a",              # isolated managed Chrome for this agent
    browser_capabilities=["storage"],   # exposes core + storage tools only
)
result = await agent.invoke({
    "query": "Open https://books.toscrape.com and list the 3 cheapest Travel books.",
    "conversation_id": "session-1",
})
```

---

## function openjiuwen.harness.subagents.build_browser_agent_config

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

Build a [`SubAgentConfig`](../schema/config.md#class-openjiuwenharnessschemasubagentconfig) that registers the browser agent as a sub-agent of a parent `DeepAgent`. The config carries `factory_name="browser_agent"` and the resolved `RuntimeSettings` in `factory_kwargs`; the parent materializes it lazily via `DeepAgent.create_subagent`, which dispatches back to [`create_browser_agent`](#function-openjiuwenharnesssubagentscreate_browser_agent) (the factory name `"browser_runtime"` is accepted as an alias).

Parameters match `create_browser_agent`, minus `subagents` and `browser_capabilities` — capabilities are chosen per task at spawn time, not at registration time.

**Returns**:

**[SubAgentConfig](../schema/config.md#class-openjiuwenharnessschemasubagentconfig)**: A declarative browser sub-agent registration.

**Example**:

```python
from openjiuwen.harness.factory import create_deep_agent
from openjiuwen.harness.subagents import build_browser_agent_config

browser_cfg = build_browser_agent_config(model, browser_key="agent-a", language="en")
main_agent = create_deep_agent(model=model, subagents=[browser_cfg])
```

At runtime the main agent spawns it through the `TaskTool`:

```jsonc
// tool: task
{
  "subagent_type": "browser_agent",
  "task": "Log in to the dashboard and export this week's report as PDF.",
  "browser_capabilities": ["pdf"]   // optional; "core" is always included
}
```

The sub-session ID for `browser_agent` is deterministic (`{parent_session}_sub_browser_agent`), so a FAIL → fix → re-verify loop resumes the same browser session and stored progress applies.

---

## Browser Capabilities

Module: `openjiuwen.harness.tools.browser_move.playwright_runtime.browser_capabilities`

Playwright MCP tools are grouped into a trusted, explicit catalog of capabilities. Matching is by exact tool name — never by prefix — so a newly introduced Playwright tool is not exposed before its policy is reviewed. The main agent selects capability names per task; the resolver performs no task interpretation of its own.

| Capability | Tools | Description |
|---|---|---|
| `core` | 24 | Always included. Navigate, click, type, fill forms, select options, hover, drag/drop, press keys, manage tabs, take snapshots and screenshots, evaluate/run code, inspect console and network, handle dialogs, upload files, wait, resize, close. |
| `pdf` | 1 | Save the current page as a PDF artifact. |
| `vision` | 6 | Coordinate-based mouse interactions for visually-positioned tasks. |
| `devtools` | 11 | Annotations, highlighting, tracing, and video capture. |
| `config` | 1 | Inspect the resolved Playwright MCP configuration. |
| `network` | 4 | Change network state; add, inspect, or remove request mocks. |
| `storage` | 17 | Inspect or modify cookies, localStorage, sessionStorage, and saved storage state. |
| `testing` | 5 | Generate Playwright locators and verify visible elements, lists, text, or values. |

The exact tool names in each capability are listed in [`browser_tools`](../tools/browser_tools.md#playwright-mcp-tools).

### function resolve_browser_capabilities

```python
resolve_browser_capabilities(
    requested_names: Iterable[str] | None,
    available_capabilities: Iterable[BrowserCapability] = DEFAULT_BROWSER_CAPABILITIES,
) -> ResolvedBrowserCapabilities
```

Validate a capability selection and expand it into a deterministic tool allowlist. Always prepends `core`, preserves first-seen order, and collects unknown names into `rejected_names` (the factory raises when any name is rejected).

**Returns**:

**ResolvedBrowserCapabilities**: Frozen dataclass with `requested_names`, `selected_names`, `rejected_names`, and the expanded `allowed_tool_names`.

The allowlist is enforced in two places: `BrowserRuntimeRail` applies it to the agent's MCP tool registration on every invocation (`ability_manager.set_mcp_tool_allowlist`), and the browser service passes the same list to its nested worker agent.

---

## Browser Options

The concepts every developer running the browser sub-agent should know. Which knob applies depends on who launches the browser — see [Driver Modes](#driver-modes):

- **Playwright-launched** (default): no CDP endpoint configured, so the Playwright MCP server launches its own browser. Configure it through `PLAYWRIGHT_MCP_ARGS` (command-line flags of `@playwright/mcp`, passed verbatim as a shell-split string or JSON array).
- **Managed**: the runtime launches its own Chrome and Playwright attaches over CDP. Configure Chrome through `BROWSER_MANAGED_*` variables.
- **Remote / extension**: the browser already exists; its characteristics (headed, engine, profile) are whatever it was started with.

### Headless vs. headed

There is no dedicated headless setting — headless is a launch argument of whichever process starts the browser:

| Driver | How to run headless |
|---|---|
| Playwright-launched | Add `--headless` to `PLAYWRIGHT_MCP_ARGS`, e.g. `PLAYWRIGHT_MCP_ARGS="-y @playwright/mcp@latest --headless"`. Default is headed. |
| Managed | Add the Chrome flag to `BROWSER_MANAGED_ARGS`, e.g. `BROWSER_MANAGED_ARGS="--headless=new"`. Default is headed. |
| Remote | Determined by how the remote Chrome was started. |
| Extension | Always headed — it is a user's real browser. |

Note for managed mode: when reconnecting, the runtime refuses to adopt an already-running Chrome whose launch arguments differ from the current `BROWSER_MANAGED_ARGS` (for example, a headed Chrome left over from a previous run while headless is now requested); it launches a fresh browser matching the current configuration instead.

Headed operation is the safer default for sites with bot detection and for debugging (you can watch the agent act); headless suits CI and server environments without a display.

### Browser engine and device emulation

- `PLAYWRIGHT_MCP_BROWSER` — engine for the Playwright-launched browser (e.g. `chrome`, `firefox`, `webkit`, `msedge`), forwarded to the MCP server. CDP-based modes (managed/remote) are Chromium-only; when a CDP endpoint is set the engine is forced to `chrome`.
- `PLAYWRIGHT_MCP_DEVICE` — Playwright device descriptor for emulation (e.g. `"iPhone 15"`). Not supported together with a CDP endpoint; combining them raises an error.
- Viewport: the agent can resize at runtime with the `browser_resize` tool; an initial size can be passed through `PLAYWRIGHT_MCP_ARGS` (e.g. `--viewport-size=1280,720`).

### Sessions, profiles, and login persistence

- **Managed mode** uses a persistent Chrome user-data directory per profile — by default `{working_dir}/.browser-profiles/{profile_name}` — so cookies and logins survive restarts. Distinct `BrowserInstanceConfig` keys get distinct profiles; the same key reuses the same profile (and, when still alive, the same running Chrome).
- **Playwright-launched mode** keeps its own profile; pass `--user-data-dir=...` or `--isolated` (fresh in-memory profile per session, optionally seeded with `--storage-state=path`) through `PLAYWRIGHT_MCP_ARGS`.
- The `storage` capability exposes tools to read/write cookies and storage state at runtime — useful for injecting a saved login or capturing one for later reuse. Because this touches sensitive session state, it is not part of `core` and must be requested explicitly.

### Network and proxies

- `HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY` are forwarded from the environment to the Playwright MCP server process.
- A browser-level proxy can be set with `--proxy-server=...` in `PLAYWRIGHT_MCP_ARGS`.
- The `network` capability adds runtime request mocking and network-state control.

### Timeouts

- **Per tool call**: `PLAYWRIGHT_MCP_TIMEOUT_S` / `BROWSER_TIMEOUT_S` (default `180`) bounds each MCP tool invocation.
- **Per task attempt**: `BrowserRunGuardrails.timeout_s` (default `180`) bounds a whole delegated browser task; see [Guardrails](#class-openjiuwenharnesstoolsbrowser_moveplaywright_runtimeconfigbrowserrunguardrails).
- **Per batch step**: `browser_batch_interact` clamps step timeouts to 250–30000 ms with a global batch cap of 90 s.

### Output directories

The runtime resolves a working directory (`PLAYWRIGHT_RUNTIME_MCP_CWD`, default: current working directory) and creates two subdirectories under it: `screenshots/` for captured screenshots and `artifacts/` for files the agent produces (downloads, exports, extracted data). Browser profiles for managed mode live under `.browser-profiles/` in the same root.

---

## class openjiuwen.harness.tools.browser_move.playwright_runtime.config.BrowserInstanceConfig

```python
@dataclass(frozen=True)
class BrowserInstanceConfig
```

Per-instance browser identity, used to isolate one browser per agent. All fields default to empty/`0`, which reproduces the legacy process-global (environment-driven) behavior. When `key` is non-empty the browser is isolated: the Playwright MCP `server_id` is suffixed with the key (`playwright_official_stdio__<key>`), and the managed profile, port, and user-data directory are derived from the key instead of shared environment settings. Agents sharing the same `key` intentionally share one browser; keyed runtimes never fall back to the legacy unkeyed MCP client.

**Attributes**:

- **key** (str): Browser identity key. Sanitized to `[A-Za-z0-9_-]` for use in server IDs and profile names.
- **driver_mode** (str): `"managed"`, `"remote"`, or `"extension"`; empty string defers to the `BROWSER_DRIVER` environment variable (default `remote`).
- **managed_port** (int): CDP debug port for managed mode. `0` auto-allocates a free port for keyed instances (legacy instances use `BROWSER_MANAGED_PORT`, default `9333`).
- **user_data_dir** (str): Chrome user-data directory. Empty derives `{working_dir}/.browser-profiles/{profile_name}`.
- **profile_name** (str): Managed profile name. Empty falls back to the key, then `BROWSER_PROFILE_NAME`.
- **cdp_url** (str): Explicit CDP endpoint for remote mode; wins over the shared environment endpoint.
- **browser_binary** (str): Optional Chrome binary path override.

### Driver Modes

- **managed** — The runtime launches and owns a dedicated local **Chrome** (Chrome-only by design) with `--remote-debugging-port` and a per-profile user-data directory, waits for the CDP endpoint to become ready (up to 20 s), and points the Playwright MCP server at it. An already-running Chrome with a matching profile is adopted without being owned (and is not killed on shutdown); `BROWSER_MANAGED_KILL_EXISTING` kills conflicting instances first.
- **remote** — Attach to an existing CDP endpoint (`cdp_url`, or `PLAYWRIGHT_MCP_CDP_ENDPOINT` / `PLAYWRIGHT_CDP_URL`). CDP mode is Chromium-only; combining it with device emulation raises an error. When no CDP endpoint is configured at all, the Playwright MCP server simply launches its own browser (the "Playwright-launched" setup in [Browser Options](#browser-options)).
- **extension** — Drive an already-running browser through the Playwright MCP extension bridge (`PLAYWRIGHT_MCP_EXTENSION`, optional `PLAYWRIGHT_MCP_EXTENSION_TOKEN`).

---

## class openjiuwen.harness.tools.browser_move.playwright_runtime.config.RuntimeSettings

```python
@dataclass(frozen=True)
class RuntimeSettings
```

Resolved browser runtime settings, stored in `SubAgentConfig.factory_kwargs` and consumed by `create_browser_agent`.

**Attributes**:

- **provider** (str): Model provider (`openai`, `openrouter`, `siliconflow`, `dashscope`).
- **api_key** (str): Model API key.
- **api_base** (str): Model API base URL.
- **model_name** (str): Model name for the nested browser worker.
- **mcp_cfg** (McpServerConfig): Playwright MCP server configuration (stdio; command defaults to `npx -y @playwright/mcp@latest` with the full capability list enabled via `--caps=`).
- **guardrails** ([BrowserRunGuardrails](#class-openjiuwenharnesstoolsbrowser_moveplaywright_runtimeconfigbrowserrunguardrails)): Run guardrails.
- **instance** ([BrowserInstanceConfig](#class-openjiuwenharnesstoolsbrowser_moveplaywright_runtimeconfigbrowserinstanceconfig), optional): Per-instance browser identity.

Use `build_runtime_settings(instance)` to resolve everything from environment variables, or let `create_browser_agent` derive provider/key/base/model from the `Model` object's client config.

---

## class openjiuwen.harness.tools.browser_move.playwright_runtime.config.BrowserRunGuardrails

```python
@dataclass
class BrowserRunGuardrails
```

Limits applied to browser task runs. Built from environment variables by `build_browser_guardrails()`.

**Attributes**:

- **max_steps** (int): Maximum worker iterations per task (becomes the nested worker's ReAct iteration cap). Default `20` (`BROWSER_GUARDRAIL_MAX_STEPS`).
- **max_failures** (int): Failure budget communicated to the worker. Default `2` (`BROWSER_GUARDRAIL_MAX_FAILURES`).
- **timeout_s** (int): Wall-clock timeout per task attempt. Default `180` (`BROWSER_TIMEOUT_S` / `PLAYWRIGHT_TOOL_TIMEOUT_S`).
- **retry_once** (bool): Retry a failed attempt once with a failure-context prompt. Default `True` (`BROWSER_GUARDRAIL_RETRY_ONCE`).
- **resume_on_max_iterations** (bool): After a max-iterations stop, run one extra attempt that resumes from the recorded progress instead of restarting the task. Default `False` (`BROWSER_GUARDRAIL_RESUME_ON_MAX_ITERATIONS`).

Retryable transport failures (detached frame, target closed, page crash, `net::ERR_*`) additionally trigger a browser runtime restart before the retry.

---

## class openjiuwen.harness.tools.browser_move.playwright_runtime.runtime.BrowserAgentRuntime

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

Runtime kernel shared by all browser helper tools. Owns the underlying `BrowserService` (browser lifecycle, heartbeat, cancellation, guardrail enforcement) and the `ActionController` (custom actions), and exposes the operations the helper tools call.

**Key methods**:

- **`ensure_runtime_ready()`** — Start the browser driver, register the Playwright MCP server, and bind the direct code executor. Requires `npx` on `PATH`.
- **`probe_interactives(...)` / `probe_cards(...)`** — Run the page probes and parse their JSON results (card probes also feed the selector cache).
- **`batch_interact(...)`** — Run a `browser_batch_interact` step list through the action controller.
- **`run_custom_action(action, ...)` / `list_actions()`** — Dispatch/enumerate custom actions.
- **`run_browser_task(task, ...)`** — Delegate a whole task to the nested browser worker with guardrails, retries, and progress tracking (used by the `browser_task` action and the standalone MCP server, not by the direct sub-agent path).
- **`cancel_run(...)` / `clear_cancel(...)`** — Cancellation control.
- **`runtime_health()`** — Connection health, heartbeat timestamp, provider/model info. The service pings the CDP endpoint and MCP subprocess every 30 s; a failed heartbeat marks the connection unhealthy but defers the restart until the next task, so an idle manually-closed browser is not revived.
- **`shutdown()`** — Stop the heartbeat, the runner, and any managed browser the runtime owns.

---

## class openjiuwen.harness.tools.browser_move.playwright_runtime.runtime.BrowserRuntimeRail

```python
class BrowserRuntimeRail(runtime: BrowserAgentRuntime)
```

Rail that makes direct browser sessions resumable and completion-aware. Injected automatically by `create_browser_agent`.

**What it does**:

- **before_invoke** — Ensures the runtime is ready, registers the Playwright MCP server on the agent's ability manager, applies the capability allowlist, and restores any stored progress state from the session.
- **before_model_call** — Adds a prompt section instructing the model to append exactly one `<browser_progress>{...}</browser_progress>` JSON block (fields: `status`, `completed_steps`, `remaining_steps`, `next_step`, `completion_evidence`, `missing_requirements`) whenever it stops without another browser tool call, with `status=completed` only when the outcome is evidenced. If stored progress exists, it is injected as a continuation attachment ("avoid repeating completed actions").
- **after_tool_call** — Folds every `browser_*` tool result into the session's progress state (recent tool steps capped at 8, last page URL/title, last screenshot).
- **after_invoke** — Extracts and strips the `<browser_progress>` block from the output, then:
  - `status=completed` (no missing requirements, evidence present) → `result_type="answer"`, progress cleared;
  - otherwise → `result_type="error"` with a structured **failure summary** (task excerpt, error, last page, screenshot reference, progress block, partial output) so the parent agent can retry with context;
  - a max-iterations stop receives the same failure-summary treatment.

Because the `TaskTool` derives a deterministic sub-session ID for `browser_agent`, the persisted progress survives across spawns of the same parent session — a failed browser task can be retried and continues from where it stopped.

---

## Observability

- **Dedicated log file** — Browser activity is written to `./logs/browser_agent.log` through the `openjiuwen.browser_agent` logger, keeping it separate from the common application log. Override the path with `OPENJIUWEN_BROWSER_AGENT_LOG_FILE` (a falsy value such as `0`/`off` disables the file), the level with `OPENJIUWEN_BROWSER_AGENT_LOG_LEVEL` (default `INFO`), and mirroring into the common log with `OPENJIUWEN_BROWSER_AGENT_LOG_MIRROR_COMMON` (default off).
- **Status telemetry** — When `BROWSER_SUBAGENT_STATUS_LOG` is enabled (default on), compact JSON records tagged `[BROWSER_SUBAGENT]` are emitted for task/model/tool start, end, and exceptions, including per-tool counts, batch step aggregates, and detection of fallbacks from a failed batch to primitive tools. Sensitive values are redacted and URLs are query-stripped.

---

## Configuration Reference

Model and provider settings resolve from the `Model` object when it carries a client config; otherwise from environment variables (a `.env` file at the repository root is loaded when `python-dotenv` is installed): `MODEL_NAME` (default `anthropic/claude-sonnet-4.5`), `MODEL_PROVIDER`, `API_KEY`, `API_BASE`, or provider-specific variants (`OPENROUTER_API_KEY`, `DASHSCOPE_API_KEY`, ...).

| Group | Environment variables (defaults) |
|---|---|
| Playwright MCP | `PLAYWRIGHT_MCP_COMMAND` (`npx`), `PLAYWRIGHT_MCP_ARGS` (`-y @playwright/mcp@latest`; add flags such as `--headless`, `--viewport-size`, `--proxy-server`, `--isolated` here), `PLAYWRIGHT_MCP_TIMEOUT_S`, `PLAYWRIGHT_MCP_ENV_JSON`, `PLAYWRIGHT_MCP_BROWSER`, `PLAYWRIGHT_MCP_DEVICE`, `PLAYWRIGHT_BROWSERS_PATH`, `HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY` |
| Working directory | `PLAYWRIGHT_RUNTIME_MCP_CWD` / `BROWSER_RUNTIME_MCP_CWD` (default: current working directory; `screenshots/`, `artifacts/`, and `.browser-profiles/` live under it) |
| Driver selection | `BROWSER_DRIVER` (`remote` \| `managed` \| `extension`; default `remote`), `PLAYWRIGHT_MCP_CDP_ENDPOINT` / `PLAYWRIGHT_CDP_URL`, `PLAYWRIGHT_MCP_CDP_HEADERS`, `PLAYWRIGHT_MCP_CDP_TIMEOUT`, `PLAYWRIGHT_MCP_EXTENSION` (+ `_TOKEN`) |
| Managed Chrome | `BROWSER_MANAGED_HOST` (`127.0.0.1`), `BROWSER_MANAGED_PORT` (`9333`), `BROWSER_MANAGED_USER_DATA_DIR`, `BROWSER_MANAGED_BINARY`, `BROWSER_MANAGED_ARGS` (add `--headless=new` here for headless managed Chrome), `BROWSER_MANAGED_KILL_EXISTING`, `BROWSER_PROFILE_NAME`, `BROWSER_PROFILE_STORE_PATH` |
| Guardrails | `BROWSER_GUARDRAIL_MAX_STEPS` (`20`), `BROWSER_GUARDRAIL_MAX_FAILURES` (`2`), `BROWSER_GUARDRAIL_RETRY_ONCE` (`true`), `BROWSER_GUARDRAIL_RESUME_ON_MAX_ITERATIONS` (`false`), `BROWSER_TIMEOUT_S` / `PLAYWRIGHT_TOOL_TIMEOUT_S` (`180`) |
| Worker sampling | `BROWSER_WORKER_TEMPERATURE` (`0.2`), `BROWSER_WORKER_TOP_P` (`0.1`) |
| Probe selector cache | `OPENJIUWEN_BROWSER_SELECTOR_CACHE` (`~/.openjiuwen/browser_selector_cache.json`) |
| File uploads | `BROWSER_UPLOAD_ROOT` (unset: `list_upload_files` reports an error) |
| Logging | `BROWSER_SUBAGENT_STATUS_LOG` (on), `OPENJIUWEN_BROWSER_AGENT_LOG_FILE` (`./logs/browser_agent.log`), `OPENJIUWEN_BROWSER_AGENT_LOG_LEVEL` (`INFO`), `OPENJIUWEN_BROWSER_AGENT_LOG_MIRROR_COMMON` (off) |
