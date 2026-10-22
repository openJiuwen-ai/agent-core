# Team Organization

Team Organization is a cross-team coordination layer built on AgentTeams. Each Team keeps its own Leader and Teammates; after joining an Organization, Team Leaders collaborate through a shared task pool, reliable inbox, and organization workspace.

## How it relates to AgentTeams

| Concept | Scope | Responsibility |
|---------|-------|----------------|
| AgentTeams | Inside one Team | A Leader coordinates Teammates |
| Team Organization | Across Teams | Team Leaders claim, delegate, review, and aggregate work |
| AgentGroup | Expert-Team template | Roles, prompts, skills, and capabilities; not a running Team |
| Summary Team | Organization-owned Team | Consolidates accepted sources in `SUMMARY_TEAM` mode |

The Leader is the organization-level actor. The runtime persists state and schedules wake-ups, but it does not decide whether a Team should claim work or how a failure should be repaired.

## Capabilities

- Organization ownership and Team membership.
- A persistent task pool with claiming, delegation, review, repair, and explicit failure.
- A reliable Leader inbox with per-recipient acknowledgement and recovery.
- A shared workspace with per-Team write boundaries.
- `HIERARCHICAL` and `SUMMARY_TEAM` aggregation.
- Host-provided discovery and launch of AgentGroup-based expert Teams.
- Recovery of bindings, active tasks, messages, reviews, and summary executions.

## Current constraints

- An Organization permits one non-terminal ordinary root task at a time.
- The current organization runtime is in-process: participating Team leaders belong to the same runtime pool and share the owner's `TeamDatabase` instance. Sharing PostgreSQL alone does not enable a multi-process Organization.
- Expert and Summary Team creation is supplied by the host. agent-core defines the contracts and coordination runtime.
- The database is the source of truth; events are notifications only.

## Next steps

- [Quick Start](./Quick Start.md)
- [Organization and Task Collaboration](./Organization and Task Collaboration.md)
- [Messaging and Workspace](./Messaging and Workspace.md)
- [Aggregation and Expert Teams](./Aggregation and Expert Teams.md)
- [Runtime Integration and Reliability](./Runtime Integration and Reliability.md)
