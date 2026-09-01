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

# Copied from agentdescent @ b3d4240, examples/era/_era_runner.py.
# Everything below this line is verbatim (sha256:e1ddfe6df2fd0d44...); only this header
# was added.
#
# DO NOT REWRITE. It encodes one non-obvious thing: RLIMIT_CPU counts CPU
# seconds *across threads*, so an OpenBLAS that helpfully starts eight would
# burn a 60-second budget in eight wall-clock seconds and the candidate would be
# killed for being fast. That is why the thread environment is pinned. A rewrite
# fails in a way that looks like poor candidate quality, far from the edit.

"""Sandbox-side runner for the candidate programs.

Executed inside Bubblewrap or Seatbelt. Standard library only, and it emits
exactly one JSON object on stdout -- the candidate's own prints are redirected
into a buffer so a chatty program cannot corrupt that object.

The candidate here *imports third-party
scientific packages*: pandas, numpy and scikit-learn are the whole point of the
benchmark. So this runner does not pretend the AST gate is the boundary. It
raises the address-space limit to something a fitted model actually needs, and
the isolation that matters -- no network, no writes outside the scratch
directory -- comes from the sandbox profile rather than from the gate.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import sys
import json
import resource
import time
from typing import List


#: 2 GiB. Upstream's runner caps at 512 MiB, which is right for a candidate
#: importing nothing but `math` and wrong here: loading ~30k rows into pandas
#: and fitting a 50-tree forest crosses that on its own, and the failure would
#: read as "the model wrote a bad program" rather than "the limit was too low".
ADDRESS_SPACE_BYTES = 2 * 1024 * 1024 * 1024


def set_resource_limits(cpu_seconds: int, nproc_limit: int) -> List[str]:
    """Apply candidate limits inside the sandbox, before importing its code.

    Returns the names of the limits this platform refused, so a caller can say
    which guarantee it does not have rather than assume it has all of them.
    Darwin has no `RLIMIT_AS`, and refusing to run there because one of five
    limits is unavailable would be the wrong trade: the CPU-seconds limit is
    what stops a runaway candidate, and that one applies on both platforms.
    """
    unavailable: List[str] = []
    for name, value in (
        ("RLIMIT_CPU", (cpu_seconds, cpu_seconds + 1)),
        ("RLIMIT_AS", (ADDRESS_SPACE_BYTES, ADDRESS_SPACE_BYTES)),
        ("RLIMIT_FSIZE", (16 * 1024 * 1024, 16 * 1024 * 1024)),
        ("RLIMIT_NOFILE", (256, 256)),
        ("RLIMIT_NPROC", (nproc_limit, nproc_limit)),
    ):
        limit = getattr(resource, name, None)
        if limit is None:
            unavailable.append(name)
            continue
        try:
            resource.setrlimit(limit, value)
        except (ValueError, OSError):
            unavailable.append(name)
    return unavailable


def load_entrypoint(path: str):
    spec = importlib.util.spec_from_file_location("candidate", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load candidate module")
    module = importlib.util.module_from_spec(spec)
    # Registered before execution: `dataclasses` is on the allowlist, and
    # `@dataclass` resolves its own module through `sys.modules[cls.__module__]`.
    # Without this line a candidate that declares one dies with "'NoneType'
    # object has no attribute '__dict__'" -- which the evaluator scores as the
    # model having written a broken program, so the search learns the wrong
    # lesson from an error that was never the candidate's.
    sys.modules["candidate"] = module
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        spec.loader.exec_module(module)
    entrypoint = getattr(module, "train_and_predict", None)
    if not callable(entrypoint):
        raise RuntimeError("candidate must define callable train_and_predict")
    return entrypoint


def as_predictions(result, expected: int) -> List[float]:
    """Coerce whatever the candidate returned into a flat list of floats.

    Upstream accepts "a numpy array or list" and then calls
    `np.array(result)`; this does the same coercion without importing numpy in
    the runner, and rejects the shapes upstream would have silently scored --
    a nested array, or a length that does not match the split.
    """
    try:
        values = list(result)
    except TypeError as exc:
        raise TypeError(f"predictions are not iterable: {type(result).__name__}") from exc
    if len(values) != expected:
        raise ValueError(f"expected {expected} predictions, got {len(values)}")
    out: List[float] = []
    for value in values:
        if isinstance(value, (list, tuple)) or hasattr(value, "__len__"):
            raise TypeError("predictions must be scalar, not nested")
        out.append(float(value))
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("candidate")
    parser.add_argument("--train", required=True)
    parser.add_argument("--test", required=True)
    parser.add_argument("--rows", type=int, required=True)
    parser.add_argument("--cpu-seconds", type=int, required=True)
    parser.add_argument("--nproc-limit", type=int, required=True)
    args = parser.parse_args()

    started = time.monotonic()
    try:
        unavailable = set_resource_limits(args.cpu_seconds, args.nproc_limit)
        entrypoint = load_entrypoint(args.candidate)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = entrypoint(args.train, args.test)
        payload = {
            "ok": True,
            "predictions": as_predictions(result, args.rows),
            "seconds": time.monotonic() - started,
            "limits_unavailable": unavailable,
        }
    except BaseException as exc:  # candidate failures become a scored zero
        payload = {
            "ok": False,
            "error": f"{type(exc).__name__}: {str(exc)[:500]}",
            "seconds": time.monotonic() - started,
        }
    print(json.dumps(payload, separators=(",", ":"), allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
