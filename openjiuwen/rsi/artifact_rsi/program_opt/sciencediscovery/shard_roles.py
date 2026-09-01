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

"""Which case each shard slot stands for.

Slots are positional and must stay that way: the search engine's held-out set is
the *tail* of the task list, and ordering rollout slots before gate slots is what
makes the engine's held-out set the same shards the scorecard gates on. Shuffling
the roles would quietly decouple the two.

What can move is the mapping underneath. A slot is only a position; what it means
is a case id handed to the evaluator, and consecutive ids are almost never
interchangeable — an author writes the easy equations before the stiff ones, the
short documents before the long ones. Handing slots 0..4 to the search and 5..8
to the gate then trains on one kind of problem and gates on another, and the two
scores differ for a reason that has nothing to do with the candidate. Observed
directly: an ODE search whose own check said 0.8441, whose gate said 0.2375, and
whose held-out test said 0.4550 — three numbers for one unchanged program.

So the slot→case mapping is a seeded permutation. Roles stay positional, the
engine stays aligned, and each role draws its cases from across the whole space.
"""

from __future__ import annotations

import random
from typing import Sequence, Tuple


def case_permutation(total: int, seed: int = 0) -> Tuple[int, ...]:
    """A fixed shuffle of `range(total)`, stable for a given `(total, seed)`.

    Deterministic because a score must be comparable across candidates, across
    expansions, and between the probe and the run that follows it.
    """
    order = list(range(max(0, total)))
    random.Random(seed).shuffle(order)
    return tuple(order)


def cases_for(slots: Sequence[int], total: int, seed: int = 0) -> Tuple[int, ...]:
    """Translate positional shard slots into the case ids they stand for.

    A slot outside `range(total)` is passed through unchanged rather than
    dropped: this mapping exists to spread cases, not to police indices, and a
    silently missing shard would show up as a score that is quietly wrong.
    """
    order = case_permutation(total, seed)
    return tuple(order[slot] if 0 <= slot < len(order) else slot for slot in slots)


def total_slots(split) -> int:
    """How many shard slots a split declares, under either field naming."""
    if not isinstance(split, dict):
        return 0
    def read(*names: str) -> int:
        for name in names:
            value = split.get(name)
            if value is not None:
                return int(value)
        return 0
    return (read("rolloutShards", "rolloutGroups")
            + read("gateShards", "gateGroups")
            + read("testShards", "testGroups"))
