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

A FACT is a DECLARATIVE statement about what the environment is like — a latent
regularity of the environment that was verified in this trajectory and is stable
across similar tasks. Typical sources: object/data state, actual API or tool
semantics, domain constraints. Its subject is the world/things, NOT you.
It states how things ARE, with no instruction.

A FACT is NOT a restatement of the system prompt, a skill's SKILL.md, or documented
tool usage. If the knowledge is already written there, do not extract it.

Examples: "objects inside closed containers are not visible until the container is opened",
"this CRM CSV export uses semicolon delimiters, not commas",
"the search API silently truncates queries longer than 200 characters".
A FACT must NOT contain "you should", "must do", or "in order to" — if it does, it's a TIP.

A TIP is a PROCEDURAL rule about what YOU should do, written EXACTLY as:
    When <condition>: use <capability> to <action>
where <capability> is ONE name copied EXACTLY from the Available Capabilities list
(a skill or basic tool such as `bash`, `python_exec`, `grep`, `read_file`).
Examples:
    When the task analyzes a large log file: use grep to extract matching lines first
    When a csv task needs row counts by group: use python_exec to load and group the csv
    When you need the contents of an existing file: use read_file to inspect it before editing
A TIP without a condition, or whose capability is not in the list, is malformed.

Classify with BOTH tests:
- SUBJECT TEST: about a property of the world -> FACT; about what you do -> TIP.
- NECESSITY TEST: going against it FAILS the task (hard constraint) -> FACT;
  going against it only makes you slower/suboptimal (soft heuristic) -> TIP.
When unsure, default to TIP.

Do NOT extract:
- this turn's user request restated as a rule
- names, secrets, account ids, one-off URLs, or ticket numbers
- guesses not verified in the trajectory"""


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
            "or contributed to the failure (a tool that errored, a missing file, an "
            "environmental constraint the agent missed) - only verified observations from "
            "the trajectory, not guesses; (2) TIPs about what the agent SHOULD have done "
            "instead, reframing the mistake as the correct positive action: 'When <cond>: "
            "use <capability> to <correct action>'. Do NOT extract the wrong actions "
            "themselves as tips."
        )
    return f"""You are extracting reusable knowledge from an agent task that was {outcome_lbl}.

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
Write each rule in the same language as the USER turn.
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
        blocks.append(f"=== Task {i}/{n} [{tid}] — {lbl} ===\nTrajectory excerpt:\n{traj}")
    tasks_block = "\n\n".join(blocks)
    return f"""You are extracting reusable knowledge from a BATCH of {n} agent tasks.

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
Write each rule in the same language as the USER turn.
Prioritize rules that GENERALIZE across tasks. For FAILED tasks, extract the lesson (what
the environment required or what the agent SHOULD have done), not the wrong action itself.

Output ONLY new rules, each on its own line, prefixed [FACT] or [TIP]:
[FACT] <declarative fact about this environment>
[TIP] When <condition>: use <capability> to <action>
If you have nothing new, output exactly: NONE
"""


BLAME_SYSTEM = (
    "You are diagnosing why an agent FAILED a task. The agent had a set of rules "
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


DREAM_MERGE_SYSTEM = (
    "You consolidate near-duplicate rules in a single track of an experience bank. "
    "Reduce redundancy without dropping mutually exclusive conditions. "
    "Always write a multi-line THINKING chain of thought first, then REASON, then the verdict. "
    "Output the structured verdict format exactly."
)


def dream_merge_prompt(
    track: str,
    rules_block: str,
    sim_table: str,
    *,
    capabilities: str = "",
) -> str:
    """Prompt for Auto-dream soft-cluster merge (FACT or TIP track)."""
    track_u = (track or "fact").upper()
    tip_extra = ""
    if track_u == "TIP":
        tip_extra = f"""
TIP constraints:
- CANONICAL (for MERGE/REWRITE) MUST be exactly: When <condition>: use <capability> to <action>
- <capability> MUST be ONE name from Available Capabilities below.
- If conditions are mutually exclusive or meaningfully different, choose KEEP_DISTINCT.
- Do NOT invent capabilities not listed.

Available Capabilities:
{capabilities or "(none)"}
"""
    else:
        tip_extra = """
FACT constraints:
- CANONICAL must remain a DECLARATIVE environment statement (no "you should", no TIP form).
- Never convert a FACT into a TIP.
"""
    return f"""You are consolidating a cluster of near-duplicate {track_u} rules from an agent experience bank.

These rules already share the same business-scenario category. Goal: reduce redundancy while
preserving distinct conditions. Prefer MERGE or REWRITE when rules are paraphrases of the
same idea; KEEP_DISTINCT when conditions conflict or cover different cases. The count field
is only an importance hint — never override "different conditions" just because one count
is higher.

Cluster rules (0-based index, text, count):
{rules_block}

Pairwise cosine similarities (i, j, sim):
{sim_table or "(none)"}
{tip_extra}
Reply in EXACTLY this format (field order mandatory):
THINKING:
<multi-line chain of thought: compare members, similarities, and conditions; justify MERGE vs KEEP_DISTINCT vs REWRITE>
REASON: <one-sentence decision summary>
VERDICT: MERGE | KEEP_DISTINCT | REWRITE
CANONICAL: <single retained or rewritten text; empty allowed for KEEP_DISTINCT>
KEEP_INDICES: <comma-separated 0-based indices to keep when KEEP_DISTINCT; else empty>

THINKING is the comparison/trade-off process (required, non-empty). REASON is the final conclusion sentence (required, non-empty).
"""


def detect_judge_prompt(query: str, final_reply: str) -> str:
    """One-shot reply-delivery judge: role → decompose goals → judge reply."""
    return f"""You are an advanced AI system serving as an impartial judge for an agent's text reply.
Your primary role is to rigorously evaluate whether the final assistant reply satisfies the user query.
Evaluate objectively, based solely on the evidence in the query and the reply. There is NO file/artifact delivery — only the final assistant reply.

Follow this process strictly:
1. Positioning: treat yourself as a neutral judge; do not rewrite the reply or invent missing content.
2. Goal decomposition: break the user query into concrete, checkable goals/requirements (atomic where possible).
3. Per-goal judgment: for each goal, decide whether the reply meets it, citing brief evidence from the reply (or noting omission).
4. Aggregate outcome from the per-goal results:
   - success: every goal is satisfied; reply is not empty, not a refusal, not a stall.
   - partial: at least one goal is satisfied, but some are unmet or only partly met.
   - fail: no goal is satisfied, or the reply is empty / a refusal / clearly failed.

User query:
{query[:1500]}

Final assistant reply:
{final_reply or "(empty)"}

For each goal use verdict SATISFIED or UNSATISFIED with a concise justification.
Output ONLY a single JSON object (no markdown fence):
{{"goals":[{{"goal":"<short goal>","verdict":"SATISFIED|UNSATISFIED","reason":"<brief evidence>"}}],"delivery":"answer","outcome":"success|partial|fail","reason":"<one-sentence overall justification>"}}
"""
