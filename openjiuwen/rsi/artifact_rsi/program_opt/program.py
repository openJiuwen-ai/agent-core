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

"""The candidate program: its identity, the AST gate, and the reply parser.

Lifted from `examples/era/_era_support.py` (see `__init__.py` for the upstream
commit). The task-specific half of that module — the S3E1 data preparation and
the RMSE evaluator — is deliberately absent: what a candidate is measured on
comes from the scorecard here, not from a hard-wired benchmark.

.. danger:: The gate is not a security boundary, and must not be read as one.

   A candidate needs pandas, numpy and scikit-learn — a stack that can read
   files and spawn processes — so admitting those admits most of what a gate
   would otherwise stop. What it buys is that the ordinary accidents (a
   candidate that shells out, calls ``open``, or reaches for a dunder) fail
   here, with a readable message, instead of at evaluation.

   What confines a candidate is whatever ``EvaluationExecution`` the provider
   was handed. That may be a gateway sandbox; by default it is a ``LOCAL``
   SysOperation, which keeps paths inside the run directory and refuses a few
   dangerous commands — a boundary worth having, and not isolation.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import math
import re
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, packages_distributions, version
from pathlib import PurePosixPath
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

#: Modules a candidate may never import, whatever is installed.
#:
#: A deny list, because the allow list this replaced was a guess and the guess
#: was wrong in the expensive direction. It named thirteen modules — no
#: `xgboost`, no `lightgbm`, no `catboost`, no `statsmodels` — which are exactly
#: what a model reaches for on a tabular task. Refusing them cost candidates on
#: every real run, and once cost a whole run: a drafted starting point imported
#: `catboost`, the gate refused it, and the probe reported that the starting
#: point would not run.
#:
#: Refusing an import was never a security decision (see the module docstring);
#: it was a claim that the import would not work here, and for an installed
#: package that claim is simply false. What is left is the handful that reach
#: outside the process, where failing early with a readable message is the
#: whole reason this gate exists.
BLOCKED_IMPORTS = {
    "asyncio",
    "ctypes",
    "http",
    "importlib",
    "marshal",
    "multiprocessing",
    "os",
    "pathlib",
    "pickle",
    "requests",
    "shutil",
    "signal",
    "socket",
    "subprocess",
    "sys",
    "tempfile",
    "urllib",
}


def local_roots(files: Iterable[str]) -> frozenset:
    """The top-level names a program's own files make importable.

    `helpers/scale.py` in the tree means `import helpers.scale` resolves inside
    the program. Without this the gate asks `find_spec("helpers")` — "is it
    installed" — and refuses every program made of more than one file for
    importing itself.
    """
    roots = set()
    for path in files:
        if not path.endswith(".py"):
            continue
        head = path.split("/")[0]
        roots.add(head[:-3] if head.endswith(".py") else head)
    return frozenset(roots)


def _import_allowed(module: str, local: Iterable[str] = ()) -> Tuple[bool, str]:
    """Whether a candidate may import this, and why not.

    "Not installed" and "not allowed" are different answers and need different
    fixes — one is a deployment, one is the candidate — so they are told apart
    here rather than merged into one refusal.
    """
    root = module.split(".")[0]
    if root in BLOCKED_IMPORTS:
        return False, f"import {module!r} is not allowed: it reaches outside the process"
    # A sibling module in the program's own tree. Checked before `find_spec`,
    # which asks a different question ("is it installed") and would answer no.
    if root in local:
        return True, ""
    if importlib.util.find_spec(root) is None:
        return False, f"import {module!r} is not installed in the candidate runtime"
    return True, ""


def available_imports() -> List[str]:
    """The packages worth naming in a prompt: installed, and not blocked.

    Probed rather than listed, so the prompt tells the model what this
    deployment actually has instead of what someone wrote down once.
    """
    return sorted(
        name for name in _WORTH_NAMING
        if name not in BLOCKED_IMPORTS and importlib.util.find_spec(name) is not None
    )


def available_imports_text() -> str:
    """The same list, with the version of everything that has one.

    Names alone were not enough, and the gap cost a whole run. `scipy` is
    installed, so the prompt said `scipy` — and three of four candidates
    reached for `scipy.signal.cwt` and `ricker`, which every peak-detection
    tutorial written before 2025 uses and which SciPy removed in 1.15. Two
    crashed, one failed at import. A model that is told `scipy 1.18.0` can
    know that; a model told `scipy` cannot.

    Same rule as the names themselves: probed here, never written down.
    """
    # The import name is not the distribution name — `sklearn` ships as
    # `scikit-learn` — so the mapping is read rather than guessed.
    distributions = packages_distributions()
    parts = []
    for name in available_imports():
        found = None
        for dist in distributions.get(name, [name]):
            try:
                found = version(dist)
                break
            except PackageNotFoundError:
                continue
        parts.append(f"{name} {found}" if found else name)
    return "、".join(parts)


#: Candidates for `available_imports` to probe. Not a permission list — anything
#: installed and unblocked may be imported — just the ones worth spending prompt
#: space on.
_WORTH_NAMING = (
    "catboost", "collections", "dataclasses", "functools", "itertools",
    "lightgbm", "math", "numpy", "pandas", "random", "scipy", "sklearn",
    "statistics", "statsmodels", "typing", "warnings", "xgboost",
)
FORBIDDEN_CALLS = {
    "breakpoint",
    "compile",
    "eval",
    "exec",
    "getattr",
    "globals",
    "help",
    "input",
    "locals",
    "open",
    "setattr",
    "vars",
    "__import__",
}



@dataclass
class Program:
    """One node's payload: the genome plus where it came from and how it did."""

    program_id: str
    iteration: int
    parent_id: Optional[str]
    code: str
    change_summary: str
    metrics: Dict[str, Any]
    valid: bool
    error: str = ""


#: Upstream's `_PROMISE` (examples/era/era_empirical_software.py).
_PROMISE = re.compile(r"PROMISE:\s*([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE)


def read_promise(reply: str) -> Optional[float]:
    """The model's own rating of the direction, or ``None`` if it did not give one.

    Read out of the reply the port was already paying for, so a prior costs no
    extra call. **Absent is not zero**: an unrated node falls back to the mean of
    the rated ones in `FlatPuct._priors`, because a missing number must not be
    the reason a direction is never explored. Upstream measured 25 replies out
    of 30 carrying one.
    """
    match = _PROMISE.search(reply or "")
    if not match:
        return None
    try:
        value = float(match.group(1))
    except ValueError:
        return None
    return value if math.isfinite(value) and value > 0 else None


def program_id(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------
# The AST gate
# --------------------------------------------------------------------------


def validate_source(source: str, max_length: int = 20_000,
                    local: Iterable[str] = ()) -> Tuple[bool, str]:
    """Reject a candidate that was never going to run, before it is run.

    Deliberately *not* as strict as upstream's own gate: that one allows six
    standard-library modules and this one has to allow scikit-learn, so it
    cannot claim to be a boundary — see the module docstring.
    """
    if not source.strip():
        return False, "empty source"
    if len(source) > max_length:
        return False, f"source length {len(source)} exceeds {max_length}"
    if "\x00" in source:
        return False, "source contains a NUL byte"
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return False, f"SyntaxError: {exc.msg} at line {exc.lineno}"

    # Deliberately no required-function check. The gate's one caller is the
    # seed validation, which sees only a path — not the scorecard — so it
    # cannot know what the evaluator will call: `train_and_predict` is one
    # task's contract, `compress`/`decompress` another's. Requiring the first
    # here rejected every legitimate seed of the second kind. Whether the seed
    # is callable is decided where it can be: the evaluator's own import, and
    # the probe that scores the seed before any budget is spent.

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                ok, why = _import_allowed(alias.name, local)
                if not ok:
                    return False, why
        elif isinstance(node, ast.ImportFrom):
            if not node.module:
                return False, "a relative import has nothing to resolve against"
            ok, why = _import_allowed(node.module, local)
            if not ok:
                return False, why
        elif isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            return False, f"dunder attribute {node.attr!r} is not allowed"
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_CALLS:
            return False, f"name {node.id!r} is not allowed"

    allowed_top_level = (
        ast.Expr,
        ast.Import,
        ast.ImportFrom,
        ast.FunctionDef,
        ast.ClassDef,
        ast.Assign,
        ast.AnnAssign,
    )
    for node in tree.body:
        if not isinstance(node, allowed_top_level):
            return False, f"top-level {type(node).__name__} is not allowed"
        if isinstance(node, ast.Expr) and not (
            isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
        ):
            return False, "only a module docstring may be a top-level expression"
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            if not isinstance(value, (ast.Constant, ast.Tuple, ast.List, ast.Dict, ast.Set)):
                return False, "top-level assignments must be literal constants"
    return True, ""


#: Shared with the text path: two regexes for "what is a fenced block" is two
#: answers, and they drift.
FENCE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)

#: The same fence, keeping the info string so a labelled block can say which
#: file it is.
_LABELLED_FENCE = re.compile(r"```([^\n]*)\n(.*?)```", re.DOTALL)


#: The path a labelled fence declares, in the four spellings models actually
#: use: ```python name=a/b.py, ```python:a/b.py, ```a/b.py, and a path on the
#: line above the fence (`### a/b.py`, `**a/b.py**`, `# a/b.py`, or bare).
_FENCE_PATH = re.compile(
    r"^(?:[a-zA-Z0-9_+-]*[:\s]+)?(?:name\s*=\s*)?[\"\']?([\w./-]+\.[\w]+)[\"\']?\s*$"
)
_LINE_PATH = re.compile(r"^[\s>*#`]*[\"\']?([\w./-]+\.[\w]+)[\"\']?[\"\'*`:\s]*$")
#: A file the model wants gone. Deliberately shouty and on its own line: a
#: program that can only ever grow accumulates dead modules the search then
#: pays to carry in every prompt.
_DELETE = re.compile(r"^\s*DELETE(?:D|:)?\s+[\"\'`]?([\w./-]+\.[\w]+)[\"\'`]?\s*$", re.M)

#: Where the evaluator imports the candidate from when nothing says otherwise.
DEFAULT_ENTRYPOINT = "candidate.py"


def files_of(code: str, entrypoint: str = DEFAULT_ENTRYPOINT) -> Dict[str, str]:
    """A genome as `{relpath: text}`.

    The genome travels as one string because that is the only channel the
    engine has -- `Strategy.render` -- and because it is also the evaluation
    cache key, so the serialisation has to be lossless. `filetree.canonical`
    is upstream's answer to exactly that, and JSON is used there rather than a
    delimiter format precisely because a file's *contents* cannot forge it.

    Plain source is still accepted and read as a one-file tree, which is what a
    resumed run written before this change contains.
    """
    from agentdescent.filetree import TreeError, parse_tree

    try:
        return parse_tree(code)
    except TreeError:
        return {entrypoint: code}


def bundle(files: Mapping[str, str]) -> str:
    """`{relpath: text}` as the one string everything downstream carries."""
    from agentdescent.filetree import canonical

    return canonical(dict(files))


def entry_source(code: str, entrypoint: str = DEFAULT_ENTRYPOINT) -> str:
    """Just the entrypoint's text, for the checks that are about one module."""
    return files_of(code, entrypoint).get(entrypoint, "")


def extract_files(
    reply: str,
    parent: Mapping[str, str],
    entrypoint: str = DEFAULT_ENTRYPOINT,
) -> Tuple[Dict[str, str], str]:
    """The model's reply as a new tree, merged onto the parent's.

    **Only the files it returned.** A model asked to restate ten files to change
    one spends the tokens on nine copies and, worse, rewrites the nine — so a
    path that does not appear is inherited unchanged, and the diff the search
    records is the edit rather than the whole program.

    A single unlabelled block is the entrypoint, which is what a one-file run
    looks like and what upstream's own parser assumed.
    """
    files = dict(parent)
    labelled = _labelled_blocks(reply)
    if labelled:
        files.update(labelled)
    else:
        # Upstream's fallback: a reply with no fence at all is taken as the
        # program. It keeps a junk reply on the tree as a node that scores
        # `-inf` rather than dropping it, which is deliberate — dropping shrinks
        # the rank denominator and raises `1/N` for every later iteration.
        # The delete directives come out first, so a reply that only asks for a
        # removal does not also overwrite the entrypoint with its own text.
        code, _ = extract_program(_DELETE.sub("", reply).strip())
        if code:
            files[entrypoint] = code
    for path in _DELETE.findall(reply):
        # Never the entrypoint: without it there is nothing for the evaluator
        # to import, and every later candidate would fail identically.
        if path != entrypoint:
            files.pop(path, None)
    return files, _summary_for(files, parent, entrypoint)


def _summary_for(
    files: Mapping[str, str],
    parent: Mapping[str, str],
    entrypoint: str,
) -> str:
    """One line describing this edit, taken from what the edit touched.

    The docstring of a file that *changed*, entrypoint first. Reading the
    entrypoint's unconditionally is what a one-file search could get away with
    and a multi-file one cannot: a candidate that rewrote a helper would be
    labelled with a docstring it did not touch, or with nothing at all.
    """
    changed = [path for path in files if files[path] != parent.get(path)]
    changed += [path for path in parent if path not in files]
    if not changed:
        return ""
    ordered = sorted(changed, key=lambda path: (path != entrypoint, path))
    for path in ordered:
        summary = _summary_of(files.get(path, ""), path)
        if summary:
            return summary
    return "changed: " + ", ".join(ordered[:5]) + (", …" if len(ordered) > 5 else "")


def edits_an_existing_file(parent: Mapping[str, str], files: Mapping[str, str]) -> bool:
    """Whether the reply changed any file the program already had.

    A reply that only *adds* files has changed nothing that runs: the evaluator
    imports the entrypoint, the entrypoint is untouched, and nothing in the
    parent references the new paths — so the candidate is the parent with dead
    code beside it, and it scores exactly the parent. It merges clean and reads
    as a valid candidate that found no improvement, which is the most expensive
    way to say nothing: a whole budget of expansions can go this way with every
    one of them reported as a success.

    Editing a helper *is* legitimate and stays legitimate — this asks whether
    any existing path's content moved, not whether the entrypoint's did.
    """
    return any(path in parent and files[path] != parent[path] for path in files)


def reply_carries_program(reply: str) -> bool:
    """Whether a reply proposed anything at all.

    Needed because "the model returned nothing" stopped being visible in the
    result once a reply became a *patch*: merging nothing onto the parent yields
    the parent, which is a valid program that costs a full evaluation to learn
    the parent's own score. Upstream turns an empty draw into a node scoring
    `-inf` and moves on -- deliberately a node, because dropping it shrinks the
    rank denominator and raises `1/N` for every later iteration -- and this is
    how that case is still recognised.
    """
    if _labelled_blocks(reply):
        return True
    if _DELETE.search(reply or ""):
        return True
    code, _ = extract_program(_DELETE.sub("", reply or "").strip())
    return bool(code.strip())


def _labelled_blocks(reply: str) -> Dict[str, str]:
    """Every fenced block that says which file it is."""
    found: Dict[str, str] = {}
    for match in _LABELLED_FENCE.finditer(reply):
        info, body = match.group(1) or "", match.group(2)
        path = _path_from(info) or _path_above(reply, match.start())
        if path:
            found[path] = body.strip()
    return found


def _path_from(info: str) -> str:
    match = _FENCE_PATH.match(info.strip())
    return match.group(1) if match else ""


def _path_above(reply: str, start: int) -> str:
    """The path on the line before the fence, when the fence itself is bare."""
    head = reply[:start].rstrip("\n")
    line = head.rsplit("\n", 1)[-1] if "\n" in head else head
    match = _LINE_PATH.match(line.strip())
    return match.group(1) if match else ""


#: What a fenced block is labelled with, per suffix. Only for the prompt's
#: benefit — the parser reads the `name=` path and ignores the label entirely
#: (`_FENCE_PATH`) — but the listing is the worked example the reply is asked
#: to copy, and a JavaScript program shown as ```python is the prompt telling
#: the model two different things about what it is working on.
_FENCE_LANGUAGES = {
    ".py": "python", ".js": "javascript", ".mjs": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".sh": "bash", ".bash": "bash",
    ".rb": "ruby", ".pl": "perl", ".lua": "lua", ".r": "r", ".jl": "julia",
    ".m": "matlab", ".go": "go", ".rs": "rust", ".java": "java", ".kt": "kotlin",
    ".scala": "scala", ".swift": "swift", ".c": "c", ".h": "c", ".cc": "cpp",
    ".cpp": "cpp", ".cxx": "cpp", ".hpp": "cpp", ".cs": "csharp", ".f": "fortran",
    ".f90": "fortran", ".sql": "sql", ".json": "json", ".yaml": "yaml",
    ".yml": "yaml", ".toml": "toml", ".md": "markdown", ".txt": "text",
    ".html": "html", ".css": "css",
}


def fence_language(path: str) -> str:
    """The markdown label for this file's blocks, or "" for one we do not know.

    An empty label is a valid fence, which is the right answer for an unknown
    suffix: guessing wrong states something false about the program, and
    stating nothing costs the model only the hint.
    """
    return _FENCE_LANGUAGES.get(PurePosixPath(path).suffix.lower(), "")


def is_python(path: str) -> bool:
    """Whether this file is the one language this side can reason about.

    The AST gate, the import list, the candidate-runtime probe and the shim
    are all Python facts, and each of them was once applied to every program
    regardless. One spelling of the question, so they cannot drift apart.
    """
    return PurePosixPath(path).suffix.lower() == ".py"


#: How a one-line comment starts, across the languages a candidate or an
#: evaluator might be written in. Where the docstring's two jobs go when there
#: is no docstring: the sentence naming an edit, and an evaluator's statement
#: of what a candidate must provide.
_COMMENT_MARKERS = ("###", "//", "--", "#", "%", ";;", ";", "!", "/*", "*")


def leading_comment_block(code: str) -> str:
    """The whole comment block a file opens with, markers stripped.

    The Python side reads a module docstring for two things: the sentence
    naming an edit, and the evaluator's statement of what a candidate must
    provide. A file in another language has neither, and the leading comment is
    where both are written by convention in every language that has one.
    """
    lines: list[str] = []
    for raw in code.splitlines():
        line = raw.strip()
        if line.startswith("#!"):
            continue
        if not line:
            if lines:
                break  # a blank line ends the header; the code below is not it
            continue
        text = _uncomment(line)
        if text is None:
            break
        lines.append(text)
    return "\n".join(lines).strip()


def _uncomment(line: str) -> Optional[str]:
    """One comment line without its marker, or None if it is not a comment."""
    for marker in _COMMENT_MARKERS:
        if line.startswith(marker):
            return line[len(marker):].strip(" \t*/-=#").strip()
    return None


def _summary_of(code: str, path: str = "") -> str:
    """The one line the reply says about its own edit.

    The module docstring is where the prompt asks for it, and where most
    replies put it. A reply whose whole program is one function usually
    documents *the function* instead — a reasonable thing to do, and a run
    measured here lost the sentence on six of eight expansions that way,
    leaving the contract's `changes[].summary` as "changed: candidate.py".
    So the first definition's docstring is read as a fallback: it is the same
    sentence, one indentation level down.

    A program in another language has neither, and is asked for the sentence as
    its first comment instead. Decided by the path rather than by whether the
    text happens to compile: a shell script whose first two lines are comments
    and whose third is ``x=1`` parses as Python perfectly well, and would then
    be searched for a docstring it can never have.
    """
    if not path or is_python(path):
        try:
            parsed: Optional[ast.Module] = ast.parse(code)
        except SyntaxError:
            # Python that does not compile has no docstring to find either, so
            # its first comment is as good a sentence as anything.
            parsed = None
        if parsed is not None:
            doc = ast.get_docstring(parsed)
            for node in parsed.body if not doc else ():
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    doc = ast.get_docstring(node)
                    if doc:
                        break
            return doc.strip().splitlines()[0][:200] if doc else ""
    header = leading_comment_block(code).splitlines()
    return header[0][:200] if header else ""


def extract_program(reply: str) -> Tuple[str, str]:
    """Upstream's markdown stripping, plus the docstring as a change summary.

    `GeminiLLM.draw_sample` removes ```python fences with three regexes and
    returns whatever is left. This takes the *longest* fenced block when there
    are several -- a model that explains itself in a second snippet should not
    have the explanation compiled -- and falls back to upstream's behaviour of
    treating the whole reply as code when there is no fence at all.
    """
    blocks = FENCE.findall(reply)
    code = max(blocks, key=len).strip() if blocks else reply.strip()
    # A reply whose fence never closed — truncation, or a model that stopped at
    # the code — falls through to "whole reply as code" with the opening fence
    # still on line 1, and ```python is a SyntaxError at import. Watched live.
    # Strip a stray opening fence (and a stray trailing one) so the fallback
    # degrades to the code instead of to a candidate that cannot parse.
    if not blocks and code.startswith("```"):
        code = code.split("\n", 1)[1] if "\n" in code else ""
        if code.rstrip().endswith("```"):
            code = code.rstrip()[: -3].rstrip()
        code = code.strip()
    return code, _summary_of(code)
