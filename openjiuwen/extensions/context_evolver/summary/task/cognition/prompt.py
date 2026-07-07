# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Prompts for Cognition summary operations."""

from dataclasses import dataclass

CLASSIFY_SOLUTION_TEMPLATES = """You are an expert AI ontologist and behavioral analyst.
Your task is to classify an agent's completed task into a dynamic schema based on the query, the execution trajectory, and an initial pre-execution guess.

### INPUT CONTEXT
- User Query: "{query}"
- Initial Classification Guess: {initial_attributes}
- Execution Trajectory:
{trajectory}

### CURRENT DYNAMIC SCHEMA
{current_schema}

### REQUIRED OUTPUT FORMAT
You MUST output ONLY a valid JSON object. Do NOT wrap the response in markdown blocks (e.g., no ```json).
The JSON object MUST contain exactly the keys listed in the schema above.

### STRICT RULES FOR CLASSIFICATION
1. EVALUATE THE TRAJECTORY: The Initial Guess was based only on the query. You must read the Execution Trajectory to understand what the agent *actually* did.
2. PREFER EXISTING VALUES: For every key in the schema, you MUST first try to assign one of the existing values from the CURRENT DYNAMIC SCHEMA.
3. GENERATE NEW VALUES (IF NECESSARY): IF AND ONLY IF none of the existing values accurately describe the actual trajectory, you are ALLOWED to generate a new value for ANY key.
4. FORMATTING FOR NEW VALUES: Any newly generated value MUST be a concise semantic tag (1-2 words). CRITICAL: You MUST replace any spaces in generated values with underscores (`_`, e.g., output "API_Integration" instead of "API Integration").
5. COMPLETENESS: Every key from the schema MUST be present in your JSON output. If a category is completely irrelevant and you cannot assign or create a meaningful tag, assign `null`. 
- IF all other keys are assigned `null`, the "other" key MUST NOT be `null`. 
- IF one or more other keys successfully receive a value, the "other" key MAY be `null`.
"""

WRITE_COGNITION_TEMPLATES = """You are an expert AI behavior analyst and knowledge extractor.
Your task is to analyze an agent's recent task execution trajectory and extract high-value cognitive memory.

### EXECUTION CONTEXT
- User Query (Task): "{query}"
- Task Successful: {is_correct}
- Execution Trajectory (Steps and Observations):
{trajectory}

### REQUIRED OUTPUT FORMAT
You MUST output ONLY a valid JSON object. Do NOT wrap the response in markdown blocks (e.g., no ```json).
The JSON object MUST contain exactly these two keys:
1. "description": A concise string (1-2 sentences) summarizing what this memory is about and what specific problem it solves.
2. "experience": A list of strings. Each string should be a highly actionable rule, insight, or "lesson learned" derived from the trajectory.

### REFLECTION RULES
- If "Task Successful" is True: Focus the "experience" list on WHY it succeeded. What specific commands, logic, or steps were effective? Extract reusable patterns.
- If "Task Successful" is False: Focus the "experience" list on WHY it failed. What were the dead ends? What errors occurred? Formulate "Do NOT do X, instead try Y" style rules to prevent future agents from repeating the exact same mistakes.
- If "Task Successful" is null or None: Focus the "experience" list on objective observations and verified mechanics. Since the final outcome is unverified or ambiguous, extract neutral facts about how the environment responded to specific actions. Formulate "Action X reliably results in Observation Y" or "Tool Z requires parameters in format W" style rules.
- Be concrete. Do NOT say "Use the right tool." DO say "When querying the database, ensure the date format is YYYY-MM-DD to avoid syntax errors."
"""


@dataclass
class CognitionSummaryPrompt:
    classify_solution_prompt: str = CLASSIFY_SOLUTION_TEMPLATES
    write_cognition_prompt: str = WRITE_COGNITION_TEMPLATES

CognitionSummaryPrompts = CognitionSummaryPrompt()