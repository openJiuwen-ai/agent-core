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

"""The one prompt this engine sends: mutate a parent program into a child.

**This is not upstream's prompt.** `examples/era` wraps its calls in a preamble
tuned for one Kaggle task; the objective here comes from the user's scorecard,
so the prompt has to state that objective instead. Written fresh, and labelled
as such rather than inheriting a fidelity claim it cannot support.

Everything in it earns its place by preventing a specific failure:

* **The gate's rules are stated up front.** The AST gate refuses ``open``, a
  forbidden import or a stray top-level statement, and a refusal is recorded as
  a failed candidate — so a model that was never told the rules produces a run
  full of failures that look like it cannot code.
* **The entrypoint's exact signature is given.** The runner calls
  ``train_and_predict(train_path, test_path)`` and expects one prediction per
  test row. A model that invents ``fit``/``predict`` classes fails at load with
  a message about a missing function.
* **The scorecard is spelled out, direction included.** "Improve the program" is
  not an objective; a candidate cannot be aimed at a metric it was not told
  about, and a *minimised* metric described without its direction gets
  optimised the wrong way.
* **A constraint is presented as a wall, not a cost.** Constraints refuse a
  merge outright, so a model told "prefer fast" will trade accuracy for speed it
  did not need to buy and be refused anyway.
* **The objective is the score, and the cost of failing is stated.** Upstream
  says "generate a NEW, IMPROVED function" and gets away with it because its task
  is a twenty-line sklearn pipeline. Asked the same way about a codec, every
  candidate replaced the whole mechanism and ten of eleven did not run. What the
  model was never told is that not running scores zero — worse than leaving the
  parent alone, with the expansion spent either way. Stated as that reason rather
  than as a ban on changing the approach: which approach wins is the search's
  question, not the prompt's. See ``_HOW_TO_CHANGE``.
* **The reply format is one fenced block whose docstring opens with the change.**
  ``extract_program`` takes the longest fenced block and reads the first
  docstring line as the change summary; that summary is what the user reads in
  the search graph, and without it every node is labelled with nothing.
"""

from __future__ import annotations

import string
from pathlib import PurePosixPath
from typing import Any, Optional, Sequence

from .program import (
    DEFAULT_ENTRYPOINT,
    available_imports_text,
    fence_language,
    files_of,
    is_python,
)

#: The closing instruction every code-shaped template ends on.
#:
#: One block rather than a line per template, because the three drifted: the
#: measured template said "make one substantive change" and the other two said
#: only "output a complete runnable program", which reads as an invitation to
#: write one from scratch. A live
#: compression run showed what that costs — a working RLE+Huffman seed of 6571
#: characters, and eleven candidates that every one of them *replaced the whole
#: mechanism* ("replaced the RLE+Huffman scheme", "restructured as LZ77
#: dictionary matching"), reaching
#: 15824 characters and implementing arithmetic coding or LZ77 from nothing in a
#: single reply. Ten of the eleven did not run. The tree stayed flat at depth 1
#: because no candidate ever beat the seed.
#:
#: The asymmetry is the part the model was never told: a candidate that does not
#: run scores zero, which is *worse than leaving the parent alone*, and the
#: expansion is spent either way. Upstream gets away without saying this
#: because its task is a twenty-line sklearn pipeline, where a rewrite is cheap
#: and rarely broken; a codec is not.
_HOW_TO_CHANGE = """## How to change it

**The only goal is to raise the score.** Whether to swap the approach, and which
algorithm to use, follow from that one thing. No approach is right or wrong in
itself.

There is one thing to keep in mind: **a program that does not run scores 0,
which is worse than the one you were given, and the attempt is spent either
way.** So work from the current version and leave what still works as it is —
not because swapping the approach is forbidden, but because rewriting the whole
thing from nothing in a single reply usually does not run, and that scores
nothing. If a part genuinely has to be replaced, replace that one part and let
the code around it keep running unchanged.

"Leave it as it is" has one exception: **{summary_place} must be rewritten
every time**, saying in one sentence what changed. Copy the previous version's
first line and every node in the search tree ends up with the same name, and
nobody reading the graph can tell them apart.

Work out which part of the current version is weakest against the scoring, then
change that part."""


def _summary_place(entrypoint: str) -> str:
    """Where the sentence about this edit goes, in this program's language.

    Python has a module docstring and the search reads it back from there. A
    program in any other language has no such thing, so the sentence is asked
    for as the first line's comment — every language has one — and
    `program.leading_comment_block` reads that. The two have to agree: a prompt
    asking for a docstring and a reader looking for a comment leaves every node
    in the tree labelled "changed: solve.rs".
    """
    if is_python(entrypoint):
        return "the first line of the module docstring of a file you changed"
    return "the first comment line of a file you changed"


def _summary_rule(entrypoint: str) -> str:
    """The requirement line naming where the change summary goes."""
    place = _summary_place(entrypoint)
    return f"{place[0].upper()}{place[1:]} says in one sentence what changed."


def _environment(entrypoint: str) -> str:
    """What the runtime offers, for the one language this side can ask.

    `available_imports` probes *this* interpreter's packages, which says
    nothing whatsoever about what a Rust or JavaScript candidate may use.
    Shown to one, it is not merely noise: it is a list of things the program
    cannot have, presented as the things it may have.
    """
    if not is_python(entrypoint):
        return ""
    return f"## What this environment has\n\nYou may import only: {available_imports_text()}.\n\n"


#: The placeholder vocabulary a task's own template may draw on, per prompt.
#: `${name}` syntax (`string.Template`), not `str.format`: a task's template
#: text is full of code, and code is full of braces.
MUTATION_SLOTS = frozenset({
    "statement", "contract", "parent_code", "parent_score", "best_score",
    "feedback", "history", "imports", "how_to_change", "reply_format",
    # Facts about the program's language rather than about the search. Each is
    # empty or reworded for a program that is not Python: `imports` lists this
    # interpreter's packages, `summary_rule` names where the change summary is
    # read back from, and `environment` is the whole section `imports` sits in
    # so that a template can drop it as a unit.
    "entrypoint", "environment", "summary_rule",
})

#: Slots a task's template cannot leave out. `reply_format` states the output
#: protocol, and it is the one section whose absence is not a worse prompt but
#: a broken run: the reader on the other side expects that shape, and a reply
#: in any other becomes a candidate that cannot compile — silently, for the
#: whole budget. Refused at load, where it is still a sentence.
MUTATION_REQUIRED = frozenset({"reply_format"})
REPAIR_SLOTS = frozenset({"code", "error", "imports"})
PRIOR_SLOTS = frozenset({"prompt"})


def validate_template(name: str, template: str, allowed: frozenset,
                      required: frozenset = frozenset()) -> None:
    """Refuse an unknown placeholder at load time, by name.

    `safe_substitute` would leave a typo like `${statment}` in the prompt as
    literal text, and the model would faithfully optimise against a prompt with
    a hole in it — for the whole run, silently. A template is data the task
    author wrote; the load is the one moment a mistake in it can still be a
    sentence instead of a wasted budget.
    """
    used = set(string.Template(template).get_identifiers())
    absent = sorted(required - used)
    if absent:
        raise ValueError(
            f"prompt template {name!r} leaves out "
            f"{', '.join('${' + a + '}' for a in absent)}, which states how the model must "
            "reply; without it the reply cannot be read back and every candidate fails to "
            "compile, for the whole budget"
        )
    unknown = sorted(used - allowed)
    if unknown:
        raise ValueError(
            f"prompt template {name!r} uses unknown placeholder(s) "
            f"{', '.join('${' + u + '}' for u in unknown)}; available: "
            f"{', '.join('${' + a + '}' for a in sorted(allowed))}"
        )


def _render(template: str, slots: dict) -> str:
    return string.Template(template).safe_substitute(slots)


def _default_format():
    """The `files` protocol, for callers that did not name one."""
    from .reply_format import format_for

    return format_for(None)


def mutation_prompt(
    *,
    statement: str,
    parent_code: str,
    parent_score: Optional[float],
    entrypoint: str = DEFAULT_ENTRYPOINT,
    best_score: Optional[float],
    recent: Sequence[str] = (),
    script_contract: str = "",
    reply_format: Any = None,
    feedback: str = "",
    template: str = "",
) -> str:
    """Build the prompt for one expansion.

    ``template`` is the task's own wording (`run_dir/prompts/mutation.md`),
    rendered over the same slots the built-in uses — different tasks need
    differently assembled prompts, and the words are the task's to choose.
    The **slots** stay the framework's: what a parent's score is, how the
    program is rendered, what the environment offers. A task changes the
    prose around the facts, not the facts.
    """
    _shape = reply_format if reply_format is not None else _default_format()
    if script_contract:
        # A scripted search's contract is whatever its evaluator calls, and the
        # evaluator is the only place that knows.
        slots = dict(
            statement=statement.strip() or "Make the evaluator report a higher score.",
            contract=script_contract.strip(),
            # Rendered by the protocol that will read the answer: the
            # instructions say "like the listing above", so showing one shape
            # and asking for another is two instructions in conflict.
            parent_code=_shape.render(parent_code, entrypoint),
            parent_score=_score(parent_score),
            best_score=_score(best_score),
            feedback=_feedback(feedback),
            history=_history(recent),
            entrypoint=entrypoint,
            imports=available_imports_text() if is_python(entrypoint) else "",
            environment=_environment(entrypoint),
            summary_rule=_summary_rule(entrypoint),
            how_to_change=_HOW_TO_CHANGE.format(summary_place=_summary_place(entrypoint)),
            # The protocol's own sentences, so the prompt and the reader on the
            # other side are never two independent statements of one thing.
            reply_format=_shape.instructions(entrypoint),
        )
        if template.strip():
            return _render(template, slots)
        return _SCRIPT_TEMPLATE.format(**slots)
    # No third template to fall through to, and that is deliberate. The one that
    # used to be here described the staged-dataset mode, and anything reaching it
    # by accident was told to define `train_and_predict(train_path, test_path)`.
    # A Gaussian-integral run did exactly that: candidates bolted on CSV readers
    # and LightGBM regressors "to match the scoring requirements", the integral
    # never changed, and all nine scores came out identical to ten decimal
    # places — a search that finished "succeeded" having learned nothing. Both
    # callers pass one of the two above; failing here says so.
    raise ValueError(
        "a mutation prompt needs the evaluator's contract: it is the only statement of "
        "what a candidate must define, and a candidate aimed at nothing is aimed at nothing"
    )


_SCRIPT_TEMPLATE = """You are rewriting a program that is scored by a **fixed evaluator script**.

## Goal

{statement}

## What the evaluator requires of a candidate

{contract}

The evaluator loads `{entrypoint}` and calls it through the interface above.
**An interface that does not match scores zero.** Write exactly the names the
contract above asks for — inventing a different one, or adding an entry point
it never mentioned, is not this run's contract.

## Current program

Its score: {parent_score}. Best so far: {best_score}.
{feedback}
{parent_code}

{environment}{history}## Requirements

1. Write to the interface the evaluator requires — every function name and
   argument exactly as it expects them.
2. {reply_format}
3. {summary_rule}

{how_to_change}
"""

def _feedback(text: str) -> str:
    """The evaluator's own diagnosis of the parent, as a prompt section.

    This existed all along — the failing test names, the "3 of 6 cases blew the
    evaluation budget"
    — stored on the node and shown in the UI, and never put in front of the one
    reader who could act on it. A real ODE run showed the cost: six candidates
    scored exactly 0, each a reasonable adaptive method that burst the eval
    budget, and the reflector, told only "score 0", kept trying new variants of
    the same overspend because nothing said *why* the last one died.
    """
    if not text.strip():
        return ""
    return f"\nWhat the evaluator said about it: {text.strip()[:500]}\n"


def _history(recent: Sequence[str]) -> str:
    if not recent:
        return ""
    # What was already tried, so the search does not spend three expansions
    # rediscovering the same idea.
    lines = "\n".join(f"- {item}" for item in recent if item)
    return f"## Changes already tried in this search\n\n{lines}\n\n" if lines else ""


def _score(value: Optional[float]) -> str:
    return "not measured yet" if value is None else f"{value:.4f}"


#: Appended to the mutation prompt when a run asks for a model prior.
#:
#: Transcribed from upstream's `PROMISE_REQUEST` (examples/era/_era_algotune.py),
#: with "faster than the reference" generalised to "better on the scoring" —
#: this port's tasks are not all speed.
#:
#: **The question is deliberately about the approach after tuning**, not about
#: this draft. Asked the other way the rating collapses into the score the
#: evaluator already produces, and the whole point of a prior here is to
#: separate "weak today, right idea" from "fine today, finished". Upstream
#: measured it on ``polynomial_real`` over 30 draws: an approach that left the
#: reference's framing rated 7.07 on average against 4.09 for one that stayed
#: inside it, and the two numba draws rated 8.00. Ratings arrived on 25 of 30.
#:
#: It rides the reply the search was already paying for, so the prior costs no
#: extra call.
PROMISE_REQUEST = """

After the code block, on its own final line, write exactly:

PROMISE: <n>

where <n> is 1 to 10: how much better than the current version you expect this
*approach* to become after further work — not how good this first version is.
1 means the approach is a dead end even if polished. 10 means it should reach
the best score this scoring can express once tuned."""


def with_promise_request(prompt: str, template: str = "") -> str:
    """The mutation prompt plus the rating request, for a run that asked for one.

    A task's template (`run_dir/prompts/prior.md`) replaces the whole assembly
    and receives the finished mutation prompt as ``${prompt}`` — full control,
    because the request's phrasing and its placement are one design decision.
    """
    if template.strip():
        return _render(template, dict(prompt=prompt))
    return prompt.rstrip("\n") + PROMISE_REQUEST + "\n"


def _block(path: str, body: str) -> str:
    """One file as the labelled block the reply is asked to copy."""
    return f"```{fence_language(path)} name={path}\n{body.strip()}\n```"


def render_tree(code: str, entrypoint: str = DEFAULT_ENTRYPOINT) -> str:
    """Every file in the program, each in its own labelled block.

    The same shape the reply is asked for, so the model has a worked example of
    the format in front of it rather than a description of one. Entrypoint
    first, then alphabetical: the file the evaluator imports is the one the
    reader needs to see before any of the others make sense.
    """
    files = files_of(code, entrypoint)
    order = sorted(files, key=lambda path: (path != entrypoint, path))
    if len(order) == 1:
        # Labelled even when there is only one. The unlabelled version read as
        # the simpler thing to show, and the output instructions say to answer
        # "exactly like the listing above" — so with no path in the listing the
        # model had nothing to copy and named the file after its own function.
        # Measured: eight expansions in a row wrote `search.py` beside an
        # untouched `candidate.py`, every one merged clean, scored exactly the
        # parent, and was recorded as a valid candidate that did not improve.
        return _block(order[0], files[order[0]])
    blocks = [_block(path, files[path]) for path in order]
    listing = ", ".join(order)
    return f"The program is {len(order)} files: {listing}.\n\n" + "\n\n".join(blocks)


def repair_prompt(code: str, error: str,
                  entrypoint: str = DEFAULT_ENTRYPOINT,
                  template: str = "") -> str:
    """Ask for the one bug this candidate has, not for a different candidate.

    A candidate that failed usually failed for something visible in its own
    traceback — an import that raises, an index off by one, a type that is not
    what the line assumed. Discarding it means the next expansion writes the
    whole program again from the parent, and on a live compression run seven of
    ten candidates never ran at all: each a fresh design with a fresh bug.

    Deliberately narrow. It carries this candidate's code and this candidate's
    failure and nothing else — no statement of the goal, no scorecard, no other
    candidate — because a wider prompt invites a redesign, and a redesign is
    what the ordinary expansion already does.
    """
    if template.strip():
        return _render(template, dict(
            code=render_tree(code, entrypoint),
            error=error.strip()[:1500] or "(the evaluator gave no reason)",
            imports=available_imports_text() if is_python(entrypoint) else "",
        ))
    return (
        # Not "it does not run": it may well run. A candidate reaches here
        # whenever it scored nothing at all, and "every case came out wrong" is
        # as common a way to get there as a traceback — telling it the program
        # does not run when the error says the round trip does not match points
        # the repair at the wrong thing.
        "The program below did not pass a single case. Fix only the problem it "
        "reports: do not redesign it, and do not tidy anything else along the "
        "way. Get it running correctly and leave the rest as it is.\n\n"
        # "Do not change the approach" used to be in the sentence above, and it
        # forbade the one repair available: when the error says `cannot import
        # name 'cwt'`, the missing thing *is* the approach. Three candidates in
        # one peak-detection search reached for scipy.signal.cwt/ricker (removed
        # in SciPy 1.15); the repair fired three times and saved none — once for
        # forbidding the fix, and again for not saying what to fix it with,
        # which is what the environment section below is for.
        "If the error says something does not exist — a failed import, a missing "
        "attribute, a function that was removed — then replacing that one thing "
        "with an equivalent the current version actually has **is** the minimal "
        "fix. Leave everything else alone.\n\n"
        "## What it reported\n\n"
        f"{error.strip()[:1500] or '(the evaluator gave no reason)'}\n\n"
        # Only for Python: `available_imports` probes this interpreter, and
        # telling a Go program what this process can import is worse than
        # saying nothing.
        + _environment(entrypoint)
        + "## Current program\n\n"
        f"{render_tree(code, entrypoint)}\n\n"
        + (
            f"Output only the fixed complete program, in a single "
            f"```{fence_language(entrypoint)} block.\n"
            if len(files_of(code, entrypoint)) == 1 else
            "Output only the files you had to change, each as its own fenced block "
            f"labelled with its path (```{fence_language(entrypoint)} "
            f"name=path/to/file{PurePosixPath(entrypoint).suffix}). A file you do "
            "not output is kept as it is.\n"
        )
    )
