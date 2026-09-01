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

.. danger:: The gate is not the security boundary, and must not be read as one.

   This benchmark needs pandas, numpy and scikit-learn — a stack that can read
   files and spawn processes — so admitting it admits most of what a gate would
   otherwise stop. What the gate still buys is that the ordinary accidents (a
   candidate that shells out, calls ``open``, or reaches for a dunder) fail
   in-process with a readable message instead of inside the sandbox.

   What actually confines a candidate is the sandbox, which lands with the
   commit that executes candidates for real.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import math
import re
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, packages_distributions, version
from typing import Any, Dict, List, Optional, Tuple

#: Wide enough for the benchmark, and no wider. Upstream ships no gate at all --
#: `sandbox.py` is an abstract class that raises -- so every name here is this
#: port's decision, and the trade is explicit: admitting scikit-learn admits a
#: package that can spawn processes and read files, which is why the sandbox
#: profile rather than this set is the actual boundary.
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
#: The gate is explicitly *not* the isolation boundary — bubblewrap and Seatbelt
#: are, and this module's own docstring says so. So refusing an import was never
#: a security decision; it was a claim that the import would not work here, and
#: for an installed package that claim is simply false. What is left on this
#: list is the handful that reach outside the process: they die against the
#: sandbox profile anyway, and failing in-process with a readable message is the
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


def _import_allowed(module: str) -> Tuple[bool, str]:
    """Whether a candidate may import this, and why not.

    "Not installed" and "not allowed" are different answers and need different
    fixes — one is a deployment, one is the candidate — so they are told apart
    here rather than merged into one refusal.
    """
    root = module.split(".")[0]
    if root in BLOCKED_IMPORTS:
        return False, f"import {module!r} is not allowed: it reaches outside the sandbox"
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


def validate_source(source: str, max_length: int = 20_000) -> Tuple[bool, str]:
    """Reject what the sandbox should never have to contain in the first place.

    Deliberately *not* as strict as upstream's own gate: that one allows
    six standard-library modules and this one has to allow scikit-learn, so it
    cannot claim to be the isolation boundary. What it does buy is that the
    common accidents -- a candidate that shells out, opens a file by hand, or
    reaches for a dunder -- fail in-process with a readable message instead of
    dying against a sandbox profile.
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

    if not any(
        isinstance(node, ast.FunctionDef) and node.name == "train_and_predict"
        for node in tree.body
    ):
        return False, "missing train_and_predict function"

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                ok, why = _import_allowed(alias.name)
                if not ok:
                    return False, why
        elif isinstance(node, ast.ImportFrom):
            if not node.module:
                return False, "a relative import has nothing to resolve against"
            ok, why = _import_allowed(node.module)
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
    summary = ""
    try:
        parsed = ast.parse(code)
    except SyntaxError:
        return code, summary
    doc = ast.get_docstring(parsed)
    if doc:
        summary = doc.strip().splitlines()[0][:200]
    return code, summary
