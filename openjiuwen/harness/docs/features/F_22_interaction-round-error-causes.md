# F_22 Interaction Round Error Causes

## Metadata

| Item | Value |
| --- | --- |
| Date | 2026-10-18 |
| Scope | DeepAgent interaction-round failure outcome and outer execution wrapper |
| Spec | S_02 DeepAgent architecture, invariant 16 |
| Tests | test_deep_agent_round_errors.py; test_interaction_round_error_stream.py |
| Refs | openJiuwen-ai/sciencediscovery#235; agent-core PR #1317 |

## Background

A failed interaction round logged its exception but returned the fixed message
`interaction round execution failed`. Exceptions escaping the round entry point
were separately reduced to `round execution failed`. Hosts consuming the output
stream therefore could not report the original gateway, tool, or runtime cause.
PR #1317 established an inner-handler precedent on an older hotfix branch; both
handlers still discarded the cause on the development target of this change.

## Data and State

The existing `RoundOutcome`, `InteractionEvent`, and output-lease schemas remain.
Both handlers keep `round_execution_error`. The ordinary outcome is emitted as
`execution.error` through the existing output lease. No new API or state is added.

## Decisions

- Use the caught exception's string as the failure message in both handlers.
  Keep each existing generic message as its fallback for an empty string.
- Preserve traceback logging via `logger.exception` inside the active handler.
  Do not append traceback text, exception attributes, or request/configuration
  context to the stream payload. This is the existing exception-message contract,
  not an additional diagnostic dump.
- Keep `CancelledError` handling and `finally` cleanup unchanged. Inner
  cancellation propagates; the outer interaction wrapper handles cancellation
  without publishing an execution error.

## Rejected Alternatives

- Changing only host/UI copy cannot recover a cause already discarded by the SDK.
- Fixing only the inner handler leaves errors escaping it hidden by the outer one.
- Changing the stable code or adding a new protocol field creates unnecessary
  consumer work: the existing message field can carry the original detail.
- Moving the earlier hotfix's unrelated retry-policy changes into this fix would
  change execution behavior without addressing this reporting contract.

## Verification

The focused unit regressions call complete SDK methods with synthetic exceptions,
assert both nonempty and empty messages, retain the stable code, verify traceback
availability to the logger, and check cancellation and cleanup. The offline system
regression passes an error through the real round wrapper, InteractionEvent,
OutputSchema and output-lease consumer for both catch paths. It replaces only the
failure-producing dependency and session boundary emitter, requiring no model or
external service. On the unfixed target four message-preservation assertions fail;
the empty-message and cancellation checks already pass.

## Known Boundaries

Hosts must consume a package containing this change. This SDK fix does not select
a downstream UI classification or update ScienceDiscovery/Swarm dependency pins.
A host should not infer that every round failure indicates model configuration.
Live gateway serialization, browser rendering, original Nightly root causes and
real model behavior are outside these deterministic regressions.
