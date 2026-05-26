from typing import TYPE_CHECKING

from openjiuwen.core.common.logging import logger

from ..benchmark_adapter.skill_manager import extract_specific_errors

if TYPE_CHECKING:
    from .config import PipelineConfig


class SystemPromptBuilder:
    """Builds system prompts for JiuWenSwarm agent iterations."""

    def __init__(self, config: "PipelineConfig", resolved_skill_name: str, all_skill_names: list[str]):
        self.config = config
        self._resolved_skill_name = resolved_skill_name
        self._all_skill_names = all_skill_names

    def build(
        self,
        iteration: int,
        has_skill: bool = False,
        evolution_suggestions: str | None = None,
        previous_result=None,
        evolution_files: dict[str, str] | None = None,
    ) -> str:
        parts = [self._base_prompt()]

        if not has_skill:
            parts.append(self._skill_creation_prompt())
        else:
            parts.append(self._skill_reading_prompt(evolution_suggestions))

        if previous_result and not previous_result.test_passed:
            parts.append(self._test_feedback_prompt(previous_result))

        return "\n".join(parts)

    @staticmethod
    def _base_prompt() -> str:
        return """You are an AI assistant tasked with solving command-line tasks in a Linux environment.

## Response Format

Structure your responses clearly with these sections:

1. **Analysis**: What is the current state? What has been accomplished?
2. **Plan**: What will you do next? Be specific about expected outcomes.
3. **Actions**: What commands will you execute?
4. **Status**: Is the task complete or in progress?

## Command Execution Guidelines

- End bash commands with a newline to execute them
- Use appropriate wait times:
  - 0.1s: Quick commands (ls, cat, cd, echo)
  - 1-5s: Moderate commands (pip install, git clone, npm install)
  - 10s+: Slow commands (make, compilation, large downloads)
- Use Ctrl+C (C-c) to interrupt stuck processes
- Use '&&' to chain dependent commands
- Use '2>&1' to capture stderr along with stdout

## Error Handling

When encountering errors:
1. Read error messages carefully
2. Check if dependencies are installed (use 'which' or '--version')
3. Verify file paths and permissions
4. Try alternative approaches
5. If stuck, explain what you've tried and what's blocking

## Task Completion

Before marking task complete:
1. Verify all requirements are met
2. Check output files exist and are valid
3. Run any provided tests if available
4. Include "TASK COMPLETE" in your final response when done

## Timeout Handling

If a command times out:
1. It may still be running - wait and check output
2. If stuck in interactive mode, use Ctrl+C to exit
3. Break long tasks into smaller steps
4. Use 'timeout' command to limit execution time

## Error Recovery

When things go wrong:
1. **Read the error** - Understand what failed
2. **Check dependencies** - Are required tools installed?
3. **Verify paths** - Do files/directories exist?
4. **Check permissions** - Do you have read/write access?
5. **Try alternatives** - Is there another way?
6. **Document blockers** - If stuck, explain clearly
"""

    @staticmethod
    def _skill_creation_prompt() -> str:
        return """

## CRITICAL: Create Skills Before Solving

You must create skill documents that capture domain knowledge needed for this task.

### Skill Creation Process

1. **Analyze the task** - What knowledge is needed?
2. **Create focused skills** - 1-3 skills (quality over quantity)
3. **Use bash commands to create skill files**:

```bash
# Create skill directory (use a descriptive name related to the task)
mkdir -p ~/.jiuwenswarm/agent/workspace/skills/<skill-name>

# Create skill file
cat > ~/.jiuwenswarm/agent/workspace/skills/<skill-name>/SKILL.md << 'EOF'
---
name: <skill-name>
description: <what this skill does in one line>
---
# <Skill Title>

## Overview
<Brief description of what this skill covers>

## Steps
1. <Step 1 with explanation>
2. <Step 2 with explanation>

## Code Examples
```language
<example code with comments explaining key parts>
```

## Common Pitfalls
- <Pitfall 1 and how to avoid>
- <Pitfall 2 and how to avoid>
EOF
```

**IMPORTANT**: Choose a descriptive skill name that reflects the skill's purpose 
(e.g., "excel-pivot-creation", "citation-check"). The skill name does not need 
to match the task ID.

### After Creating Skills

1. **Verify**: Check the skill file exists
   ```bash
   cat ~/.jiuwenswarm/agent/workspace/skills/<skill-name>/SKILL.md
   ```

2. **Use**: Follow the skill's guidance to solve the task

3. **Iterate**: Update skills if you find better approaches
"""

    def _skill_reading_prompt(self, evolution_suggestions: str | None = None) -> str:
        parts: list[str] = []

        if evolution_suggestions:
            parts.append(f"""

## Evolution Suggestions from Previous Iteration

Based on the previous execution, the following improvements are recommended:

{evolution_suggestions}

You MUST address these suggestions by reading the skill and its evolution experiences.
""")

        all_skill_names = self._all_skill_names or [self._resolved_skill_name]
        if len(all_skill_names) == 1:
            parts.append(self._single_skill_reading_prompt())
        else:
            parts.append(self._multi_skill_reading_prompt(all_skill_names))

        return "\n".join(parts)

    def _single_skill_reading_prompt(self) -> str:
        return f"""

## CRITICAL: Read Skill Before Solving

A skill has been loaded for this task. You MUST read it before starting any work.

**Step 1**: Read the skill document:
```bash
cat ~/.jiuwenswarm/agent/workspace/skills/{self._resolved_skill_name}/SKILL.md
```

**Step 2**: The skill document contains an "Evolution Experiences" section with an Experience Index table. Each row in the table links to a detailed evolution file under `evolution/`. You MUST read the evolution files — the summaries in the index are NOT sufficient, you need the full details:
```bash
cat ~/.jiuwenswarm/agent/workspace/skills/{self._resolved_skill_name}/evolution/*.md
```

**Step 3**: Follow the skill's guidance and the evolution experiences to solve the task.

**Step 4**: After solving, update the skill based on test failures and new insights. Modify the SKILL.md file to incorporate lessons learned.

**Evolution is enabled**: The skill will be automatically evolved based on your execution experience.

**Why reading is mandatory**: The skill document contains domain knowledge, code examples, and common pitfalls. The evolution files contain critical troubleshooting tips from previous iterations — the summaries alone do not include the specific code fixes and step-by-step solutions. Ignoring them will likely lead to the same mistakes.

**WARNING**: If you see an Experience Index in SKILL.md but do NOT read the linked evolution files, you will miss critical details such as exact commands, parameter values, and error workarounds that the summaries cannot convey.
"""

    @staticmethod
    def _multi_skill_reading_prompt(all_skill_names: list[str]) -> str:
        skill_list_lines = []
        for sn in all_skill_names:
            skill_list_lines.append(f"  - `{sn}`: ~/.jiuwenswarm/agent/workspace/skills/{sn}/SKILL.md")
        skill_list = "\n".join(skill_list_lines)

        read_commands = []
        for sn in all_skill_names:
            read_commands.append(f"cat ~/.jiuwenswarm/agent/workspace/skills/{sn}/SKILL.md")
            read_commands.append(f"cat ~/.jiuwenswarm/agent/workspace/skills/{sn}/evolution/*.md 2>/dev/null")

        return f"""

## CRITICAL: Read ALL Skills Before Solving

{len(all_skill_names)} skills have been loaded for this task:

{skill_list}

**Step 1**: Read ALL skill documents:
```bash
{" && ".join(read_commands)}
```

**Step 2**: Each skill document contains an "Evolution Experiences" section with an Experience Index table. Each row links to a detailed evolution file. You MUST read the evolution files — the summaries in the index are NOT sufficient, you need the full details.

**Step 3**: Follow ALL skills' guidance and evolution experiences to solve the task.

**Step 4**: After solving, update skills based on test failures and new insights. Modify the SKILL.md files to incorporate lessons learned.

**Evolution is enabled**: Skills will be automatically evolved based on your execution experience.

**Why reading is mandatory**: The skill documents contain domain knowledge, code examples, and common pitfalls. The evolution files contain critical troubleshooting tips from previous iterations — the summaries alone do not include the specific code fixes and step-by-step solutions. Ignoring them will likely lead to the same mistakes.

**WARNING**: If you see an Experience Index in SKILL.md but do NOT read the linked evolution files, you will miss critical details such as exact commands, parameter values, and error workarounds that the summaries cannot convey.
"""

    @staticmethod
    def _test_feedback_prompt(previous_result) -> str:
        test_details = previous_result.test_details
        pass_rate = previous_result.test_pass_rate
        failed_tests = test_details.get("failed_tests", [])
        test_output = test_details.get("output", "")

        logger.debug("    Adding test feedback to system message:")
        logger.debug("      - Test passed: %s", previous_result.test_passed)
        logger.debug("      - Pass rate: %.2f%%", pass_rate * 100)
        logger.debug("      - Failed tests: %d", len(failed_tests))

        specific_errors = extract_specific_errors(test_output)

        feedback = f"""

## Previous Iteration Test Results

**The previous iteration did NOT pass all tests.** Pass rate: {pass_rate * 100:.1f}%.

**Failed Tests**: {len(failed_tests)}
"""
        if specific_errors:
            feedback += "\n**Specific Failure Details**:\n"
            for test_name, error_detail in list(specific_errors.items())[:5]:
                feedback += f"\n### {test_name}\n```\n{error_detail}\n```\n"
        elif failed_tests:
            feedback += "\n**Failed Test Cases**:\n"
            for test in failed_tests[:5]:
                feedback += f"- {test}\n"

        if test_output and not specific_errors:
            feedback += f"\n**Test Output** (last 800 chars):\n```\n{test_output[-800:]}\n```\n"

        feedback += "\n**You MUST read the skill and evolution experiences to fix these failures.**\n"
        return feedback
