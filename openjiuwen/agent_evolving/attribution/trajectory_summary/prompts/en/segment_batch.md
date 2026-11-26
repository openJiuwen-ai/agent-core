# Short-turn batch: return each segment independently

This request contains segments from adjacent short turns of the same agent. They share one request, not one execution scope; this is not an overall merge.

The batch output rule overrides the common top-level narrative/problems submission shape: call report_summary exactly once with a top-level results array. Return exactly one item per input segment, containing the copied segment_id, nonempty narrative and problems ([] when no material issue exists). Do not return top-level narrative or display_summary. Input order is preferred; never omit or duplicate an item, combine segments, or invent a segment_id.

reference is shared task background. Each segments item's primary contains only that segment's nodes. Apply the original-node summary rules independently: issue and recovery evidence must belong to that item's primary. State is relative to that segment's end; never use later turns in this batch to claim that an earlier segment already recovered or delivered successfully. Cross-segment recovery and relationships are handled by later merges.

Keep each account focused on key operations, consequential results, verification gaps and its local ending state. Do not repeat other items or add an overall batch summary.
