# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Resume journal — content-addressed by *structural call path*.

Each ``agent()`` call is keyed by where it sits in the orchestration tree
(``("call", k)`` / ``("par", k, i)`` / ``("pipe", k, i, s)`` / ``("wf", k,
name)``), which is a deterministic, latency-independent function of the script
— unlike a global entry counter, which reorders under ``pipeline`` streaming.
The key answers "did this call's *position* change?"; a SHA-256 of ``prompt +
opts + schema`` answers "did its *content* change?".

Replay is purely content-addressed: a call is a cache **hit** iff its key is in
the prior journal *and* its signature matches. No global "live latch" — the
cascade is automatic, because a downstream prompt that embeds an upstream
result changes its own signature once the upstream re-runs. This is both
simpler and deterministic under concurrency.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def key_str(key: tuple) -> str:
    """Serialise a structural path tuple to a stable string."""
    return json.dumps(key, ensure_ascii=False)


def call_signature(prompt: str, opts: dict, json_schema: dict | None) -> str:
    """SHA-256 over the call's *content* (prompt + identity opts + schema)."""
    blob = "\x00".join(
        [
            prompt,
            json.dumps(
                {k: opts.get(k) for k in ("label", "phase", "model")},
                sort_keys=True,
                ensure_ascii=False,
            ),
            json.dumps(json_schema, sort_keys=True, ensure_ascii=False),
        ]
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class Journal:
    def __init__(self, prior: dict[str, dict] | None = None) -> None:
        self.prior = prior or {}
        # Records actually used this run (cache-hit -> reused prior; miss -> fresh).
        self.used: dict[str, dict] = {}

    @classmethod
    def load(cls, path: str | None) -> "Journal":
        prior: dict[str, dict] = {}
        if path and Path(path).exists():
            for line in Path(path).read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                prior[rec["key"]] = rec  # last record wins
        return cls(prior)

    def get_cached(self, ks: str, sig: str) -> dict | None:
        rec = self.prior.get(ks)
        return rec if rec is not None and rec.get("sig") == sig else None

    def use(self, ks: str, record: dict) -> None:
        self.used[ks] = record

    def save(self, path: str) -> None:
        # Sort by key for byte-stable output regardless of completion order.
        lines = [json.dumps(self.used[k], ensure_ascii=False) for k in sorted(self.used)]
        Path(path).write_text(("\n".join(lines) + "\n") if lines else "", encoding="utf-8")

    # --- stats helpers (for tests / CLI) ---
    @property
    def hits(self) -> int:
        return sum(1 for k, r in self.used.items() if self.prior.get(k) is r)
