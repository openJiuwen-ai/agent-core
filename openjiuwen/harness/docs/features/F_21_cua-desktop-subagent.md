# cua_agent desktop subagent

## Metadata

| Item | Value |
| --- | --- |
| Date | 2026-10-14 |
| Scope | `cua_agent` preset; `tools/cua/` (desktop backends, native desktop tools, capability catalog, rails, user-activity probes); TaskTool cua delegation and resume; `os_control` permission category and the cua agent's permission rules; shared `MultimodalContextSummarizerRail` |
| Specs | S_04, S_05, S_08, S_18 |
| Test baseline | 580 passed, 1 skipped across the targeted suites listed under Verification (2026-10-14) |
| Refs | #1043 |

## Background

The harness could drive web pages (browser_agent) and Android devices
(mobile_gui), but not native desktop applications. `cua_agent` is the desktop
counterpart: a preset subagent that sees application windows through
screenshots and acts on them with real mouse and keyboard input, on Windows
and Linux X11. It follows the browser agent's pattern: a factory pair, a
trusted capability catalog, and rails that handle failure modes seen in
recorded runs. Desktop errors come back as ordinary tool results rather than
exceptions, so nothing in the retry stack sees them.

## Decision and state

### Tools and backends

- The tools are native in-process agent-core tools
  (`tools/cua/desktop_tools.py`), not an MCP server: no subprocess, no daemon
  to probe, and no prefix on the model-facing names. They follow the common
  computer-use tool vocabulary (`list_windows`, `get_window_state`, `click`,
  `type_text`, `press_key`, `hotkey`, `launch_app`, …) and its result
  contract: a reply starting with `✅` is an action that ran, one starting
  with `❌` is one that could not run.
- Perception is screenshot-only. `get_window_state(pid, window_id)` brings the
  window to the front and captures exactly its bounds, because a screen grab
  shows whatever is on top. Every pointer `x,y` is a pixel in that window's
  latest screenshot; the tool adds the window origin. `zoom` captures a
  padded region at native resolution, and `from_zoom=true` maps zoom-image
  pixels back.
- Input is real foreground input. A window-scoped action brings its window to
  the front first and returns `❌` without sending anything when that fails,
  so keystrokes never land in another window. pyautogui's fail-safe stays on:
  moving the mouse into a screen corner aborts the next action.
- `backend.py` holds the platform code behind a `DesktopBackend` protocol.
  `WindowsBackend` uses ctypes Win32 (visible, non-cloaked top-level windows;
  DWM frame bounds; foreground switch through `AttachThreadInput`; per-monitor
  DPI awareness so screenshots, bounds and clicks share physical pixels; text
  through `SendInput` `KEYEVENTF_UNICODE`, since pyautogui drops non-ASCII).
  `X11Backend` uses python-xlib and EWMH (`_NET_CLIENT_LIST_STACKING`,
  `_NET_WM_PID`, `_NET_ACTIVE_WINDOW`; client-window bounds, decorations
  excluded) and types non-ASCII through a temporary keycode remap plus XTest.
  Other platforms fail at preflight.
- The backend is built on first use and pyautogui is imported then, so
  building the agent or importing the module never touches a display; unit
  tests run against a fake backend. All blocking calls go through
  `asyncio.to_thread`.
- `launch_app` takes `path`, `name` (an executable on PATH, or on Windows a
  URI such as `ms-settings:`), or on Windows an `aumid`. It reports the pid
  that owns the new window, not the launcher's: Win11 Notepad and Calculator
  start through stub processes.
- Dependencies are pure Python (`cua` extra: pyautogui, psutil, pygetwindow
  on Windows, python-xlib on Linux), so they install on every CI image,
  including manylinux_2_28.

### Linux hosts

Three conditions common in minimal X11 containers are handled in the backend:

- With no `XAUTHORITY` and no `~/.Xauthority`, python-xlib refuses to open
  even an auth-less display. The backend points `XAUTHORITY` at the null
  device in that case; a display that needs a cookie still refuses.
- pyautogui imports mouseinfo, which calls `sys.exit()` when tkinter is
  missing and would end the agent process. A `None` entry in `sys.modules`
  turns that into the ImportError pyautogui already tolerates.
- mouseinfo installs python3-xlib 0.15 into the same `Xlib` package as
  python-xlib 0.33, and whichever installs last wins. The backend uses only
  calls both versions have, and the X11 idle probe sends the
  MIT-SCREEN-SAVER `QueryInfo` request itself.

### Capabilities and prompt

- `cua_capabilities` resolves against `DEFAULT_CUA_CAPABILITIES`: `core`
  (read-only, always), `input`, `app_lifecycle`, and `app_termination`
  (`kill_app`, only on explicit request). Only the granted tools are built,
  so an ungranted tool does not exist for the model. The catalog names
  exactly the built tools; a test pins that.
- One default prompt per language. It states the window-local pixel
  convention, the edge margin for actions on visually estimated regions
  (estimates are off by tens of pixels and input that starts outside a
  region fails silently), that input is real, that every action is verified
  by capturing again, and that Electron apps are desktop work while web
  browsers belong to browser_agent. The `kill_app` guidance is appended only
  when that tool is granted.

### Rails

All cua rails take the model-facing tool prefix (`""` here) and act only on
cua tool names, so the agent's other tools are never counted, held or
rewritten.

- `CuaRuntimeRail` builds the backend and reads the screen size on the first
  invoke. A missing extra, unsupported platform or unreachable display fails
  there with install and session hints instead of as a `❌` on the model's
  first action. The success is cached because `before_invoke` re-fires on
  every approval re-entry.
- `CuaRepeatFailureRail` counts identical calls that got the same non-`✅`
  reply: an advisory at 3, a refusal before dispatch at 6. Repetition alone is
  not the signal (`press_key tab` ×27 walking a form is legitimate); in the
  recorded corpus the longest legitimate run of identical non-success replies
  was 4 and a real stuck loop reached 20.
- `CuaProgressRail` records the current window, `❌` blockers (up to 5) and
  hard blocks, and writes `result["cua_result"]` at run end. Only `❌` counts
  as a blocker: one recorded run had 86 unconfirmed clicks and one real
  failure, and listing every unmarked reply would have listed 87.
- `CuaScreenshotDownscaleRail` re-encodes a screenshot over the image token
  budget as JPEG (max edge 1536, then 800) and tells the model the scale
  factor.
- `MultimodalContextSummarizerRail` keeps only the latest
  `cua_snapshot_keep_last_k` screenshots, and `ToolResultWindowProcessor`
  keeps the same number of `get_window_state` / `zoom` results inline (older
  ones offloaded with a 200-character preview). The default is 3: with 1, a
  two-window task loses the first window's capture as soon as the second is
  taken. The rail lives in `harness/rails/` and is shared with mobile_gui,
  whose module is a subclass that pre-fills its protected message names.
- `CuaUserTakeoverRail` (opt-in, `cua_pause_on_user_input`) holds desktop
  actions while the OS reports recent user input, releases them after an idle
  period, and ends the delegation with a report after `max_wait_s`. The
  agent's own input is attributed to the agent for the span of its call.
  Probes: `GetLastInputInfo` on Windows, MIT-SCREEN-SAVER idle on X11. It is
  off by default because holding every action made interactive runs stall.

### Progress and resume

- cua_agent is non-sticky: two unrelated desktop errands in one parent
  conversation must not share a sub-session. Resume is an explicit
  `resume_task_id`, as for browser_agent.
- TaskTool cancels a cua delegation 20 s before the tool ceiling (configurable
  through `OPENJIUWEN_SUBAGENT_TASK_TIMEOUT_S`) and returns a successful,
  resumable result instead of a bare timeout that loses every step. Nothing
  records whether the cancelled action took effect, so the result sets
  `last_action_confirmed=False` and the resumed run captures and checks first.
- A resume with a stored record rewrites the task description to carry the
  last status, window, blockers and recovery hint, followed by the parent's
  remaining task.

### Permissions

- `_tool_category` has an `os_control` category for the 12 mutating desktop
  tools; read-only tools stay uncategorized.
- The cua rules (`kill_app` deny, `launch_app` / `type_text` ask) live in the
  cua agent, not in the package builtin rules. The tool names are bare and
  other agents have same-named tools (mobile_gui's `type_text`), so a rule
  every engine inlines would gate them too. When a caller passes
  `permissions`, `create_cua_agent` appends the rules to a copy of it. They
  are user-layer rules: a user-layer DENY is decided before
  `approval_overrides`, `tools` and `defaults`, so no caller allow lifts the
  `kill_app` floor, and tagging them `layer: builtin` would make the engine
  skip the package's shell rules. Without `permissions` the agent runs
  without a permission engine.
- Inside a TaskTool delegation an ASK cannot reach the outer conversation, so
  a policy that can produce ASK needs a
  `ToolPermissionHost.request_permission_confirmation` hook that answers
  in-process.

## Rejected alternatives

- **Accessibility-tree perception.** Element trees give exact targets on
  native widgets but differ per toolkit, come back empty or lazy on
  Chromium/Electron surfaces, and need native UI-automation dependencies per
  platform. Screenshots plus pixels work the same on every window and
  platform, and match how vision models are trained for computer use.
- **Serving the tools over MCP.** It would add a subprocess and a lifecycle
  to manage, and prefix every tool name, for no capability the in-process
  tools lack.
- **Rail prefix matching alone.** With bare names every tool name starts with
  the empty prefix, so prefix checks would apply the rails to the agent's
  other tools; the rails check membership in the cua tool set.
- **Package builtin rules for the desktop tools.** Rejected for the
  same-name collision described under Permissions.
- **Turning the permission engine on whenever the agent is built.** ASK
  prompts inside a delegation cannot reach the user, so a default ASK on
  `type_text` would stall every run without a host hook.

## Verification

- Unit tests: `tests/unit_tests/harness/test_cua_agent.py`,
  `tests/unit_tests/harness/tools/cua/` (desktop tools against a fake backend,
  rails, capabilities, user activity),
  `tests/unit_tests/harness/tools/test_task_tool.py`,
  `tests/unit_tests/harness/security/` (including
  `test_tiered_policy_os_control.py`), and the core tool suites. Result:
  580 passed, 1 skipped.
- Permission engine, checked through `PermissionEngine`: with a caller config
  that allows everything, allows `kill_app` by name and adds an approval
  override for it, `kill_app` is DENY, `type_text` and `launch_app` are ASK,
  the caller's own deny rule still applies, and `click` follows the caller's
  defaults. A non-cua agent's `type_text` is unaffected.
- Linux X11, live in Docker (Xvfb + openbox + xterm, sources installed with
  `uv sync --frozen --extra cua`): 18/18 checks through the real factory —
  preflight, window list with pid and bounds, screenshot size equal to the
  window bounds, typing `héllo 世界` into a shell and reading the file back,
  keys, launch, `bring_to_front`, kill, zoom with `from_zoom`, scroll, drag,
  and the idle probe.
- Windows bench (`notepad_type`, `calc_multiply`, `explorer_select`,
  `explorer_largest_file`, `settings_about`; each verified in code against
  the OS), one run with qwen3.7-plus: 5/5 passed.

## Limits

- Pointing accuracy is the model's. A synthetic probe with qwen3.7-plus
  (red dot on a plain canvas, 100 answers) gave 60 answers within 40 px of
  the dot, 10 that matched a 0–1000 grid instead of pixels, and 30 further
  off. Large windows with dense targets (Explorer lists, Settings
  navigation) take more capture–click–capture rounds.
- Input is foreground-only: the agent takes over the mouse and keyboard while
  it acts.
- Linux needs an X11 display; Wayland sessions without X are unsupported.
  Window activation depends on an EWMH window manager, with a direct-focus
  fallback.
- `cua_result.status` is a heuristic, not a task-completion judgment.
- Without Pillow, an oversized screenshot is sent at full size, the same as
  `ReadFileTool`.
