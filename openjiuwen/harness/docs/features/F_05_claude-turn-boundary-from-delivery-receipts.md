# F_05 Claude Turn Boundaries from Delivery Receipts

## Metadata

| Item | Value |
| --- | --- |
| Date | 2026-09-21 |
| Scope | `harness_providers/base.py` `_steer`, `harness_providers/claudecode` (lifecycle / harness / mapping / options / config) |
| Spec | S_19 harness providers (invariant 1), `harness_providers/AGENTS.md` invariant 11 |
| Tests | `tests/unit_tests/harness_providers/test_claudecode_lifecycle.py`, `test_claudecode.py`, `test_base.py` |
| Refs | Session `web_1a0bdfd432b_1eee9c02bd0e`: a Claude member had called tools that the trajectory only showed part of |

## Background

`_run_turn` consumed the SDK's `receive_response()`, which stops at the first
`ResultMessage`. That is only a turn boundary when the CLI answers every
message of the turn in one cycle.

Raw-CLI probes established that it does not:

| Case | Frames | Results |
| --- | --- | --- |
| Folded (steered while busy) | `A:queued→started` … `B:queued` … `A:completed` `B:completed` | 1, `num_turns=2` |
| New cycle (steered while idle) | `A:queued→started→completed` + result; `B:queued→started→completed` + result, each with its own `system/init` | 2 |
| Interrupted | `A:queued→started`, then a result with `subtype=error_during_execution`; **no** `completed` | 1 |

In the new-cycle case the turn ended at the first result, so the second cycle's
assistant text, tool calls and result escaped the protocol stream: the
trajectory showed only part of the work, and every later turn ran one cycle
behind, consuming frames the previous turn had left in the queue.

Two further facts shaped the result mapping: a result's `usage` covers only its
own cycle, while `total_cost_usd` is the running session total (0.136951 then
0.1850035 for a second, cheaper cycle).

## State and Decisions

**A turn is over once a result has arrived and no message the turn submitted is
still outstanding.** The CLI acknowledges every submitted message with
`command_lifecycle` frames keyed by the `uuid` that message carried, and a
message that is worked on always reaches `completed`.

- **Label outbound messages.** `SerializedTurnHarness.send` mints the receipt's
  `message_id` before calling `_steer`, which now takes it, so the id the host
  is handed is the id written to the CLI. The Claude provider submits a message
  dict through `query(AsyncIterable[dict])` rather than a plain string, since a
  string makes the SDK mint an unlabelled frame.
- **Read the receipts below the parser.** `parse_message` drops
  `command_lifecycle` (`case _: return None`), so `claudecode/lifecycle.py`
  wraps the transport instead: `LifecycleTap` records the ids written out,
  follows the receipts coming back, forwards every real frame unchanged, and
  appends one synthetic `{"type": "system", "subtype": "openjiuwen.turn.settled"}`
  frame when the turn settles. Any unknown system subtype parses into a plain
  `SystemMessage`, which is how the signal reaches the turn loop in band — no
  side channel, no cancelling a read, no race with the SDK's queue.
- **Build the transport in the provider.** The SDK builds one only when none is
  supplied, so `_connect` builds it (`build_claude_subprocess_transport`, or the
  `transport_factory` seam that SSH already uses) and hands the SDK the wrapped
  one. Supplying a transport also skips the SDK's resume materialization, which
  is a no-op here: it only runs for options carrying a `session_store`.
- **Consume to the sentinel.** `_run_turn` iterates `receive_messages()` and
  ends on the sentinel, not on a `ResultMessage`.
- **Degrade rather than hang.** A CLI build that reports no receipts at all
  settles on the first result (the old behaviour, self-calibrating on the first
  turn). An interrupted or failed cycle settles at once, because the receipts
  still outstanding never arrive. A message left unacknowledged for
  `lifecycle_ack_timeout_s` (10s) after a result is dropped with a WARNING
  `DiagnosticEvent`; a message the CLI has taken (`queued` / `started`) is never
  timed out, however long its answer runs. An abort drops its running message by
  design, so that one is not reported as a fault.
- **Merge the cycles.** `ClaudeTurnAccumulator` sums per-cycle usage from the
  raw counters (one place keeps Anthropic's cache accounting folded into the
  conventions), accumulates `num_turns`, and reports cost as
  `session_total - baseline`, the baseline being what earlier turns already
  reported; a reconnected CLI counts from zero, which reads as a drop and falls
  back to the reported total. `provider_data` keeps `session_cost_usd`. Before
  this, every turn reported the session's running cost as its own.

## Rejected

- **Waiting on an out-of-band event after the result.** The receipts never reach
  `receive_messages()`, so the loop would have to race `anext()` against an
  event and cancel it — and cancelling an async generator's in-flight `__anext__`
  leaves it unusable.
- **Counting `system/init` frames.** A folded message produces none, and init
  says nothing about which messages are still pending.
- **Deriving the boundary in the team layer.** The receipts are a CLI detail;
  the layering keeps vendor differences inside the provider.

## Verification

Unit tests cover folded, new-cycle, interrupted, receipt-less and
unacknowledged-message paths, plus multi-cycle usage/cost merging and the
receipt id round trip. Against a real CLI: a steer at 2s into a 10s tool run
folded into one turn (`num_turns=2`, both answers, no diagnostic); a steer
landing at the cycle boundary was answered in a new cycle and stayed in the same
turn (two final outputs, `num_turns=2`) where it previously leaked into the next
turn; an abort settled immediately; three sequential turns each kept their own
answer, with per-turn costs summing to the session total.
