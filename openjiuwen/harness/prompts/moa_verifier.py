# coding: utf-8
# SPDX-License-Identifier: MIT
# Derived from https://github.com/llm-as-a-verifier/llm-as-a-verifier
# Revision: 8db8a114355a9d7fdf9a8d1d5c87f6aeebd18770
#
# MIT License
#
# Copyright (c) 2026 llm-as-a-verifier
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
"""Upstream progress-scoring prompt, restricted to its unchanged text-only form."""

from collections.abc import Sequence


def format_steps(steps: Sequence[str]) -> str:
    """Number the agent steps the way the checkpoint prompt refers to them."""
    parts = []
    for k, step in enumerate(steps, start=1):
        parts.append(f"=== Agent Step {k} ===")
        parts.append(str(step).strip())
        parts.append("")
    return "\n".join(parts)


def build_progress_prompt(problem: str, trajectory_text: str, n_steps: int, checkpoint_steps: Sequence[int]) -> str:
    """Build a neutral progress-scoring prompt.

    It never reveals whether the trajectory eventually succeeded — successes
    and failures see the same template.
    """
    n = len(checkpoint_steps)
    out = [
        "You are a strict, skeptical evaluator of agent task attempts. "
        "Agents routinely declare victory while their environment still "
        "shows errors, edit the wrong target, or never actually run the "
        "verification the task asks for. Trust observed output — NOT the "
        "agent's narration.",
        "",
        "**Task instruction:**",
        problem.strip(),
        "",
    ]
    out += [
        f"**Agent trajectory ({n_steps} agent steps; each step is one "
        "action by the agent, with its observed output):**",
        trajectory_text,
        "",
        f"You will score the trajectory at {n} CHECKPOINTS. The score measures exactly ONE thing:",
        "",
        '    "Given everything the agent has done up to and including '
        "this step, would the agent's CURRENT state actually satisfy the "
        "task's hidden grader (i.e. produce the expected files / output / "
        'behavior the task requires)?"',
        "",
        "Use the 20-letter A..T scale:",
        "  A = certainly NO — nothing useful done yet, or the agent is going down a clearly wrong path.",
        "  B-G = leans NO — partial work exists but key pieces are missing or broken.",
        "  H-M = uncertain — a plausible solution is taking shape, but no convincing verification yet.",
        "  N-S = leans YES — the right artifacts appear to be in place and "
        "partial verification has worked, with minor concerns.",
        "  T = essentially certain YES — the agent has run the relevant "
        "verification and the observed output literally matches what the "
        "task calls for, with no outstanding errors.",
        "",
        "CRITICAL CALIBRATION RULES:",
        "  * Effort, exploration, step count, and confident-sounding "
        "narration are NOT progress. An agent that ran 20 commands and "
        "still has not produced the right output deserves a score near A.",
        "  * Default to skepticism. The hidden grader is NOT visible to "
        "you. A result with no real verification step should not exceed "
        "~K, and even a verified-looking one should rarely exceed ~R "
        "unless the verification clearly matches the task's stated "
        "success criterion.",
        '  * Treat the agent\'s prose declarations ("done!", "all tests '
        'pass") as ZERO evidence. Ground your score in the actual actions '
        "and the actual output you can see.",
        "",
        "EXPECTED PATTERNS — successive checkpoints do NOT have to rise:",
        "  * On a trajectory that genuinely solves the task, scores typically rise from A toward T.",
        "  * On a trajectory committed to a WRONG approach, scores should PLATEAU once the wrong artifact is in place.",
        "  * If the agent regresses (breaks something that worked), scores should DECREASE.",
        "",
        "The N checkpoints to score are:",
    ]
    for i, k in enumerate(checkpoint_steps, start=1):
        out.append(f"  Checkpoint {i} = state right after Agent Step {k}")
    out.append("")
    out.append(
        "Score each checkpoint INDEPENDENTLY based on the agent's current "
        "best attempt at that point in the trajectory. Output EXACTLY N "
        "lines and nothing else, in the format:"
    )
    for i in range(1, n + 1):
        out.append(f"<c{i}>LETTER</c{i}>")
    out.append("")
    out.append("where each LETTER is a single letter from A to T.")
    return "\n".join(out)
