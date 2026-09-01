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

"""Keeping a candidate's source where a reader can find it again.

The event stream carries only the hash (graph = directory, CAS = warehouse); the
body lands here so the run directory is self-contained -- spec, log and sources
together are everything needed to continue the run on another machine, or to
copy one candidate out of it.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from .logging_config import get_logger

log = get_logger("candidates")

#: The tree snapshot's filename inside a search's directory.
TREE_FILE = "tree.json"

#: Bumped when the shape below changes in a way a reader must notice.
TREE_SCHEMA_VERSION = 1


def write_tree_snapshot(path: Path, payload: Dict[str, Any]) -> None:
    """Replace the tree snapshot atomically.

    A reader can arrive at any moment — the control plane answering a query, an
    adapter serving `get_tree`, a person looking at the directory — and a
    half-written file is worse than an old one. Written beside the target so the
    rename stays on one filesystem, which is what makes it atomic.

    Never raises. The snapshot is a convenience for readers; a disk that is full
    or read-only must not end a search that is otherwise working.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        os.replace(temporary, path)
    except Exception:  # noqa: BLE001 - a snapshot is never worth a failed run
        log.warning("could not write the tree snapshot to %s", path, exc_info=True)


class CandidateStore:
    """Candidate sources, addressed by the hash of their content.

    Content-addressed rather than indexed by node: an identical candidate
    proposed twice is one file, and the hash in the event stream is enough to
    find it without a lookup table that could disagree with the log.
    """

    def __init__(self, root: Path, flat: bool = False) -> None:
        self.root = root
        #: A run directory already belongs to one search, so nesting the id
        #: under it again would give every path the search id twice. The
        #: deployment-wide store still needs that level, because one root holds
        #: every run's candidates.
        self.flat = flat

    def run_path(self, search_id: str, name: str) -> Path:
        """A per-search file beside that search's candidates.

        Same flat/nested rule the candidate paths follow, so everything one
        search produced lands under one directory and a run directory is a
        portable thing on its own.
        """
        return (self.root / name) if self.flat else (self.root / search_id / name)

    def path_for(self, search_id: str, code_hash: str) -> Path:
        name = f"{code_hash.removeprefix('sha256:')}.py"
        return (self.root / "candidates" / name) if self.flat else (self.root / search_id / name)

    def put(self, search_id: str, code: str) -> str:
        digest = hashlib.sha256(code.encode("utf-8")).hexdigest()
        path = self.path_for(search_id, digest)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text(code, encoding="utf-8")
        return f"sha256:{digest}"

    def get(self, search_id: str, code_hash: str) -> Optional[str]:
        path = self.path_for(search_id, code_hash)
        return path.read_text(encoding="utf-8") if path.exists() else None
