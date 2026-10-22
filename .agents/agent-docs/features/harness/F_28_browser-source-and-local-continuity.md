# Browser source retention and local continuity after develop integration

## Metadata

| Item | Value |
| --- | --- |
| Date | 2026-10-16 |
| Scope | Existing source windows, completion protocol, local phase, PageState bindings and page-tool errors |
| Specs | S_05, S_18 |
| Test baseline | Z20/Z21 source loss; Z19/Z41 detours; Z23 scroll round trips; Z40 missing tab index |
| Refs | Multiversion analysis section 12; local implementation requested without a new issue association |

## Background

The F26 selector favored the earliest content of early sources. It could discard a later
full-page observation while retaining local find results and older search sources. The
parent could therefore miss post-action facts even though the shared journal still had ACKs.
An independent completion-protocol gap accepted empty model output when old page metadata
contained typed values. Long reasoning ending in `length` could consequently look completed.

F27 released local UI candidates after an expired bound phase. A harmless impact class did
not imply relevance: search and breadcrumb clicks could undo the LLM's selected page.
Objective-only phases lacked a page scope. Generic decision snapshots also exposed fewer
controls than the shared target registry, making some current explicit bindings impossible.
These fixes are integrated with the latest develop implementation; they do not replace the
core model streaming, tool lifecycle or existing F26/F27 changes wholesale.

## Data and transitions

- Existing source records carry `read_scope` (operation, optional query/target/viewport).
  Duplicate reads refresh their reference and recency. The 20-record state and six-record
  parent excerpt retain recent sources, preferring the latest broad source content before
  local diagnostics. Unknown script scope is not promoted to a whole-page read.
- A reasoning-only response with no answer or tool calls returns the existing interrupted
  result. `model_output_incomplete` identifies `finish_reason=length`; `model_output_empty`
  identifies other empty endings. Facts survive, and existing deadline/resume limits decide
  recovery. A page title/URL cannot substitute for the worker's final answer. The legacy
  explicit progress-tool path can still return actual extracted structured values.
- The existing phase stores its observed document and source URL, including objective-only
  phases. A new document or route marks the existing local handoff. Same-document query or
  filter changes do not require a phase per step. Missing observations alone do not expire it.
- Pending handoff retains fixed reads and allowed scrolling. It does not release click,
  navigation or other local UI merely because its impact is non-business. A fresh LLM phase
  restores its selected operation/object scope without any business completion conditions.
- Existing Jev receipts retain action source/destination and bounded scroll positions.
  An explicit acknowledged LLM return suppresses that particular detour in the same phase.
  An exact repeated scroll round trip on the same document/layout/phase yields that direction
  to the LLM. Other reads, objects and new scopes remain available; LLM tools are not blocked.
- PageState records the interaction revision of each probed control. Phase validation may use
  exact current-registry identities from the same document/revision. Only explicitly selected
  bindings supplement the Jev menu and take priority before its existing candidate limit;
  unrelated registry history does not expand the default menu.
  Stale generation/observation and missing identity errors remain explicit refusals.
- Page-operation shape errors include missing/unexpected fields, required keys and an example.
  `select_tab` needs both the index and exact URL from observed tabs. Examples are not bindings,
  and invalid arguments still produce `rejected_before_dispatch`.

## Decisions and layer ownership

The LLM interprets the goal, selects the business object, restores a local intent and writes
the conclusion. Jev chooses among compiled local options. Runtime owns shared observations,
dispatch facts and precise freshness checks. Tools supply fixed reads and executable target
facts. Source retention uses scope and time, never judgments about cart contents or hotel
requirements. Historical text remains useful context without granting executable node access.

The existing output diagnostics, client first-token/idle waits and task deadline remain the
owners of long streams. Parent prompts explain incomplete model endings and prefer retained
facts before a focused resume. Existing popup/same-page waiting guidance is retained. This
change adds no provider call, alters neither confidence threshold, nor grows action budgets.

## Rejected alternatives

- Unlimited history, larger evidence windows, business row certification or a second memory store.
- Automatically declaring completion from page metadata, repeating ACKed writes to recreate facts,
  or treating empty output as evidence that an earlier action did not occur.
- Globally disabling Jev on stale intent, releasing all local UI after one handoff, or rebinding
  by matching labels. Current controls still pass the existing dispatch guard.
- Website-specific search/breadcrumb/scroll rules, global read/scroll quotas and automatic plans.
- A whole-stream 60-second cap, lower decision thresholds or uniform larger budgets as a substitute
  for fixing fact loss and incorrect handoff.

## Validation

`test_browser_source_continuity.py` covers source competition, scope/reference preservation,
empty output versus real answers/tools, bound/unbound phase retirement, filter changes,
registry freshness, phase-to-Jev dispatch, correctable page arguments and local detours.
Existing phase, decision, receipt, stream, deadline and runtime tests remain regression coverage.

The frozen Z20/Z21 raw-result sequence was passed through the actual record extraction and
selection methods. The latest cart source containing the LG item survives both existing limits;
the previous selector loses it on the same sequence. This reconstructs last_page/phase version,
not the complete Runtime state or a new browser run. Retained Z19 controls verify that SEARCH
is removed after document handoff while reading remains. Z41 uses its actual objective and
breadcrumb with a source-document fixture because its phase result did not save that document.
Z40's actual malformed select_tab arguments now report missing index before dispatch.

Local policy fixtures test candidate admission and output shape. They do not measure model
confidence or prove live task success. No paid-provider A/B, real shopping or live account
actions are part of this validation. The full browser suite passed 1,503 tests, including
local Edge DOM tests, with no skips. After the final selected-target ordering and invalidated-
observation checks, 169 related tests passed (three local-browser skips, 19 DOM/ARIA cases
excluded from this repeat). Swarm integration passed 52 tests; its existing flaky browser
delegation-chain test stayed skipped. All 33 new regressions passed in the final related run.

## Known limits

A bounded excerpt cannot retain every source indefinitely. An omitted source is not proof
that work was absent. These corrections do not guarantee model compliance with local intent
or eliminate long reasoning before a length/deadline ending. A first off-target selection in
an unbound task can still occur; the local receipt rule prevents the same confirmed detour
from being repeatedly reintroduced. Live quality, effective actions, usage and time still
require same-goal/same-endpoint evaluation after restart, separately from synthetic menu tests.
