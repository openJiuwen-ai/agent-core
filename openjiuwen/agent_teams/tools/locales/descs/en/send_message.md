Send a message to team members. Use for task assignment notifications, progress reports, escalation of blockers, or dependency coordination.

| `to` | |
|---|---|
| member name | Point-to-point message (DM / direct message / private chat) — visible ONLY to you and that member, like a 1:1 private chat in any IM tool; no other teammate (and not even the leader) receives it. To reach a subset of members with the same content, use multicast; to reach everyone, use broadcast |
| array of member names (e.g. `["m1","m2"]`) | Multicast — the same content is sent as separate point-to-point messages to each listed member, each with its own read state. **Cost grows linearly with the number of recipients (one message row + one event per target), and for the same audience size this is MORE expensive than broadcast.** Use only when a specific subset of members must see the identical content AND broadcast is not appropriate; choose carefully. **When the recipients exactly cover every other team member (everyone but yourself), multicast is forbidden — you MUST broadcast with `"*"` instead; a multicast over the whole team is just a more expensive broadcast and the tool will reject it.** Cannot be mixed with `"*"` or `"user"`. Treated as a whole-success only when every target succeeds; on partial failure the result lists who was delivered and who failed — do NOT resend to members already in the delivered list |
| `"user"` | The **human outside the team** who commissioned this team's work — not a team member, and absent from the roster (not finding them there is normal). **Teammates only** — when an incoming message has `from = user`, a teammate MUST use this tool with `to = "user"` to deliver its reply, since teammate plain text never reaches the user and skipping this call means no reply was sent at all. The leader does NOT use this value: every leader plain-text output is shown directly to the user |
| `"*"` | Broadcast to the team channel — like posting to a shared team channel in any IM tool, visible to every member. Expensive (linear in team size), use only for global decisions, constraint changes, or announcements everyone needs |

## Honor user @ scope
If the user mentioned specific members, **kickoff and dispatch may only target those members** (unicast or a name-array multicast). **Do not** switch to `"*"` and pull in unmentioned members. Use `"*"` or the full roster only when the user wrote `@all` / "everyone" / "all hands" / "all three of you", or @mentioned nobody and whole-team participation is intended.

## Interactive debate (P2P)
- **Teammates**: send positions, rebuttals, and supplements with `to` set to the other participants — do not send the full view only to `team-leader` and wait for relay
- **Leader**: after kickoff, do not `send_message` member A's view (full text or summary) to member B; lateral debate is member P2P. If a member mistakenly sends you their view, remind them to message the peer directly — do not relay it

{{artifact_handoff_policy}}

Teammate plain text output is NOT visible to other agents or to the user — teammates MUST call this tool to communicate. Leader plain text output IS shown to the user directly, so the leader does not need this tool to reply to the user. Messages from teammates are delivered automatically; you don't poll. Refer to members by name, never by internal ID.
