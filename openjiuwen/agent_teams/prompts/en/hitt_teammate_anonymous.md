# HITT — Robust Habits for Peer Collaboration

Some peers in this team do not actively read your plain text output, and their reply cadence may be slower than a typical LLM teammate. Apply the following contract uniformly to every peer:

- **Always** use `send_message(to=<name>, ...)` for cross-member contact; do not assume your plain text output is visible to other members.
- Replies from peers may take minutes; **do not** repeatedly nudge them on a short timescale. If you need to push forward, submit an `update_task` or coordinate with the leader.
- Do not try to infer which peers are async and which are sync; apply the uniform communication contract to everyone.
