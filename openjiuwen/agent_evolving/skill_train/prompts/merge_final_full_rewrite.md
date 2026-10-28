You perform the final choice between a failure-driven skill document and a success-driven skill document.
Return one complete SKILL.md. Failure-driven content takes priority when the two disagree. Do not return edits.

Respond with one JSON object. Leave "new_skill" as "". After the JSON, write a line that is exactly <<<SKILL>>>, then the entire SKILL.md. Do not wrap that document in a code fence.

{
  "reasoning": "<why this document was chosen>",
  "skill_candidates": [
    {
      "title": "<short name>",
      "change_summary": ["<what this skill changes>"],
      "new_skill": "",
      "support_count": 1,
      "source_type": "failure"
    }
  ]
}
<<<SKILL>>>
---
name: <skill name>
---
<the entire SKILL.md>
