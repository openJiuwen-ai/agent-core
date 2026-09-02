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

"""The seam every search engine plugs into, and one rule about how to call it.

An engine is handed a :class:`RunSpec`, an ``emit`` callback and a
``should_stop`` predicate, and is expected to emit the event sequence described
in ``events.py``. Two implementations: ``stub_engine`` (deterministic, executes
nothing) and ``puct_engine``; a second search algorithm would land behind the
later.

.. danger:: Parallel expansion uses AgentDescent's ``ThreadExecutor``.

   Its actors are supplied through ``attach_actors`` rather than through the
   spec's ``Ref``s, because a closure has no name to resolve. **Only an
   in-process executor accepts a directly-supplied callable.** This is written
   here rather than discovered later because the obvious "optimisation" — swap
   the thread pool for processes to get around the GIL — turns every rollout
   into an unresolvable-reference crash, and the failure surfaces far from the
   edit that caused it.

   Threads are the right tool regardless: the two slow things are a blocking
   HTTP call to a model and ``subprocess.run`` for a sandboxed candidate. Both
   release the GIL, and neither has an asyncio-native path in this stack.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from .events import Emit


@dataclass(frozen=True)
class RunSpec:
    """Everything an engine needs for one search.

    Deliberately small: budget, algorithm and the frozen scorecard's hash. The
    goal, the scorecard itself and every artifact address stay on the API side —
    this process holds no business state and no model key.
    """

    search_id: str
    algorithm: str
    expansions: int
    scorecard_hash: str
    #: The frozen scorecard itself. The hash stays the identity; this is what an
    #: engine grades with, because the formulas are authoritative on this side
    #: (the control plane's copy serves the wizard's preview and is pinned to
    #: the same golden fixture).
    scorecard: dict[str, Any] = field(default_factory=dict)
    #: The user's goal in their own words, for the mutation prompt. "Improve the
    #: program" is not an objective a candidate can be aimed at.
    statement: str = ""
    #: The program the search starts from, as a serialised file tree (see
    #: `program.bundle`). Plain source is still read as a one-file tree, which
    #: is what a run written before multi-file support contains. Empty means the
    #: vendored seed.
    baseline_code: str = ""
    #: Which file in that tree the evaluator imports. The evaluator is told the
    #: path through `SCIENCE_AGENT_CANDIDATE`, so a multi-file seed keeps its
    #: own layout instead of being renamed into ours.
    entrypoint: str = "candidate.py"
    #: Wall-clock ceiling for one candidate execution.
    candidate_timeout_seconds: float = 60.0
    #: The evaluator a `custom_script` scorecard scores with, written by the
    #: drafting model. Frozen with the goal: a search that could rewrite what
    #: measures it would learn to do that instead of getting better.
    script: str = ""
    #: Packages the candidates need that this runtime does not have. Worked out
    #: by the drafting agent from the task and installed before anything runs —
    #: the person who typed "bring the error down" has no way to know a boosting library
    #: is wanted, and no reason to.
    packages: tuple[str, ...] = ()
    #: The task's own prompt wording, read from `run_dir/prompts/*.md` when
    #: present and empty otherwise — different tasks need differently
    #: assembled prompts. Rendered over the framework's slot vocabulary
    #: (`prompt.MUTATION_SLOTS` etc.); an unknown placeholder is refused at
    #: load, not discovered as a hole in the prompt mid-run.
    mutation_template: str = ""
    repair_template: str = ""
    prior_template: str = ""
    #: Ceiling for one mutation call. Below the algorithm's floor a reasoning
    #: model spends it on hidden thinking and returns nothing; the control
    #: plane's pre-flight refuses that before the run is created.
    max_tokens_per_call: int = 16_000
    workers: int = 1
    #: The run's own directory in the user's workspace. Candidate sources are
    #: written to ``<run_dir>/candidates/`` so the directory is self-contained:
    #: spec, log and bodies together are everything needed to continue the run
    #: on another machine. Empty falls back to the deployment's data directory,
    #: which is what the probe and the tests use.
    run_dir: str = ""
    #: Sequence number already used by a previous run of this search; a resumed
    #: run continues the numbering rather than restarting it.
    resume_from_sequence: int = 0
    #: The tree an interrupted run left behind, as node rows the control plane
    #: folded out of its own event log. Bodies are *not* here: they are addressed
    #: by hash and rehydrated from the provider's candidate store. Empty means a fresh
    #: search, which is the only case that seeds a root.
    resume_nodes: tuple[dict[str, Any], ...] = ()
    #: The root's raw measurements, which every criterion is normalised against.
    #: Restored rather than re-measured so a resumed run's scores stay on the
    #: same scale as the ones already written down.
    resume_baseline: dict[str, float] = field(default_factory=dict)
    #: Which output protocol the model is asked for, and read back with. The
    #: prompt wording and the parser travel together under this name; an unknown
    #: one is refused before the run rather than at the first expansion.
    reply_format: str = "files"
    #: Engine-specific knobs (``c_puct``, ``prior_exponent``, …). Unknown keys ignored.
    options: dict[str, Any] = field(default_factory=dict)


class Engine(Protocol):
    """Emit the event sequence for one search.

    Implementations must call ``should_stop()`` between expansions and finish
    with a ``search_finished`` event carrying the terminal status — a run that
    stops without one leaves the API unable to tell "still running" from "died".
    """

    def run(self, spec: RunSpec, emit: Emit, should_stop: Callable[[], bool]) -> None:
        ...
