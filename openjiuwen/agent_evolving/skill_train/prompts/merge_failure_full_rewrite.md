You coordinate complete skill documents proposed from failed trajectories.
Keep one full replacement skill. Do not convert the documents into edits.

When several candidates exist, keep the one that preserves the task's domain procedure and covers the shared failure. Drop duplicates. Do not paste sample-specific answers into the document.

Respond with one JSON object. Leave "new_skill" as "". After the JSON, write a line that is exactly <<<SKILL>>>, then the entire SKILL.md. Do not wrap that document in a code fence.

{
  "reasoning": "<which document was kept and why>",
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
