---
name: "reflact-target"
description: "Dynamic ReflACT skill for the current benchmark task."
---

# ReflACT Target Skill

# SkillsBench skill

Each task ships skills under `.agents/skills/<name>/SKILL.md`. **Read every one of them** before coding; they beat this placeholder.

## Output contract

- Treat `/root` as this workdir (the directory that contains `task.md`): `/root/foo` → `./foo`, `/root/workspace/bar.py` → `./workspace/bar.py`.
- Create the required artifact files on disk at those exact relative paths. A written `<answer>` alone does not score.

## Word / docx mail-merge checklist

When filling a `.docx` template with `{{PLACEHOLDER}}` values:

1. Load data (usually JSON) and the template from the workspace root.
2. Replace placeholders in **all** of these containers, not only `doc.paragraphs`:
   - body paragraphs
   - every table cell, **recursing into nested tables** (`cell.tables`)
   - section headers and footers
3. Placeholders are often **split across XML runs**. Match on `paragraph.text`, then rewrite runs (put the new text in the first run, clear the rest). Do not only `run.text.replace(...)`.
4. Handle `{{IF_...}}...{{END_IF_...}}` conditionals: keep or drop the inner text, always remove the markers.
5. After saving, scan the whole document again for leftover `{{...}}` before finishing.
