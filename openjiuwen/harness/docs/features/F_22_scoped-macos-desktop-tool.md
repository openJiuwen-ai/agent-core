# F_22 Scoped macOS desktop tool

## Metadata

| Item | Value |
|---|---|
| Date | 2026-10-21 |
| Scope | `tools/desktop/`, its unit tests, optional dependencies, S_05 |
| Test baseline | Python 3.13 offline suite: 963 desktop + 92 CUA passed; one Windows-only CUA test skipped |
| Refs | #1043 |

## Background

The existing `tools/cua/` provides a Windows/Linux desktop subagent. This
complementary, opt-in macOS tool lets the existing host model operate explicitly
shared windows. It packages the direct-control prototype without its inner agent,
model gateway, host context modifications, demo runners, or application-specific
workflows. WorkSwarm and OpenJiuwen runtime internals are unchanged.

## Data and lifecycle

`DesktopSettings` defines immutable host-granted targets and action/time limits.
`DirectComputerUseTool` registers as `computer_use`. Observation starts an exclusive
session with a subprocess worker. Subsequent actions require the latest observation
token and the owning session. Done, close, deadline, input failure, cancellation,
or the host turn-end callback releases the worker and lease. Applications remain
open. Standalone callers must close the tool in a `finally` block.

Window identities and process creation times fence stale targets. Screenshots
contain only approved windows; multi-window observations compose those captures
on black. Shared attached sheets are allowed; newly opened modal dialogs require
explicit host opt-in. Other document windows are excluded. Native permission and
foreground checks run before input. Ambiguous focus fails without guessing.
Opening-dialog metadata retries use the existing two-second grant deadline rather
than exhausting the ordinary three capture attempts first. Only observation is
retried; input is never replayed and no dialog is captured before identity checks pass.

## Decisions

- Use the normal `Tool` registration, callback and multimodal result contracts.
  The host owns reasoning, permissions, model/provider choice, history and cache.
- A single action returns its observation. A batch contains at most eight actions,
  stops at unsafe transitions, and returns progress plus a final observation.
  Later clicks require stable observed accessibility controls.
- The optimized path reduces subprocess round trips and redundant image capture;
  native state/focus checks remain between actions. Captured pixels are never
  reused as fresh observations. Unsupported capture bindings use window-only
  `screencapture`; a runtime capture failure does not expand scope.
- Keep native imports in the worker, optional dependencies behind `desktop`, and
  registration opt-in. No default tool catalog or existing CUA behavior changes.

## Rejected alternatives

- An inner model or model gateway duplicates the host loop and couples this tool
  to provider caching features. Neither is necessary for native execution.
- Whole-screen capture or application-wide sharing would expose unshared windows.
- Continuing a batch across changed windows or replaying partially applied input
  can operate on the wrong surface or duplicate text.
- Shipping the prototype's benchmark harness, recordings, host patches and new
  proposed features would obscure review of the reusable tool.

## Verification

The unit suite covers scoped capture, window/dialog identity, stale observations,
focus restoration, text/key input, batch boundaries, cleanup and ownership. Fake
native APIs and a headless subprocess exercise native contracts without controlling
the desktop. Host tests exercise model-driven tool invocation and streaming while
preserving history and the tool catalog. Deterministic performance tests compare
work counts, not claimed live speedups.

## Known limits

Native execution requires macOS, an unlocked interactive session and OS permissions.
Accessibility varies by application; incomplete or ambiguous state fails closed.
Host grants and tool permission policy remain the host's responsibility. The
description's task restrictions are guidance, not an OS sandbox. A draft PR and
offline tests do not establish live compatibility with every WorkSwarm deployment
or a universal performance improvement.
