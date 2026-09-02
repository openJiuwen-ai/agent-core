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

"""The completion seam's shared types.

The HTTP client that used to live here is gone: the RSI contract injects an
initialized ``Model`` into every request, ``completion_factory_from_model``
(`runtime.py`) adapts it to the engine's seam, and a provider that built its
own client from config was the one path that could bypass the injection.

What remains is the seam's vocabulary: ``CompletionUsage`` is what a call cost
and whether it ran out of room, and ``CompletionUnavailable`` is what a factory
raises to say this run was given no model — the engine turns it into a refusal.
"""

from __future__ import annotations

from dataclasses import dataclass



class CompletionUnavailable(RuntimeError):
    """No model access was configured for this run."""


@dataclass(frozen=True)
class CompletionUsage:
    """What one call cost, and whether it ran out of room.

    ``capped`` is the difference between "the model had nothing to say" and "the
    model never got to the saying part". A reasoning model can spend an entire
    output budget on hidden thinking and return empty content — observed on a
    real deployment at exactly 16001 of 16000 permitted tokens — and without
    this the engine reports an empty reply, which reads as a model that cannot
    write code.
    """

    total: int
    completion: int
    capped: bool
