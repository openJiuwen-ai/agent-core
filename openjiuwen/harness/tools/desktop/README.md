# Scoped macOS computer use

An optional `computer_use` tool driven by the existing host model. It operates
explicitly shared windows through a bounded local worker. It complements the
Windows/Linux `tools/cua/` subagent; neither that subagent nor host internals change.
The package provides one tool, with optimized execution and batches of up to eight
actions enabled by default. The host model chooses when to group predictable actions.

## Install and register

From this checkout, with Python 3.11–3.13:

```sh
uv venv --python 3.13
uv pip install --python .venv/bin/python -e '.[desktop]'
```

Native use requires an unlocked macOS desktop and Accessibility plus Screen &
System Audio Recording permission for the launching host. These OS permissions
are broader than the tool's window grants; the tool separately limits capture
and input to host-approved windows. ScreenCaptureKit screenshots need macOS 14+;
`capture_backend="auto"` uses window-only `screencapture` if bindings are unavailable.

Register through the existing public API. `model` is the host's configured vision
model; `shared_pid` and `shared_window_id` come from the host's explicit user grant:

```python
from openjiuwen.harness import create_deep_agent
from openjiuwen.harness.tools.desktop import DesktopSettings, DirectComputerUseTool

settings = DesktopSettings(
    enabled=True,
    targets=[{"name": "shared-document", "pid": shared_pid, "window_ids": [shared_window_id]}],
    window_navigation=True,
    accessibility_enabled=True,
)
tool = DirectComputerUseTool(settings)
agent = create_deep_agent(model=model, tools=[tool])
# Invoke the agent through the host's usual session and permission flow.
# Close on host shutdown as well:
# await tool.close()
```

The existing YAML plugin loader also accepts serialized settings, without host
source changes. This disabled example is safe to load headlessly:

```yaml
id: scoped-desktop
tools:
  - type: harness.tool.import
    params:
      import_path: openjiuwen.harness.tools.desktop.DirectComputerUseTool
      settings:
        enabled: false
```

Load that file through `agent.load_plugin(...)`, or a host's existing plugin UI.
Before enabling it, add targets from the host's window-sharing/consent flow and
apply the normal tool permission policy. This package does not implement that UI
or change WorkSwarm configuration. WorkSwarm deployments must support the current
SDK plugin/tool contract; old prototype gateway profiles are not accepted here.

## Actions and sharing

- Start with `operation="observe"`. Every native action uses the latest returned
  `observation_id`. Actions return a fresh image and receipt through normal SDK
  multimodal tool results. The host controls their conversation history.
- With `active_window_screenshots=True`, an unambiguous active shared window is captured alone at up to
  `screenshot_max_side` pixels. `context.view_window_id` identifies that crop;
  use its image pixels for input. Other shared windows remain listed for `focus`.
  With accessibility enabled, a single unmodified left click on a uniquely observed
  checkbox/radio can use native activation when the exact point passes fresh hit
  testing. It never snaps to nearby controls, infers desired state, or retries input.
  Unsupported native activation keeps the original physical click.
  After focus loss, the last active window remains the crop while it is still
  shared. `context.active_window_id` becomes null: visible pixels do not authorize
  input. With no retained shared window, or a shared sheet/window overlapping it
  from above, the tool returns the masked display overview.
  Set `active_window_screenshots=False` to use that overview on every observation.
- Optional `observation_settle_seconds=0.25` polls only final full screenshots for
  two matching frames, within a bounded interval (at most 0.5 seconds configured).
  It can avoid showing a transient animation to the model. It does not prove task
  completion or add waits between characters. Both new observation options are
  disabled by default; enable them explicitly when evaluating model behavior.
- `focus` selects an approved target/window or an observed editable control.
  Missing/unshared focus at the initial input guard returns a fresh observation
  and keeps the session open for explicit refocusing. No action is replayed.
  Use that returned observation directly; an extra `observe` call is unnecessary.
  Focus loss after input starts, outside input, and release/capture failures still
  stop the session.
  Click, type, shortcut, scroll, move, drag and bounded holds use native input.
  `type` sends a whole text string in one tool invocation, even in single mode.
- Targets may use exact window IDs, or a unique exact window title plus PID/bundle.
  Granting a PID/bundle alone does not share all application windows. Attached
  sheets follow their parent; newly opened dialogs require
  `share_opened_dialogs=True`. Other document windows remain private.
- Explicit `allow_launch=True` targets may launch a configured bundle and share
  its newly opened windows. Launch arguments are host-only configuration. Use
  fresh/isolated application instances when granting this broader capability.
- Native typing rechecks editable focus and secure fields. Keyboard-mode text is
  restricted to a validated noneditable window. Missing/ambiguous state stops
  input; it does not select an application-specific fallback.
- `done`, `close`, or `release_after=True` releases control without closing apps.
  Completion is a model claim (`completed_unverified`), not proof a file was saved.
  Host turn-end cleanup and deadlines also reap the worker. Standalone callers
  must `await tool.close()` in `finally`.

Sharing is enforced by this worker, not an OS sandbox. Screen content is untrusted.
The host still owns consent and permissions for consequential actions. Optional
`evidence_dir` stores screenshots and trace metadata locally; leave it unset unless
those contents may be retained.

## Execution defaults and benchmark configurations

Normal integration uses the optimized defaults. Single-action and batching-only
settings below are comparison baselines and diagnostic/rollback controls for the
same tool; they are not separate tools to install or register.

| Mode | Settings | Execution and observations |
|---|---|---|
| Single actions | `max_batch_actions=1`, `optimized_batches=False`, `capture_backend="screencapture"`, `compact_tool_results=False` | One action per tool invocation; fresh image after each action. |
| Batching only | Same, but `max_batch_actions=8` | Up to eight model-selected actions per invocation; serial worker requests and intermediate capture/checks, with the final observation returned to the host. |
| Optimized | Defaults: eight actions, `optimized_batches=True`, `capture_backend="auto"`, `compact_tool_results=True` | Eligible sequences use one worker request; native checks remain between actions, redundant image work is reduced, and the final image plus batch progress is returned. |

The host may issue several tool calls per model response; “single” does not enforce
one model request per action or character. Batch actions cannot depend on reading
intermediate screens. Changes in window/control identity stop a batch, with partial
progress reported and no automatic replay. Launch is separate. Later clicks require
stable, uniquely identified accessibility controls.

The `DESKTOP_TOOL_PERFORMANCE=legacy` rollback switch disables optimized batching,
in-memory capture, compact results, active-window crops and final-frame settling;
it preserves the configured batch size.
Leave it unset to use explicit settings. The tool makes zero model calls, supplies
no provider-specific cache parameters, and works regardless of model cache support.
These options reduce local work; live speed also depends on model calls, app latency
and task choices. No universal speedup is claimed.

## Offline verification

```sh
uv pip install --python .venv/bin/python --group test
.venv/bin/python -m pytest -o addopts='' tests/unit_tests/harness/tools/desktop
```

Tests use fake native APIs and headless subprocesses: they exercise sharing,
focus, cancellation, batching, registration and the real host loop with a fake
model. They do not post desktop input, call a paid model or prove live WorkSwarm UI
operation. The deterministic performance tests assert reduced work and equivalent
results, not wall-clock claims about native applications.
