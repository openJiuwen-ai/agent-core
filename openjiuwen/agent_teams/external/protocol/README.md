# Third-party agent harness protocol

`openjiuwen.agent_teams.external.protocol` defines the public Python SPI for a
third-party agent harness that runs as an OpenJiuwen team member. The package is
independent of the current Claude Code, Codex, and subprocess runtimes; those
backends will migrate separately.

The current contract version is `4.0`.

## Boundary

```text
Team coordination / MemberRuntime
              |
 ExternalHarnessMemberRuntime
              |
     ExternalHarnessProtocol
              |
  Claude Code / Codex / Jiuwen SDK / other harness
```

`ExternalHarnessProtocol` is a high-level, multi-turn behavioral contract. A
conforming implementation owns the provider session, accepts concurrent
commands, emits a cycle-long ordered event stream, answers provider-initiated
interactions through host services when required, and publishes recoverable
checkpoints only when it declares that capability.

## Terminology

This protocol uses the following hierarchy consistently:

```text
Session
└── Turn          external input -> stable external output
    └── Iteration one Agent Loop cycle
        └── Step  one observable atomic execution action
```

`Round` is reserved for a multi-agent collaboration or protocol phase that may
contain turns from multiple agents. The single-agent harness API therefore uses
`turn_id`, `TurnLifecycleEvent`, and `turn_events()`.

## Public concepts

- `ExternalHarnessProtocol`: lifecycle, input delivery, abort/pause/resume,
  cycle-long `events()`, finite per-turn `turn_events()`, and checkpoint
  snapshot export.
- `ExternalHarnessProvider`: provider-owned configuration validation and
  construction of an unstarted harness.
- `ExternalHarnessCard`: static identity, protocol version, and optional
  harness capabilities, compatible protocol versions, and required/optional
  host capabilities.
- `ExternalHarnessContext`: member identity plus host services injected at
  `start`, including tools, MCP, hooks, interactions, and checkpoint storage.
- `HarnessEvent`: an event envelope with global ordering and correlation IDs.
  Its payload is provider-neutral; `ProviderEvent` preserves namespaced
  extensions without changing the shared protocol.
- `HarnessEventCursor`: a closable async cursor that releases the observation
  consumer lease on normal completion or early `aclose()`.
- `EventBufferConfig` and `event_retention`: bounded backpressure with derived
  required/coalescible/best-effort retention classes.
- `harness_event_to_dict` / `harness_event_from_dict`: the stable JSON codec;
  unknown shared event types survive decode/re-encode.
- `TurnResult`: a typed terminal result containing normalized messages,
  convenience output projections, termination/failure, usage, exact monetary
  cost, timing, and provider extension data.
- `HarnessInteractionHandler`: awaited request/response control plane for tool
  approvals, user input, MCP elicitation, dynamic tool calls, and provider
  extensions.
- `HarnessHookDispatcher`: lifecycle policy callbacks. Hooks are not the SDK's
  general request/response channel.
- `HarnessCheckpoint` and `HarnessCheckpointSink`: versioned, monotonically
  ordered provider state and an idempotent durable push path.
  `export_checkpoint()` remains available for snapshots.

## The three planes

```text
observation: Harness -> events() -> host consumer
interaction: Harness -> interaction handler -> response -> Harness
hooks:       Harness -> lifecycle hook -> policy result -> Harness
```

An event consumer never returns approval or user input. A hook event is only an
observation of a hook invocation. Provider SDK requests that block execution
must use `HarnessInteractionHandler`.

## Continuous and per-turn streams

The observation channel has two alternative consumption views, following the
Claude SDK distinction between continuous messages and one response:

```python
# Simple request/response workflow: correlate acceptance to its finite turn.
receipt = await harness.send(input)
async for event in harness.turn_events(receipt.turn_id):
    consume(event)

# Long-running team workflow: crosses turn boundaries, ends at stop.
async for event in harness.events():
    consume(event)
```

Both methods consume the same logical single-consumer stream. They are not
independent subscriptions and must not run concurrently. Repeated
Without a turn ID, `turn_events()` calls consume consecutive turns. A supplied
turn ID validates the next unconsumed turn; it is not an out-of-order selector.
Concurrent runtimes consume `events()` and group by receipt turn ID instead of
skipping intervening turns. `PAUSED` and `RESUMED` are
non-terminal transitions and do not close the iterator. The terminal event is
included so the caller receives the complete `TurnResult` before the iterator
ends. A second active iterator must fail with `ExternalHarnessStateError`.

## Minimal shape

```python
from openjiuwen.agent_teams.external.protocol import (
    ExternalHarnessCard,
    ExternalHarnessProtocol,
    HostCapability,
)


class MyHarness:
    card = ExternalHarnessCard(
        name="my-agent",
        implementation_version="1.0.0",
        required_host_capabilities=frozenset({HostCapability.TOOL_APPROVAL}),
    )

    # Implement every member of ExternalHarnessProtocol.


# Structural presence only; behavioral contract tests are still required.
# assert isinstance(MyHarness(), ExternalHarnessProtocol)
```

## Required invariants

1. Public commands are concurrency-safe and state transitions have one logical
   writer inside the harness.
2. `start` creates one cycle and settles in `HarnessState.IDLE`; idempotent
   `stop` closes events and settles in `TERMINATED`.
3. `events()` is the full-cycle stream; `turn_events()` is the finite next-turn
   view. They share one consumer, and envelope sequence numbers remain
   strictly increasing across every payload type.
4. Every turn emits one `STARTED` and exactly one matching terminal event.
   Pause/resume keeps the same turn ID and is not terminal. Every terminal
   event carries a `TurnResult` with the corresponding status.
5. `send` acknowledges acceptance without waiting for the turn to finish and
   returns the accepted input's turn ID. Steering returns the active turn ID.
6. Unsupported optional behavior raises
   `UnsupportedHarnessCapabilityError`; it must not silently succeed.
7. Awaited provider requests use `context.interactions`; events are observation
   and hooks are lifecycle policy callbacks.
8. Checkpoints are provider-owned, versioned, JSON-serializable, scoped to one
   `member_agent_id`, and carry an idempotency ID plus monotonic sequence. The
   sink rejects stale or failed compare-and-set writes.
9. Interaction responses match both the request ID and request type. Requests
   may declare a deadline; abort and stop cancel all pending interactions.
10. Output blocks have stable IDs, channels, indexes, and explicit
    DELTA/SNAPSHOT/FINAL operations. Terminal messages are authoritative;
    `final_output` is a convenience projection.
11. Provider startup validates the host protocol version and every required
    fine-grained host capability before doing work.
12. Events carry team-session/member scope. `correlation_id` groups a logical
    trace; `causation_ids` lists every exact input/request that caused an event.
13. JSON data is recursively validated and frozen. Event buffers are bounded;
    required events never drop, and cursors expose idempotent `aclose()`.
14. Wire transport uses the official discriminator-bearing codec and preserves
    unknown event types and schema versions.
15. Environment values, credentials, and provider client objects must never be
   copied into events, checkpoints, exceptions, or logs.

## Documents

- Third-party development guide:
  `docs/dev/agent_teams/external_harness_integration.md`
- Long-lived team subsystem specification:
  `openjiuwen/agent_teams/docs/specs/S_24_external-harness-protocol.md`

`external/member_runtime.py` now provides the provider-neutral projection onto
the internal MemberRuntime behavior. `external/dsh/` is the first protocol
implementation and is currently wired programmatically. The Claude Code and
Codex implementations remain under `external/cli_agent/` and do not yet
implement this package.
