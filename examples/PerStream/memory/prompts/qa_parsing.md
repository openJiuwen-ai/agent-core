Parse the QA query for video memory retrieval.

Return valid JSON only:

{
  "qa_types": ["detail"],
  "entities": ["..."],
  "time_range": [0, 100],
  "temporal_hint": "none",
  "time_order": "none",
  "intent": "..."
}

Rules:
- qa_types must contain exactly one primary type: detail, summary, or preference.
- Use detail for factual questions about specific visible values, titles, names, prices, accounts, emails, page fields, or selected options.
- Use summary for questions asking for an overall summary, multi-item summary, process outcome, or the most accurate summary option.
- Use preference for questions asking what the user prefers, tends to use, most often uses, or is more likely to prefer.
- time_range should be frame-based and must not go beyond the QA time.
- If the question does not specify a temporal restriction, use the full available past range: [video_start_time, qa_time_id].
- If the question uses "recently", set temporal_hint to "recently"; the system will map it to the last 100 frames before QA time.
- Only narrow time_range when the question explicitly uses temporal hints such as "just now", "recently", "earlier", "before", "first", "last", "morning", "evening", "yesterday", or similar expressions.
- temporal_hint should summarize the temporal phrase in the question, such as "none", "recently", "just_now", "before", "first", "last", "morning", "evening".
- time_order must be one of: none, recent, earliest, latest.
- Be careful: phrases like "last updated time" usually refer to a visible page field, not the retrieval time range.
- entities should be concise query-related mentions.
- intent should describe what evidence the retrieval system should find.
