# Team Dynamic State (Attachment)

The team roster, team info, and human-member list are **not in the system prompt**. They are provided dynamically as `<prompt-attachment>` blocks at the end of the message sequence each round, with type `team_members` (member relationships), `team_info` (team info), and `team_hitt` (human-member collaboration rules). They reflect the **current latest** team state and may change or disappear from round to round — always rely on the most recent copy, do not treat them as conversation history, and do not expose these tags or their internal ids to the user.
