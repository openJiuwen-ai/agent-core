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

"""Turning a scorecard into the one number the engine ranks on, and the veto it
does not.

**This side is the authority.** The control plane has the same formulas in
TypeScript for the wizard's preview and its structural checks, but the numbers a
run is actually decided by are computed here, where the measurements are. The
two are kept honest by a shared fixture that both sides assert against
(`tests/fixtures/scorecard-golden.json`), so a change to one that the other does
not follow fails a test rather than drifting quietly.

Two paths out of one card:

* the weighted aggregate, in ``[0, 1]``, which is what ``evolve()`` ranks and
  what the Beta-posterior gate reads;
* the constraint verdicts, which decide **only** whether a candidate may merge.

A violating candidate keeps its score. Writing the veto as "score 0" would make
every violating candidate tie, and the tree's exploitation term reads *ranks* —
so the tie would flatten exactly the signal the search steers by, and "crashed"
would become indistinguishable from "promising but illegal".
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional

#: The key the tree ranks on -- upstream's contract, and the reason a
#: scorecard's aggregate has to be oriented before it lands in `metrics`.
SCORE_KEY = "score"


@dataclass(frozen=True)
class Violation:
    constraint_id: str
    criterion_id: str
    detail: str


@dataclass(frozen=True)
class CandidateScore:
    """What one candidate is worth, and whether it may merge."""

    reward: float
    criteria: Dict[str, float]
    violations: List[Violation]

    @property
    def acceptable(self) -> bool:
        return not self.violations


#: Every normalisation this side implements. Mirrors `EvolveNormalize` in
#: `packages/schema`; the golden fixture is what keeps the two honest.
KNOWN_NORMALIZE = frozenset({"clamp", "identity", "reciprocal", "relative_to_baseline"})


def normalize(criterion: Mapping[str, Any], raw: Optional[float], baseline: Optional[float]) -> float:
    """One criterion's measurement, as higher-is-better in ``[0, 1]``.

    Every normalisation produces higher-is-better, because the search maximises
    a single scalar: a quantity the user wants *small* has to turn around here
    and nowhere else. The reference for ``relative_to_baseline`` is the baseline
    and never the current best — a drifting reference means yesterday's 0.8 and
    today's 0.8 are different numbers, which pollutes the acceptance prior and
    makes a replay of the same candidates score differently.
    """
    if raw is None or not isinstance(raw, (int, float)) or not math.isfinite(float(raw)):
        return 0.0
    value = float(raw)
    kind = criterion.get("normalize", {}).get("kind", "identity")

    if kind == "identity":
        return _clamp(value)
    if kind == "reciprocal":
        return _clamp(1.0 / (1.0 + max(value, 0.0)))
    if kind == "relative_to_baseline":
        reference = float(baseline) if baseline not in (None, 0) else None
        if reference is None:
            return 0.0
        # `ratio / (1 + ratio)`, not `clamp(ratio)`. The plain ratio saturates
        # the instant a candidate beats the baseline: every improvement ties at
        # 1.0, and with the default solved threshold the baseline itself scores
        # 1.0 and the search stops before its first expansion. Strictly
        # increasing and never at its bound — baseline 0.5, half the error
        # 0.667, a tenth 0.909, twice the error 0.333.
        #
        # Which way the ratio goes is the criterion's `direction`, and it was
        # not read here: every metric was treated as lower-is-better. Measured
        # on AlgoTune's `polynomial_real`, whose metric is a speedup to be
        # *maximised*: a candidate at 0.51x — twice as slow as the reference —
        # scored 0.66, was adopted as the best node, and the run reported
        # `completed`. Absent, the direction stays lower-is-better, which is
        # what every card written against this normaliser so far meant.
        if criterion.get("direction") == "maximize":
            if value <= 0:
                return 0.0
            ratio = value / reference
        else:
            if value <= 0:
                return 1.0
            ratio = reference / value
        return _clamp(ratio / (1.0 + ratio))
    if kind == "clamp":
        lo = float(criterion["normalize"].get("lo", 0.0))
        hi = float(criterion["normalize"].get("hi", 1.0))
        if hi == lo:
            return 0.0
        scaled = (value - lo) / (hi - lo)
        return _clamp(1.0 - scaled if criterion.get("direction") == "minimize" else scaled)
    # Not a zero. A normalisation nobody implements would score every candidate
    # 0.0, so the search would run its whole budget, accept nothing, and report
    # that it found no improvement — with no error anywhere. `KNOWN_NORMALIZE`
    # is checked once before the search starts; reaching here means that check
    # was skipped, and being loud about it is the only useful thing left.
    raise ValueError(f"unknown normalisation {kind!r}")


def normalized_weights(scorecard: Mapping[str, Any]) -> Dict[str, float]:
    """Weights as fractions of their sum.

    So a card whose weights read 3 and 1 means the same as one that reads 0.75
    and 0.25.
    """
    criteria = scorecard.get("criteria") or []
    total = sum(float(c.get("weight", 0)) for c in criteria)
    if total <= 0:
        return {c["id"]: 0.0 for c in criteria}
    return {c["id"]: float(c.get("weight", 0)) / total for c in criteria}


def aggregate(scorecard: Mapping[str, Any], normalized: Mapping[str, float]) -> float:
    """Weighted sum, or weighted geometric mean when no weak dimension is
    acceptable.

    A geomean is dragged to zero by any criterion near zero, which is exactly
    what "don't trade this away" means.
    """
    weights = normalized_weights(scorecard)
    if scorecard.get("aggregate") == "weighted_geomean":
        product = 1.0
        for criterion_id, weight in weights.items():
            value = max(float(normalized.get(criterion_id, 0.0)), 0.0)
            if value <= 0:
                return 0.0
            product *= value ** weight
        return _clamp(product)
    return _clamp(sum(weight * float(normalized.get(cid, 0.0)) for cid, weight in weights.items()))


def evaluate_constraints(
    scorecard: Mapping[str, Any],
    raw: Mapping[str, float],
    baseline: Mapping[str, float],
) -> List[Violation]:
    """Which constraints this candidate broke.

    Constraints read **raw** values, not normalised ones: "must finish within
    300 seconds" is a statement about seconds, and normalising first would make
    the threshold depend on the card's scaling choice.

    A criterion with no measurement cannot violate anything — unknown is not
    failure. Refusing to start a run whose criteria cannot be measured is the
    validator's job, and it happens before any of this.
    """
    violations: List[Violation] = []
    for constraint in scorecard.get("constraints") or []:
        criterion_id = constraint["criterionId"]
        value = raw.get(criterion_id)
        if value is None:
            continue
        threshold = constraint["value"]
        if isinstance(threshold, Mapping):
            reference = baseline.get(criterion_id)
            if reference is None:
                continue
            threshold = float(reference) * float(threshold["relativeToBaseline"])
        if not _satisfies(float(value), constraint["op"], float(threshold)):
            violations.append(Violation(
                constraint_id=constraint["id"],
                criterion_id=criterion_id,
                detail=f"{criterion_id}={value:g} does not satisfy {constraint['op']} {threshold:g}",
            ))
    return violations


def score_candidate(
    scorecard: Mapping[str, Any],
    raw: Mapping[str, float],
    baseline: Mapping[str, float],
) -> CandidateScore:
    """The scalar the tree ranks on, plus the verdicts that gate the merge."""
    normalized = {
        criterion["id"]: normalize(criterion, raw.get(criterion["id"]), baseline.get(criterion["id"]))
        for criterion in scorecard.get("criteria") or []
    }
    return CandidateScore(
        reward=aggregate(scorecard, normalized),
        criteria=normalized,
        violations=evaluate_constraints(scorecard, raw, baseline),
    )


def _satisfies(value: float, op: str, threshold: float) -> bool:
    if op == "<":
        return value < threshold
    if op == "<=":
        return value <= threshold
    if op == ">":
        return value > threshold
    if op == ">=":
        return value >= threshold
    return False


def _clamp(value: float) -> float:
    if not math.isfinite(value):
        return 0.0
    return min(1.0, max(0.0, value))
