# Browser local decision loop and shared observation lifecycle

| Item | Value |
| --- | --- |
| Date | 2026-09-25 |
| Scope | Existing browser policy, PageState, phase, journal and execution boundaries |
| Specs | S_05, S_18 |
| Test baseline | F_12 and frozen September 25 traces; generic deterministic integration fixtures |
| Refs | User request to implement six ordered improvements; no issue assigned |

## Background

The September 25 run exposed duplicate listing revisions, inconsistent observed
labels, missing popup landing evidence, cumulative recovery exhaustion and long
stream/tool waits. Jev dispatched only 26 of 307 windows, mostly navigation.

## Decisions and state

Reuse existing facilities. LLM supplies an optional local objective and observed
bindings once, or acts to recover. Jev chooses a closed action and target from one
multi-head request using the same provider-neutral System One schema. Runtime
owns one PageState, evidence acceptance and journal; tools own execution facts.
Runtime facts retire a completed first-result navigation constraint. No model
response can certify business completion or create an observed target.

Normalize observed labels at the projection boundary. An admitted action advances
interaction revision once; its subsequent observed filter change retires stale
cards without advancing that same interaction again. Unsolicited changes still
advance revision. Navigation evidence binds the observed selected link to the
actual landing receipt, including compact batches and popup selection. Returning
to an engine homepage is not proof of a selected result.

Recovery counts consecutive failed trials and resets on new observed facts.
Task deadlines and ordinary action budgets remain hard bounds. Browser calls use
the existing serial tool scheduler. Model streaming has both total and idle
bounds; tool calls use existing ToolCard resilience timeouts without write retry.

Unknown effects restrict potentially conflicting writes, not fixed reads or
observed local UI. Fixed text/find/snapshot/tab/wait operations reuse the page
action tool and exact late guards. Arbitrary scripts are never promoted to reads
by keyword matching. Repeated unsuccessful actions are excluded per operation,
observed node and relevant state; a failed button does not disable the whole
policy. A new binding/observation permits re-entry.

Fixed observers retain task, arguments, URL and document identity checks but may
refresh a viewport whose layout changed asynchronously. Mutation node/page guards
remain exact. Host routing sends closed readers through the native snapshot review
path; it does not grant permission or relabel arbitrary scripts.

The policy receives a compact view of the existing state, recent real receipts,
current bindings and missing facts. Executable URLs and identities are never
truncated. Runtime-owned observations may be combined within a single RPC and
published once; permission and precise target checks remain mandatory.

The post-action probe has a two-second bound. Its one-use observation is valid
for at most five seconds and the same page/generation/interaction revision/URL.
Probe failure preserves the action receipt without replay. Popup rebinding still
uses the native selected-tab path. Model waits are bounded to 60 seconds total
and 15 seconds of streaming idle; ordinary tool resilience uses 15/30-second
bounds with shorter configured values preserved. Consecutive replan limit is four;
Jev's 40 decisions and 0.65 confidence defaults are unchanged.

## Rejected approaches

No new workflow framework, independent executor, unsafe batch loop, fuzzy Jev
target rebinding, global confidence reduction, blanket budget increase or
site-specific task scripts. Public third-party speed claims are not our benchmark.

## Verification

Exercise generic sorting, popup/redirect, no-effect, multi-step recovery, partial
stream, cancelled tool, stale target and unknown business-write cases, including
negative evidence association tests. Validate multi-head parsing over both
TypeSafe and OpenRouter and preserve deterministic LLM fallback.

Related SDK regression scope: 1,171 cases. The full run passed 1,170, with one
new fixture failing from nested Playwright/asyncio event loops; after correcting
that test fixture, local-loop/handover tests passed 67/67. Final milestone,
evidence, factory and phase tests passed 159/159, with local Edge DOM execution
and no skips. Test sets overlap and are not additive. Host permission/log tests
passed 1,264; thirteen existing Windows path, shell-allowlist and symlink failures
were reproduced unchanged with the original permission router loaded in memory.
Both repository whitespace checks and staged Python syntax checks passed.

## Known limits

Real speed and task completion require matched live acceptance runs; deterministic
tests establish protocol, lifecycle and guard behavior only. Captchas, missing
credentials, unavailable fields and unsupported effects still need explicit
handoff or a partial result.
