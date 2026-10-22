# F_24 Desktop exact-point toggle activation

## Metadata

| Item | Value |
|---|---|
| Date | 2026-10-21 |
| Scope | `tools/desktop/`, its tests, S_05 |
| Test baseline | Offline native contracts; scoped live coordinate diagnostics |
| Refs | #1043 |

## Decision

Native checkbox and radio frames can include pixels which identify the control in
AX hit testing but do not respond to a physical mouse pair. The existing explicit
element-ID path already supports one guarded AXPress. Extend that path to a single
unmodified left coordinate click only when its point is inside exactly one observed,
enabled/actionable toggle and fresh native hit testing proves that exact recipient.

Keep the requested point through capability queries and final guards. Revalidate
scope, identity, state, geometry, hit ownership and Stop before the one activation.
No nearest-control lookup, coordinate normalization, desired-state inference or
input replay is permitted. If AXPress is unsupported, use the original physical
point after revalidation. Once AXPress is attempted, retain the existing timeout
verification and no-retry behavior. Other controls and pointer gestures keep their
physical semantics; accessibility remains optional. No new public export/config.

## Verification and limits

Cover varied roles, labels, sizes and image scales; ambiguous/absent targets;
disabled/protected/stale/occluded recipients; changed state during capability
lookup; unsupported AXPress and unknown completion. Live replay must include the
previous failing coordinate sequence and independent saved-file verification.
Model-driven comparisons retain failures and use identical prompts and limits.
Wrong points outside a verified toggle remain wrong; this does not solve general
model grounding or make application-specific plans part of the executor.
