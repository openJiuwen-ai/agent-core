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

"""What a task has to supply for the PUCT tree search to run on it.

`futs.search` is indifferent to what it is searching over: it ranks nodes by a
score it never interprets, expands the one PUCT picks, and appends whatever
comes back. Upstream demonstrates that on one task and hard-codes it; this
struct is the seam that keeps the loop honest across two.

Nothing algorithmic lives here. A :class:`Domain` is the four things the search
cannot invent -- the program it starts from, the way a program is scored, the
prompt that rewrites one, and the name of the number being reported -- and
`search.py` holds everything else.

Copied from `examples/era/_era_domain.py` (see `__init__.py` for the upstream
commit). Upstream's own default is its Kaggle task; here the one implementation
is :mod:`.script_domain`, which is the user's scorecard —
which is exactly what this seam was cut for.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Dict, Sequence, Tuple


@dataclass(frozen=True)
class Domain:
    """One scientific task, as the PUCT search sees it."""

    #: Printed in the run plan and stored in the result file.
    name: str
    #: The function a candidate program must define.
    entrypoint: str
    #: The key this task's metric occupies in a node's `metrics` dict, and the
    #: field name it is reported under. `rmse` for the Kaggle task,
    #: `mean_digits` for the integrals -- reported under its own name so a
    #: result file says which number it is holding.
    metric_key: str
    #: "lower" or "higher". Only the sign of the reported gain depends on it;
    #: node ordering is always by `metrics["score"]`, which each task supplies
    #: already oriented so that larger is better, as `futs.search` requires.
    metric_better: str
    #: The root node's program and the summary shown for it.
    initial_program: str
    initial_summary: str
    #: `(code, shards) -> (valid, metrics, error)`, sandboxed.
    evaluate: Callable[[str, Sequence[int]], Tuple[bool, Dict[str, Any], str]]
    #: `metrics -> [0, 1]`, order-preserving with `metrics["score"]`.
    reward: Callable[[Dict[str, Any]], float]
    #: `(parent_program, best_score) -> prompt`, upstream's
    #: `PlaygroundGenerator.__call__` plus the one number upstream had no place
    #: for: what the run's best candidate scores. Without it the mutation prompt
    #: says "Best so far: not measured yet" for the whole budget, and the model
    #: optimises with no idea what it has to beat.
    prompt: Callable[[Any, Optional[float]], str]
    #: `shard_index -> task prompt`, for the rollout tasks.
    task_prompt: Callable[[int], str]
    #: The shards the search never sees, scored once at the end.
    test_shards: Tuple[int, ...]
    #: Whatever the run should record about the data it ran on.
    data_summary: Dict[str, Any] = field(default_factory=dict)

    def gain(self, baseline: Any, best: Any) -> Any:
        """The improvement in this task's own metric, signed so that up is good."""
        if baseline is None or best is None:
            return None
        if self.metric_better == "lower":
            return float(baseline) - float(best)
        return float(best) - float(baseline)
