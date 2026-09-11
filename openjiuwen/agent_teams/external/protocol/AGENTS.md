# external/protocol maintenance guide

This directory defines the provider-neutral public Python SPI for third-party
agent harnesses. It contains contracts and immutable value objects only. The
current Claude Code, Codex, and subprocess implementations remain outside this
directory until their explicit migrations.

## Scope

- `protocol.py`: behavioral Harness and provider factory Protocols.
- `models.py`: lifecycle commands, context, capability card, and JSON types.
- `events.py`: ordered observation-plane event envelope and payloads.
- `results.py`: normalized terminal result, usage, and failure types.
- `interactions.py`: awaited provider-request/host-response control plane.
- `checkpoints.py`: versioned checkpoint envelope and durable sink Protocol.
- `hooks.py`: awaited control-plane hook types and dispatcher Protocol.
- `tools.py`: native-tool gateway and provider-neutral MCP descriptions.
- `stream.py`: explicitly closable single-consumer event cursor.
- `serialization.py`: stable discriminator-bearing event JSON codec.
- `errors.py`: errors crossing this protocol boundary.
- `README.md`: package-level rationale and invariant summary.

Do not add vendor SDK imports, spawn logic, registries, session persistence,
team database access, or runtime adapters here. Those belong in implementation
packages that depend on this SPI.

## Terminology

- `Session`: the full conversation lifecycle.
- `Turn`: one external input through one stable external output.
- `Iteration`: one Agent Loop control cycle inside a turn.
- `Step`: one observable atomic execution action inside an iteration.
- `Round`: a multi-agent collaboration/protocol phase. Do not use it for a
  single-agent turn.

Public names and documentation in this package must preserve
`Session > Turn > Iteration > Step`. Use `turn_id`, `TurnLifecycleEvent`, and
`turn_events()` for the harness boundary. Do not introduce Round aliases for
these concepts.

## Invariants

1. `ExternalHarnessProtocol` remains a high-level, multi-turn behavior
   contract. Do not reduce it to only a provider-specific `receive_response`,
   raw notification, or single-turn driver interface.
2. `events()` is observation, interactions are SDK request/response control,
   and hooks are lifecycle policy control. An event consumer can never be
   required to return an authorization decision.
3. Optional behavior is capability-gated. Unsupported commands raise
   `UnsupportedHarnessCapabilityError` instead of degrading silently. Provider
   startup validates compatible protocol versions and every required
   fine-grained `HostCapability`.
4. Public commands are concurrency-safe by contract. Implementations must
   serialize their own state transitions.
5. The event stream is single-consumer, cycle-long, and strictly ordered. The
   envelope owns sequence, timestamp, and correlation metadata; payloads do
   not duplicate those fields. `events()` is the continuous view;
   `turn_events(turn_id)` is a serialized finite view that validates the next
   unconsumed turn and includes its STARTED and terminal events; it must not
   discard intervening turns as an out-of-order selector. PAUSED/RESUMED are
   non-terminal and retain the same turn ID. The stream views consume the same
   logical stream and cannot be active concurrently.
6. Checkpoints are opaque, versioned by the provider, JSON-serializable, and
   scoped to one member. Checkpoint IDs make retries idempotent and sequence
   numbers prevent stale overwrite. Harnesses proactively save material
   changes through the host sink; snapshot export is not the sole persistence
   path. Protocol code must not inspect provider checkpoint data.
7. Static implementation metadata belongs in `ExternalHarnessCard`; runtime
   values belong in `ExternalHarnessContext` or the live harness.
8. Context environment and credentials are sensitive. Never render them in
   events, exceptions, examples, or logs.
9. Terminal turn events carry a structured `TurnResult` whose status matches
   the event kind. Ordered messages are the lossless normalized output;
   final/structured output fields are projections. Shared event and result
   fields stay provider-neutral; namespaced JSON extensions preserve
   vendor-specific data.
10. Interaction responses must match request IDs and response types. Requests
    may declare a deadline; cancellation is idempotent and abort/stop cancel
    every pending request. Missing host interaction support must fail or
    decline safely; it must never default to approval.
11. Output events identify stable blocks and use explicit operation, channel,
    and content-index fields. Do not replace delta/snapshot/final semantics
    with a boolean or encode reasoning as a content representation.
12. Hook ASK resolves through tool-approval interaction; PROVIDER_POLICY
    delegates to an explicitly configured provider-native policy. Neither is
    an observational event response.
13. JSON values are recursively validated, copied, and frozen at construction.
    NaN, infinity, mutable container aliases, and arbitrary Python objects do
    not cross the provider-neutral boundary. In-process MCP instances are the
    only explicitly local opaque object.
14. Event buffers are bounded. Lifecycle, item, delta usage/output, final
    output, warnings, errors, and unknown events are REQUIRED and never drop.
    Only derived COALESCIBLE or BEST_EFFORT events may be compacted/dropped.
15. Public event cursors provide idempotent `aclose()` so early consumer exit
    releases the single-consumer lease. Wire events use the official codec and
    preserve unknown event types.

## Compatibility

- Public symbols are the names exported by `__init__.py`.
- Additive minor changes keep the current `PROTOCOL_VERSION` major version.
- Removing a member, changing command/event semantics, or making an optional
  capability mandatory requires a protocol major-version proposal.
- Keep event and enum values stable once released; providers may persist or
  transmit them.
- Do not add a backend-specific field to shared models. Put backend options in
  provider-owned configuration or context metadata.

## Change checklist

- Update `README.md` and
  `docs/dev/agent_teams/external_harness_integration.md` for public changes.
- Update `agent_teams/docs/specs/S_24_external-harness-protocol.md` when the
  long-lived contract changes.
- Add or update mirrored unit tests under
  `tests/unit_tests/agent_teams/external/protocol/`.
- Verify importability without installing optional Claude, Codex, or other
  provider SDK dependencies.
- Run the targeted tests and Ruff on changed Python files.
