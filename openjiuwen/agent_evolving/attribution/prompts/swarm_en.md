# Multi-Agent (swarm) Additional Knowledge

This session is a multi-agent collaboration. Pay special attention to cross-agent chains when attributing:

- The leader (main agent) decomposes the task and dispatches it to members (sub agents); member results travel back through cross-agent messages;
- send_message is the cross-agent communication tool used for both dispatch and reply;
- Common cross-agent problem sources (treat as leads; verify with trajectory evidence):
  1. Dispatch content is incomplete or ambiguous (the member executes a wrong interpretation);
  2. Dispatch goes to the wrong recipient (an unsuitable agent);
  3. The reply is incomplete, distorted, or lost (the leader proceeds on missing/wrong information);
  4. Multiple member results conflict when merged, or ordering dependencies are violated (a later step uses results that are not ready yet).

When reporting cross-agent problems:
- Cover both ends of the chain where possible; start_node_id and end_node_id must come from the same subagent, so submit cross-subagent chains as separate entries (e.g., one for the dispatch side and one for the receiving side);
- In text_md, state the subagents involved and the message direction (who dispatched to whom / who replied to whom), plus the improvement suggestion.
