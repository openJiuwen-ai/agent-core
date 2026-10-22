# openjiuwen.harness.subagents.cua_agent

Desktop sub-agent that operates native applications on the host computer through window screenshots and real mouse and keyboard input.

---

## Overview

`cua_agent` drives real application windows: it lists windows, captures a window's screenshot, and clicks, types, scrolls and launches applications at pixel positions read off that screenshot. It needs a vision-capable model.

Input is real foreground input: the agent moves the mouse and types into whatever has keyboard focus, so every action on a window brings that window to the front first. Do not use the machine while the agent is working, or enable the [user-takeover pause](#function-openjiuwenharnesssubagentscreate_cua_agent). Moving the mouse into a screen corner aborts the agent's next action.

Web pages are not this agent's job. Browser automation belongs to [`browser_agent`](./browser_agent.md). Electron desktop apps such as Discord, Slack or VS Code are ordinary desktop windows and are handled here.

Use `create_cua_agent` to build a standalone agent, or `build_cua_agent_config` to register it as a sub-agent that a parent reaches through `task_tool` with `subagent_type="cua_agent"`. `DeepAgent` also accepts the factory name `"desktop_agent"` as an alias.

**Platform**: Windows, and Linux with an X11 display (`DISPLAY` set; Wayland without X is not supported). The agent must run inside the interactive desktop session it operates.

### Setup

`uv sync --extra cua` (or `pip install 'openjiuwen[cua]'`) installs the desktop dependencies: pyautogui and psutil, plus pygetwindow on Windows and python-xlib on Linux.

| Environment variable | Purpose |
|---|---|
| `OPENJIUWEN_SUBAGENT_TASK_TIMEOUT_S` | Ceiling for one `task_tool` delegation, for all sub-agent types. Default: `720`. |

Before the first model call, `CuaRuntimeRail` checks that the desktop is usable and fails with a remediation message if the extra is missing, the platform is unsupported or no display can be reached.

---

## function openjiuwen.harness.subagents.create_cua_agent

```python
create_cua_agent(
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
    workspace: str | Workspace | None = None,
    skills: list[str] | None = None,
    backend: Any | None = None,
    sys_operation: SysOperation | None = None,
    language: str | None = None,
    prompt_mode: str | None = None,
    cua_capabilities: list[str] | None = None,
    cua_instance_key: str = "",
    cua_snapshot_keep_last_k: int = 3,
    cua_pause_on_user_input: bool = False,
    **config_kwargs,
) -> DeepAgent
```

Create the desktop sub-agent. The [desktop tools](#desktop-tools) of the granted capabilities and the [built-in rails](#built-in-rails) are added after any `tools` and `rails` you pass.

**Parameters**:

- **model** (Model): Vision-capable model instance.
- **card** (AgentCard, optional): Agent identity card. Default: `None`, which creates a card named `cua_agent`.
- **system_prompt** (str, optional): Replaces the default prompt. Default: `None`.
- **tools**, **mcps**, **subagents**, **rails**, **enable_task_loop**, **workspace**, **skills**, **backend**, **sys_operation**, **prompt_mode**: Passed to [`create_deep_agent`](../factory.md#function-openjiuwenharnesscreate_deep_agent).
- **max_iterations** (int, optional): Maximum iterations. Default: `25`. Every action is followed by a fresh capture to verify it, so budgets under about 20 tend to run out mid-task.
- **language** (str, optional): `"cn"` or `"en"`. Default: `None` (resolved by the harness).
- **cua_capabilities** (list[str], optional): Capabilities to grant; see [Capabilities](#capabilities). Default: `None`, which grants `input` and `app_lifecycle`. `core` is always included.
- **cua_instance_key** (str, optional): Accepted for API stability; it has no effect, because every agent gets its own tool instances. Default: `""`.
- **cua_snapshot_keep_last_k** (int, optional): Number of recent captures and screenshots kept in context. Must be at least 1. Default: `3`. Use `1` for single-window tasks to save context.
- **cua_pause_on_user_input** (bool, optional): Hold desktop actions while the user is using the mouse or keyboard. Default: `False`.
- ****config_kwargs**: Passed to `create_deep_agent`, for example `permissions` and `permission_host`. See [Permissions](#permissions).

**Returns**:

**[DeepAgent](../deep_agent.md#class-openjiuwenharnessdeepagent)**: The configured desktop sub-agent.

**Raises**:

- **ValueError**: An unknown capability name, or `cua_snapshot_keep_last_k` below 1.

**Example**:

```python
from openjiuwen.core.runner import Runner
from openjiuwen.harness.subagents import create_cua_agent

agent = create_cua_agent(model, language="en", cua_capabilities=[])  # read-only: core only

await Runner.start()
try:
    result = await Runner.run_agent(
        agent,
        {"query": "List the open application windows.", "conversation_id": "cua-demo"},
    )
finally:
    await Runner.stop()
```

---

## function openjiuwen.harness.subagents.build_cua_agent_config

```python
build_cua_agent_config(
    model: Model,
    *,
    card: AgentCard | None = None,
    system_prompt: str | None = None,
    tools: list[Tool | ToolCard] | None = None,
    mcps: list[McpServerConfig] | None = None,
    rails: list[AgentRail] | None = None,
    enable_task_loop: bool = False,
    max_iterations: int = 25,
    workspace: str | Workspace | None = None,
    skills: list[str] | None = None,
    backend: Any | None = None,
    sys_operation: SysOperation | None = None,
    language: str | None = None,
    prompt_mode: str | None = None,
    cua_capabilities: list[str] | None = None,
    cua_instance_key: str = "",
    cua_snapshot_keep_last_k: int = 3,
    cua_pause_on_user_input: bool = False,
    permissions: dict | None = None,
    permission_host: Any | None = None,
) -> SubAgentConfig
```

Build a [`SubAgentConfig`](../schema/config.md#class-openjiuwenharnessschemasubagentconfig) that the parent materializes with `create_cua_agent` when it first delegates. The arguments are validated now, with the same `ValueError` cases as `create_cua_agent`.

**Parameters**: Same as `create_cua_agent`, without `subagents` and `**config_kwargs`, plus:

- **permissions** (dict, optional): Permission policy for the sub-agent. Default: `None`.
- **permission_host** (ToolPermissionHost, optional): Host that answers permission confirmations. Default: `None`. See [Permissions](#permissions).

**Returns**:

**[SubAgentConfig](../schema/config.md#class-openjiuwenharnessschemasubagentconfig)**: The sub-agent configuration, with `factory_name="cua_agent"`.

**Example**:

```python
from openjiuwen.harness.factory import create_deep_agent
from openjiuwen.harness.subagents import build_browser_agent_config, build_cua_agent_config

coordinator = create_deep_agent(
    model=model,
    subagents=[
        build_cua_agent_config(model, language="en"),
        build_browser_agent_config(model, language="en"),
    ],
)
```

---

## Desktop tools

The agent sees a window by calling `get_window_state(pid, window_id)`, which brings the window to the front and returns its bounds and a screenshot of exactly that window. Every `x,y` passed to a pointer tool for that window is a pixel in its latest screenshot, measured from the top-left corner. Without `pid`, `x,y` are screen coordinates. `zoom` captures a region at native resolution for small text or icons; pointer tools then take `from_zoom=true` with `x,y` read off the zoom image.

| Tool | What it does |
|---|---|
| `list_windows`, `list_apps` | Windows (`window_id`, `pid`, `title`, `bounds`, `minimized`) and the apps that own them |
| `get_window_state` | Front the window and return its screenshot |
| `zoom` | Capture a padded window region at native resolution |
| `get_screen_size`, `get_cursor_position` | Display size and cursor position in pixels |
| `click`, `double_click`, `right_click`, `move_cursor`, `drag`, `scroll` | Pointer input at window pixels |
| `type_text`, `press_key`, `hotkey` | Keyboard input to the window; `x,y` clicks a field first |
| `launch_app`, `bring_to_front` | Start an app (path, executable name, a URI such as `ms-settings:` on Windows, or an AUMID) or front its window |
| `kill_app` | Force-terminate a process |

A reply starting with `✅` is an action that ran; one starting with `❌` could not run, and no input was sent.

---

## Capabilities

`cua_capabilities` selects tools from a fixed catalog (`openjiuwen.harness.tools.cua`). Only the selected tools are built, so the model never sees the others.

| Capability | Tools | Granted by default |
|---|---|---|
| `core` | `list_windows`, `list_apps`, `get_window_state`, `zoom`, `get_screen_size`, `get_cursor_position` | Always |
| `input` | `click`, `double_click`, `right_click`, `move_cursor`, `drag`, `scroll`, `type_text`, `press_key`, `hotkey` | Yes |
| `app_lifecycle` | `launch_app`, `bring_to_front` | Yes |
| `app_termination` | `kill_app` | No |

Pass `[]` for a read-only agent (core only).

### function openjiuwen.harness.tools.cua.resolve_cua_capabilities

```python
resolve_cua_capabilities(
    requested_names: Iterable[str] | None,
    available_capabilities: Iterable[CuaCapability] = DEFAULT_CUA_CAPABILITIES,
) -> ResolvedCuaCapabilities
```

Expand capability names into a tool allowlist. `core` is always added, duplicates are removed, and unknown names are reported in `rejected_names` instead of raising. The factories raise `ValueError` when `rejected_names` is not empty.

**Returns**:

**ResolvedCuaCapabilities**: A frozen dataclass with `requested_names`, `selected_names`, `rejected_names` and `allowed_tool_names`.

---

## Built-in rails

`create_cua_agent` adds these rails after the caller's rails. They act only on the desktop tools:

| Rail | When | What it does |
|---|---|---|
| `CuaRuntimeRail` | Always | Checks once, before the first model call, that the desktop is usable. |
| `CuaRepeatFailureRail` | Always | When an identical call keeps getting the same non-success reply, adds advice on the 3rd time and refuses the call on the 6th. |
| `CuaProgressRail` | Always | Reports the run's progress (see below). |
| `CuaScreenshotDownscaleRail` | Always | Re-encodes screenshots over the image token budget and tells the model the scale factor. |
| [`MultimodalContextSummarizerRail`](../rails/rails.md) | Always | Keeps only the last `cua_snapshot_keep_last_k` screenshots. |
| `CuaUserTakeoverRail` | `cua_pause_on_user_input=True` | Holds actions while the user is active, resumes after 5 s idle, and stops with a report after 60 s. |
| `ContextProcessorRail` | No `ContextProcessorRail` passed in `rails` | Keeps the last `cua_snapshot_keep_last_k` `get_window_state` / `zoom` results; older ones are offloaded to the workspace and replaced by a short preview. |

---

## Delegation results and resume

When a parent delegates through `task_tool`, the result `data` for `cua_agent` contains:

| Field | Meaning |
|---|---|
| `output` | The sub-agent's answer. |
| `resume_task_id` | Pass it back to continue the same desktop session. |
| `cua_result` | Progress report from `CuaProgressRail`. |
| `retryable` | `True` unless `cua_result.status` is `"completed"`. |
| `resume_context` | `status`, `blockers`, `current_window`, `recommended_recovery`, `resume_count`. |

`cua_result.status` is `"completed"`, `"partial"` (some actions failed) or `"blocked"` (a repeated failure was refused). It is a heuristic, not a judgment that the task succeeded. `blockers` lists up to 5 failed actions as `tool: first line of the error`.

**Timeouts**: a `cua_agent` delegation stops itself 20 s before the `task_tool` ceiling and returns a successful result with `status="timeout"`, `retryable=True` and `resume_context.last_action_confirmed=False`. The desktop session and the steps taken so far are kept, but the action running at the cut-off may have taken effect without being recorded.

**Resuming**: call `task_tool` again with `subagent_type="cua_agent"`, the same `resume_task_id`, and a `task_description` that covers only what is still missing. The sub-agent receives its last status, window, blockers and recovery hint, and is told to capture the window again before acting. Without `resume_task_id`, each delegation starts a new session.

---

## Permissions

When you pass `permissions`, `create_cua_agent` adds these rules to a copy of your policy (your dict is not changed):

| Rule | Tool | Decision |
|---|---|---|
| `cua_desktop_force_terminate_app` | `kill_app` | Deny |
| `cua_desktop_launch_app` | `launch_app` | Ask |
| `cua_desktop_type_text` | `type_text` | Ask |

The `kill_app` deny holds even if your policy allows everything, allows `kill_app` by name or adds an approval override for it. Without `permissions`, the agent runs without a permission engine. The rules apply only to this agent; other agents' tools with the same names, such as mobile_gui's `type_text`, are not affected.

Mutating desktop tools are in the `os_control` category. An `os_control` rule with `pattern: "*"` matches every call to the tool. Any other pattern (exact, glob or `re:`) is matched against the call's `text`, `name`, `bundle_id`, `value`, `key` or `keys` argument.

`kill_app` is denied by these rules, so it is not granted by default. Request `app_termination` to grant it; the system prompt then adds guidance to close applications cooperatively first.

Inside a `task_tool` delegation, an Ask decision cannot reach the user through the parent conversation. Pass a `permission_host` whose `request_permission_confirmation` answers it in-process, or use only Allow and Deny rules.

```python
permissions = {
    "enabled": True,
    "schema": "tiered_policy",
    "defaults": {"*": "allow"},
    "rules": [
        {
            "id": "deny_shell_launch",
            "tools": ["launch_app"],
            "match_type": "os_control",
            "pattern": "re:(?i)^(powershell|pwsh|cmd)(\\.exe)?$",
            "action": "deny",
            "severity": "CRITICAL",
        }
    ],
}
```

Give every rule an explicit `action`. `severity` alone never makes a rule deny.
