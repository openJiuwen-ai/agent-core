# F_04 Authoritative Terminal Stream

## Metadata

| Item | Value |
| --- | --- |
| Date | 2026-09-15 |
| Scope | DeepAgent terminal delivery, ReAct result envelope, Browser Rail, TaskTool |
| Spec | S_02 DeepAgent architecture |
| Tests | test_deep_agent_terminal_stream.py and neighboring lifecycle/TaskTool suites |
| Refs | Local reproduction of a completed Bing weather read reported as missing authority |

## Background

DeepAgent forwarded the inner answer before AFTER_INVOKE ran. Both the outer
stream and TaskTool copied this payload. Adding authority later could not update
the result already delivered. An omitted session also left outer rails without
the state that the inner ReAct created. ReAct's answer emitter dropped extension
fields, and TaskTool raised on structured partial outcomes as if they were
unstructured exceptions.

## State and Decisions

No new manager, completion schema, model protocol or browser lifecycle is added.
The invocation keeps one pending terminal chunk and its existing result object.

- Restore a single-round session for an explicit conversation_id before outer
  BEFORE_INVOKE; share it with ReAct. Calls without both session and conversation_id
  retain their existing sessionless outer lifecycle and expert-role behavior.
  Do not reuse the parent session or change task-loop ownership.
- Continue streaming intermediate events immediately. Buffer answers, finalize
  through existing rails, save state, then emit the final result once.
- Preserve envelope metadata and honor replacement of InvokeInputs.result.
- Forward interrupt events without synthesizing success from provisional text.
- Preserve ReAct result extension fields and structured Browser error outcomes.
- Log Browser task_end after finalization so its result keys describe the result
  that is actually delivered. A missing payload is an internal transport error,
  not evidence of a website blocker.

## Rejected Alternatives

- TaskTool reading private DeepAgent attributes or checkpoints after streaming:
  this duplicates ownership and introduces timing-dependent recovery.
- Emitting an initial answer followed by a corrected answer: consumers could act
  on the first one or duplicate user output.
- Always accepting the model's natural-language completion claim: this hides
  genuine missing fields and blockers.
- Reintroducing the paused target/price/evidence heuristics in the same change:
  their independent effects need separate validation.

## Verification

Tests cover dict and OutputSchema envelopes, implicit/explicit sessions, rail
replacement of a result, state-save ordering, text-only fallback, multiple inner
answers, interrupts, cancellation and method-level aclose. A real
TaskTool -> DeepAgent -> ReAct stream -> BrowserRuntimeRail path uses mocked model
and browser I/O only, covering completed, partial, blocked, execution error and
max-iteration outputs. Unstructured errors still raise.

## Known Boundaries

- This fixes delivery, not evidence inference. Weather text returned as
  `{sel, text}` may still require evidence normalization; no unsupported
  completed status is fabricated to hide that separate issue.
- The generic BaseAgent callback decorators do not immediately close nested
  generators on instance-level aclose. Method-level DeepAgent closing is tested;
  a broader callback-wrapper cancellation change is outside this fix.
- Caller-owned sessions keep their existing persistence ownership. No Chrome,
  profile, cookie, MCP cold-start, locator, replan or timeout policy is changed.
- Packaged applications must include this SDK change; editing the source checkout
  does not patch an already-installed executable.
