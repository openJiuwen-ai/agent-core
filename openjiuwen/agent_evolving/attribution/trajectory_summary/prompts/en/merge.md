# Merge: summarize child accounts and update known issues

children contains ordered lower-level summaries, ranges and generation status, not full original nodes. Turn requests may additionally provide inputs for the received messages. Follow the request's output_guidance to summarize the current turn, agent or overall scope. Avoid copying each child verbatim; connect consequential actions and ending states. A failed/missing child summary is an analysis gap, not an original agent execution failure. Do not fill gaps with invented events.

Update supplied problems using later evidence within this scope. Copy complete leaf source_problem_ids from supplied issues; never use node IDs or invent sources. Do not create unsupported new issues. Preserve recovery node IDs when carrying resolved issues upward, using supplied node references. Do not merge unrelated issues merely sharing a kind; when combining different kinds, explicitly choose the resulting kind.

Only explicitly dismiss source IDs proven to be harmless noise through dismissed_problem_ids. Do not dismiss material unresolved issues; do not treat an issue omitted from the response as resolved. Preserve uncertainty when later summaries do not establish recovery.
