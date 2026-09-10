# F_02 Configured Paid Search

## Metadata

| Item | Value |
|---|---|
| Date | 2026-09-09 |
| Issue | #1252 |
| Scope | Paid-search metadata, dispatch, and same-ID tool reload |
| Spec | `S_05_tools-contract.md` |
| Tests | `tests/unit_tests/harness/tools/web/test_paid_search_configuration.py` |

## Background

The paid-search tool was gated by the presence of any API key, but its model-facing
description listed every provider. Explicit selections could therefore dispatch
to a provider guaranteed to fail because no key was configured.

## Decisions

- Derive bilingual descriptions and the provider enum from the same configured
  provider reader used by automatic dispatch. Whitespace-only keys are absent.
- Keep `auto` and explicit configured-provider selection. Preserve the existing
  fallback order and HTTP request implementations.
- Re-read configuration at invocation and before each fallback. A queued call
  naming a removed provider uses the current configured fallback list. An
  unavailable environment override does not replace that list.
- Reload paid-search cards with changed metadata even when their IDs are stable.
  Do not change the same-ID reload behavior of other tools.

## Rejected Alternatives

- Registration order alone cannot constrain provider choices inside one tool.
- Returning a missing-key error for an obsolete choice wastes another model turn.
- Hiding the provider parameter entirely would remove valid explicit selections.

## Validation

Offline tests cover each provider in both languages, request/response handling,
key rotation, missing and blank keys, stale choices, environment overrides,
mid-fallback removals, and same-ID reload without changing other tool behavior.

## Limits

Presence of a key does not prove validity, quota, or network availability.
Requests already in flight cannot be recalled when configuration changes.
Hosts must refresh registered cards when applying configuration updates.
