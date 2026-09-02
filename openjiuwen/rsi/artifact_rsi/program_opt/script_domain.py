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

"""Scoring by a program the drafting model wrote.

This was one of four, and it is now one of two. The three it replaced each
assumed something about the goal: that there was a table with a column to
predict, that there was a suite whose pass rate was the answer. Plenty of real
goals have neither. "Write a function that splits Chinese addresses into four
fields, as accurately as possible" has an obvious deterministic score — run it
over labelled examples and count exact matches — and had no home in any of
them. Asked to design that search, the drafting model said so itself: *the
workspace is empty and I cannot create files, so I will use llm_judge*, and
fell back to the non-deterministic, most gameable mode for a goal that did not
need it. Across thirty real runs the drafting agent chose this mode every time.

So this mode's answer is: the model writes the evaluator. Same `Domain` seam,
so the engine, the tree and the aggregator are untouched — what is new is a
second program in the sandbox.

**The contract is four lines, because a contract a model gets wrong is a run
that fails at the end.** The evaluator is run with its working directory set to
a throwaway copy; the candidate is beside it; three environment variables say
where things are; and it writes one JSON object.

    SCIENCE_AGENT_CANDIDATE   the candidate program's filename
    SCIENCE_AGENT_SHARDS      which shards to score, comma-separated
    SCIENCE_AGENT_RESULT      where to write the result JSON

    {"valid": true, "metrics": {"<criterion id>": 0.83}, "error": ""}

**The result is a file, not stdout.** A candidate that prints is ordinary; a
candidate whose print lands in the middle of the result is a run that fails for
a reason nobody can see. The file also survives a noisy dependency.

**The shards are not decoration.** The evaluator is given which slice to score
and is expected to use it — that is what makes the gate a held-out gate rather
than a second look at the same examples. An evaluator that ignores them turns
the gate into a copy of the rollout, and nothing downstream can tell.

**What stops a candidate from scoring itself.** Nothing, inside one process:
the evaluator imports the candidate, so the candidate can in principle write
the result file too. The guard is the discrimination probe, and it is a real
one — it damages the candidate and requires the score to move. A candidate that
reports its own score reports the same number after being damaged, which is
exactly the shape the probe refuses to start.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Mapping, MutableMapping, Optional, Sequence, Tuple


from .domain import Domain
from .events import finite as _finite
from .logging_config import get_logger
from .program import Program, files_of
from .prompt import mutation_prompt
from .execution import EvaluationExecution
from .scorecard import SCORE_KEY, evaluate_constraints, score_candidate
from .shard_roles import cases_for, total_slots

log = get_logger("script")


#: The three names the evaluator reads. Environment rather than argv because a
#: candidate imported into the same process sees both, and the environment is
#: the one every language spells the same way.
CANDIDATE_ENV = "SCIENCE_AGENT_CANDIDATE"
SHARDS_ENV = "SCIENCE_AGENT_SHARDS"
RESULT_ENV = "SCIENCE_AGENT_RESULT"

#: What the candidate is written as. Named rather than guessed so an evaluator
#: can `import candidate` without the drafting model having to invent a
#: convention that this side would then have to match.
CANDIDATE_FILE = "candidate.py"
EVALUATOR_FILE = "evaluate.py"
_SHIM_FILE = "_entry.py"

#: Why there is a shim at all: the sandbox runs the interpreter with ``-I``,
#: which since 3.11 implies ``-P`` — the script's own directory is *not* put on
#: ``sys.path``. So the most natural line an evaluator can contain,
#: ``import candidate``, raises ImportError, and the drafting model would have
#: to know that to write a working evaluator. Requiring it to is a trap; four
#: lines here is not.
_SHIM = """import os, runpy, sys
sys.path.insert(0, os.getcwd())
runpy.run_path({evaluator!r}, run_name="__main__")
"""


class ScriptError(RuntimeError):
    """The evaluator could not be run at all, which is a run-level fault."""


def script_domain(
    *,
    scorecard: Mapping[str, Any],
    script: str,
    execute: EvaluationExecution,
    statement: str = "",
    baseline_code: str = "",
    entrypoint: str = CANDIDATE_FILE,
    candidate_timeout: float = 120.0,
    baseline: Optional[MutableMapping[str, float]] = None,
    mutation_template: str = "",
) -> Domain:
    """Build a domain that scores a candidate by running the drafted evaluator."""
    reference: MutableMapping[str, float] = {} if baseline is None else baseline
    criteria = list(scorecard.get("criteria") or [])
    if not criteria:
        raise ScriptError("this scorecard has no criteria")
    criterion = criteria[0]
    metric_id = str(criterion.get("id") or SCORE_KEY)
    # Slots are positional (the engine holds out by tail); the case behind a slot
    # is not. See `shard_roles` — without this the search trains on one end of
    # the evaluator's case list and gates on the other.
    _split = (criterion.get("measure") or {}).get("split") or {}
    _total = total_slots(_split)
    _seed = int(_split.get("seed") or 0)
    if not script.strip():
        raise ScriptError("this scorecard says it is scored by an evaluator script, but the script is empty")

    def evaluate(code: str, shards: Sequence[int]) -> Tuple[bool, Dict[str, Any], str]:
        try:
            payload = _run_evaluator(
                code, script, cases_for(shards, _total, _seed),
                execute=execute, timeout=candidate_timeout,
                entrypoint=entrypoint,
            )
        except ScriptError:
            # A broken evaluator is not a bad candidate. Raised so the run
            # stops and says which of the two is wrong, rather than reporting
            # every candidate as invalid until the budget runs out.
            raise

        if not payload.get("valid", False):
            # The failure text is what the reflector learns from and what the
            # user reads on the candidate card. An evaluator that writes
            # valid:false with no error produces "marked this candidate invalid"
            # and nothing
            # else — seen on a real run, on a card, saying nothing anyone could
            # act on. Whatever the evaluator *did* say (metrics it still
            # reported, its stdout) is better than that.
            reason = str(payload.get("error") or "").strip()
            if not reason:
                reported = payload.get("metrics")
                detail = (
                    f"the values it reported were {json.dumps(reported, ensure_ascii=False)[:200]}"
                    if isinstance(reported, dict) and reported
                    else "and gave no reason — the contract asks it to say why in the "
                         "error field, which is what the next author reads"
                )
                reason = f"the evaluator marked this candidate invalid: {detail}"
            return False, {SCORE_KEY: float("-inf")}, reason

        values = payload.get("metrics")
        if not isinstance(values, dict) or metric_id not in values:
            # Named precisely: "the evaluator returned nothing useful" is the
            # kind of message that sends a user to read a hundred lines of
            # someone else's Python.
            raise ScriptError(
                f"the evaluator did not report criterion {metric_id} — what it gave was "
                f"{sorted(values) if isinstance(values, dict) else type(values).__name__}"
            )

        raw = {key: value for key, value in values.items() if isinstance(value, (int, float))}
        scored = score_candidate(scorecard, raw, reference or raw)
        metrics: Dict[str, Any] = {**raw, SCORE_KEY: scored.reward}

        violations = evaluate_constraints(scorecard, raw, reference or raw)
        if violations:
            metrics[SCORE_KEY] = float("-inf")
            metrics["violated"] = violations[0].constraint_id
            return False, metrics, violations[0].detail
        return True, metrics, _diagnosis(payload)

    def reward(metrics: Mapping[str, Any]) -> float:
        value = metrics.get(SCORE_KEY)
        if not isinstance(value, (int, float)):
            return 0.0
        return max(0.0, min(1.0, float(value)))

    contract = _contract_of(script)

    def prompt(program: Program) -> str:
        return mutation_prompt(
            statement=statement,
            scorecard=scorecard,
            parent_code=program.code,
            entrypoint=entrypoint,
            parent_score=_finite(program.metrics.get(SCORE_KEY)),
            best_score=None,
            recent=(),
            script_contract=contract,
            feedback=program.error,
            template=mutation_template,
        )

    return Domain(
        name=str(scorecard.get("hash") or "custom-script"),
        entrypoint=entrypoint,
        metric_key=metric_id,
        metric_better="higher",
        initial_program=baseline_code,
        initial_summary="the starting point",
        evaluate=evaluate,
        reward=reward,
        prompt=prompt,
        task_prompt=lambda shard: f"evaluate this program on shard {shard}",
        test_shards=_test_shards(criterion.get("measure") or {}),
        data_summary={"mode": "custom_script"},
    )


def _contract_of(script: str) -> str:
    """What the evaluator requires of a candidate, in the evaluator's words.

    The module docstring, because that is where the drafting guidance tells the
    model to state the interface — and it is the part of the script worth
    showing. The whole script would also show the sample list, and a candidate
    that has read the answer key optimises for reciting it; the docstring shows
    the contract and keeps the answers out of the prompt.

    A script with no docstring falls back to its head — a wrong contract is a
    zero on every candidate, which is worse than a leaky prompt.
    """
    import ast

    try:
        doc = ast.get_docstring(ast.parse(script))
    except SyntaxError:
        doc = None
    if doc and doc.strip():
        return doc.strip()
    return "\n".join(script.splitlines()[:40])


def _run_evaluator(
    code: str,
    script: str,
    shards: Sequence[int],
    *,
    execute: EvaluationExecution,
    timeout: float,
    entrypoint: str = CANDIDATE_FILE,
) -> Dict[str, Any]:
    """Materialise both programs in a throwaway directory and read the result.

    A fresh copy per candidate, so an evaluator a candidate managed to damage is
    damaged for exactly one evaluation and the next one starts from the text the
    user approved.
    """
    # The whole tree at its own relative paths, plus the evaluator and shim,
    # handed to the injected execution as *content*. Path validation happened
    # when the tree was parsed (`files_of` refuses traversal); where and how a
    # scratch directory exists is the execution's business — agent-core's own
    # sandbox stages it behind the gateway.
    files = dict(files_of(code, entrypoint))
    files[EVALUATOR_FILE] = script
    files[_SHIM_FILE] = _SHIM.format(evaluator=EVALUATOR_FILE)

    result_file = "result.json"
    env = {
        CANDIDATE_ENV: entrypoint,
        # Relative to the run's own working directory: a host-absolute path
        # means nothing inside a remote sandbox instance.
        RESULT_ENV: result_file,
        SHARDS_ENV: ",".join(str(int(shard)) for shard in shards),
    }
    try:
        outcome = execute(files, ["python", "-I", _SHIM_FILE], env, timeout, result_file)
    except Exception as error:  # noqa: BLE001 - the seam's own failure is run-level
        raise ScriptError(f"the evaluator could not be executed: {error}") from error

    tail = (outcome.output or "").strip()[-400:]
    if outcome.result_text is not None:
        try:
            payload = json.loads(outcome.result_text)
        except json.JSONDecodeError as error:
            raise ScriptError(f"what the evaluator wrote is not parsable JSON: {error}") from error
        if not isinstance(payload, dict):
            raise ScriptError("the JSON the evaluator wrote is not an object")
        # What the candidate itself printed while dying. An evaluator that
        # wraps each case in try/except — which it is told to do — usually
        # records that the case failed and not why, so this is the only
        # place the traceback survives. Carried, not merged: only used when
        # the evaluator's own diagnosis turns out to say nothing.
        payload["_processTail"] = tail
        return payload

    # No file, but the answer may still be right there. The contract says
    # write the file and says it twice, and a real model printed the correct
    # object to stdout anyway — refusing a correct answer for arriving in
    # the wrong envelope is the same mistake as refusing a drafted plan that
    # came back as prose. The file stays preferred; this is the fallback.
    printed = _last_result(outcome.output or "")
    if printed is not None:
        log.info("evaluator printed its result instead of writing %s", RESULT_ENV)
        return printed

    raise ScriptError(
        f"the evaluator wrote neither the result file {RESULT_ENV} points at nor a "
        "result JSON on its output. "
        + (f"It said: {tail}" if tail else _silent_death(outcome.exit_code))
    )


def _silent_death(returncode: Optional[int]) -> str:
    """What to say when a process died without a byte of output.

    The exit code is the only witness left, and the negative ones are signals —
    each pointing somewhere different. Seen live: an evaluator integrating
    near-singular functions was SIGKILLed with empty stdout and stderr, and
    "It said: (no output)" gave the user nothing to act on and us nothing to
    debug. The code was in `completed.returncode` the whole time; it was just
    never printed.
    """
    if returncode == -9:
        return (
            "it was killed outright (SIGKILL) without printing a word — most likely the "
            "system killed it for running out of memory, or it went over a CPU quota. "
            "Having the evaluator work through the cases rather than computing them all "
            "at once, and keeping the arrays smaller, usually avoids it"
        )
    if returncode == -11:
        return ("it printed nothing and died with a segmentation fault (SIGSEGV) — most likely a "
                "binary dependency crashed inside the sandbox")
    if returncode < 0:
        return f"it printed nothing and was terminated by signal {-returncode}"
    return f"it printed nothing and exited with code {returncode}"


def _last_result(stdout: str) -> Optional[Dict[str, Any]]:
    """The last line that parses as a result object.

    The *last*, and only whole lines: a candidate prints too, and taking the
    first JSON-looking thing would let ordinary debug output decide the score.
    The evaluator runs after the candidate is imported, so its line comes last —
    and a candidate that wanted to forge one would have to be the final printer,
    which is the same exposure the discrimination probe already covers.
    """
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line.startswith("{") or "valid" not in line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and "valid" in payload:
            return payload
    return None


def _test_shards(measure: Mapping[str, Any]) -> Tuple[int, ...]:
    """The shards the search never sees.

    Positional, like every other mode: the framework holds out by index, so the
    ordering is what makes the last few shards the held-out ones.
    """
    split = measure.get("split") or {}
    rollout = int(split.get("rolloutShards") or 0)
    gate = int(split.get("gateShards") or 0)
    count = int(split.get("testShards") or 0)
    return tuple(range(rollout + gate, rollout + gate + count))


def _diagnosis(payload: Mapping[str, Any]) -> str:
    """What the reflector is told about this candidate.

    The evaluator's own text when it carries a reason. When it does not — the
    shape seen on a real run was every case reporting `err=None, nfev=0`, which
    says the candidate never ran but not why — the process output is appended,
    because that is where the traceback went. Without it the reflector is told
    "score 0" seven times and keeps proposing variants of the same broken idea.
    """
    said = str(payload.get("error") or "").strip()
    tail = str(payload.get("_processTail") or "").strip()
    if not tail:
        return said
    # "Says nothing" is not the same as "is empty": a line of `err=None` for
    # every case is text, and it is still no reason.
    uninformative = not said or ("err=None" in said and "Traceback" not in said)
    if not uninformative:
        return said
    return (said + "\n" if said else "") + f"what the candidate process printed: {tail}"

