# F_75 Idle Trigger and Interrupt Resume Settlement

## Status

Implemented incrementally on `fix/dev-stable-idle-trigger-self-deadlock`, based on
`upstream/dev-stable`.

## Root Cause Boundary

The original deadlock was a synchronous IDLE callback waiting for the same
`StreamController._interrupt_lock` held by a sender awaiting the NativeHarness
ACK. Upstream commit `7aac7162` already fixes that cycle by making the callback
schedule one owned drain worker. This feature does not replace or duplicate that
scheduler.

## Structured Follow-ups

`InteractiveInput` and ordinary text have different consumers and therefore use
different queues:

- StreamController retains inputs whose target interrupt has not committed yet.
- NativeHarness retains structured inputs consumed while a round is RUNNING in
  `HarnessInternalState.pending_queue`.
- Ordinary text keeps the existing `LoopQueues.follow_up` and
  `DeepAgentState.pending_follow_ups` path.

Native settlement reads the current session interruption state. A matching
structured input starts as its own round before text; it is never converted to a
list batch or checkpoint string. When no interrupt remains, stale structured
inputs are discarded and text batching resumes.

The structured queue is cycle-local. Graceful round settlement and terminal
stop clear it so an abandoned resume cannot cross a lifecycle boundary.

Tool duplicate suppression requires three facts: the queued message recorded a
non-empty tool scope at admission, its IDs belong to the active resume round's
tool scope, and those IDs are no longer pending after the resume handler was
entered. ReAct therefore leaves interruption state intact during invoke
preparation and clears it at `_handle_resume()` entry.

For a queued reply containing multiple IDs, suppression is field-granular. IDs
that are proven consumed are removed from a copied `InteractiveInput`; fields
that remain pending stay eligible for the next resume round.
