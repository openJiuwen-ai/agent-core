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

## Abort and Teardown

Abort owns every queue that could restart the cancelled round. NativeHarness
discards structured inputs, transient text follow-ups, and persisted text
follow-ups for hard abort, graceful abort, and already-idle or paused aborts.
Graceful abort discards once when intent is accepted and again at settlement so
inputs arriving between the early ACK and the actual IDLE transition cannot
escape into a new round.

StreamController closes resume admission synchronously before its first await,
detaches its owned drain worker, and clears pending approvals. Non-terminal
cancel remains closed until the cancelled round reports IDLE; overlapping or
caller-cancelled requests cannot reopen it early. Stop, lifecycle drain, and
self-shutdown use a terminal latch that is reset only by the next cycle's
`start()`.

`TeamRuntimeManager.interact()` applies the existing `InteractGate` to
`InteractiveInput` as well as ordinary messages. The ticket remains held until
`resume_interrupt()` completes, while StreamController's closure remains the
teardown guard because Runner finalization currently begins before the gate is
closed.

## Workflow Interrupt Inputs

TeamHarness admission and NativeHarness settlement use one matcher over the
actual interruption state. Tool interrupts require a non-empty keyed subset of
pending request IDs. Workflow interrupts accept a raw value, including falsey
values, or keyed input containing the current component ID. Unknown state is
rejected, and workflow matches never acquire tool duplicate provenance even
when IDs happen to be equal.

ReAct converts a raw workflow reply into a new keyed `InteractiveInput` for the
current component. It preserves the raw value without string conversion and
does not mutate the caller's raw-bearing object.

NativeHarness also records the pending workflow and component IDs when a
workflow reply is admitted. Queue settlement requires that exact slot to still
be current, so a duplicate reply cannot be reinterpreted as feedback for
another component, a different workflow that uses the same component ID, or a
tool interrupt with the same ID.
