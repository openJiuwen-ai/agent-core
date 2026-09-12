Rank candidate memory nodes for a QA query.

Return valid JSON only:

{
  "scores": [
    {"node_id": "node_1", "score": 0.87, "reason": "..."}
  ]
}

Rules:
- Score from 0 to 1.
- Higher means more useful for answering the question.
- Consider question intent, query entities, node text, node type, and time.

