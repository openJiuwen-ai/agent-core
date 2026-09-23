# Messaging and Workspace

Team Organization has two cross-Team sharing mechanisms: the Leader Inbox carries coordination context, while the Organization Workspace carries artifacts. Neither replaces task state.

## Leader Inbox

Messages are persisted before a wake-up event is published. A missed event therefore does not lose the message; rebinding scans unacknowledged receipts.

| Tool | Purpose |
|------|---------|
| `org_send_leader_message` | Send to one member Team or broadcast to all Leaders |
| `org_get_leader_message` | Read one message by ID |
| `org_list_leader_messages` | List messages and pending receipts for this Team |
| `org_ack_leader_message` | Acknowledge after successful handling |

A broadcast stores one body and one receipt per recipient Team. Do not acknowledge before the Leader turn has handled the message. Use tasks, not messages, for assignment and review.

## Organization Workspace

`OrganizationWorkspaceManager` mounts an Organization/session workspace into each Team workspace. Its logical layout is:

```text
organization-workspace/
├── teams/<team_id>/
└── summary/
```

Ordinary Teams write only under their own directory. A Summary Team reads bound source artifacts and writes final deliverables under `summary/`. The workspace rail validates paths and can record version-control commits.

Store stable references in task `output_context`—for example URI, type, description, and hash—instead of copying large artifacts into task records. A shared database does not imply a shared filesystem; distributed hosts must provide storage visible to all participating Teams.

