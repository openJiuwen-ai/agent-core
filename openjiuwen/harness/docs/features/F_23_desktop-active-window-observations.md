# F_23 Desktop active-window observations

## Metadata

| Item | Value |
|---|---|
| Date | 2026-10-21 |
| Scope | `tools/desktop/`, its unit tests, S_05 |
| Test baseline | Offline desktop suite; scoped live native smoke checks |
| Refs | #1043 |

## Background

Composing all shared windows on a display-sized canvas captures inactive windows
and can shrink a small active window unnecessarily. Screenshot-based models also
need an unambiguous image coordinate system. This change stays within the optional
desktop tool; the host model, history, WorkSwarm and OpenJiuwen runtime are unchanged.

## Data and decisions

`active_window_screenshots=True` uses the existing `view_window_id` contract when
native foreground identity uniquely matches a shared window. Only that window is
captured, up to `screenshot_max_side`, with its exact pixel-to-desktop geometry.
Other shared windows remain in the metadata catalog for explicit focus changes.
After focus loss, retain the last successfully observed active window as the crop
only while its live handle remains shared. `active_window_id` still becomes null;
`view_window_id` describes pixels, not keyboard ownership. This avoids changing
image scale merely because another application gained focus. Disappearance or
revocation retires the retained handle. With no retained shared window, or when a
shared sheet/window overlaps it from above, return the masked overview.
Metadata-only batch observations use the same geometry as full observations.

Optional `observation_settle_seconds` polls final full observations until two
successive pixel/geometry/window signatures match or the configured interval
ends. Intermediate batch observations do not poll. Every sample performs the
ordinary fresh capture and scope checks; only the latest frame and token return.
Performance metadata on both the returned observation and the worker's retained
observation counts all successful samples/captures and the full settling duration.
Discarded capture attempts are not included in capture counts. The interval is 0 by default
and limited to 0.5 seconds; visual stability is not application readiness.

Excluded same-app surfaces still mask the crop. Scope, focus, native hit testing,
tokens, partial-batch boundaries, cancellation and deadlines retain their guards.
Out-of-image errors state the exact allowed pixel ranges. The tool does not guess
whether an invalid model coordinate was normalized or replay an action.

Missing or unshared window focus is recoverable only at the initial action guard,
before any side effect. Use the existing `InputNotStarted` receipt to return fresh
scoped pixels and preserve the session for an explicit focus action. The same
failure during input remains fatal; release/capture failure, identity changes,
Stop and outside input also retain fail-closed behavior. Reuse a returned fresh
observation instead of asking for another identical screenshot.

## Rejected alternatives

- Full-desktop capture would expose unshared content.
- Enlarging a composed thumbnail cannot recover the original window detail.
- Removing checks or inserting task-specific actions would change the safety and
  agent contracts instead of reducing observation work.
- Automatically activating a window or replaying input after focus loss would
  invent actions and risk sending text to the wrong recipient.

## Verification and limits

Tests cover capture count, crop/input mapping, focus transitions, stale tokens,
metadata consistency, private-window masking, and overview fallback. Native smoke
checks cover form input, two-window editing, TextEdit, and scrolling.

Models must use the latest image geometry; input coordinates change after focus.
The default remains `active_window_screenshots=False` with no settling; the
existing `legacy` profile also disables both observation options. Tests cover
stable frames, continuous animation bounds, and skipping intermediate polls.
Fewer inactive captures do not guarantee
faster model responses or better completion across all applications.
Offline focus-recovery tests cover foreign applications, private sibling windows,
missing focus, partial input, explicit refocusing, and all three execution modes.
Live focus recovery and model-call reduction must be revalidated separately.
