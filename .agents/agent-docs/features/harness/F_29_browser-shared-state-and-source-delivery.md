# Browser shared-state lifecycle and complete retained-source delivery

## Metadata

| Item | Value |
| --- | --- |
| Date | 2026-10-21 |
| Scope | Existing browser callbacks, Session snapshots, source projection, guards and delegation |
| Specs | S_05, S_18 |
| Test baseline | AA4/AA19 retry, AA41 rebinding, AA4 multi-page reads and Z20/Z21 later cart reads |
| Refs | Multiversion analysis section 17; no issue association requested |

## Background

F28 checked empty output in an after callback which core also invokes for failed attempts.
It could cancel an already scheduled retry. Phase updates assumed a replacement dictionary,
but Session recursively merges updates: omitted handoff and recovery fields survived. Both
problems were missed by tests using plain dictionaries instead of the actual core lifecycle.

The parent received only six records with at most 4,000 characters each. Adjusting which
records won those six slots preserved one source while displacing another. The parent needs
more of the observations already collected, not a site-specific importance classifier.

## Decisions and ownership

- Validate output only after a successful model attempt. Exceptions keep existing retry and
  structured interruption ownership. A real empty/length response still cannot be completed
  by page metadata. No new retry layer or automatic browser write replay is added.
- Reuse phase_contract.save as the single save path for complete browser task snapshots.
  Use Session's explicit deletion before replacing this owned key, synchronously with no
  await between updates. Other session state is untouched. This also makes clearing resume
  errors and invalidated-observation fields effective. Use the real get-copy/merge path in
  regressions, including checkpoint round trips and sibling-state preservation.
- Keep the existing 20-record history window. Prioritize substantive sources over metadata
  when that window fills, retaining source, scope, chronology and recall references. Do not
  interpret script content as business evidence or as a fresh executable binding.
- Return every deduplicated retained observation to the parent instead of reselecting six.
  Retain up to 12,000 characters per source record and a 96,000-character text budget in the
  returned projection. Short records remain complete; larger records share the remaining
  budget without dropping their identities/references. Explicit lengths and truncation flags
  distinguish a bounded excerpt from the original source. This is a source-text budget, not
  a claim that the entire serialized result or an arbitrary execution history is unbounded.
  Legacy checkpoints without original-length metadata report null completeness rather than
  asserting that previously clipped text was complete.
- For parents with a configured browser subagent, reuse the existing offloader's protected
  tool/argument patterns for that subagent's task result. Its already bounded delivery must
  not immediately become another head/tail excerpt. Other task results, tool outputs and
  whole-context compression keep their existing behavior; no second delivery store is added.
- Pure URL navigation depends on session/page/document and arguments, not unrelated page
  geometry. Observed-link and first-result navigation additionally retain their live link
  and order checks. Node actions retain exact identity and actionability. Other page actions
  keep their existing dependent state checks.
- A binding error explains the missing identity and the existing fixed probe needed to obtain
  it. No silent same-label rebinding, force-click or relaxed old-generation execution is allowed.
  Unperformed action errors retain executor-reported visibility/stability/obstruction categories
  in the existing journal. These categories do not change dispatch classification or guess causes.
- The cloned browser model supplies max_tokens=8192 only when the caller has supplied no total
  output limit (including provider-native aliases). An explicit reasoning token budget is
  preserved with room for answer/tool output. Existing explicit total limits remain untouched,
  as do the parent model, client waits, task deadline and confidence thresholds. This default is
  a bounded starting point, not a model-quality or latency guarantee; site work is not retried
  automatically after a length ending.
- Parent planning may suggest efficient sources and enrich the strategy. Such preferences
  must remain distinct from user requirements: prefer an available direct answer/widget, then
  inspect another suitable source when unavailable. Preserve explicit site/result constraints,
  inequalities, budgets, authorization and endpoint. Historical and current observations are
  time-scoped; a changed page does not rewrite previously observed facts.

## Rejected alternatives

- Site-specific cart/title/weather ranking, a second evidence store or a business verifier.
- Unlimited parent history, silently clipped text, or instructing the LLM to reconstruct
  missing facts by repeating ACKed mutations.
- New global Jev gates, lower confidence thresholds, all-purpose force-click and larger
  action/task budgets. Output work belongs to existing model request budgets, not another
  runtime timer or a renewed whole-stream 60-second cutoff.
- Assuming plain-dict mocks prove persistence or source-only inspection proves rail behavior.

## Validation

Use real core rail and StateCollection with offline models/executors. Exercise failed-then-
successful attempts, exhausted failure, normal length/empty endings, new phase followed by
Jev candidate construction, reset/resume, and source projection with both earlier and later
facts. Use frozen AA/Z sequences as offline evidence, not as a replay of live provider decisions.
Completed validation on 2026-10-21:

- Browser unit suite plus context-processor/delivery regressions: **1601 passed**, no skips.
  Includes actual headless Edge fixtures with temporary profiles and local DOM only; no
  authenticated browser session or external website was used. Two existing Pydantic
  deprecation warnings remain. The initial sandboxed run could not launch Edge; the final
  run used the permitted process environment and passed those paths as well.
- JiuwenSwarm prompt, adapter, tool-routing and browser-config integration: **143 passed**.
- Frozen AA41 set parameters/control identities survive real StateCollection checkpoint
  restoration with the intended click visible after both replacements.
- Frozen AA4 projection returns both page titles (9 observations, 15,499 text characters),
  while the former six-record projection omitted the first. Replaying Z20/Z21 source reads
  retains the later LG cart observation within the same 20-record history and declared text budget.

Evidence and reproducible scripts are in the host checkout's ignored task folder
`.tmp/jev-section17-20261021/`; raw logs and trace fixtures are not part of the SDK change.
No provider-quality/latency A/B was run, and no browser writes were replayed against user accounts.

## Known limits

Bounded history still cannot hold all observations indefinitely; existing recall handles are
the recovery path. Prompt changes guide model planning but cannot certify compliance. Long
reasoning and click actionability need model/executor-specific diagnosis, not business rules.
