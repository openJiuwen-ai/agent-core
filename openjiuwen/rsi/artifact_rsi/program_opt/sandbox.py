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

"""What actually confines a candidate.

Lifted from `examples/era/_era_support.py` (see `__init__.py` for the upstream
commit), with one deliberate change: **the backend is supplied by the caller
rather than probed here.**

Upstream asks `shutil.which("bwrap")` and assumes the answer means it works.
This repository has already paid for the knowledge that it does not: inside a
container, bwrap may need `--disable-userns`, and a fresh procfs mount can be
refused so the profile has to fall back to a bind. That knowledge lives in the
Node-side probe (`packages/sandbox-capability`), which runs once and hands the
result down with the run request. Asking twice, in two languages, is how the two
answers start to disagree.

.. danger:: The AST gate is not the boundary here, and must not be read as one.

   This workload needs pandas, numpy and scikit-learn — a stack that can read
   files and spawn processes — so admitting it admits most of what a gate would
   otherwise stop. What confines a candidate is this file: **Bubblewrap** on
   Linux, **Seatbelt** on macOS, both denying network access and confining
   writes to a scratch directory, with CPU / address-space / file-size / fd /
   process limits applied by the runner inside.

   The Bubblewrap profile binds the root **read-only** rather than a handful of
   directories: a candidate's imports live wherever the interpreter was
   installed, and enumerating those would be a guess that fails differently on
   every host. Reads are therefore open on both platforms; writes and the
   network are not. That is a weaker boundary than a per-directory allowlist and
   it is stated rather than glossed.

   It is checked against the kernel rather than by reading the profile back —
   see `tests/test_puct_sandbox.py`.
"""

from __future__ import annotations

import math
import shutil
import sys
import sysconfig
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

_SEATBELT_PROFILE = """(version 1)
(allow default)
(deny network*)
(deny file-write*)
(allow file-write-data (literal "/dev/null") (literal "/dev/urandom") (literal "/dev/zero"))
(allow file-write* (subpath "{scratch}"))
"""

#: Threads are capped at one because ``RLIMIT_CPU`` counts CPU seconds across
#: all of them: an OpenBLAS that helpfully starts eight threads burns a
#: 60-second budget in eight wall-clock seconds, and the candidate is then
#: killed for being fast. Upstream sets no thread policy and has no CPU limit to
#: protect.
_THREAD_ENV = {
    "OMP_NUM_THREADS": "1",
    "OPENBLAS_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "NUMEXPR_NUM_THREADS": "1",
    "VECLIB_MAXIMUM_THREADS": "1",
    "JOBLIB_MULTIPROCESSING": "0",
    "PYTHONDONTWRITEBYTECODE": "1",
}


class SandboxUnavailable(RuntimeError):
    """Raised instead of running model-written code unconfined."""


@dataclass(frozen=True)
class SandboxCapability:
    """What the control plane's probe found, handed down with the run.

    ``backend`` is ``"bwrap"``, ``"seatbelt"`` or ``None``. The two flags are
    the container-specific corrections this repository learned the hard way and
    that a bare ``which bwrap`` cannot see.
    """

    backend: Optional[str] = None
    bwrap_path: str = "bwrap"
    #: Some container runtimes refuse the user namespace unshare.
    disable_userns: bool = False
    #: ``"proc"`` mounts a fresh procfs; ``"bind"`` is the fallback for hosts
    #: that refuse it.
    proc_mode: str = "proc"

    @property
    def available(self) -> bool:
        return self.backend in ("bwrap", "seatbelt")


def detect_local_capability() -> SandboxCapability:
    """A local fallback for tests and for a sidecar started without a probe.

    Deliberately not used on the run path: the control plane's probe is the
    authority, because it knows about the container corrections.
    """
    if shutil.which("bwrap"):
        return SandboxCapability(backend="bwrap", bwrap_path=shutil.which("bwrap") or "bwrap")
    if sys.platform == "darwin" and Path("/usr/bin/sandbox-exec").exists():
        return SandboxCapability(backend="seatbelt")
    return SandboxCapability()


def _python_roots() -> List[str]:
    """Directories a candidate's ``import sklearn`` has to be able to read.

    The interpreter here is this process's own — a virtualenv, a conda prefix, a
    Homebrew cellar — and its packages live wherever the installer put them.
    """
    roots = {sys.prefix, sys.base_prefix, str(Path(sys.executable).resolve().parent)}
    for key in ("purelib", "platlib", "stdlib", "platstdlib"):
        path = sysconfig.get_paths().get(key)
        if path:
            roots.add(path)
    return sorted(root for root in roots if root and Path(root).exists())


def sandbox_command(
    scratch: Path,
    inner: Sequence[str],
    *,
    capability: SandboxCapability,
    timeout: float,
    extra_env: Optional[Mapping[str, str]] = None,
) -> Tuple[List[str], Dict[str, str]]:
    """Wrap ``inner`` (a python argv tail) in this platform's isolation.

    Raises :class:`SandboxUnavailable` when there is no backend. That refusal is
    the point: a candidate is model-written Python that gets executed, and
    running it unconfined because the sandbox is missing would be the wrong way
    to make anything portable. Upstream ships ``Sandbox.run`` as a
    ``NotImplementedError`` reading "Must provide a sandbox for executing
    untrusted code" — this is that.
    """
    if not capability.available:
        raise SandboxUnavailable(
            "no candidate isolation available: install Bubblewrap (bwrap) on Linux, "
            "or run on macOS where sandbox-exec ships with the system"
        )

    scratch = scratch.resolve()
    env = dict(_THREAD_ENV)
    env["TMPDIR"] = str(scratch)
    env["PATH"] = "/usr/bin:/bin"
    # Inside the scratch bind, because it is the one writable place: numpy,
    # matplotlib and joblib all want a home to put a cache in, and the host's
    # is neither writable here nor any of the candidate's business.
    env["HOME"] = str(scratch)
    # Folded in *before* the per-backend plumbing, because the two backends
    # deliver the environment through different holes and only one of them
    # forgives the caller. Seatbelt inherits the subprocess env, so extras
    # merged into `subprocess.run(env=...)` arrive; bubblewrap runs with
    # `--clearenv` and re-adds exactly what is `--setenv`-ed — extras passed
    # any other way are silently absent. That asymmetry shipped: on Linux the
    # scripted evaluator saw no SCIENCE_AGENT_SHARDS and no result path, so it
    # graded every shard identically (the held-out gate was a re-read of the
    # rollout) and only survived at all via the stdout fallback. Tested on
    # macOS, where none of that reproduces.
    if extra_env:
        env.update(extra_env)

    if capability.backend == "bwrap":
        command = [
            _resolve_backend(capability.bwrap_path),
            # A read-only root rather than a handful of binds: the candidate's
            # imports are spread across the interpreter prefix and site-packages,
            # and enumerating those would be a guess that fails differently on
            # every host. Writes are still denied everywhere but the scratch
            # bind, and the network is still unshared.
            "--ro-bind", "/", "/",
        ]
        command.extend(("--proc", "/proc") if capability.proc_mode == "proc"
                       else ("--bind", "/proc", "/proc"))
        command.extend([
            "--dev", "/dev",
            "--tmpfs", "/tmp",
            "--bind", str(scratch), str(scratch),
            "--unshare-net",
            "--die-with-parent",
            "--clearenv",
        ])
        if capability.disable_userns:
            # The two travel together: bubblewrap refuses `--disable-userns`
            # without `--unshare-user`, because "the sandbox may not create
            # further user namespaces" is only enforceable from inside one. The
            # control plane's probe pairs them too — that pairing is what it
            # verified works on this host, and sending half of it here means
            # running a profile nobody probed.
            command.extend(("--unshare-user", "--disable-userns"))
        for name, value in env.items():
            command.extend(("--setenv", name, value))
        for root in _python_roots():
            command.extend(("--ro-bind-try", root, root))
        command.extend((sys.executable, "-I", *inner))
        return command, env

    profile = scratch / "sandbox.sb"
    # `.resolve()` above matters: `tempfile` hands back `/var/folders/...`,
    # `/var` is a symlink to `/private/var`, and Seatbelt matches the resolved
    # path.
    profile.write_text(_SEATBELT_PROFILE.format(scratch=str(scratch)), encoding="utf-8")
    command = ["/usr/bin/sandbox-exec", "-f", str(profile), sys.executable, "-I", *inner]
    # `env` alone, never merged over `os.environ`. Seatbelt inherits whatever
    # the subprocess is given, and bubblewrap's `--clearenv` gives it exactly
    # this dict -- so merging the host environment in here is what made the two
    # backends disagree about what a candidate can read. It mattered once this
    # engine moved in-process: as a sidecar the surrounding environment held no
    # provider key, and in a host that runs the model it holds every one of
    # them. The profile denies the network, so the leak was a candidate reading
    # a key and writing it into its own output -- which the search records as
    # the node's reason and shows the user.
    return command, env


def _resolve_backend(path: str) -> str:
    """The sandbox binary, resolved against *this process's* PATH.

    Not the candidate's. `sandbox_command` hands the child a minimal
    ``PATH=/usr/bin:/bin`` to constrain what a candidate can exec — and
    ``subprocess.run(env=...)`` resolves the executable against that same PATH,
    so a bare ``bwrap`` would be looked up there too. On a host with two
    bubblewraps (a new one in ``/usr/local/bin``, the distro's in ``/usr/bin``)
    the control plane probes one and the candidate is confined by the other.
    That was not hypothetical: the older binary has no ``--clearenv``, and the
    run failed reporting that the *baseline program* would not run.

    Using the candidate's PATH to find the sandbox is a category error. This
    resolves it before the environment is narrowed, and leaves an absolute path
    alone.
    """
    if "/" in path:
        return path
    return shutil.which(path) or path


def cpu_seconds_for(timeout: float) -> int:
    """The runner's ``--cpu-seconds``. At least 2: a one-second budget is spent
    before an interpreter has finished importing pandas."""
    return max(2, int(math.ceil(timeout)))
