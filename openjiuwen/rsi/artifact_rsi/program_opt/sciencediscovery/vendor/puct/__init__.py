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

"""Vendored ERA port from AgentDescent, exposed here as ``puct``.

The upstream files are named after their example ("ERA"); this package is named
after what the algorithm actually is, because that is what the name has to tell
a reader tracing it back upstream. Every ``examples/era/...`` path
below is a provenance fact and stays as upstream spells it.

Upstream: https://github.com/Birfy/agentdescent @ b3d4240
Files:    examples/era/era_empirical_software.py, examples/era/_era_support.py,
          examples/era/_era_runner.py

**Why vendored rather than imported.** The algorithm bodies live under
`examples/`, which the wheel does not package (`pyproject.toml` only ships
`agentdescent*`), and examples explicitly do not promise API stability. The
engine itself — `evolve()`, `FlatPuct`, `Ledger`, the verifier and the policies —
*is* imported from the installed package; only the port is copied.

**What changed on the way in**, and nothing else:

* The task is gone. Upstream is hard-wired to Kaggle Playground S3E1 (the
  `MedHouseVal` target, the 80/20 head/tail split, RMSE); this port takes its
  objective from the scorecard instead, so `prepare_splits`, `run_candidate`
  and `evaluate_source` are not here.
* `_era_runner.py` is copied **byte for byte**. See the note in that file: it
  encodes a non-obvious CPU-budget interaction, and a rewrite fails in a way
  that looks like poor candidate quality.
* Imports are rewritten for this package's layout.

The upstream tests came with it (`tests/test_puct_fidelity.py`): the two
`futs_test.py` fixtures, the line-by-line reproduction of `futs.search`, and the
gate/parser checks. Moving the code without them would discard the
`benchmark_faithful` claim, which is the only reason to vendor rather than
re-implement.

**What is here now.** The tree, the AST gate, the sandbox, the runner — and
`search.py`, which is `futs.search`'s loop body as the Strategy and Aggregator
that `evolve` / `async_evolve` plug in. That last file is the answer to "where
do workers, staleness and a barrier-free runtime come from": upstream does not
implement any of them, it hands the loop body to the engine through
`aggregator_factory` and lets the engine supply the rest.

`domain.py` is the seam upstream cut so a second task could run on the same
loop. Here the one implementation is the user's scorecard
(`sciencediscovery_evolve.script_domain`), which is what the seam is for.
"""

from .domain import Domain
from .program import (
    BLOCKED_IMPORTS,
    available_imports,
    FORBIDDEN_CALLS,
    Program,
    extract_program,
    program_id,
    validate_source,
)
from .search import PuctStrategy, PuctTreeAggregator, make_propose, make_reward, make_run
from .tree import PuctTree, Node

__all__ = [
    "BLOCKED_IMPORTS",
    "available_imports",
    "FORBIDDEN_CALLS",
    "Domain",
    "PuctStrategy",
    "PuctTree",
    "PuctTreeAggregator",
    "Node",
    "Program",
    "extract_program",
    "make_propose",
    "make_reward",
    "make_run",
    "program_id",
    "validate_source",
]
