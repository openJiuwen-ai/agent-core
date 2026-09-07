# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Prompt building blocks for TTSE (Two-Track Self-Evolution).

Ported from the TTSE reference implementation (``ttseopenclaw/ttse/prompts.py``).
``FACT_TIP_DEFINITION`` is the frozen dual-judgment core. Induce / blame no
longer paste a separate task-brief copy: ``traj_text`` already includes the
USER turn (see ``messages_to_trajectory_text``). ``ExistingBank`` bundles
correlated ``induce_prompt`` parameters to stay under G.FNM.03.

A FACT is a declarative statement about THIS environment; a TIP is a procedural
rule of the form ``When <condition>: use <capability> to <action>``.
"""

from dataclasses import dataclass


FACT_TIP_DEFINITION = """\
Each rule is either a FACT or a TIP, with DIFFERENT grammatical forms.

A FACT is a DECLARATIVE statement describing a property of THIS benchmark environment
(the harness, task types, file layouts, what the grader checks, how tools behave here).
Its subject is the world/things, NOT you. It states how things ARE, with no instruction.
Examples: "the grader for csv tasks checks exact column names case-sensitively",
"log_analysis tasks expect output as plain text in answer.txt, not JSON",
"meeting transcripts are large; read them with grep first, not wholesale".
A FACT must NOT contain "you should", "must do", or "in order to" — if it does, it's a TIP.

A TIP is a PROCEDURAL rule about what YOU should do, written EXACTLY as:
    When <condition>: use <capability> to <action>
where <capability> is ONE name from the Available Capabilities list (a built-in skill
like `session-logs`, or a basic tool like `shell`, `jq`, `python3`, `grep`, `read`).
Examples:
    When the task analyzes a large log file: use grep to extract matching lines first
    When a csv task needs row counts by group: use python3 to load and group the csv
    When you must search prior conversation history: use the `session-logs` skill
A TIP without a condition, or whose capability is not in the list, is malformed.

Classify with BOTH tests:
- SUBJECT TEST: about a property of the world -> FACT; about what you do -> TIP.
- NECESSITY TEST: going against it FAILS the task (hard constraint) -> FACT;
  going against it only makes you slower/suboptimal (soft heuristic) -> TIP.
When unsure, default to TIP."""


@dataclass(frozen=True)
class ExistingBank:
    """Existing FACT/TIP bank text fed to :func:`induce_prompt`.

    Bundles the correlated facts/tips strings so ``induce_prompt`` stays under
    the repo's argument-count limit (G.FNM.03); the rendered prompt is unchanged.
    """

    facts: str
    tips: str


def induce_prompt(_task_prompt: str, traj_text: str, capabilities: str, bank: ExistingBank, outcome: str) -> str:
    if outcome == "success":
        outcome_lbl = "SOLVED SUCCESSFULLY"
        guidance = "Extract the tactics and environment facts that LED to this success."
    elif outcome == "partial":
        outcome_lbl = "PARTIALLY SOLVED"
        guidance = "Extract rules that would help a future agent finish similar tasks fully."
    else:
        outcome_lbl = "FAILED COMPLETELY"
        guidance = (
            "This task FAILED. Extract LESSONS: (1) FACTS about the environment that CAUSED "
            "or contributed to the failure (a tool that errored, a missing file, a grader "
            "requirement the agent missed) - only verified observations from the trajectory, "
            "not guesses; (2) TIPs about what the agent SHOULD have done instead, reframing "
            "the mistake as the correct positive action: 'When <cond>: use <capability> to "
            "<correct action>'. Do NOT extract the wrong actions themselves as tips."
        )
    return f"""You are extracting reusable knowledge from an agent task on a benchmark that was {outcome_lbl}.

{FACT_TIP_DEFINITION}

Available Capabilities (TIPs may only reference these names):
{capabilities}

Existing bank — do NOT output rules that duplicate or are subsumed by these:
FACTS:
{bank.facts or "(none)"}
TIPS:
{bank.tips or "(none)"}

Agent trajectory (USER turn is the task; then what the agent actually did):
{traj_text}

{guidance}

Extract NEW rules that would help a future agent on SIMILAR tasks in THIS environment.
Prefer specific, verified observations over vague generalities. Output ONLY new rules,
each on its own line, prefixed [FACT] or [TIP]:
[FACT] <declarative fact about this environment>
[FACT] ...
[TIP] When <condition>: use <capability> to <action>
[TIP] ...
If you have nothing new (everything is already in the existing bank), output exactly: NONE
"""


# outcome label map for batch prompt
_OUTCOME_LBL = {
    "success": "SOLVED SUCCESSFULLY",
    "partial": "PARTIALLY SOLVED",
    "fail": "FAILED COMPLETELY",
}


def induce_batch_prompt(group, capabilities: str, existing_facts: str, existing_tips: str) -> str:
    """group: list of (task_id, task_prompt, traj_text, outcome_lbl). One GLM call."""
    n = len(group)
    blocks = []
    for i, (tid, _prompt, traj, lbl) in enumerate(group, 1):
        blocks.append(
            f"=== Task {i}/{n} [{tid}] — {lbl} ===\nTrajectory excerpt:\n{traj[:1100]}"
        )
    tasks_block = "\n\n".join(blocks)
    return f"""You are extracting reusable knowledge from a BATCH of {n} agent tasks on a benchmark.

{FACT_TIP_DEFINITION}

Available Capabilities (TIPs may only reference these names):
{capabilities}

Existing bank — do NOT output rules that duplicate or are subsumed by these:
FACTS:
{existing_facts or "(none)"}
TIPS:
{existing_tips or "(none)"}

The {n} tasks in this batch (outcome + trajectory excerpt each; USER turn is the task):
{tasks_block}

Extract NEW rules that would help a future agent on SIMILAR tasks in THIS environment.
Prioritize rules that GENERALIZE across tasks. For FAILED tasks, extract the lesson (what
the environment required or what the agent SHOULD have done), not the wrong action itself.

Output ONLY new rules, each on its own line, prefixed [FACT] or [TIP]:
[FACT] <declarative fact about this environment>
[TIP] When <condition>: use <capability> to <action>
If you have nothing new, output exactly: NONE
"""


BLAME_SYSTEM = (
    "You are diagnosing why an agent FAILED a benchmark task. The agent had a set of rules "
    "(facts/tips) in its context during the task. Attribute the failure to AT MOST ONE rule "
    "that was WRONG or MISLED the agent (caused a wrong action or made it miss the right one). "
    "If no rule is at fault, say NONE. Be strict: only blame a rule you can tie to a concrete "
    "wrong step in the trajectory."
)


def blame_prompt(_task_prompt: str, traj_text: str, rules_numbered: str) -> str:
    return f"""Rules that were in the agent's context during this task (numbered, facts then tips):
{rules_numbered}

The agent FAILED this task. Its trajectory (USER turn is the task; then what it did):
{traj_text}

Which ONE rule (by its number) most contributed to the failure by being wrong or misleading?
If none of the rules are at fault (the failure was due to something else), say NONE.

Reply in EXACTLY this format:
VERDICT: <single number from the list above, or NONE>
REASON: <one sentence tying the rule to a concrete wrong step, or why none apply>
"""


SYNTH_SYSTEM = (
    "You review a rule bank for CONTRADICTIONS (two rules that conflict) or near-DUPLICATES. "
    "If you find a contradiction, propose AT MOST ONE synthesized TIP that resolves it. "
    "If there are no contradictions, output NONE."
)


def synthesize_prompt(rules_numbered: str, capabilities: str) -> str:
    return f"""Current rules in the bank (numbered, facts then tips):
{rules_numbered}

Available Capabilities (a synthesized TIP may only reference these):
{capabilities}

Find a contradiction or a pair of conflicting/duplicating rules. If one exists, propose
ONE resolving TIP in the form 'When <condition>: use <capability> to <action>'.
Output exactly one line:
[TIP] When <condition>: use <capability> to <action>
If there is no contradiction or duplication, output exactly: NONE
"""
