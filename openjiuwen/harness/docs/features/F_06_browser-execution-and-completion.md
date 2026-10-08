# Browser Execution and Completion Boundaries

## Metadata

| Item | Value |
| --- | --- |
| Date | 2026-09-22 |
| Scope | Batch page ownership, MCP decoding, Probe representation, advisory completion |
| Specs | S_05, S_18 |
| Baseline | Sanitized September 22 trace shapes and isolated DOM tests |
| Refs | #1574 (related page ownership; not complete concurrent-task isolation) |

## Background

Retained tabs matched unrelated URL waits. Real MCP arrays and redirects were
not recognized by evidence adapters. Field coverage therefore disagreed with
correct observed answers, while structured Cards could themselves contain wrong
prices or navigation content. More domain field aliases would not close this gap.

## Decisions

- Keep existing PageState, observations, evidence slots and the final payload.
  No additional manager, verifier, persistent outcome state or model round.
- Bind waits to the action source and its new pages. The MCP-selected page must
  agree with the page used by the compact Batch RPC. Preserve browser reuse.
- Decode transport envelopes before extraction. Keep source-relative URLs and
  requested/landed navigation relationships; never interpret executed code as data.
- Probe is an optional representation, not the sole completion certificate.
  Preserve text-node price boundaries, distinguish offscreen/disabled/occluded
  controls, and keep answer panels readable without counting them as organic ranks.
- Inferred slots/counts are extraction hints. Sourced observations support the
  worker's completion judgment without requiring every fact to fit an ontology.
  Explicit absence, concrete contradictions, explicit structured requirements and
  hard execution boundaries remain checked. Unverified fields do not demand a retry.
- Genuine incompleteness can use the existing terminal progress text once. Focused
  continuation keeps the user's goal and actual repair instruction, not a fill-slots task.

## Rejected Alternatives

Do not close old tabs or recreate profiles to avoid ownership bugs. Do not fix
false partial by declaring every answer completed. Do not add a second LLM judge,
site-specific completion ontology, lower time budgets or tighter context limits.

## Verification and Limits

The same-day review retains the earlier targeted-Probe, inline-AX and sourced
observation changes, but corrects their empty-result boundary. A nested empty
field is not confirmed absence, and cannot erase a sourced value merely because
it came from a detail page. Absence needs an explicit unavailable field status
and local raw evidence. Short nonempty observations are not rejected by a length
heuristic. No site-specific aliases, extra checks or model rounds are introduced.

Tests use generated scripts, raw MCP envelopes, retained-tab scenarios and local
DOM fixtures; no account login, real booking, purchase or live provider is needed.
Tests cover false completion as well as false partial. Real-site completion rate
and latency still require repeated user queries with unchanged goals and model.
Heuristic Card extraction is not a proof of real-world accuracy. Ambiguous page
selection remains explicit rather than guessed, and explicit partial/unknown
answers must not be promoted solely because an output string is nonempty.
