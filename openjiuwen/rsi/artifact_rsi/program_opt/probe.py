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

"""Does this scorecard have any ordering power?

Score the starting point, then score a copy that was deliberately made worse.
Two numbers that come back the same mean the scoring cannot separate a good
candidate from a bad one, and a search on flat terrain is a random walk that
looks completely normal from outside: every event fires, every candidate is
recorded, the dashboard shows a search that simply found nothing.

That is why this is worth two evaluations up front. The other pre-flight checks
catch failures that announce themselves — a wrong scale makes the engine throw,
a wrong direction is caught by the normalisation table. This one is the silent
case, and it was not hypothetical: the first model-graded run (in the system
this port came from) gave four candidates in a row full marks, so the tree had
no signal after the first expansion.

The damage is deliberately crude. Being subtle would be measuring something
else: a scorecard that cannot see a predictor replaced by a constant is not
going to separate two real candidates.
"""

from __future__ import annotations

import math
from typing import Any, Callable, Dict, Optional, Tuple

from .engine import RunSpec
from .execution import EvaluationExecution
from .logging_config import get_logger
from .program import DEFAULT_ENTRYPOINT, bundle, files_of

log = get_logger("probe")

#: How close two scores may be and still count as the same. Judged scorers
#: wobble, so exact equality would pass a scorecard that is flat in every way
#: that matters.
TOLERANCE = 0.01

#: What a program candidate is damaged into: a predictor that ignores its input.
#:
#: The damage fallback when hollowing changed nothing worth scoring: the
#: entrypoint's content is replaced with this. Any scoring worth running marks it
#: down; one that does not is not going to rank two real drafts.
_EMPTY_WORDS = "This work did some things, obtained some results, and has a certain significance."


class ProbeError(RuntimeError):
    """The probe could not be taken, which is not the same as failing it."""


def run_probe(spec: RunSpec, execute: "EvaluationExecution") -> Dict[str, Any]:
    """`{baseline, worsened, flat, label}` for one scorecard."""
    from .puct_engine import _mode_of

    mode = _mode_of(spec)

    if spec.packages:
        # The probe runs the starting point, and the starting point is exactly
        # what uses these — a draft that reaches for a boosting library fails
        # here first, as "the starting point does not run", if the library is
        # not there yet.
        from .provision import ProvisionError, ensure

        try:
            ensure(spec.packages, execute)
        except ProvisionError as error:
            raise ProbeError(str(error)) from error

    if not spec.baseline_code.strip():
        raise ProbeError(
            "there is no starting point to measure — the discrimination probe compares "
            "it against a deliberately damaged copy"
        )

    if mode == "custom_script":
        from .script_domain import ScriptError, script_domain

        try:
            domain = script_domain(
                scorecard=spec.scorecard, script=spec.script, execute=execute,
                baseline_code=spec.baseline_code,
                candidate_timeout=spec.candidate_timeout_seconds,
            )
        except ScriptError as error:
            raise ProbeError(str(error)) from error
        # This is the mode where the probe earns its keep twice over. An
        # evaluator nobody has run before can be flat for the ordinary reason —
        # it does not measure what the goal is about — and also because a
        # candidate can write the result file the evaluator was supposed to
        # write. Both look the same from here, and both are refused: a
        # candidate that scores itself scores the same after being damaged.
        # The engine holds out the *tail* of the slot list, so every node score —
        # including the seeded baseline the run card shows — is measured on the
        # gate slots. `range(gate)` starts at 0 and lands on the first rollout
        # slots instead: with one generator per shard the two slices are
        # different problems, and a live run showed the probe saying 0.7157 for
        # a start the run then seeded at 0.2218. Two numbers for one program,
        # both called "the starting point".
        shards = _gate_slots(spec)
        try:
            baseline, _raw, why = _measure(domain.evaluate, spec.baseline_code, shards)
        except ScriptError as error:
            raise ProbeError(
                f"the evaluator could not even finish on the starting point: {error}"
            ) from error
        if baseline is None:
            raise ProbeError(
                "the starting point does not hold up under your evaluator, and every "
                "score in the search is measured relative to it. "
                + (f"The script reported: {why}" if why.strip() else "The script gave no reason.")
            )
        damaged, damage_label = _damage(spec.baseline_code, spec.entrypoint)
        try:
            worsened, _damaged_raw, damaged_why = _measure(domain.evaluate, damaged, shards)
        except ScriptError as error:
            # The damaged copy is what most candidates will look like: a
            # function that raises, or returns None. An evaluator that dies on
            # one would die partway through the search and spend the budget
            # reporting that every candidate is broken. Caught here so the fix
            # names the evaluator rather than arriving as a 502.
            raise ProbeError(
                "the evaluator does not survive a bad candidate — hollowing out the "
                f"starting point's function body made it crash: {error}. Most "
                "candidates in a search look like that, and the evaluator has to score "
                "them as wrong rather than dying with them. Guard two places:"
                "\n1) `import candidate` itself — a hollowed-out module can leave a "
                "module-level name as None or raise outright, and that happens before "
                "any case, where a per-shard try/except cannot reach it. A failed "
                "import scores **worst** (0.0 on a larger-is-better scale), not best — "
                "inverted, the search converges on candidates that do not load."
                "\n2) Each individual call."
                "\nNote: it is the evaluator that has to be robust, never the candidate "
                "— a damaged candidate is exactly what this probe is for."
            ) from error
        flat = worsened is not None and abs(baseline - worsened) <= TOLERANCE
        # Ordered by what each costs. The first three read numbers already paid
        # for; the last two each spend one more evaluation, so they come after
        # everything that can refuse for free.
        _refuse_saturated(spec, baseline, worsened)
        _refuse_nameless_diagnosis(damaged_why)
        _refuse_locationless_diagnosis(damaged_why)
        _refuse_rewarding_the_unimportable(domain.evaluate, shards, baseline,
                                           spec.baseline_code, spec.entrypoint)
        _refuse_noisy(domain.evaluate, spec.baseline_code, shards, baseline, worsened)
        return {"baseline": baseline, "flat": flat,
                "label": damage_label, "worsened": worsened}

    # `custom_script` returned above, so anything still here is a measurement
    # kind this engine does not score: `llm_judge` (the request carries one
    # optimizer model and no judge channel — and grading candidates with the
    # model that wrote them is self-scoring), or a kind that was never ported
    # (`dataset_metric`, `test_gate`). Named rather than half-handled: the old
    # fall-through reached shared code with no domain bound and died as an
    # UnboundLocalError.
    raise ProbeError(
        f"this engine scores by sandboxed evaluation only; a scorecard measured by "
        f"{mode!r} cannot run here — write an evaluator script instead"
    )


def _refuse_saturated(spec: RunSpec, baseline: float, worsened: Optional[float] = None) -> None:
    """A starting point already at the solved threshold has nowhere to climb.

    The third way a scoring scheme can be useless, next to flat and broken —
    and the sneakiest, because everything *works*: the probe discriminates, the
    run starts, the seed is immediately "solved", and the search ends having
    done nothing. Seen live on a drafted evaluator whose integration samples
    were all easy: the fixed 5-point rule it was supposed to improve on scored
    0.9999999 out of the gate.
    """
    threshold = float(spec.scorecard.get("solvedThreshold") or 0.999)
    if baseline < threshold:
        _refuse_thin_headroom(spec, baseline, worsened, threshold)
        return
    raise ProbeError(
        f"the starting point already scores {baseline:.4f} against this scoring, past the "
        f"{threshold:.3f} solved threshold — there is no slope for the search to climb, and "
        "a run would finish where it started. The scoring has to be made harder: harder "
        "cases, tighter tolerances, or a more demanding metric. Saying \"make the cases "
        "harder\" in the box above is enough to redesign it."
    )


def _hollow_out(source: str) -> str:
    """Every top-level function keeps its name and loses its body.

    Parsed rather than string-edited: a regex over `def` would break on nested
    functions, decorators and multi-line signatures, and a damaged copy that
    fails to *parse* measures the parser rather than the scorer.

    Used by both the gated and the scripted probes: neither knows what the
    candidate's entrypoint is called, and "every function still exists and
    every answer is wrong" is the damage that works without knowing.
    """
    import ast

    try:
        tree = ast.parse(source)
    except SyntaxError as error:
        raise ProbeError(f"the project's current implementation does not parse: {error}") from error

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            node.body = [ast.Return(value=ast.Constant(value=None))]
        elif isinstance(node, ast.ClassDef):
            for member in node.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    member.body = [ast.Return(value=ast.Constant(value=None))]
    return ast.unparse(ast.fix_missing_locations(tree))


def _score(
    evaluate: Callable[[str, Any], Tuple[bool, Dict[str, Any], str]],
    candidate: str,
    shards: Any,
) -> Optional[float]:
    return _measure(evaluate, candidate, shards)[0]


def _measure(
    evaluate: Callable[[str, Any], Tuple[bool, Dict[str, Any], str]],
    candidate: str,
    shards: Any,
) -> Tuple[Optional[float], Dict[str, float], str]:
    """`(score, the raw numbers behind it, why not)`.

    The raw half is what a `relative_to_baseline` criterion needs as its
    reference, and it is only available here — the aggregate has already
    divided by it by the time a score comes out.

    The third is the reason the domain gave, and it used to be dropped on the
    floor. What the user then saw was "the starting point does not run, fix it
    first" and nothing
    else — true, and useless: a syntax error, a missing entrypoint and a missing
    dependency all read the same, and none of them is something they can act on
    without being told which. The domain always knows; this is just carrying it.
    """
    valid, metrics, error = evaluate(candidate, shards)
    if not valid:
        return None, {}, error
    raw = {
        key: float(value) for key, value in metrics.items()
        if key != "score" and isinstance(value, (int, float)) and math.isfinite(float(value))
    }
    value = metrics.get("score")
    return (float(value) if isinstance(value, (int, float)) else None), raw, error


def _gate_slots(spec: RunSpec) -> Tuple[int, ...]:
    """The slot positions the run itself gates on: after rollout, before test."""
    for criterion in spec.scorecard.get("criteria") or []:
        split = (criterion.get("measure") or {}).get("split")
        if isinstance(split, dict):
            rollout = max(0, int(split.get("rolloutShards") or 0))
            gate = max(1, int(split.get("gateShards") or 1))
            return tuple(range(rollout, rollout + gate))
    return (0,)


def _gate_count(spec: RunSpec) -> int:
    for criterion in spec.scorecard.get("criteria") or []:
        split = (criterion.get("measure") or {}).get("split")
        if isinstance(split, dict):
            return max(1, int(split.get("gateShards") or 1))
    return 1


def _refuse_noisy(evaluate, baseline_code: str, shards, baseline: float,
                  worsened: Optional[float]) -> None:
    """Refuse a scoring that moves as much on a re-run as it does on real damage.

    "Are the shards enough?" cannot be answered by counting them. A deterministic
    evaluator is stable on three; one that samples, times, or asks a model can be
    unstable on thirty. So it is measured: score the *same* starting point a
    second time and compare the spread against the damage signal the probe has
    already paid for.

    When the two are comparable the tree is climbing noise. Every selection
    afterwards is a coin flip, the run finishes, reports an improvement, and the
    number does not survive a re-run — the most expensive way to learn nothing.
    """
    if worsened is None:
        return
    signal = abs(baseline - worsened)
    if signal <= TOLERANCE:
        return  # Already refused as flat; a noise reading adds nothing.
    from .script_domain import ScriptError

    try:
        repeat = _score(evaluate, baseline_code, shards)
    except ScriptError:
        return  # The first measurement worked; a flaky second one is not this check's call.
    if repeat is None:
        return
    noise = abs(baseline - repeat)
    if noise < signal / 2:
        return
    raise ProbeError(
        f"the same starting point measured twice gave {baseline:.4f} and {repeat:.4f} "
        f"(a difference of {noise:.4f}), while damaging it moved the score by only "
        f"{signal:.4f} — the jitter is as large as the real difference, so the search "
        "would climb the noise: it would finish reporting an improvement, and that "
        "number would not survive a re-run. Make each shard bigger, or pin down what is "
        "random in the scoring (fix the seed, take the median of several passes)"
    )

def _refuse_thin_headroom(spec: RunSpec, baseline: float, worsened: Optional[float],
                          threshold: float) -> None:
    """Refuse a start with less room above it than the scoring can resolve.

    Past the solved threshold is the obvious case and `_refuse_saturated` has it.
    The quieter one is a start that is merely *close*: seen live on a SQL
    normaliser that began at 0.8477 against a 0.999 threshold and finished with
    `bestNodeIndex: 0` — four candidates, none of which beat the seed, because
    there was almost nothing left to win.

    Judged against the probe's own damage signal rather than a second invented
    threshold: that signal is roughly what one real change is worth on this
    scoring, so headroom smaller than a fraction of it means the search is
    working inside its own measurement error. A quarter, not a half — half would
    refuse a start at 0.7, which is inside the range the design guidance asks
    for.
    """
    if worsened is None:
        return
    signal = abs(baseline - worsened)
    headroom = threshold - baseline
    if signal <= TOLERANCE or headroom >= signal / 4:
        return
    raise ProbeError(
        f"the starting point already scores {baseline:.4f}, leaving {headroom:.4f} before the "
        f"{threshold:.3f} solved threshold, while damaging it moved the score by only "
        f"{signal:.4f} — there is less room to win than this scoring can resolve, so the run "
        "will most likely end with no candidate beating the starting point. Make the cases "
        "harder, tighten the tolerances, or pick a more demanding metric, so the starting "
        "point lands back in 0.3-0.7."
    )


def _damage(code: str, entrypoint: str = DEFAULT_ENTRYPOINT) -> Tuple[str, str]:
    """A deliberately worse copy of the starting point, whatever it is made of.

    The damage lands on the entrypoint and the rest of the tree is left alone:
    hollowing every file would test whether the scoring notices a program that
    was deleted, which is a much easier question than the one being asked.

    Hollowing out every function is the right damage for code and a no-op for
    anything else. `custom_script` does not promise the candidate is Python — it
    is whatever the evaluator imports and reads, and a run whose candidate was a
    piece of prose produced `0.1625 vs 0.1625`, exactly equal, three times in a
    row. That was reported as "the scoring cannot tell good from bad", and the
    author rewrote a scorer that was working correctly.

    So the hollowing is checked for having done anything, and when it has not
    the copy is replaced outright. Content that is *there* but says nothing is
    the damage that works on text the way an empty function body works on code.
    """
    files = files_of(code, entrypoint)
    source = files.get(entrypoint, "")
    try:
        hollowed = _hollow_out(source)
    except ProbeError:
        # Not parseable as code, so it was never code. Fall through to text.
        hollowed = source
    if hollowed.strip() != source.strip():
        return bundle({**files, entrypoint: hollowed}), "every function body hollowed out"
    return bundle({**files, entrypoint: _EMPTY_WORDS}), "content replaced with empty phrases"


#: A candidate that cannot be imported at all — the single commonest way a
#: generated program fails, and the one the hollowed-out copy does not reach
#: (a gutted function still imports; its body just returns None).
_UNIMPORTABLE = "raise RuntimeError('this candidate does not import')\n"


def _refuse_rewarding_the_unimportable(evaluate, shards, baseline: float,
                                       code: str = "",
                                       entrypoint: str = DEFAULT_ENTRYPOINT) -> None:
    """Refuse a scoring that pays a candidate for failing to load.

    The evaluator is told to guard `import candidate`, because a broken module
    raises there and a per-case try/except cannot reach it. Guarding it is easy
    to get structurally right and semantically backwards, and one live run did
    exactly that::

        if _cand is None:
            return 1.0, f"import failed: {_IMP_ERR}"

    1.0 was the *best* score. The search's winner was a candidate that raised at
    import, at a perfect 1.0000 on both the rollout and the held-out gate, and
    nothing looked wrong anywhere: the probe had passed, because hollowing out
    function bodies leaves a module that still imports.

    So the probe scores one that does not. An unimportable candidate must not do
    as well as the starting point — if it does, the search converges on programs
    that do not load.
    """
    from .script_domain import ScriptError

    try:
        # The entrypoint raises; every other file is left as it is, so what is
        # being scored is a program whose *import* fails and not one whose files
        # are missing.
        unimportable = _score(
            evaluate,
            bundle({**files_of(code, entrypoint), entrypoint: _UNIMPORTABLE}),
            shards,
        )
    except ScriptError:
        # The evaluator died rather than scoring it. That is the other failure
        # mode, and it already has its own diagnosis on the path above.
        return
    if unimportable is None or unimportable < baseline - TOLERANCE:
        return
    raise ProbeError(
        f"a candidate that cannot even be imported scores {unimportable:.4f} under your "
        f"scoring, against {baseline:.4f} for the starting point — the scoring rewards "
        "programs that do not load, and the search will converge straight onto them. "
        "Most likely the import guard has the score inverted: a candidate that fails to "
        "load must score **worst** (0.0, say), not best."
    )


def _refuse_locationless_diagnosis(said: str) -> None:
    """Refuse a crash report that says what broke but never where.

    Stricter than the gate below it, and for the reader that gate exists to
    protect. `repr(e)` clears "nameless" — ``ValueError('byte must be in
    range(0, 256)')`` is a real message — and is still not enough to repair
    from: the candidate is 245 lines and the value is appended in one of a
    dozen places. Measured on a live compression run: five candidates crashed,
    the repair fired four times and landed once, and every diagnosis it worked
    from named an exception with no file and no line. Two of those failures
    were literally the same one-line bug (`bytearray.append` of a value wider
    than a byte), rediscovered from scratch each time.

    Only fires when the text names an exception. A semantic failure — "round
    trip does not match", "3 of 6 over budget" — has no location to give, and
    demanding one would refuse the evaluators that report best.
    """
    import re

    text = (said or "").strip()
    if not text:
        return
    # `ValueError(`, `IndexError(`, and `error(` — what `repr` of a
    # `struct.error` renders as.
    if not re.search(r"\b\w*(?:error|exception)\s*\(", text, flags=re.IGNORECASE):
        return
    if re.search(r'(?:\bline\s+\d+|\.py[\"\']?[,:]\s*\d+|\bFile\s+")', text, flags=re.IGNORECASE):
        return
    raise ProbeError(
        f"the scoring says what a bad candidate raised but not where: \"{text[:120]}\". "
        "What reads it is the repair step — a candidate is hundreds of lines and the "
        "message on its own points at none of them, so the repair can only tear the "
        "whole thing down and rewrite it, and a rewrite usually does not run. Write "
        "`error` as a trimmed traceback.format_exc(), carrying a file and a line."
    )


def _refuse_nameless_diagnosis(said: str) -> None:
    """Refuse a scoring whose failure text names the exception and nothing else.

    The contract asks the evaluator to fill `error`, and one live run filled it
    with ``f"text{i}: exc {type(e).__name__}"``. Structurally compliant, and the
    reflector then received six identical "IndexError"s with no message, no line
    and no traceback — nothing to fix. What it did instead was re-roll the whole
    approach every expansion (LZ77, then PPM-D, then BWT+MTF+RLE, then a
    multi-strategy coder), each with a fresh bug, and five of six candidates
    landed on exactly 0.000.

    The rule is conservative: strip the case labels, the word `exc`, and the
    exception class names, and if nothing but punctuation is left then the text
    carried no reason at all.
    """
    import re

    text = (said or "").strip()
    if not text:
        return  # Empty is handled where the process tail is appended.
    residue = re.sub(r"\b\w*(?:Error|Exception|Warning)\b", " ", text)
    residue = re.sub(r"\b(?:exc|exception|error|shard|case|text|item)\s*\d*\b", " ", residue,
                     flags=re.IGNORECASE)
    residue = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", residue)
    if residue:
        return
    raise ProbeError(
        f"the scoring reports only the exception's class name for a bad candidate: "
        f"\"{text[:120]}\" — no message, no line number. What reads it is whoever writes "
        "the next candidate, and it cannot fix anything from that; it will discard the "
        "whole approach and start again. Write `error` as repr(e) or a trimmed "
        "traceback.format_exc(), carrying at least the exception's own sentence."
    )
