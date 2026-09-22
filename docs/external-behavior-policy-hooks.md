# External behavior policy hooks

`ToolCallInputs.execution_started` distinguishes a tool rejected by a before
hook from an invocation returning `None`. It becomes true immediately before
invocation; output observers should check it before reporting a tool result.

Hosts can set `permissions.defer_unmatched: true` to receive `PermissionLevel.UNDETERMINED`
when no explicit tool/parameter rule matches. Other hosts keep existing defaults.
Explicit ASK/DENY and other guardrails retain precedence.

`ToolPermissionHost.on_permission_evaluated(PermissionEvaluationRequest)` observes the
initial effective local decision. It can resolve UNDETERMINED to a `PermissionResult`.
It cannot replace an explicit ALLOW/ASK/DENY decision. Missing, invalid or failed
external decisions fall back to ordinary user confirmation. A resumed confirmation
does not issue another initial evaluation notification.

The host owns transport, event correlation and external service protocols. Observers
of explicit local decisions should enqueue reporting without waiting for a remote
response. Existing `request_permission_confirmation` semantics are unchanged.

Skill installers can call `before_skill_install(context, hook)` after staging content
and before deleting/replacing/registering the target. `SkillInstallContext` does not
require an Agent instance. The installer must enforce the returned decision: ALLOW
commits, DENY stops, ASK waits for user confirmation. An absent hook preserves legacy
installers; a failing configured hook returns ASK. Cancellation propagates.

The library does not upload skill files or define vendor-specific request fields.
