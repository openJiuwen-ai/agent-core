# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Deterministic, offline mock backend.

Makes every example runnable with zero network and *reproducibly*: the result
of a call is a pure function of ``sha256(prompt + opts-signature + schema)``,
which seeds a **local** ``random.Random`` (never the global ``random`` module,
so process RNG state is untouched and ``PYTHONHASHSEED`` is the only global
knob).

When a schema is requested, the mock *synthesises a JSON-Schema-conforming
object* (honouring ``enum``/``const``/``type``/``properties``/``required``/
``items``/``minItems``/``maxItems`` and pydantic's ``$ref``/``$defs``/
``anyOf``/``allOf``). String fields are filled with light, name-aware content
(``url`` -> a URL, ``file`` -> a path, ``query`` -> a query) so realistic
control flow (URL dedup, etc.) exercises end-to-end.

Two override hooks, both optional:

* ``fixtures``: ``{label: value | callable}`` — pin the few agents whose output
  drives control flow (e.g. werewolf role assignment) for snapshot stability.
* ``responder``: ``callable(prompt, opts, schema_json, rng) -> value | None`` —
  context-aware mocking (e.g. pick a live player name out of the prompt). Return
  ``None`` to fall through to schema synthesis.

A hook may return :data:`SKIP` to make the call behave as a user-skip
(``agent()`` -> ``None``).
"""
from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass
from typing import Any, Callable, Sequence

from .base import AgentBackend, AgentResult

#: Sentinel a fixture/responder can return to force a skip (agent() -> None).
SKIP = object()

_MAX_ARRAY = 6  # clamp synthesised arrays so example runs stay small/fast
_MAX_DEPTH = 8  # guard against self-referential schemas


def _opt_sig(opts: dict) -> dict:
    """Deterministic, JSON-able slice of opts for the seed/identity."""
    return {k: opts[k] for k in ("label", "phase", "model", "isolation") if k in opts}


def _seed(prompt: str, opts: dict, schema_json: dict | None, history: Sequence[dict] | None = None) -> int:
    parts = [
        prompt,
        json.dumps(_opt_sig(opts), sort_keys=True, ensure_ascii=False),
        json.dumps(schema_json, sort_keys=True, ensure_ascii=False),
    ]
    if history:
        # Fold prior turns so each turn of a session is deterministically distinct.
        parts.append(json.dumps(list(history), sort_keys=True, ensure_ascii=False, default=str))
    blob = "\x00".join(parts)
    return int(hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16], 16)


def _resolve_ref(ref: str, root: dict) -> dict:
    # Only local pointers like "#/$defs/Name" are produced by pydantic.
    node: Any = root
    for part in ref.lstrip("#/").split("/"):
        if not part:
            continue
        node = node.get(part, {}) if isinstance(node, dict) else {}
    return node if isinstance(node, dict) else {}


def _synth_string(key: str | None, rng: random.Random) -> str:
    k = (key or "").lower()
    n = rng.randint(1000, 9999)
    url_like = "url" in k or "link" in k or "source" in k
    if url_like and "quality" not in k:
        return f"https://example{rng.randint(1, 99)}.test/{k or 'page'}/{n}"
    if "file" in k or "path" in k:
        return f"/tmp/wf-mock/{k or 'out'}_{n}.md"
    if "date" in k:
        return f"202{rng.randint(0, 4)}-0{rng.randint(1, 9)}-1{rng.randint(0, 9)}"
    if "query" in k:
        return f"mock query about topic {n}"
    if "quote" in k:
        return f"“mock supporting quote {n}”"
    return f"[mock:{key or 'text'}] generated text {n}"


def _synth(schema: Any, rng: random.Random, root: dict, key: str | None, depth: int) -> Any:
    if depth > _MAX_DEPTH or not isinstance(schema, dict):
        return None
    if "$ref" in schema:
        schema = _resolve_ref(schema["$ref"], root)
    if "const" in schema:
        return schema["const"]
    if "enum" in schema:
        return rng.choice(schema["enum"])
    for comb in ("anyOf", "oneOf"):
        if comb in schema:
            choices = [s for s in schema[comb] if s.get("type") != "null"] or schema[comb]
            return _synth(choices[0], rng, root, key, depth + 1)
    if "allOf" in schema:
        merged: dict = {}
        for s in schema["allOf"]:
            merged.update(_resolve_ref(s["$ref"], root) if "$ref" in s else s)
        return _synth(merged, rng, root, key, depth + 1)

    t = schema.get("type")
    if isinstance(t, list):
        t = next((x for x in t if x != "null"), t[0])
    if t == "object" or "properties" in schema:
        props = schema.get("properties", {})
        return {k: _synth(sub, rng, root, k, depth + 1) for k, sub in props.items()}
    if t == "array":
        items = schema.get("items", {"type": "string"})
        lo = int(schema.get("minItems", 1))
        hi = int(schema.get("maxItems", lo + 2))
        hi = max(lo, min(hi, _MAX_ARRAY))
        count = rng.randint(lo, hi) if hi >= lo else lo
        return [_synth(items, rng, root, key, depth + 1) for _ in range(count)]
    if t == "boolean":
        return rng.random() < 0.5
    if t == "integer":
        return rng.randint(0, 5)
    if t == "number":
        return round(rng.uniform(0, 5), 2)
    return _synth_string(key, rng)


def synth(schema_json: dict, rng: random.Random) -> Any:
    """Synthesize one object conforming to *schema_json*."""
    return _synth(schema_json, rng, schema_json, None, 0)


@dataclass
class _MockSession:
    """In-memory mock session row (mirrors the real backend's session state)."""

    kind: str
    instructions: str | None
    turns: int = 0


class MockBackend(AgentBackend):
    def __init__(
        self,
        fixtures: dict[str, Any] | None = None,
        responder: Callable[[str, dict, dict | None, random.Random], Any] | None = None,
    ) -> None:
        super().__init__()
        self.fixtures = fixtures or {}
        self.responder = responder
        self._sessions: dict[str, _MockSession] = {}

    async def run(self, prompt: str, opts: dict, schema_json: dict | None) -> AgentResult:
        rng = random.Random(_seed(prompt, opts, schema_json))
        raw = self._pick_raw(prompt, opts, schema_json, rng)
        return self._report(self._result(prompt, opts.get("label"), raw, schema_json, rng))

    # ------------------------------------------------------------------
    # Stateful sessions (deterministic; a "human" session answers like an agent)
    # ------------------------------------------------------------------

    async def open_session(self, *, kind: str, instructions: str | None, opts: dict) -> str:
        sid = f"mock-sess-{len(self._sessions)}"  # deterministic by open order
        self._sessions[sid] = _MockSession(kind=kind, instructions=instructions)
        return sid

    async def send_turn(
        self,
        session_id: str,
        prompt: str,
        opts: dict,
        schema_json: dict | None,
        *,
        history: Sequence[dict] = (),
        correlation_id: str | None = None,
    ) -> AgentResult:
        # Folding history into the seed makes each turn deterministically distinct,
        # so a multi-turn session does not echo the same synthetic answer.
        rng = random.Random(_seed(prompt, opts, schema_json, history))
        raw = self._pick_raw(prompt, opts, schema_json, rng)
        result = self._report(self._result(prompt, opts.get("label"), raw, schema_json, rng))
        sess = self._sessions.get(session_id)
        if sess is not None:
            sess.turns += 1
        return result

    async def close_session(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    async def aclose(self) -> None:
        self._sessions.clear()

    # ------------------------------------------------------------------
    # Shared synthesis (used by ``run`` and ``send_turn``)
    # ------------------------------------------------------------------

    def _report(self, result: AgentResult) -> AgentResult:
        """Bill the synthesised call to the ledger (backends are its only writer).

        There is no model here to ask for real usage, so ``_result``'s length
        estimate is all the mock has — enough to drive ``budget.*`` and the
        engine's gates deterministically in offline tests.
        """
        self.budget.add(result.tokens)
        return result

    def _pick_raw(self, prompt: str, opts: dict, schema_json: dict | None, rng: random.Random) -> Any:
        """Resolve a fixture/responder override, or ``None`` to fall through to synth."""
        label = opts.get("label")
        if label in self.fixtures:
            raw = self.fixtures[label]
            return raw(prompt, opts, schema_json, rng) if callable(raw) else raw
        if self.responder is not None:
            return self.responder(prompt, opts, schema_json, rng)
        return None

    @staticmethod
    def _result(
        prompt: str,
        label: str | None,
        raw: Any,
        schema_json: dict | None,
        rng: random.Random,
    ) -> AgentResult:
        """Turn a raw override (or synth fallback) into an :class:`AgentResult`."""
        if raw is SKIP:
            return AgentResult(skipped=True)
        if raw is None:
            raw = synth(schema_json, rng) if schema_json is not None else _synth_string(label, rng)
        if schema_json is not None:
            payload = json.dumps(raw, ensure_ascii=False)
            return AgentResult(structured=raw, tokens=len(prompt) // 4 + len(payload) // 4)
        text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
        return AgentResult(text=text, tokens=len(prompt) // 4 + len(text) // 4)
