---
id: F_20261106_1222_meeting-audio-queue
title: Meeting audio queue and host publication controls
type: F
author: Chu Phan Hien
author_email: hienchuphan@gmail.com
modules:
- openjiuwen/agent_teams
files:
- openjiuwen/agent_teams/schema/meeting.py
- openjiuwen/agent_teams/meeting/coordinator.py
- openjiuwen/agent_teams/meeting/member.py
- openjiuwen/agent_teams/meeting/tools.py
- openjiuwen/agent_teams/tools/database/meeting_dao.py
- openjiuwen/agent_teams/tools/message_manager.py
- openjiuwen/agent_teams/tools/models.py
- openjiuwen/agent_teams/tools/team.py
- openjiuwen/agent_teams/external/client.py
designs: []
design_note: The meeting RFC is tracked in agent-core issue 1951. The feature record describes the SDK implementation and the agreed host publication boundary.
---

# Meeting audio queue and host publication controls

Date: 2026-11-06. This record describes the meeting branch as one feature;
publication IDs complete its existing host publication flow.

## Implemented scope

The branch adds session-scoped meeting and speech-request storage, ordered
admission, single-owner leases, interruption acknowledgement and guarded recovery.
A trusted coordinator binds member clients; native and external members can opt
into speaker observation and Leader interruption controls. Team lifecycle events
refresh meeting state across runtimes.

Queued text carries a route and is published through normal Team messaging by
the host. Audio remains host-owned. A grant does not itself send text or start
playback. Stable sender IDs support publication retries; the coordinator validates
published sources before recording them.

## Outbound message policy

During PR preparation on 2026-11-09, the branch owner confirmed that JiuwenSwarm
should own the decision about which outbound messages wait for a speaking turn.
This lets the application choose a policy for user replies, messages between
agents, and broadcasts, including messages from external members. Agent-core
provides a shared queue and lease contract for the messages the host selects.

To make text wait as well as audio, JiuwenSwarm must enqueue the selected message
before the ordinary send path publishes it. The host publishes the text through
Team messaging once it has a claim, then handles playback and completion. A
listener that runs after publication can only coordinate playback. Ordinary
Team sends keep their existing behavior.

This allocation differs from the SDK send-path integration proposed in
[agent-core #1951](https://github.com/openJiuwen-ai/agent-core/issues/1951). Track
application wiring under [JiuwenSwarm #8260](https://github.com/openJiuwen-ai/jiuwenswarm/issues/8260).
[PR #1905](https://github.com/openJiuwen-ai/agent-core/pull/1905) is the original
RFC's duplex dependency. The host-managed queue can be reviewed independently
of that wiring. The SDK takes caller-supplied speech text. The RFC's
same-model speech generation hook remains open.

## Publication identifier decision

Previously a granted direct request exposed no publication IDs, but completion
required the deterministic IDs calculated by a private coordinator helper. Using
the normal sender's random default UUID failed completion and left the claim held.

`SpeechRequest.publication_message_ids` now exposes the existing derived IDs on
text requests before publication and after coordinator reconstruction. The field
is appended with an empty-tuple default, adds no persisted column, and does not
change the ID calculation. Speech-only requests return an empty tuple. Member
speaker views redact it alongside the other message references.

`source_message_ids` continues to describe recorded Team publications. It is not
populated merely because publication IDs have been reserved. This distinction
preserves the playback completion guard and prevents reservations from being
mistaken for sent messages.

Alternatives considered: importing the private helper or copying its algorithm
would couple host code to coordinator internals; reusing source_message_ids before
publication would misrepresent message existence. No separate bugfix record or
per-commit migration is introduced. Schema migration is considered for the whole
feature, as requested by the user.

## Validation

On 2026-11-06, Windows / Python 3.12.14 using `.tmp/meeting-venv`:

```powershell
$env:PYTHONPATH = (Get-Location).Path
& ./.tmp/meeting-venv/Scripts/python.exe -m pytest tests/unit_tests/agent_teams/test_meeting_publication_ids.py tests/unit_tests/agent_teams/test_message_manager.py -q -o addopts= -p no:cacheprovider
```

Result: **44 passed**, six existing dependency deprecation warnings. The six new
cases cover real in-memory queue/message operations for direct, multicast and
broadcast publication, source validation before sending, playback release,
coordinator reconstruction, group ID stability and request retries, speech-only
requests, and member-view redaction. Message file spill is disabled in this focused
contract fixture; it does not validate file persistence or a real audio player.

The preceding review run had 65 passes and five group-chat integration failures.
The same five failures reproduced against the unchanged branch HEAD before this
field addition. They involve existing Windows conversation projection paths. That
run is historical context.

On 2026-11-09, review of the prepared candidate on Windows with Python 3.12.14
passed 131 focused tests covering queue priority, persisted recovery, publication
IDs, concurrent retries, external member controls, and durable tool approvals.
The broader database and group conversation run passed 157 tests. The remaining
group history test passed on rerun in UTF-8 mode after the default Windows
encoding failed to read its UTF-8 fixture.

The final PR candidate on upstream `e241f796b` passed all 289 of those tests
together with Python UTF-8 mode enabled.

On 2026-11-11, the follow-up to PR #2574 bound external meeting callbacks to
the receiving client's session, translated framework errors at the meeting
tool boundary, and validated distinct multicast IDs before writing. Failed
multicast batches are logged if the write and retry checks leave messages
missing. On Windows with Python 3.12.14 and UTF-8 mode enabled, 67 focused tests
passed across external meeting controls, the meeting queue, the message manager,
reserved publication IDs, and concurrent publication retries.

## Current contract and remaining work

The publication field contract is maintained in
[S_12_schema-data-models](../specs/agent_teams/S_12_schema-data-models.md);
[the host guide](../../../docs/dev/meeting_audio_queue.md) shows the public flow.
Revalidate when the route/ID algorithm, claim lifecycle, or message publication
contracts change.

Concurrent stable-ID sends now preserve the accepted message body. The SDK
generates request IDs by default. Hosts that supply their own group publication
IDs must follow Team group chat's session-wide deduplication contract. The
built-in group sender uses the plain protocol.

On Windows, file-backed text that already contains CRLF can read back with an
extra newline and fail publication completion. The PR leaves this edge case for
follow-up. Real JiuwenSwarm playback and interruption integration remain
application validation work.
