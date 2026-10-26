# Browser lean guards: general guarantees instead of special cases

## Metadata

| Item | Value |
| --- | --- |
| Date | 2026-09-29 |
| Scope | Browser runtime guards: cart proof, uncertain writes, budgets, stop point. A trial switch now; the rest decided and deferred |
| Specs | S_05, S_18 |
| Test baseline | Browser suite 1124 passed, 0 skipped (Playwright-capable interpreter, PYTHONUTF8=1) |
| Refs | F_09 to F_13; live Trip.com and Lazada checkout tasks of 2026-09-29 in llm, shadow and hybrid, in English and Chinese; no issue assigned |

## Background

Most runtime guards (reconciliation lock, `dispatched_unknown` as the default for failed steps,
`cart_delta` baseline, 60 s LLM wait cap) were written between September 18 and 25, one per
trace, and first met long real tasks on 2026-09-29. The per-phase budgets and keyword task
typing date from August. Across 21 run logs:

- **False uncertain writes.** In 65 of 66 click timeouts, Playwright was still waiting for the
  element to be visible, enabled and stable and never performed the click. Each was recorded as
  `dispatched_unknown`, and the reconciliation lock fired in 10 runs. F_10 already treats proven
  preflight failures as non-writes, but actionability timeouts were not recognised as such.
- **Cart adds refused.** `cart_baseline_required_before_write` refused every Lazada Add to cart,
  from Jev and from the LLM, while the user was logged in. The LLM called `browser_phase` verify
  and set but never built a `cart_delta` spec (SKU attribute, row, quantity and count selectors,
  deltas), and it then told the user "not logged in".
- **Keyword task typing.** It classed "帮我订…付款", "buy me ingredients" and "帮我买…" as simple
  (navigation plus extraction, 32 steps). A Trip.com booking hit `browser_phase_budget_exhausted`.
- **Per-task phase counters.** The counters never reset, so a shopping task runs out of
  navigation (12) by design. In the lean trial, about five of twelve page changes went on the LLM
  re-opening the cart to confirm an add.
- **Budget stops are final.** A budget stop is returned as blocked and not retryable, so TaskTool
  refused both the resume and a fresh call (`browser_query_resume_not_allowed`) with 4 of 5
  items in the cart.
- **LLM wait cap.** The 60 s cap applies in every decision mode, including `llm`, and was
  reported as `model_provider_unavailable`.
- **Unguarded stop point.** No guard stopped the only irreversible-risk action of the day, an
  LLM click on Trip.com "Book now" in `llm` mode. The host permission engine, whose semantic
  reviewer checks actions against the user's original intent, ships disabled.

## Guarantees

1. **Stop point.** Never perform the step the user said to stop before.
2. **No blind repeat.** Never repeat an action whose effect is genuinely unknown.
3. **Bounded.** Finish or stop within the user's time, and never loop without progress.
4. **Truthful.** Report what happened and why the task stopped, in plain words.

## Decisions and state

Implemented as a trial:

- **The switch.** `OPENJIUWEN_BROWSER_GUARDS=lean` (read per call). Any other value, or none,
  keeps strict behaviour, which is the default.
- **No cart proof protocol in lean.** `prepare_effects` returns without classifying cart effects,
  so an Add to cart is an ordinary click.
- **Truthful dispatch in lean (guarantee 2).** If a step's error or a native click/type/select
  error is a Playwright timeout whose call log never reaches "performing <action>", the step is
  `rejected_before_dispatch`. It is not a write and not reconciled. An action that was performed
  and then timed out stays `dispatched_unknown` and keeps the lock in both modes.
- **Interim strict fix.** Task typing also recognises buying and booking wording (buy, order,
  reserve, pay, 订, 付款, 支付, 买, 下单). The 17 read tasks keep their classification.

Decided and deferred:

- **Guarantee 3: budget by progress.** Replace per-phase counters with a stop after N consecutive
  steps without observable progress, keep the overall deadline, make the LLM limit an idle limit,
  and allow the one resume after a limit stop while progress was being made. On the tasks
  measured, successful checkout flows took 17 to 43 actions with 2 to 6 page changes when direct.
- **Guarantee 1: the stop point belongs in the host permission layer.** Enable the existing
  semantic reviewer route for browser clicks and types, with Jev as the reviewer client in place
  of an LLM.
- **Guarantee 4: plain blocks.** Every refusal the model sees names the rule, the fact and the
  allowed next action, and the terminal payload names the stopping rule. Today the LLM invents
  reasons when it cannot read a block code.
- **Removal.** Once lean has been proven, remove the strict cart protocol (`cart_verification`,
  the `cart_delta` handling in `phase_contract`, and the prompt text) and the per-phase counters,
  with their tests, in one planned change.

## Rejected approaches

- **Relaxing the cart rule without a switch.** It was committed and then reverted the same day.
  It changed strict behaviour without a side-by-side comparison.
- **Deleting `cart_verification.py` now.** Five files and the `browser_phase` schema and prompt
  depend on it. The switch already removes its effect, and deletion belongs to the planned
  strict removal.
- **A label list of commit buttons as the stop-point guard.** "Pay, book, place order" misses
  "Proceed", "确定" and icon buttons. The stop point is defined by the user's words, so it needs a
  judgement, not a list.
- **A site-specific "add worked" signal** (cart badge, toast, button text). No general signal
  exists. A generic before/after diff shows that something changed, not what. The LLM verifying
  by hand is the accepted fallback.
- **Raising phase budgets or adding more keywords.** This keeps the task-shape assumption that
  caused the failures.

## Verification

Lean and strict are tested side by side:

- an Add to cart without a contract is refused in strict and proceeds in lean
- a never-performed timeout locks in strict and is `rejected_before_dispatch` in lean
- a performed-then-timed-out click stays uncertain in both

Buying and booking prompts are complex, and a read-only lookup stays simple.

The stop-point judge was measured offline on 46 labelled cases from real 2026-09-29 targets and
synthetic commit buttons. Jev caught all Lazada commit buttons (0.93 to 1.00) and all
instruction-contrast cases, and allowed every navigation, filter, Reserve and Add to cart step.
It allowed the real Trip.com "Book now" (0.19). That case's instruction text and missing page
context favoured "safe", and whether that button pays or only advances to the final step was
not established.

Live lean trial, Lazada English, threshold 0.55:

| Mode | Cart start | Result | Time |
| --- | --- | --- | --- |
| LLM only | empty | 5 of 5 ingredients in cart, no checkout reached | 217 s |
| Hybrid | empty | 4 of 5 in cart, stopped by the navigation budget | 427 s |

Before the switch, no run in any mode added an item. No run in the trial clicked a commit button,
and none produced a duplicate line.

## Known limits

- **Lean is a trial.** Strict remains the default. The per-phase budget, the retry rule, the stop
  point and plain blocks are not yet changed.
- **Cart cleanup is unreliable.** The cleanup agent failed or was blocked in four of six
  completed attempts across the two lean batches, and one run reported leftover cart items as its own
  work. Evaluation runs must verify the starting state independently.
- **Bot checks.** Lazada's slider and Alipay security pages appear intermittently and need a
  human.
- **Clarifying questions.** The parent agent sometimes asks for the storefront or login before
  any browser work, which varies between runs.
