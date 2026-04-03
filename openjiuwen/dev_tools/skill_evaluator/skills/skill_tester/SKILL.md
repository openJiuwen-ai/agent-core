---
name: skill_tester
description: >
 Generate and run synthetic test cases against any skill by executing real prompts through the skill and grading actual outputs — functional and behavioral testing only. Use when verifying a skill WORKS: correct outputs, edge case handling, error recovery, end-to-end workflow. Triggers on: "test this skill", "generate test cases", "run evals", "validate the skill", "run tests on skill", or when given a skill path to verify. Outputs pass_rate and structured assertions.json for eval pipeline consumption. Do NOT use for reviewing skill design quality, knowledge delta, anti-patterns, or SKILL.md structure — use skill_llm_judge for that.
---
# Generate Synthetic Test Case

A skill for automatically generating and running test cases against other skills.

## Overview

This skill analyzes a target skill's SKILL.md and generates comprehensive test cases that:

- Exercise the skill's core functionality
- Test edge cases and boundary conditions
- Verify expected behaviors with concrete expectations
- Can be run and graded automatically

## Workflow

### Before You Begin: Resolve the Output Directory

Read your system prompt to find the designated output directory\. All paths in this skill are relative to that directory.
Prefix every path with it before writing any file.

For example, if `out_put_dir` is `/home/user/outputs`, then:

- `evals/skill-tests/<skill-name>/skill_tester_cases.json` → `/home/user/outputs/evals/skill-tests/<skill-name>/skill_tester_cases.json`
- `evals/skill-tests/<skill-name>/run-1/` → `/home/user/outputs/evals/skill-tests/<skill-name>/run-1/`

Never write files to bare relative paths. Always resolve against output directory first.

### Step 1: Analyze the Target Skill

First, read and understand the skill to be tested. Extract key information:

- **Purpose**: What does the skill do?
- **Triggers**: When should it activate?
- **Inputs**: What inputs does it accept? (files, parameters, etc.)
- **Outputs**: What outputs does it produce?
- **Dependencies**: What tools/libraries does it need?

### Step 2: Design Test Cases

Aim to cover distinct categories with as few cases as needed — typically 3 to 8. Prioritize **category coverage** over hitting a fixed count. Pick the most representative case for each applicable category:

| Category       | Purpose                          | Example                                          |
| -------------- | -------------------------------- | ------------------------------------------------ |
| Smoke Test     | Basic sanity check               | Create simplest possible output                  |
| Happy Path     | Standard, realistic user request | Feature-complete request a real user would make  |
| Edge Case      | Boundary or unusual input        | Empty input, very large input, unusual format    |
| Error Handling | Graceful failure                 | Invalid input type, missing required file        |
| Integration    | Full end-to-end workflow         | Multi-step process using multiple skill features |

Skip a category only if it genuinely does not apply to the skill being tested.

### Step 3: Generate `skill_tester_cases.json`

Save the test case definitions (without assertions) to:

```
evals/skill-tests/<skill-name>/skill_tester_cases.json
```

Use this format:

```json
{
  "skill_name": "<skill-name>",
  "evals": [
    {
      "id": 1,
      "name": "smoke_test",
      "case_category": "smoke_test",
      "prompt": "User prompt that exercises the skill",
      "expected_output": "Description of what success looks like",
      "files": [],
      "expectations": [
        "Specific verifiable outcome 1",
        "Specific verifiable outcome 2"
      ]
    }
  ]
}
```

> **Note:** Do not add assertions here yet. Assertions are written per-run in Step 5 once you have actual output to evaluate against.

### Step 4: Create Test Input Files (if needed)

If any test cases require input files, create synthetic mock files and save them to:

```
evals/skill-tests/<skill-name>/files/
```

Ensure the filenames match the `"files"` entries in `skill_tester_cases.json`.

### Step 5: Run Test Cases and Write Assertions

For each test case, execute with `scripts/run_eval_query.py`. The script is located relative to ******this SKILL.md file****** — not your working directory or PYTHONPATH.

```bash
python <fill_relative_path>/scripts/run_eval_query.py\
  --prompt "YOUR PROMPT FROM EVALS JSON" \
  --skill-path "./skills/your_skill/"\
  --output-path "<output_folder>/evals/skill-tests/<skill-name>/run-<id>/"
```


| Argument             | Short  | Required | Description                                        |
| -------------------- | ------ | -------- | -------------------------------------------------- |
| `--prompt`         | `-p` | ✅       | The test prompt                                    |
| `--skill-path`     | `-s` | ✅       | Path to the skill being tested                     |
| `--output-path`    | `-o` | ✅       | Where to save the result (file or folder).         |
| `--files-base-dir` | `-f` | ❌       | Base directory for any input files the agent needs |
| `--max-iterations` | `-m` | ❌       | Max agent steps (default: 40)                      |

Give each test case a distinct `--output-path` with its run folder so results don't overwrite each other. For test case with id N, always pass:
--output-path `<output_dir>/evals/skill-tests/<skill-name/run-N/`

**NEVER** pass the same `--output-path` for two different test cases. Each run must have its own `run-<id>/` folder.

Once each subagent completes, evaluate its output against the `expected_output` in `skill_tester_cases.json`. Then write an `assertions.json` file into the same run folder:

```json
{
  "id": 1,
  "name": "smoke_test",
  "status": "passed",
  "assertions": [
    {
      "text": "Output file has the correct extension",
      "passed": true,
      "evidence": "File was created at output.docx"
    },
    {
      "text": "Document contains the expected heading",
      "passed": true,
      "evidence": "Heading 'Test Report' found on page 1"
    }
  ]
}
```

**Grading rules:**

- **Passed**: All assertions are true
- **Failed**: One or more assertions are false
- **Partial**: Some assertions passed and some failed — note which ones failed and why

### Step 6: Generate Test Report

Gather assertions and consolidate results into a report and save to:

```
evals/skill-tests/<skill-name>/skill_tester_report.json
```

```json
{
  "skill_name": "<skill-name>",
  "skill_path": "/path/to/SKILL.md",
  "timestamp": "2026-01-15T10:30:00Z",
  "summary": {
    "total": 4,
    "passed": 3,
    "partial": 1,
    "failed": 0,
    "pass_rate": 0.75
  },
  "results": [
    {
      "id": 1,
      "name": "smoke_test",
      "status": "passed",
      "assertions": [
        { "text": "...", "passed": true, "evidence": "..." }
      ]
    }
  ],
  "recommendations": [
    "Edge case handling for empty inputs could be documented more explicitly",
    "The skill would benefit from a validation step after file creation"
  ]
}
```

## Output Structure

After all steps complete, the directory should look like:

```
evals/skill-tests/<skill-name>/
├── skill_tester_cases.json                  # Test case definitions
├── files/                      # Mock input files (if any)
│   └── sample_input.txt
├── run-1/
│   ├── output.docx             # Primary output
│   ├── output.txt              # Execution log
│   └── assertions.json         # Assertions + verdict for this run
├── run-2/
│   └── ...
└── skill_tester_report.json                 # Final consolidated report
```

## Writing Good Test Cases

### Prompts

Good prompts should be specific, realistic, and test one thing clearly.

**Good:** `"Create a Word document with a table of contents, three sections about climate change, and page numbers"`

**Bad:** `"Make a doc"` — too vague, won't meaningfully exercise the skill

### Expectations

Expectations should be:

- **Verifiable**: Can be checked against actual output
- **Specific**: Reference concrete outcomes
- **Discriminating**: Would fail if the skill didn't work correctly

**Good expectations**:

- "The output file is a valid .docx file"
- "The document contains exactly 3 section headings"
- "Page numbers appear in the footer of each page"

**Bad expectations**:

- "The output looks good" (subjective)
- "The skill worked" (not specific)
- "A file was created" (doesn't verify correctness)

### Assertions

Assertions should be verifiable against the actual output, not subjective.

**Good:**

- `"The output file has a .docx extension"`
- `"The document contains exactly 3 section headings"`
- `"Page numbers appear in the footer of each page"`

**Bad:**

- `"The output looks good"` — subjective
- `"The skill worked"` — not specific enough
- `"A file was created"` — doesn't verify correctness

## Example: Testing the `docx` Skill

### Skill Analysis (from SKILL.md)

- Creates Word documents using `docx` (npm) for new files; unpack → edit XML → repack for edits
- Supports formatting, tables, images, headers/footers, tracked changes
- Validates output with `scripts/office/validate.py`

### Generated `skill_tester_cases.json`

```json
{
  "skill_name": "docx",
  "evals": [
    {
      "id": 1,
      "name": "basic_document",
      "case_category": "smoke_test",
      "prompt": "Create a Word document with the title 'Test Report' and one paragraph saying 'This is a test.'",
      "expected_output": "A valid .docx file containing the title and paragraph",
      "files": []
    },
    {
      "id": 2,
      "name": "table_creation",
      "case_category": "happy_path",
      "prompt": "Create a Word document with a 3x3 table containing numbers 1 through 9",
      "expected_output": "A .docx file with a properly formatted 3x3 table",
      "files": []
    },
    {
      "id": 3,
      "name": "edit_existing_document",
      "case_category": "integration",
      "prompt": "Edit the provided document to replace 'Hello' with 'Goodbye' using tracked changes",
      "expected_output": "The document contains a tracked deletion of 'Hello' and a tracked insertion of 'Goodbye'",
      "files": ["evals/skill-tests/docx/files/sample.docx"]
    },
    {
      "id": 4,
      "name": "empty_document",
      "case_category": "edge_case",
      "prompt": "Create an empty Word document with no content",
      "expected_output": "A valid .docx file that opens without errors and contains no text",
      "files": []
    }
  ]
}
```

### Example `run-1/assertions.json`

```json
{
  "id": 1,
  "name": "basic_document",
  "status": "passed",
  "assertions": [
    {
      "text": "Output file has .docx extension",
      "passed": true,
      "evidence": "File saved as output.docx"
    },
    {
      "text": "Document contains heading 'Test Report'",
      "passed": true,
      "evidence": "Heading found at top of document in Heading 1 style"
    },
    {
      "text": "Document contains paragraph 'This is a test.'",
      "passed": true,
      "evidence": "Paragraph text matched exactly"
    }
  ]
}
```

## Tips

1. **Start simple**: A smoke test that barely passes tells you more about a broken skill than a complex test that partially passes
2. **Use real examples**: Base prompts on the kinds of requests shown in the skill's own documentation
3. **Test failures too**: Include at least one test that exercises error handling or graceful degradation
4. **Be specific in assertions**: Vague assertions produce false passes; check specific values, counts, and file properties
5. **Iterate**: If a test produces unexpected output, update the assertion with the real finding before marking it as a failure — the skill may be correct and the expectation may be wrong
