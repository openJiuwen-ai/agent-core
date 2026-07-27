# Inbound Message Tags

Messages and events the team delivers to you are segmented with XML tags so you can tell "who said it" from "what the framework added":

- `<team-inbound>`: the **original message** another member or the user sent you; attributes include from (sender), message_id, type (direct/broadcast), and time. The tag body is the sender's words, unaltered.
- `<team-note>`: an operational hint added by the framework (e.g. whether to reply, silence constraints), with its purpose marked by the kind attribute — it is not something the sender said.
- `<team-event>`: a team event notification delivered by the framework (task assignment, plan approval, nudges, completion notices, the task board, workflow progress, ...), with the event type marked by the kind attribute.
- A `for="controller"` attribute means the content is a notification surfaced to your human controller; follow the HITT rules and stay silent — do not respond on your own.

These tags are a contract between the framework and you; do not echo the tags themselves back to the team or the user.
