# Browser Sub-Agent Tools

Tools available to the [browser sub-agent](../subagents/browser_agent.md). They come in two layers:

1. **Playwright MCP tools** — the primitive `browser_*` tools served by the official Playwright MCP server (`npx @playwright/mcp`). Which of them the model can see is controlled by the [capability allowlist](../subagents/browser_agent.md#browser-capabilities).
2. **Runtime helper tools** — deterministic helpers injected by `create_browser_agent` alongside the primitives: page probes, batch interaction, custom actions, cancellation, and health checks.

The default system prompt establishes a working order: probe first (`browser_probe_interactives` / `browser_probe_cards`), act on `selector_hint`s (single primitives or `browser_batch_interact`), and fall back to `browser_snapshot` or `browser_run_code` only when the compact route is insufficient.

## Playwright MCP Tools

Grouped by capability. `core` is always available; every other group must be requested via `browser_capabilities` when the sub-agent is spawned.

### core (always included)

| Tool | Description |
|---|---|
| `browser_navigate` | Navigate to a URL. |
| `browser_navigate_back` | Go back to the previous page. |
| `browser_click` | Click an element (by snapshot reference). |
| `browser_type` | Type text into an element. |
| `browser_fill_form` | Fill multiple form fields in one call. |
| `browser_select_option` | Select an option in a native dropdown. |
| `browser_hover` | Hover over an element. |
| `browser_drag` / `browser_drop` | Drag an element and drop it on a target. |
| `browser_press_key` | Press a keyboard key (e.g. `Enter`, `Escape`). |
| `browser_find` | Find elements on the page. |
| `browser_snapshot` | Capture an accessibility snapshot of the page (the reference source for element-addressed actions). |
| `browser_take_screenshot` | Take a screenshot of the page or an element. |
| `browser_evaluate` | Evaluate a JavaScript expression on the page. |
| `browser_run_code_unsafe` | Run arbitrary Playwright code against the page. Used by the probes and batch helpers; the prompt restricts direct use to cases with a known selector/computation. |
| `browser_wait_for` | Wait for text to appear/disappear or for a time interval. |
| `browser_tabs` | Manage tabs (`list`, `new`, `close`, `select`). |
| `browser_resize` | Resize the browser window/viewport. |
| `browser_handle_dialog` | Accept or dismiss a browser dialog (alert/confirm/prompt). |
| `browser_file_upload` | Provide files for an active file chooser. |
| `browser_console_messages` | Read the page's console messages. |
| `browser_network_request` / `browser_network_requests` | Inspect a single network request / list requests made by the page. |
| `browser_close` | Close the page. |

### pdf

| Tool | Description |
|---|---|
| `browser_pdf_save` | Save the current page as a PDF artifact. |

### vision

Coordinate-based mouse control for tasks that require visual positioning (canvas apps, maps, custom widgets without stable DOM handles).

| Tool | Description |
|---|---|
| `browser_mouse_click_xy` | Click at absolute coordinates. |
| `browser_mouse_move_xy` | Move the cursor to coordinates. |
| `browser_mouse_drag_xy` | Drag from one coordinate to another. |
| `browser_mouse_down` / `browser_mouse_up` | Press / release a mouse button. |
| `browser_mouse_wheel` | Scroll with the mouse wheel. |

### devtools

| Tool | Description |
|---|---|
| `browser_highlight` / `browser_hide_highlight` | Highlight an element on the page / remove the highlight. |
| `browser_annotate` | Add an annotation overlay to the page. |
| `browser_start_tracing` / `browser_stop_tracing` | Start / stop a Playwright trace recording. |
| `browser_start_video` / `browser_stop_video` | Start / stop video capture. |
| `browser_video_chapter` | Mark a chapter in the captured video. |
| `browser_video_show_actions` / `browser_video_hide_actions` | Toggle action overlays in the video. |
| `browser_resume` | Resume after a paused debugging state. |

### config

| Tool | Description |
|---|---|
| `browser_get_config` | Inspect the resolved Playwright MCP configuration (useful when diagnosing runtime setup). |

### network

| Tool | Description |
|---|---|
| `browser_network_state_set` | Change the browser network state (e.g. offline emulation). |
| `browser_route` | Add a request mock/route. |
| `browser_route_list` | List active routes. |
| `browser_unroute` | Remove a route. |

### storage

Session-state access — cookies, localStorage, sessionStorage, and Playwright storage state. Sensitive by nature, which is why it is a separate opt-in capability.

| Tool | Description |
|---|---|
| `browser_cookie_list` / `browser_cookie_get` | List cookies / read one cookie. |
| `browser_cookie_set` / `browser_cookie_delete` / `browser_cookie_clear` | Write / delete / clear cookies. |
| `browser_localstorage_list` / `browser_localstorage_get` | List / read localStorage entries. |
| `browser_localstorage_set` / `browser_localstorage_delete` / `browser_localstorage_clear` | Write / delete / clear localStorage. |
| `browser_sessionstorage_list` / `browser_sessionstorage_get` | List / read sessionStorage entries. |
| `browser_sessionstorage_set` / `browser_sessionstorage_delete` / `browser_sessionstorage_clear` | Write / delete / clear sessionStorage. |
| `browser_storage_state` | Export the full storage state (cookies + origins), e.g. to capture a login. |
| `browser_set_storage_state` | Restore a previously saved storage state, e.g. to inject a login. |

### testing

| Tool | Description |
|---|---|
| `browser_generate_locator` | Generate a stable Playwright locator for an element. |
| `browser_verify_element_visible` | Assert an element is visible. |
| `browser_verify_list_visible` | Assert a list of elements is visible. |
| `browser_verify_text_visible` | Assert text is visible on the page. |
| `browser_verify_value` | Assert an input's value. |

---

## Runtime Helper Tools

Module: `openjiuwen.harness.tools.browser_move.playwright_runtime.runtime_tools`

Deterministic helpers backed by the shared [`BrowserAgentRuntime`](../subagents/browser_agent.md#class-openjiuwenharnesstoolsbrowser_moveplaywright_runtimeruntimebrowseragentruntime). They are always injected, regardless of the capability selection.

| Tool | Description |
|---|---|
| [`browser_probe_interactives`](#tool-browser_probe_interactives) | Compact ranked list of visible interactive elements. |
| [`browser_probe_cards`](#tool-browser_probe_cards) | Compact repeated card/listing structures with extracted fields. |
| [`browser_batch_interact`](#tool-browser_batch_interact) | Execute multiple deterministic interaction steps in one call. |
| [`browser_custom_action`](#tool-browser_custom_action) | Run a registered custom action (drag-and-drop, coordinate resolution, file upload, ...). |
| [`browser_list_custom_actions`](#tool-browser_list_custom_actions) | List available custom actions and their parameter documentation. |
| [`browser_cancel_run`](#tool-browser_cancel_run) / [`browser_clear_cancel`](#tool-browser_clear_cancel) | Cancel an in-progress browser task / clear the cancellation flag. |
| [`browser_runtime_health`](#tool-browser_runtime_health) | Report runtime readiness, heartbeat status, and provider/model configuration. |

### Page Probes

The two probe tools generate JavaScript that runs in the page (via the Playwright run-code tool) and returns a compact JSON summary. They exist so the agent can understand a page in a few hundred tokens instead of a full accessibility snapshot or DOM dump. Both return `selector_hint` values that can be used directly in follow-up actions.

#### tool browser_probe_interactives

Returns up to `max_items` visible interactive elements, ranked by a heuristic score (test IDs, ARIA labels, tag/role, query match, position). Use it for page-level controls: buttons, links, inputs, forms, navigation, login, pagination, menus.

**Parameters**:

- **max_items** (int, optional): Maximum elements to return. Default `50`, hard cap `100`. The prompt recommends 20–30 for page-level controls.
- **viewport_only** (bool, optional): Only elements currently visible in the viewport. Default `true`.
- **query** (str, optional): Text filter. Alias-aware: e.g. `"search"` also matches placeholders/labels containing 搜索, 关键词, 検索; `"next"` matches 下一页, load more; `"login"` matches 登录.

**Result**: `{ok, url, title, viewport, elements: [...]}` where each element carries `id`, `tag`, `role`, `action_likelihood` (`search` / `input` / `pagination` / `login` / `filter` / `commerce` / `button` / `link`), visible `text`, `accessible_name`, `aria_label`, `testid`, `placeholder`, `href`, `disabled`, `bbox`, and `selector_hint` (prefers `data-testid` → `#id` → attribute selectors → an `nth-of-type` path).

#### tool browser_probe_cards

Detects repeated card/listing structures — product grids, search results, catalogs, article lists — and extracts structured fields per card. Use it **first** on any page with repeated visible cards or listing rows; if it returns the fields the task needs, use them directly instead of screenshots or broad DOM evaluation.

**Parameters**:

- **max_cards** (int, optional): Maximum cards to return. Default `20`, hard cap `50`.
- **viewport_only** (bool, optional): Only inspect cards in the current viewport. Default `true`.
- **include_buttons** (bool, optional): Include visible buttons/links per card. Default `true`.
- **query** (str, optional): Text filter, e.g. `"laptop"` or `"cart"`.

**Result**: `{ok, url, title, cards: [...], recurring_signatures, selector_source, ...}` where each card carries `title`, `primary_link`, `author`, `source`, `summary`, `price` (multi-currency), `rating`, `review_count`, `availability`, `buttons`, `has_image`, `bbox`, `quality_score`, and per-field `*_selector_hint` values.

Container selectors are tried in three tiers, stopping at the first that yields enough high-quality cards:

1. **Selector cache** — learned selectors from previous successful probes on the same domain and route. Persisted at `~/.openjiuwen/browser_selector_cache.json` (override with `OPENJIUWEN_BROWSER_SELECTOR_CACHE`). Successful probes generalize and store their selector hints; rejected cache attempts are tracked so stale entries stop being retried.
2. **Site profiles** — static built-in per-domain selector hints.
3. **Generic heuristics** — a broad set of structural selectors (`article`, `li`, `[class*="card"]`, ...).

### tool browser_batch_interact

Executes an ordered list of deterministic browser steps in a single tool call. Intended for flows with multiple known targets discovered via the probes — e.g. fill three form fields, submit, and wait for results. It is a first-class helper, not routed through `browser_custom_action`. For a single ordinary visible text field, `browser_fill_form` remains the right tool.

**Parameters**:

- **steps** (list, required): Ordered steps; each step is an object with an `op` and target/value fields (below).
- **timeout_ms** (int, optional): Default per-step timeout. Default `5000`, clamped 250–30000.
- **wait_after_each_ms** (int, optional): Wait after each successful step, clamped 0–5000.
- **continue_on_error** (bool, optional): Continue after failed steps and report per-step errors. Default `false`.
- **global_timeout_ms** (int, optional): Hard timeout for the whole batch. Default computed from step count, capped at `90000`; explicit values clamped 1000–120000.

**Step operations**:

| Op | Description |
|---|---|
| `click` | Click the resolved target. |
| `fill` | Set an input's value directly. |
| `type` | Clear the field, then type the value key by key (optional `delay_ms`). |
| `autocomplete` | Type a query into the target, wait (`wait_after_type_ms`), then click the matching dropdown option (`option_text` / `option_selector` / `option_role`+`option_name`). |
| `select_visible_text` | Click a visible option in a custom (non-native) dropdown widget. |
| `select_option` | Select in a native `<select>` by `values`, visible label, or `index`. |
| `press` | Press a keyboard `key` (default `Enter`), on the target if given, else globally. |
| `set_checked` | Check/uncheck a checkbox (`checked`, default `true`). |
| `wait_for_selector` | Wait for a selector to reach a `state` (default `visible`). |
| `wait_for_text` | Wait for visible text to appear. |
| `wait_for_load_state` | Wait for a page load state (default `domcontentloaded`). |
| `sleep` | Wait `ms` milliseconds. |
| `extract_text` | Return the target's inner text (compacted to `max_chars`, default 500). |
| `extract_value` | Return the target input's value. |
| `screenshot` | Save a screenshot (`path`, `full_page`). |

**Target resolution** per step, in priority order: `selector` (CSS/Playwright — prefer `selector_hint` from probes) → `role` (+ `name`, `exact`) → `label` → `placeholder` → `testid` → `text`. The first match is used.

**Semantics**:

- Maximum 25 steps per batch; extra steps are dropped and reported (`truncated`, `dropped_step_count`).
- A step marked `optional: true` (or a batch with `continue_on_error: true`) records the failure and continues; otherwise the batch aborts with per-step diagnostics (target, error, elapsed time).
- The result includes per-step outcomes, final `url` and `title`, and a visible-text preview of the page.

### tool browser_custom_action

Runs a named deterministic helper from the action registry. Use it for actions that are awkward to express with the primitive tools; call [`browser_list_custom_actions`](#tool-browser_list_custom_actions) first to discover actions and parameters.

**Parameters**:

- **action** (str, required): Name of the custom action.
- **params** (dict, optional): Key-value parameters forwarded to the action. Aliases `source`/`target` and `source_x`/`source_y`/`target_x`/`target_y` are accepted for the coordinate-based actions.
- **session_id** / **request_id** (str, optional): Task scoping; default to the parent tool-call context.

**Built-in actions**:

| Action | Description |
|---|---|
| `browser_drag_and_drop` | Drag from a source to a target (selectors, visible text, or raw coordinates), with interpolated mouse movement (`steps`, `delay_ms`). |
| `browser_get_element_coordinates` | Resolve screen coordinates for one or two elements. Falls back from CSS selector to visible-text search when the selector does not match. |
| `browser_set_input_files` | Upload files into a file input (`selector` defaults to `input[type="file"]`). Detects Playwright strict-mode violations and suggests a more specific selector. |
| `list_upload_files` | List files available for upload under the `BROWSER_UPLOAD_ROOT` directory. |
| `browser_task` / `run_browser_task` | Delegate a whole natural-language task to the nested browser worker agent. Blocked (`recursive_browser_task_blocked`) when already executing inside the worker. |
| `ping` / `echo` | Health check / debug passthrough. |

### tool browser_list_custom_actions

List available custom actions with their summaries, when-to-use guidance, and parameter specifications. No parameters.

### tool browser_cancel_run

Cancel an in-progress browser task. Sets a cancellation flag (per session, or per request when `request_id` is given) and cancels matching in-flight asyncio tasks. A cancelled run returns `error="cancelled_by_frontend"`.

**Parameters**:

- **session_id** (str, required): Session whose task should be cancelled.
- **request_id** (str, optional): Target a specific request within the session.

### tool browser_clear_cancel

Clear the cancellation flag for a session (or a specific request) so new tasks can run again. Same parameters as `browser_cancel_run`.

### tool browser_runtime_health

Report runtime readiness and heartbeat status: whether the browser connection is healthy, whether the runtime is started, the timestamp of the last successful heartbeat, and the configured provider / API base / model name. No parameters.
