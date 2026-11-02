# Browser dispatch facts and local recovery

## Metadata

| Item | Value |
| --- | --- |
| Date | 2026-10-13 |
| Scope | Undispatched strict-mode errors, stale observation coverage, local binding and recovery |
| Specs | S_05, S_18 |
| Refs | W1/W2/W6/W18 frozen October 9 traces; Refs: #1574 |
| Test baseline | F_23: 1,348 distinct SDK cases |

## Decision

Keep execution truth separate from task sufficiency. A complete executor call log proving
that a locator failed strict resolution before dispatch records rejected_before_dispatch.
Explicit dispatch, truncated/missing logs, and errors after execution remain uncertain.
Reuse the journal classification for native and compact operations; do not reconcile
unknown writes from page text or subsequent navigation.

PageState emits a stable stale-observation code with generation/ref/selector/target reason.
The existing invalidation hook accepts these codes and the exact legacy generation format,
marks the shared snapshot stale, and triggers the existing single bounded repair. It also
recognizes unexecuted stale steps in a partial batch without undoing earlier ACKs. Unknown
IDs, argument/permission failures and transport errors do not prove the page stale.

Guard predicates remain unchanged. Their results include an enumerated mismatch category
(URL, document, geometry, count, identity, field state or actionability), never field values.
Existing policy receipts retain the affected operation/object and mismatch. Two repeated
invalidations of that binding request one LLM recovery turn and retire only that candidate.
Fixed observations and unrelated local operations remain possible. A successful LLM refresh
with a new observation, or changed intent/binding, re-admits the operation subject to all
late guards; no denied action is automatically replayed.

Local handoff uses the existing objective, target_ids and allowed_operations. Binding
previews and Jev choices preserve role, link destination, region and bounded nearby text,
so an object-title link is not presented as an unlabeled business button. The LLM binds the
observed operation control; if it is missing, it observes or uses an ordinary tool instead
of naming the object and permitting all operations. No site-specific action inference.

Existing recent LLM actions retain a bounded navigation destination identity and the
observed page identity. Repeated navigation to the same observed outcome adds recovery
guidance; it neither blocks navigation nor counts URL changes as task completion. The LLM
chooses an observed alternative, authorized human handoff or truthful blocker report.

## Rejected alternatives

- Looser node checks, automatic rebinding/replay, global Jev shutdown or new action quotas.
- A second recovery manager, task planner or business-completion validator.
- Site-specific captcha, shopping-cart or advertisement rules.
- Reopening every reader on arbitrary text or time changes. Reader freshness limitations
  remain separate follow-up work; this change does not claim to solve asynchronous reads.

## Verification

Replay W2 strict-mode and W1 generation errors, plus negative cases for actual dispatch,
malformed errors, permissions and partial batches. Exercise guard diagnostics on synthetic
local DOM and policy recovery across changing captures, LLM refresh, other controls and
restored task state. Verify bound-control handoff and navigation feedback without a live
model or external website. Keep test logs isolated from service logs.

## Limits

Offline tests establish receipt/routing contracts, not model compliance or live task speed.
New service runs must check fewer recovery loops and preservation of necessary reads.

## Completed verification

- Final results by distinct test: 1,307 passed, 58 skipped for optional browser dependencies,
  no remaining failures. This combines the complete browser-tool suite and the final
  affected-file rerun (91 passed). All 46 new regression cases pass.
- W2's original ANSI strict-mode error passes through native, generated compact RPC and
  primitive receipts. Performed/truncated/missing-log/explicit-dispatch cases remain unknown.
  The four W1 legacy generation receipts enter the existing observation refresh group.
- Actual batch compilation and rail exception tests cover generation and native-ref
  errors, partial ACKs, and old/new error envelopes. One additional native-ref recovery
  error category was found by the new test and brought into the same contract.
- Generated guard JavaScript runs in an isolated Node harness for URL/count/document,
  signature/region/fields/value/disabled changes. No comparison predicate was relaxed.
- Policy tests cover passive recapture drift, one recovery handoff, remaining readers,
  unsuccessful/same-capture recovery, fresh LLM observations, new intent/binding and ACK
  reset. Local binding tests retain a remove control while excluding its object's link.
- Changed-line Ruff has no new findings; existing unrelated lint is retained. Git
  whitespace checks pass. Test logs are isolated; no provider or live website was called.

Evidence: Swarm `.tmp/jev-receipts-recovery-20261012/verification-summary.json`,
`regression.xml`, `final-targeted.xml`, `checks.json` and `turn.diff`.
