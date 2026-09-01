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

"""Reading a rewritten *text* out of a model's reply.

The fence handling is shared with the program path — `vendor/puct/program.py`
owns the regex, because it is upstream's and the two must agree about what a
block is. What differs is where the summary comes from: a program carries one
in its module docstring, and a prompt or an abstract has no docstring, so the
line before the fence is used instead.

With no fence at all the whole reply is the candidate. Discarding a perfectly
good rewrite over formatting is the worse failure.
"""

from __future__ import annotations

from typing import Tuple

from .program import FENCE

#: A summary longer than this is not a summary; it is the model explaining
#: itself into the node label.
_SUMMARY_MAX = 200


def extract_text(reply: str) -> Tuple[str, str]:
    """`(candidate, change summary)`."""
    stripped = (reply or "").strip()
    if not stripped:
        return "", ""

    blocks = list(FENCE.finditer(stripped))
    if not blocks:
        # No fence: the reply is the candidate. Taking the first line as a
        # summary here would eat a line of the content itself.
        return stripped, ""

    # The longest block, for the same reason the program path takes it: a model
    # that explains itself in a second snippet should not have the explanation
    # adopted as the candidate.
    block = max(blocks, key=lambda match: len(match.group(1)))
    candidate = block.group(1).strip()
    preamble = stripped[:block.start()].strip()
    summary = preamble.splitlines()[-1].strip() if preamble else ""
    return candidate, summary[:_SUMMARY_MAX]
