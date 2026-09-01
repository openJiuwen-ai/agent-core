# Copyright (C) 2026-2026 Huawei Technologies Co., Ltd
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Scoring by a model against a rubric — the mode that needs no dataset.

This is what makes `/evolve` work on things that are not programs over tables:
a prompt, an abstract, a protocol, a plan. There is nothing to execute and
nothing to measure, so a judge model reads the candidate against a frozen rubric
and gives it a number.

It is also the only **non-deterministic** mode, and five things follow from
that. None of them is optional, and each is here because leaving it out fails
quietly:

1. **Blind.** The judge is shown the rubric and the candidate, and nothing else
   — no index, no iteration, no parent's score. Tell a judge that this is
   "version 7, the previous scored 0.8" and "newer is better" becomes a
   self-fulfilling ranking.
2. **Median of several gradings, not one.** A single sample of a noisy scorer is
   a coin flip that the tree will treat as a measurement.
3. **Spread over the threshold is *undecidable*, not zero.** Zero says "this
   candidate is bad"; the truth is "this scorer could not tell". Recording the
   first turns noise into a ranking signal and poisons the denominator.
4. **The rubric is frozen and out of reach.** The candidate is never executed
   here, so it cannot rewrite the thing grading it — but the rubric also has to
   be in the goal's frozen set, or a later mutation of the *goal* could.
5. **A lower solved threshold.** A graded scorer rarely reaches 0.999, and at
   that default every rollout asks for a proposal and the run reports
   `below-threshold` — which reads as the reflector failing when in fact nothing
   was ever counted as solved.

A shard here is one independent grading. For a dataset a shard is a group of
rows; for a judge there is one artifact and the independence has to come from
repetition, so the split's shard counts are how many times each role grades.
"""

from __future__ import annotations

import re
import statistics
from typing import Any, Callable, Dict, List, Mapping, MutableMapping, Optional, Sequence, Tuple

from .logging_config import get_logger
from .prompt import mutation_prompt
from .scorecard import SCORE_KEY, evaluate_constraints, score_candidate
from .vendor.puct.domain import Domain
from .vendor.puct.program import Program
from .vendor.puct.tree import finite as _finite

log = get_logger("judge")


#: Below this many gradings a spread is not a measurement of anything. The
#: design's own floor, and the same argument as four gate shards.
MIN_GRADINGS = 3


class JudgeUnavailable(RuntimeError):
    """No judge model was configured for a scorecard that needs one."""


def judge_domain(
    *,
    scorecard: Mapping[str, Any],
    rubric: str,
    grade: Callable[[str, int], Optional[float]],
    statement: str = "",
    baseline_text: str = "",
    baseline: Optional[MutableMapping[str, float]] = None,
) -> Domain:
    """Build a domain that scores text by asking a model.

    ``grade`` is ``(candidate, seed) -> raw score or None``; the seed is what
    makes repeated gradings independent rather than the same call three times.
    ``None`` means the judge did not answer, which is different from a low
    score and is carried through as such.
    """
    reference: MutableMapping[str, float] = {} if baseline is None else baseline
    criteria = list(scorecard.get("criteria") or [])
    if not criteria:
        raise JudgeUnavailable("this scorecard has no criteria")
    criterion = criteria[0]
    measure = criterion.get("measure") or {}
    scale = measure.get("scale") or {"max": 10, "min": 0}
    variance_threshold = float(measure.get("varianceThreshold") or 0.15)
    samples = max(1, int(measure.get("samplesPerCandidate") or 1))

    def evaluate(text: str, shards: Sequence[int]) -> Tuple[bool, Dict[str, Any], str]:
        if not text.strip():
            return False, {SCORE_KEY: float("-inf")}, "the candidate is empty"

        gradings: List[float] = []
        for shard in shards:
            for repeat in range(samples):
                # The seed is the shard and the repetition, not the candidate:
                # two identical candidates must be gradable to the same number,
                # and a seed derived from the text would make that impossible.
                value = grade(text, shard * 1000 + repeat)
                if value is not None:
                    gradings.append(_normalise(value, scale))

        if len(gradings) < min(MIN_GRADINGS, len(shards) * samples):
            return False, {SCORE_KEY: float("-inf")}, (
                f"the judge returned only {len(gradings)} usable grades, which is not enough to decide"
            )

        median = statistics.median(gradings)
        spread = (max(gradings) - min(gradings)) if len(gradings) > 1 else 0.0
        metrics: Dict[str, Any] = {
            criterion["id"]: median,
            "gradings": len(gradings),
            "spread": spread,
        }

        if spread > variance_threshold:
            # Undecidable, not bad. Recording it as a low score would turn the
            # judge's noise into a ranking signal.
            metrics[SCORE_KEY] = float("-inf")
            metrics["undecidable"] = True
            return False, metrics, (
                f"the spread across {len(gradings)} gradings, {spread:.3f}, is over the "
                f"{variance_threshold:.3f} threshold, so this candidate is undecidable "
                f"(which is not the same as graded badly)"
            )

        raw = {criterion["id"]: median}
        scored = score_candidate(scorecard, raw, reference or raw)
        metrics[SCORE_KEY] = scored.reward

        violations = evaluate_constraints(scorecard, raw, reference or raw)
        if violations:
            metrics[SCORE_KEY] = float("-inf")
            metrics["violated"] = violations[0].constraint_id
            return False, metrics, violations[0].detail
        return True, metrics, ""

    def reward(metrics: Mapping[str, Any]) -> float:
        value = metrics.get(SCORE_KEY)
        if not isinstance(value, (int, float)):
            return 0.0
        return max(0.0, min(1.0, float(value)))

    def prompt(program: Program) -> str:
        return mutation_prompt(
            statement=statement,
            scorecard=scorecard,
            parent_code=program.code,
            parent_score=_finite(program.metrics.get(SCORE_KEY)),
            best_score=None,
            recent=(),
            rubric=rubric,
        )

    return Domain(
        name=str(scorecard.get("hash") or "judge"),
        entrypoint="",
        metric_key=str(criterion["id"]),
        metric_better="higher",
        initial_program=baseline_text,
        initial_summary="first draft",
        evaluate=evaluate,
        reward=reward,
        prompt=prompt,
        task_prompt=lambda shard: f"grading pass {shard}",
        test_shards=(),
        data_summary={"mode": "llm_judge"},
    )


def grader(
    complete: Callable[..., str],
    rubric: str,
    scale: Mapping[str, Any],
    source: str = "",
) -> Callable[[str, int], Optional[float]]:
    """Turn a completion callable into `(candidate, seed) -> score`.

    The prompt carries the rubric, the candidate, and — when there is one — the
    source material the candidate is supposed to be faithful to. **Nothing
    else**: no index, no iteration, no history. That is requirement 4, and it is
    enforced here rather than trusted to the caller, because the caller is the
    only place that knows the identity and so is the only place that could leak
    it.

    The source is what makes an anti-fabrication rubric possible. Measured on
    the first real judged run: asked for "checkable specifics" with nothing to
    check against, the search produced an abstract of invented benchmarks and
    percentages and scored full marks on every grading.
    """
    low = float(scale.get("min", 0))
    high = float(scale.get("max", 10))

    def grade(candidate: str, seed: int) -> Optional[float]:
        reply = complete(_JUDGE_PROMPT.format(
            candidate=candidate, high=high, low=low, rubric=rubric, seed=seed,
            source=(_SOURCE_BLOCK.format(source=source.strip()) if source.strip() else ""),
        ))
        return _first_number(reply, low, high)

    return grade


_SOURCE_BLOCK = """## Source material (what the grade must be faithful to)

{source}

"""

_JUDGE_PROMPT = """Grade the piece of writing below against the rubric.

## Rubric

{rubric}

{source}## What to grade

{candidate}

## Output

Output a single number between {low} and {high} and nothing else.
(Grading pass {seed}; it has nothing to do with the content, do not mention it.)"""

_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


def _first_number(reply: str, low: float, high: float) -> Optional[float]:
    match = _NUMBER.search(reply or "")
    if not match:
        # No answer is not a zero: a judge that failed to reply says nothing
        # about the candidate, and scoring it would say something.
        log.warning("judge returned no number: %r", (reply or "")[:120])
        return None
    value = float(match.group(0))
    return min(high, max(low, value))


def _normalise(value: float, scale: Mapping[str, Any]) -> float:
    low = float(scale.get("min", 0))
    high = float(scale.get("max", 10))
    if high <= low:
        return 0.0
    return (value - low) / (high - low)
