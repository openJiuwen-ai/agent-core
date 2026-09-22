# Runtime Integration and Reliability

This page is for host developers integrating Team Organization.

## Components

| Component | Responsibility |
|-----------|----------------|
| `TeamOrganizationManager` | Facade for one Organization |
| `OrgTaskManager` | Tasks, reviews, sources, and atomic transitions |
| `OrgMessageService` | Leader messages, receipts, and system notifications |
| `OrganizationRuntimeManager` | Binding, tools, subscriptions, Leader turns, and recovery |
| `OrganizationWorkspaceManager` | Shared layout, mounts, and write boundaries |
| `TransportAPI` | In-process or pyzmq direct delivery negotiation |

The process manager registry is only a facade cache. `TeamDatabase` remains the source of truth across restarts.

## Host hooks

A host may inject a Leader-turn runner, expert catalog, expert launcher, Summary Team launcher, and progress publisher. Initialize these adapters lazily: unused expert or summary capabilities should not scan packages or start Teams.

Persist tasks, reviews, messages, membership, and summary executions before publishing events. Events are at-least-once notifications, so handlers and state transitions must tolerate duplicates.

On activation or rebinding, recover membership and tools, matching `OPEN` tasks, assigned active tasks, pending messages, reviews, parent follow-ups, Summary Teams, and incomplete Summary Executions. Background turns are serialized per Team, but database conditional updates—not in-memory queues—provide correctness.

## Integration checklist

- Members share one organization database and session.
- Paused Teams can be resumed for background Leader turns.
- Failed expert launches are rolled back.
- The Summary Team shares the owner's database and only reads bound sources.
- Distributed Teams have genuinely shared artifact storage.
- Messages are acknowledged only after successful handling.
- Restart recovery scans persisted state rather than waiting for old events.
- Shutdown cancels subscriptions, scanners, and queued turns.

Import stable types from `openjiuwen.agent_teams.organization`. SQL records, private methods, and event handlers are implementation details and should not become host integration contracts.

