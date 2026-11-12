# Segment: summarize original nodes

Use reference as task background and primary as the ordered nodes covered by this segment. Summarize intent, key operations, consequential results and local ending state; usually a few compact paragraphs suffice. Extract local material problems with kind, summary and supporting node_ids; include state and recovery evidence when supported.

User refusal is not automatically a correction or tool failure. user_correction needs an actual external user correction, not leader/member instructions rendered as USER. USER rows can be runtime approval responses, system context or team events. Within a turn, plain and enveloped views of one request do not prove the user sent it twice. Duplicate views of a tool call are not repeated executions.

In node_ids and resolution_node_ids, copy complete node_id values from this segment's primary nodes. Never use numeric labels, tool call IDs, span IDs or another segment's evidence. Do not generate source_problem_ids; source IDs are assigned by the program after extraction.

Tool bodies can be previews: previewed_fields gives original lengths and source_truncated_fields marks upstream loss. Do not claim to have inspected omitted code or verified its implementation.
