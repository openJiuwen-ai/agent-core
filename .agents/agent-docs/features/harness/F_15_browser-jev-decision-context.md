# Browser Jev decision context and handover repairs

## Metadata

| Item | Value |
| --- | --- |
| Date | 2026-09-29 |
| Scope | What Jev is shown, which options it is offered, and how its failures hand back to the LLM |
| Specs | S_05, S_18 |
| Test baseline | Browser suite 1124 passed, 0 skipped (Playwright-capable interpreter, PYTHONUTF8=1) |
| Refs | Live runs of 2026-09-29: 17 read tasks (Baidu, Bilibili, Douban) in shadow and hybrid; language A/B over logged requests; no issue assigned |

## Background

In shadow on 17 read tasks, Jev's median operation confidence was 0.48. It chose FINISH 28
times, and in 21 of those the LLM kept browsing afterwards. It matched the LLM's reader steps
in 8 of 29 cases. Nine further problems were found:

- **Failed targets reset per run.** They were reset for every subagent run and on every url,
  intent or re-render change, so a link that timed out twice was offered again in the next run.
- **No view of the LLM's steps.** Jev saw only its own receipts.
- **Covered controls were invisible.** They were dropped as not actionable, with no mention of
  what covered them.
- **Options were bare strings.** They carried no control state such as role, region, value or
  checked/expanded.
- **One error ended Jev for the task.** A single inconsistent answer (`invalid_choice_winner`)
  or an auth, billing or config error switched Jev off for the rest of the task. In one run a
  near-tie answer cost six later decisions.
- **FINISH ignored missing facts.** FINISH was offered while `runtime_progress` listed missing
  requirements, and no state field was explained to Jev.
- **Fill values came from the rewrite.** They were read from the browser subagent's English
  rewrite, which produced plan fragments (". On the search results page") in 21 of 21 cases,
  and 82 of 94 requests were marked ambiguous. `normalize_goal` only unwrapped the Chinese host
  envelope prefix, not "You receive a new message:".
- **Jev could not tell its view was partial.** `page_text` is cut at 2200 characters and result
  cards are often unread.
- **One failed step erased a fill.** When a later step in a batch failed, the successful fill
  before it was not bound for search submit, and it was reported to Jev as failed.

## Decisions and state

- **Failed targets are remembered per parent query.** A coarse target key (op, role, normalized
  label, href path or node signature, and the control's own checked/expanded state) is counted
  per parent query (`query_id` or the session id). It survives new subagent runs, url, intent,
  phase and re-render changes. After two failures from Jev, the LLM or `no_observable_progress`,
  the target is retired as `failed_target`. It is cleared only when that target later succeeds.
  The per-run action key and its recovery clearing are unchanged.
- **Jev sees the LLM's last six browser steps.** `state.llm_recent_actions` holds op, observed
  label and region, per-step outcome (ok, failed, not_run) taken from the journal, the fill value
  only for non-sensitive fields, and the destination host+path for navigations (never the
  query). Consecutive pointer moves become one entry. The field is part of the state
  fingerprint.
- **Covered controls are reported.** The interactive probe records `blocked_by`, naming the
  covering element (aria-label, else the text of the enclosing dialog or banner, else its tag).
  Up to eight such controls are sent as `state.blocked_controls` and are never offered as
  targets.
- **Target options carry control state.** They are sent as
  `{"action", "role", "region", "current_value", "checked", "selected", "expanded"}` with only
  observed fields present. `ActionMenu.criteria` and the action head stay strings for logs,
  guards and validation.
- **No failure disables Jev for a whole task.** A failure hands back only the current observed
  state, and a changed state asks Jev again.
- **Jev is told what each state field means.** The action-head instructions explain the state
  fields.
- **FINISH is gated on missing facts.** FINISH is removed from the menu while
  `runtime_progress.missing_requirements` is non-empty. HANDOFF stays available.
- **Fill values come from the user's own request.** `normalize_goal` accepts both envelope
  prefixes. `task_literals(original, intent)` takes search terms and quoted values from the
  user's request, keeps only those the current intent still names, and feeds both the menu and
  `intent_ambiguous`. The rewrite remains Jev's task text and its source of navigable URLs.
- **Jev is told when its view is partial.** `page_text_truncated` and `cards_observed` are sent,
  and the instructions say to choose a reader when task facts are not visible.
- **An acknowledged fill survives a failed batch.** Search binding uses the journal's
  acknowledged steps even when a later step in the call failed.
- **Payload and top-3 pick logs are opt-in.** They carry task text, so they are written only
  with `OPENJIUWEN_BROWSER_POLICY_DEBUG_LOG`. The LLM action log carries page labels only and
  is always written.

## Rejected approaches

- **Making `browser_phase` mandatory for search binding.** It adds an LLM turn per task, and the
  prompt calls it optional. In one September 24 trace 26 of 32 phase calls failed. The runtime
  binding above covers the need without it.
- **Feeding the original request as Jev's task text.** In an A/B over 20 logged requests it
  matched the LLM's next move 8 times, against 16 for the rewrite. The same rewrite translated
  into Chinese scored 12 of 35 where English scored 11 of 35, so the difference is the rewrite's
  explicit plan, not its language.
- **Retrying the same request to beat stochastic answers.** Repeated identical requests moved
  probabilities by about ±0.02, so a low-confidence answer does not clear the bar on retry.
- **Blanket threshold changes as the fix for low confidence.** Much of the low confidence came
  from the correct action missing from the menu (TYPE_TEXT was offered in 3 of 94 requests).

## Verification

Regressions cover:

- two failed clicks in run A are not offered in run B, and a later success re-admits the target
- the LLM's steps, outcomes, destinations and pointer gestures appear in the payload
- sensitive fill values are never sent
- covered controls appear with no candidate, checked in a DOM test with real Chromium
- the structured option shape
- any failure is step-scoped and re-admitted on a new intent
- FINISH is gated on missing requirements
- both envelope prefixes unwrap to the user's request
- search terms come from the request, not the rewrite, including when a subtask names only one
  of two values
- the partial-view flags
- an acknowledged fill is bound after a later step fails
- debug logs stay off by default and cannot change the route

Live results (17 read tasks, shadow, same day, before and after these changes):

| Measure | Before | After |
| --- | --- | --- |
| Median operation confidence | 0.48 | 0.62 |
| FINISH choices (premature) | 28 (21) | 14 (7) |
| Reader steps matched | 8 of 29 | 15 of 34 |
| Requests offering TYPE_TEXT | 3 | 27 |
| Requests offering PRESS_ENTER | 0 | 49 |
| Decisions that would execute at 0.65 | 12 | 38 |

In hybrid on the same tasks, stable answers were 5 of 5 correct in every run. At 0.55, Jev took
70 of 125 decisions. Against the LLM-only run with a similar LLM latency, the 17 tasks took 11%
less wall time and 46% less LLM time.

## Known limits

- **Complex flows.** On Trip.com and Lazada checkout flows Jev's median operation confidence was
  0.39, and it acted on 9 to 20% of steps, mostly navigation, card reads and simple clicks.
  It prefers "Go to cart" and PROBE_CARDS where the LLM verifies or scans controls.
- **Wasted repeats.** In hybrid at 0.55, 21 of 70 Jev steps were wasted, usually in pairs on the
  same target: actionability timeouts, runtime loop-detector refusals, and an Enter press that
  did nothing in Douban's search box. Retiring a target after one decisive failure and telling
  Jev about loop refusals are deferred.
- **Failed-target memory is in memory only.** The per-query failed-target store lives in the
  policy object and is lost on process restart.
- **The stop point is still unguarded.** Whether a click crosses the user's stop point is not
  checked by Jev or the runtime. See F_16.
