# Meeting audio queue

Meetings coordinate host-selected messages and speech for an existing Team and
session. The host binds trusted member identities, chooses when to publish text,
and owns audio playback.

`MeetingCoordinator`, `MeetingScope`, `MeetingRoute`, and `SpeechRequest` are
exported from `openjiuwen.agent_teams`.

## Choose what waits for a speaking turn

JiuwenSwarm owns the policy for user replies, messages between agents, and
broadcasts, including those from external members. Agent-core coordinates
speaking turns for requests the host explicitly queues. Ordinary Team sends
continue through their usual delivery paths.

When a message should wait, route it to the queue before sending it. After the
host claims the request, publish the text through the existing Team messaging
path and record the publication before starting audio. If a host subscribes to
an event emitted after sending, it can order playback but the text has already
been delivered. The caller supplies `speech_text`.

## Publish a queued direct message

The example assumes an initialized Team database and a Team message manager for
the same Team. The sender and recipient are already registered members. Run the
message-manager calls with the meeting's session bound in the Team context.

```python
from openjiuwen.agent_teams import MeetingCoordinator, MeetingRoute, MeetingScope
from openjiuwen.agent_teams.context import reset_session_id, set_session_id

scope = MeetingScope(session_id="session-1", team_name="research", meeting_id="meeting-1")
meeting = MeetingCoordinator(database, scope, message_manager=message_manager)
await meeting.start()

# Bind this identity from the trusted runtime, not from agent arguments.
member = meeting.for_member("speaker")
await member.enqueue_message(
    "The analysis is ready.",
    speech_text="The analysis is ready.",
    route=MeetingRoute(kind="direct", recipients=("reviewer",)),
)

request = await meeting.grant_next(playback_ready=True)
if request is not None:
    token = set_session_id(scope.session_id)
    try:
        message_id = await message_manager.send_message(
            content=request.content,
            to_member_name=request.route.recipients[0],
            from_member_name=request.member_name,
            protocol=request.route.protocol,
            message_id=request.publication_message_ids[0],
        )
    finally:
        reset_session_id(token)
    if message_id is None:
        raise RuntimeError("Publication failed; recover the existing claim before retrying")
    published = await meeting.finish_publication(
        request.request_id,
        request.lease_token,
        source_message_ids=(message_id,),
    )
    # Start audio only if the returned request still owns a granted lease.
    # After playback actually completes, acknowledge that lease with:
    # await meeting.release(request.lease_token)
```

`publication_message_ids` contains stable reserved IDs before publication. They
remain stable when the coordinator is reconstructed or the same request is
retried. Their presence does not mean messages exist or have been delivered.
`source_message_ids` identifies messages recorded by `finish_publication()`.

For multicast, IDs follow `request.route.recipients` order. Pass them through
`multicast_message(message_ids=list(request.publication_message_ids), ...)`.
Broadcast has one reserved ID, passed as `broadcast_message(message_id=..., ...)`.
For the built-in group-chat sender, use the plain route protocol and pass
`request.request_id` as `client_message_id`. The SDK generates a request ID
by default. If the host supplies one for retries, use a fresh ID for each new
group message across the Team session.

Speech-only requests use `member.request_speech(speech_text=...)` and expose an
empty `publication_message_ids` tuple. They require no Team message publication.
Another member's `get_speaker()` view redacts both publication and source IDs.

The host owns recovery after a failed publication or interrupted playback. Observe
the current lease before using the coordinator's interruption or guarded force
release operations. Queue notifications are wakeups; persisted state remains the
authority. The SDK does not start a background publisher or audio player.
