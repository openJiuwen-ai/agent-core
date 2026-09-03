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

"""Putting the packages a search needs into the runtime its candidates use.

The person who types "bring the error down" has no way to know the search wants a
gradient-boosting library, and no reason to. The drafting agent does know, so it
says, and this is the half that acts on it.

**Only what is missing, and only into the candidate runtime.** An import probe
first, inside the execution environment: the common case is that everything
asked for is already importable and pip never runs.

.. warning:: The runtime is whatever the provider injected. Against a gateway
   sandbox that is a container. Against the default ``LOCAL`` execution it is
   **this machine's interpreter**, so a card that names packages installs them
   on the host. That followed from removing the sandbox and is not a decision
   this module can make on its own.

**A name is a name.** Anything that could redirect where the package comes from
— an index URL, an editable path, a VCS reference, an environment marker — is
refused rather than passed through, because the whole value of the list is that
a reader can see what it says. `lightgbm` is legible; `-i http://…/simple` is a
different supply chain wearing the same field.

**Failure is a refusal, not a warning.** A run whose candidates were promised
`xgboost` and did not get it fails every expansion with `ModuleNotFoundError`
and reads as a model that cannot write code — the same failure the engine's
pre-flight probe of `CANDIDATE_RUNTIME` exists to prevent at the other end.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, List, Optional, Sequence, Tuple

from .logging_config import get_logger

log = get_logger("provision")

if TYPE_CHECKING:
    from .execution import EvaluationExecution

#: A bare distribution name, optionally with a version pin. Deliberately narrow:
#: everything pip would read as an option, a path or a URL falls outside it.
_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{0,63}(==[A-Za-z0-9][A-Za-z0-9.*+!-]{0,31})?$")

#: How long one install may take before it is abandoned. A wheel for a boosting
#: library is tens of megabytes; a source build is not something to wait out
#: while a user watches a wizard.
TIMEOUT_SECONDS = 600.0

#: The import name for distributions that do not share one with their package.
_IMPORT_NAME = {
    "scikit-learn": "sklearn",
    "opencv-python": "cv2",
    "pillow": "PIL",
    "pyyaml": "yaml",
}


class ProvisionError(RuntimeError):
    """The runtime could not be made to match what the run was promised."""


def import_name(package: str) -> str:
    """What `import` would spell this distribution."""
    base = package.split("==")[0].strip().lower()
    return _IMPORT_NAME.get(base, base.replace("-", "_"))


def validate_names(packages: Sequence[str]) -> List[str]:
    """The stripped entries, after refusing anything that is not a bare name.

    Split out of `ensure` so a caller can compose an import probe from these
    names without first paying for an install attempt — splicing an
    *unvalidated* entry into ``python -c "import …"`` would let a pip option
    ride into a command line.
    """
    wanted = [package.strip() for package in packages if package.strip()]
    for package in wanted:
        if not _NAME.match(package):
            raise ProvisionError(
                f"{package!r} is not a package name. Only names are accepted here "
                "(optionally with ==version); not paths, URLs, index addresses or pip options"
            )
    return wanted


def probe_imports(names: Sequence[str], execute: "EvaluationExecution") -> Optional[str]:
    """``None`` when every module imports inside the execution environment,
    else the probe's output tail. Only vetted names go in — they are spliced
    into a command line."""
    joined = ", ".join(names)
    outcome = execute({}, ["python", "-c", f"import {joined}"], {}, 120.0, None)
    if outcome.exit_code == 0:
        return None
    return (outcome.output or "").strip()[-200:] or "(no output)"


def ensure(packages: Sequence[str], execute: "EvaluationExecution") -> Tuple[List[str], str]:
    """Install `packages` into the run's execution environment.

    Through the injected execution, because that is where candidates actually
    run — see the module's warning about what that environment is.

    An import probe first, and pip only when it fails: pip resolves for
    seconds even when every requirement is already satisfied, and `ensure` is
    reached more than once per run. A ``==version`` pin is treated as
    satisfied by importability alone, exactly as the old host-side
    `find_spec` fast path treated it.

    Raises :class:`ProvisionError` when a name is not a name, or when pip
    refuses: both are things the user has to see before a run starts, and both
    are invisible afterwards — the run would just report that every candidate
    failed to import something.
    """
    wanted = validate_names(packages)
    if not wanted:
        return [], ""
    names = sorted({import_name(package) for package in wanted})

    try:
        if probe_imports(names, execute) is None:
            return [], f"already importable: {', '.join(names)}"
        command = ["python", "-m", "pip", "install", "--no-input",
                   "--disable-pip-version-check", *wanted]
        log.info("installing %s inside the execution environment", ", ".join(wanted))
        outcome = execute({}, command, {}, float(TIMEOUT_SECONDS), None)
    except Exception as error:  # noqa: BLE001 - the seam's failure is run-level
        raise ProvisionError(
            f"installing {', '.join(wanted)} could not be run: {error}") from error
    if outcome.exit_code != 0:
        tail = (outcome.output or "").strip()[-400:]
        raise ProvisionError(f"installing {', '.join(wanted)} failed: {tail or '(no output)'}")

    # Verified where it matters: the same probe, after the install.
    failure = probe_imports(names, execute)
    if failure is not None:
        raise ProvisionError(
            f"pip reported success and yet importing {', '.join(names)} still fails: {failure}"
        )
    return wanted, f"installed {', '.join(names)}"


#: Allowlisted modules a candidate may import; probed at run start through the
#: injected execution, because that is the environment candidates run in.
CANDIDATE_RUNTIME = ("numpy", "pandas", "scipy", "sklearn")
