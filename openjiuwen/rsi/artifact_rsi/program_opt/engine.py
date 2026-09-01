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
from .sandbox import SandboxCapability


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
    #: The program the search starts from. Empty means the vendored seed.
    baseline_code: str = ""
    #: Wall-clock ceiling for one candidate execution.
    candidate_timeout_seconds: float = 60.0
    #: The rubric a judged scorecard grades against. Frozen with the goal: a
    #: search that could rewrite its own marking scheme would learn to do that
    #: instead of getting better.
    rubric: str = ""
    #: The evaluator a `custom_script` scorecard scores with, written by the
    #: drafting model. Frozen with the goal for the same reason the rubric is:
    #: a search that could rewrite what measures it would learn to do that
    #: instead of getting better.
    script: str = ""
    #: Packages the candidates need that this runtime does not have. Worked out
    #: by the drafting agent from the task and installed before anything runs —
    #: the person who typed "bring the error down" has no way to know a boosting library
    #: is wanted, and no reason to.
    packages: tuple[str, ...] = ()
    #: Material a judged candidate must stay faithful to. Without it a rubric
    #: can only reward properties the candidate can fabricate.
    source_material: str = ""
    #: ``{url, token}`` for the judge model, when the scorecard needs one. A
    #: second token because the proxy pins the model to the token.
    judge_url: str = ""
    judge_token: str = ""
    #: ``"disabled"`` / ``"enabled"`` / empty for the provider's own default.
    #: The user's trade, not this process's: on, a reasoning model spends ~46k
    #: output tokens per call for better candidates; off, ~1.2k for weaker ones.
    thinking: str = ""
    #: Ceiling for one mutation call. Below the algorithm's floor a reasoning
    #: model spends it on hidden thinking and returns nothing; the control
    #: plane's pre-flight refuses that before the run is created.
    max_tokens_per_call: int = 16_000
    baseline_score: float | None = None
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
    #: by hash and rehydrated from this sidecar's own store. Empty means a fresh
    #: search, which is the only case that seeds a root.
    resume_nodes: tuple[dict[str, Any], ...] = ()
    #: The root's raw measurements, which every criterion is normalised against.
    #: Restored rather than re-measured so a resumed run's scores stay on the
    #: same scale as the ones already written down.
    resume_baseline: dict[str, float] = field(default_factory=dict)
    #: Output tokens a previous attempt at this search already spent. The `cost`
    #: event is absolute by contract, so without this a resume would report the
    #: spend dropping back to near zero.
    resume_tokens: int = 0
    #: Engine-specific knobs (``c_puct``, ``prior_exponent``, …). Unknown keys ignored.
    options: dict[str, Any] = field(default_factory=dict)
    #: What the control plane's probe found. The sidecar never probes for
    #: itself: the container corrections (`--disable-userns`, the procfs
    #: fallback) are knowledge that has one owner, and asking twice is how two
    #: answers start to disagree.
    sandbox: SandboxCapability = field(default_factory=SandboxCapability)
    #: Where to reach the control plane's model proxy, and the run-scoped token
    #: that authenticates there. **Never a provider key**: this process runs
    #: model-written code, so the worst a leak here can do is spend the run it
    #: belongs to, and only until the run ends.
    llm_url: str = ""
    llm_token: str = ""


class Engine(Protocol):
    """Emit the event sequence for one search.

    ``requires_sandbox`` is what makes the isolation refusal enforceable in one
    place: an engine that executes model-written code says so, and a run that
    asks for it without a backend is refused before it starts rather than at the
    first expansion. The stub executes nothing, so it runs anywhere.

    Implementations must call ``should_stop()`` between expansions and finish
    with a ``search_finished`` event carrying the terminal status — a run that
    stops without one leaves the API unable to tell "still running" from "died".
    """

    name: str
    requires_sandbox: bool

    def run(self, spec: RunSpec, emit: Emit, should_stop: Callable[[], bool]) -> None:
        ...
