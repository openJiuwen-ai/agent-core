You coordinate complete skill documents proposed from successful trajectories.
Keep one full replacement skill. Do not convert the documents into edits.

Respond with one JSON object. Leave "new_skill" as "". After the JSON, write a line that is exactly <<<SKILL>>>, then the entire SKILL.md. Do not wrap that document in a code fence.

{
  "reasoning": "<which document was kept and why>",
  "skill_candidates": [
    {
      "title": "<short name>",
      "change_summary": ["<what this skill changes>"],
      "new_skill": "",
      "support_count": 1,
      "source_type": "success"
    }
  ]
}
<<<SKILL>>>
---
name: <skill name>
---
<the entire SKILL.md>
