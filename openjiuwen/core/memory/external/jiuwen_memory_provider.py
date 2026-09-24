# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Jiuwen (JiuwenMemory) memory provider — two switchable backends.

A single ``JiuwenMemoryProvider`` exposes the ``MemoryProvider`` interface while
delegating to one of two backends selected at construction time:

- ``mode="server"`` (default): talks to a remote ``JiuwenMemory`` HTTP service
  over httpx — ``POST /v1/<verb>`` + ``GET /healthz``. No local engine is built;
  only a network client is needed. Best for production where the mem2 engine
  runs as its own process/container.
- ``mode="sdk"``: builds a local ``JiuwenMemory`` kernel **in-process** via
  ``from api import assemble`` and calls it directly — no HTTP hop. Requires
  the ``JiuwenMemory`` package installed (``pip install JiuwenMemory``). Best
  for embedding the engine into the host process (single process, no server to
  run).

Both backends expose the same tools (``mem2_search`` / ``mem2_add``) and the
same system prompt block, so the surrounding consumer is agnostic to the mode.

Mem2 write semantics (mirrors the upstream server's three modes):
- default (``infer`` unset / false): the raw ``content`` is stored verbatim and
  indexed — no LLM extraction, durable fact-as-written.
- ``infer=true``: the server runs synchronous LLM extraction (dedup-aware),
  storing derived facts instead of the raw message. This is the mem0-like
  path and the default for ``sync_turn`` so conversations are distilled into
  facts rather than dumped raw.
- ``procedural=true``: the turn is summarized into one PROCEDURAL execution
  history. Not exposed as a tool here; reachable via ``sync_turn`` only when
  the caller opts in through ``write_mode="procedural"``.

Scope mapping:
- SDK backend: ``Scope(org=tenant_id, user=user_id)`` — the provider calls the
  in-process kernel with ``legacy_request_context(scope)``, so the target scope
  doubles as the identity and any org/user pair is writable.
- Server backend: the HTTP contract takes a five-segment scope object, and the
  server's permission model requires the **authenticated identity** to cover the
  target scope (org is a hard boundary). The identity-coverable axes therefore
  come from ``identity_org``/``identity_user`` config (defaults match the dev
  authenticator's fixed identity ``org="local"`` / ``user="developer"``; set
  them to the real authenticated identity in production), while the provider's
  per-call ``tenant_id``/``user_id`` map onto the ``agent``/``session`` axes
  underneath — isolation between tenants/users is preserved, and the identity
  prefix-covers the resulting scope.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from openjiuwen.core.common.logging import logger
from openjiuwen.core.memory.external.provider import MemoryProvider

# Per-verb HTTP timeouts (seconds). `/v1/add` with infer=true triggers real-LLM
# extraction + dedup, so writes get a larger ceiling than reads.
_READ_TIMEOUT = 30.0
_WRITE_TIMEOUT = 120.0

# Circuit breaker: after N consecutive failures, short-circuit for a cooldown
# so a down/broken server doesn't stall every turn.
_BREAKER_THRESHOLD = 5
_BREAKER_COOLDOWN_SECS = 120.0

_DEFAULT_TOP_K = 10
_MAX_TOP_K = 50


# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

SEARCH_SCHEMA = {
    "name": "mem2_search",
    "description": (
        "Search long-term memories by meaning. Returns ranked hits across "
        "keyword (BM25), vector, and graph recall channels. Use this to recall "
        "user facts, preferences, or past context."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "What to search for."},
            "top_k": {
                "type": "integer",
                "description": f"Max results (default {_DEFAULT_TOP_K}, max {_MAX_TOP_K}).",
            },
        },
        "required": ["query"],
    },
}

ADD_SCHEMA = {
    "name": "mem2_add",
    "description": (
        "Store a durable memory. By default the content is stored verbatim and "
        "indexed (no LLM extraction). Set infer=true to have the server extract "
        "and dedup self-contained facts from the content instead."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "The text to remember."},
            "infer": {
                "type": "boolean",
                "description": "Run LLM extraction + dedup on the content (default: false).",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional labels for later filtering.",
            },
        },
        "required": ["content"],
    },
}


# ---------------------------------------------------------------------------
# Server backend — remote JiuwenMemory HTTP client
# ---------------------------------------------------------------------------


class _ServerBackend:
    """Backend that talks to a remote JiuwenMemory server over HTTP.

    The server dispatches every operation through ``POST /v1/<verb>`` with a
    JSON body; ``<verb>`` selects the handler (``add`` / ``search`` / ...).
    Liveness is probed at ``GET /healthz``.
    """

    def __init__(
        self,
        *,
        base_url: str = "http://127.0.0.1:8137",
        api_key: str = "",
        tenant_id: str = "default",
        user_id: str = "",
        identity_org: str = "local",
        identity_user: str = "developer",
        read_timeout: float = _READ_TIMEOUT,
        write_timeout: float = _WRITE_TIMEOUT,
    ):
        self._base_url = base_url.rstrip("/") if base_url else ""
        self._api_key = api_key
        self._tenant_id = tenant_id or "default"
        self._user_id = user_id
        # Server-side authenticated identity (org/user). The server's permission
        # model requires the identity to cover the target scope, so these two
        # axes cannot carry per-call tenant/user data — they must match whoever
        # the server authenticated. Defaults match the dev authenticator's fixed
        # identity; point them at the real authenticated identity in production.
        self._identity_org = identity_org or "local"
        self._identity_user = identity_user or "developer"
        self._read_timeout = read_timeout
        self._write_timeout = max(write_timeout, _WRITE_TIMEOUT)
        self._http: Any | None = None
        self._is_initialized = False

        # Circuit breaker state — shared across read and write paths so a broken
        # server is avoided on both prefetch and tool calls.
        self._consecutive_failures = 0
        self._breaker_open_until = 0.0

    @property
    def is_initialized(self) -> bool:
        return self._is_initialized

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    @property
    def user_id(self) -> str:
        return self._user_id

    def is_available(self) -> bool:
        return bool(self._base_url)

    # -- circuit breaker --------------------------------------------------- #

    def _is_breaker_open(self) -> bool:
        if self._consecutive_failures < _BREAKER_THRESHOLD:
            return False
        if time.monotonic() >= self._breaker_open_until:
            self._consecutive_failures = 0
            return False
        return True

    def _record_success(self) -> None:
        self._consecutive_failures = 0

    def _record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= _BREAKER_THRESHOLD:
            self._breaker_open_until = time.monotonic() + _BREAKER_COOLDOWN_SECS
            logger.warning(
                "[JiuwenMemoryProvider/server] circuit breaker opened after %d failures, "
                "cooldown %ss",
                self._consecutive_failures,
                _BREAKER_COOLDOWN_SECS,
            )

    # -- http plumbing ----------------------------------------------------- #

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def _scope_payload(self, kwargs: dict[str, Any]) -> dict[str, str]:
        """Build the five-segment target scope object for the current call.

        The server's permission model requires the authenticated identity to
        cover the target scope (org is a hard boundary). Under the dev
        authenticator the identity is fixed, so ``org``/``user`` come from the
        configured ``identity_org``/``identity_user`` — NOT from per-call data —
        and the per-call ``tenant_id``/``user_id`` isolate onto the ``agent``/
        ``session`` axes underneath them. The identity prefix-covers the result,
        so writes succeed while tenants/users stay mutually invisible.
        """
        tenant = kwargs.get("tenant_id", self._tenant_id) or "default"
        user = kwargs.get("user_id", self._user_id) or kwargs.get("scope_id", "")
        return {
            "org": self._identity_org,
            "space": "",
            "user": self._identity_user,
            "agent": tenant,
            "session": user,
        }

    async def initialize(self, **kwargs: Any) -> None:
        self._tenant_id = kwargs.get("tenant_id", self._tenant_id) or "default"
        # ``user_id`` (caller convention) is the mem2 ``scope``; accept
        # ``scope_id`` too for parity with the other providers.
        self._user_id = kwargs.get("user_id", self._user_id) or kwargs.get("scope_id", "")
        if kwargs.get("identity_org"):
            self._identity_org = str(kwargs["identity_org"])
        if kwargs.get("identity_user"):
            self._identity_user = str(kwargs["identity_user"])

        if "base_url" in kwargs and kwargs["base_url"]:
            self._base_url = str(kwargs["base_url"]).rstrip("/")
        if "api_key" in kwargs:
            self._api_key = kwargs["api_key"]

        if not self._base_url:
            raise ValueError("Mem2 server base_url is required.")

        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "httpx is required for Mem2 server mode. Install with `pip install httpx`."
            ) from exc

        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            headers=self._headers(),
            timeout=self._read_timeout,
        )

        # Optional liveness probe — never fatal; the server may come up later.
        try:
            resp = await self._http.get("/healthz")
            if resp.status_code == 200:
                logger.info(
                    "[JiuwenMemoryProvider/server] connected to %s", self._base_url
                )
            else:
                logger.warning(
                    "[JiuwenMemoryProvider/server] healthz returned %s at %s",
                    resp.status_code,
                    self._base_url,
                )
        except Exception as exc:
            logger.warning(
                "[JiuwenMemoryProvider/server] healthz failed (%s): %s",
                self._base_url,
                exc,
            )

        self._is_initialized = True

    # -- core dispatch ----------------------------------------------------- #

    async def _post_verb(
        self,
        verb: str,
        payload: dict[str, Any],
        *,
        write: bool = False,
    ) -> Any:
        """POST ``/v1/<verb>`` with a JSON body; return the parsed body or None.

        The body is returned as-is — the server's JSON contract is verb-shaped:
        ``search`` returns an object (``items``/``trajectory``/``errors``) while
        ``add`` returns a **top-level array** of serialized memory units.

        Sets the breaker on failure (raise or non-2xx). Network/parse failures
        return None after recording the failure, so callers can degrade to an
        empty result instead of propagating.
        """
        if self._http is None or not self._is_initialized or self._is_breaker_open():
            return None
        try:
            resp = await self._http.post(
                f"/v1/{verb}",
                json=payload,
                timeout=self._write_timeout if write else self._read_timeout,
            )
            resp.raise_for_status()
            self._record_success()
            return resp.json()
        except Exception as exc:
            self._record_failure()
            logger.debug(
                "[JiuwenMemoryProvider/server] /v1/%s failed: %s", verb, exc
            )
            return None

    # -- read path --------------------------------------------------------- #

    async def search(
        self,
        query: str,
        *,
        top_k: int = _DEFAULT_TOP_K,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        if not query or self._is_breaker_open():
            return []
        # Server contract (MemoryAPI.search signature): query + context{scope}
        # + top_k (+ filters/as_of/disclosure/with_trajectory). Field names must
        # match the signature exactly — unknown fields (e.g. "k") are a 400.
        payload = {
            "query": query,
            "context": {"scope": self._scope_payload(kwargs)},
            "top_k": min(int(top_k or _DEFAULT_TOP_K), _MAX_TOP_K),
            "disclosure": "l2",  # full content, parity with the SDK backend
        }
        data = await self._post_verb("search", payload, write=False)
        if not isinstance(data, dict):
            return []
        items = data.get("items", [])
        if not isinstance(items, list):
            return []
        return [
            {
                "item_id": item.get("unit_id", ""),
                "content": item.get("content", ""),
                "score": item.get("score", 0.0),
            }
            for item in items
            if isinstance(item, dict)
        ]

    async def prefetch(self, query: str, **kwargs: Any) -> str:
        if not query or self._is_breaker_open():
            return ""
        top_k = min(int(kwargs.pop("top_k", _DEFAULT_TOP_K) or _DEFAULT_TOP_K), _MAX_TOP_K)
        hits = await self.search(query, top_k=top_k, **kwargs)
        if not hits:
            return ""
        lines = [
            str(h.get("content", "")).strip()
            for h in hits
            if h.get("content")
        ]
        lines = [ln for ln in lines if ln]
        if not lines:
            return ""
        return "## Jiuwen Memory\n" + "\n".join(f"- {ln}" for ln in lines)

    # -- write path -------------------------------------------------------- #

    async def add(
        self,
        content: str,
        *,
        infer: bool = False,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        """Store one content string. ``infer=True`` enables LLM extraction+dedup."""
        if not content or self._is_breaker_open():
            return None
        # Server contract (MemoryAPI.add signature): content + scope object +
        # tags + system_metadata (+ source/assets/user_metadata/occurred_at).
        # "metadata" is rejected outright; the infer switch rides in
        # system_metadata={"infer": "true"}.
        system_metadata: dict[str, str] = {}
        if infer:
            system_metadata["infer"] = "true"
        payload: dict[str, Any] = {
            "content": content,
            "scope": self._scope_payload(kwargs),
            "tags": tags or [],
            "system_metadata": system_metadata or None,
        }
        units = await self._post_verb("add", payload, write=True)
        if units is None:
            return None
        # The server returns a top-level array of serialized units. With
        # infer=true it may legally be empty (every derived memory deduped to
        # update/noop) — report that as item_id=None, not as a failure.
        if not isinstance(units, list) or not units:
            return {"item_id": None}
        unit = units[0] if isinstance(units[0], dict) else {}
        # Serialized units carry no top-level "content" (it is a property on
        # MemoryUnit, not a field); rebuild it from segments the same way the
        # kernel does — newline-joined segment contents.
        segments = unit.get("segments") or []
        content_view = "\n".join(
            seg.get("content", "") for seg in segments if isinstance(seg, dict)
        )
        return {
            "item_id": unit.get("id", ""),
            "item": {"content": content_view, "tier": unit.get("tier", "")},
        }

    async def sync_turn(
        self,
        user_msg: str,
        assistant_msg: str,
        *,
        infer: bool = True,
        save_assistant: bool = False,
        **kwargs: Any,
    ) -> None:
        """Persist a completed turn.

        Only the **user** turn is stored by default — the assistant reply is
        derivable/low-value and is dropped unless ``save_assistant=True``.
        With ``infer=True`` (default) the server distills the user message
        into deduped facts; with ``infer=False`` it stores the raw text.
        """
        if self._is_breaker_open():
            return
        if user_msg:
            await self.add(
                user_msg,
                infer=infer,
                tags=["conversation", "user"],
                **kwargs,
            )
        if save_assistant and assistant_msg:
            await self.add(
                assistant_msg,
                infer=False,
                tags=["conversation", "assistant"],
                **kwargs,
            )

    # -- tool dispatch ----------------------------------------------------- #

    async def handle_tool_call(self, tool_name: str, args: dict) -> str:
        if not self._is_initialized:
            return json.dumps({"error": "Memory provider not initialized"})
        if self._is_breaker_open():
            return json.dumps(
                {"error": "Mem2 server temporarily unavailable (repeated failures); will retry."}
            )
        try:
            if tool_name == "mem2_search":
                query = args.get("query", "")
                if not query:
                    return json.dumps({"error": "Missing required parameter: query"})
                top_k = min(int(args.get("top_k", _DEFAULT_TOP_K) or _DEFAULT_TOP_K), _MAX_TOP_K)
                hits = await self.search(query, top_k=top_k)
                payload = [
                    {
                        "content": h.get("content", ""),
                        "item_id": h.get("item_id", ""),
                        "score": h.get("score", 0.0),
                    }
                    for h in hits
                ]
                return json.dumps(
                    {"results": payload, "count": len(payload)}, ensure_ascii=False
                )

            if tool_name == "mem2_add":
                content = args.get("content", "")
                if not content:
                    return json.dumps({"error": "Missing required parameter: content"})
                infer = bool(args.get("infer", False))
                tags = args.get("tags")
                data = await self.add(content, infer=infer, tags=tags)
                if data is None:
                    return json.dumps({"error": "Mem2 add failed (server unavailable)."})
                item = data.get("item") or {}
                return json.dumps(
                    {
                        "result": "stored" if data.get("item_id") else "deduped",
                        "item_id": data.get("item_id"),
                        "content": item.get("content", ""),
                        "tier": item.get("tier", ""),
                    },
                    ensure_ascii=False,
                )

            return json.dumps({"error": f"Unknown tool: {tool_name}"})
        except Exception as exc:
            self._record_failure()
            return json.dumps({"error": str(exc), "results": []})

    async def shutdown(self) -> None:
        if self._http is not None:
            try:
                await self._http.aclose()
            except Exception as exc:
                logger.debug(
                    "[JiuwenMemoryProvider/server] http client close failed: %s", exc
                )
        self._http = None
        self._is_initialized = False


# ---------------------------------------------------------------------------
# SDK backend — in-process JiuwenMemory engine (no HTTP)
# ---------------------------------------------------------------------------


class _SDKBackend:
    """Backend that drives a local ``JiuwenMemory`` kernel in-process.

    Builds the engine via ``from jiuwen_memory.api import assemble`` (the same
    entry the HTTP server uses) and calls ``api.add`` / ``api.search`` directly.
    No network hop, no separate server to run — the kernel lives for the
    provider's lifetime.

    The ``JiuwenMemory`` package must be installed (``pip install JiuwenMemory``)
    so that ``from jiuwen_memory.api import assemble`` resolves.
    """

    def __init__(
        self,
        *,
        config_dict: dict[str, Any] | None = None,
        tenant_id: str = "default",
        user_id: str = "",
        infer_turns: bool = True,
        save_assistant_turns: bool = False,
    ):
        self._config_dict = config_dict
        self._tenant_id = tenant_id or "default"
        self._user_id = user_id
        self._infer_turns = infer_turns
        self._save_assistant_turns = save_assistant_turns

        self._api: Any | None = None        # LocalMemoryAPI instance
        self._is_initialized = False
        # JiuwenMemory symbols, captured at initialize() so call sites stay lean
        self._scope_cls: Any = None
        self._modality_cls: Any = None
        self._context_cls: Any = None
        self._disclosure_level_cls: Any = None
        self._security_ctx_factory: Any = None

    @property
    def is_initialized(self) -> bool:
        return self._is_initialized

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    @property
    def user_id(self) -> str:
        return self._user_id

    @staticmethod
    def is_available() -> bool:
        # Always available in principle — the kernel builds lazily on init.
        return True

    # -- import + build the kernel ----------------------------------------- #

    def _resolve_scope(self, **kwargs: Any) -> Any:
        """Build a Scope for the current call (tenant_id=org, user_id=user)."""
        tenant = kwargs.get("tenant_id", self._tenant_id) or "default"
        user = kwargs.get("user_id", self._user_id) or kwargs.get("scope_id", "")
        return self._scope_cls(org=tenant, user=user)

    async def initialize(self, **kwargs: Any) -> None:
        self._tenant_id = kwargs.get("tenant_id", self._tenant_id) or "default"
        self._user_id = kwargs.get("user_id", self._user_id) or kwargs.get("scope_id", "")
        if "config_dict" in kwargs and kwargs["config_dict"]:
            self._config_dict = kwargs["config_dict"]
        if "infer_turns" in kwargs:
            self._infer_turns = bool(kwargs["infer_turns"])
        if "save_assistant_turns" in kwargs:
            self._save_assistant_turns = bool(kwargs["save_assistant_turns"])

        try:
            from jiuwen_memory.api import assemble
            from jiuwen_memory.api import legacy_request_context
            from jiuwen_memory.common.type_def import Scope, Modality, Context
            from jiuwen_memory.retrieval.types import DisclosureLevel
        except ImportError as exc:
            raise RuntimeError(
                "JiuwenMemory is not installed. Install it with "
                "`pip install JiuwenMemory` to use SDK mode."
            ) from exc

        # Build the kernel config from a two-level namespace dict; None → the
        # built-in in-memory defaults (good for tests, lost on restart).
        cfg = None
        try:
            from jiuwen_memory.config import Config
            if self._config_dict:
                cfg = Config.from_dict(self._config_dict)
        except Exception as exc:  # pragma: no cover - config parse failure
            logger.warning(
                "[JiuwenMemoryProvider/sdk] config build failed, using defaults: %s", exc
            )

        try:
            self._api = assemble(config=cfg)
        except Exception as exc:
            raise RuntimeError(f"JiuwenMemory kernel assembly failed: {exc}") from exc

        self._scope_cls = Scope
        self._modality_cls = Modality
        self._context_cls = Context
        self._disclosure_level_cls = DisclosureLevel
        self._security_ctx_factory = legacy_request_context
        self._is_initialized = True
        logger.info(
            "[JiuwenMemoryProvider/sdk] kernel assembled in-process (tenant=%s user=%s)",
            self._tenant_id, self._user_id,
        )

    # -- read path --------------------------------------------------------- #

    async def search(
        self,
        query: str,
        *,
        top_k: int = _DEFAULT_TOP_K,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        if not self._api or not self._is_initialized or not query:
            return []
        scope = self._resolve_scope(**kwargs)
        # JiuwenMemory's LocalMemoryAPI.search is a *sync* method that internally
        # asyncio.run()s the engine. We're already in a running loop, so run the
        # sync call in a worker thread — its asyncio.run() works there.
        ctx = self._context_cls(scope)
        disclosure = self._disclosure_level_cls.L2  # full content, parity with server mode
        k = min(int(top_k or _DEFAULT_TOP_K), _MAX_TOP_K)
        try:
            security = self._security_ctx_factory(scope)
            res = await asyncio.to_thread(
                self._api.search, query, ctx, security=security, top_k=k, disclosure=disclosure
            )
            return [
                {
                    "item_id": getattr(item, "unit_id", ""),
                    "content": getattr(item, "content", ""),
                    "score": getattr(item, "score", 0.0),
                }
                for item in getattr(res, "items", [])
            ]
        except Exception as exc:
            logger.warning("[JiuwenMemoryProvider/sdk] search failed: %s", exc)
            return []

    async def prefetch(self, query: str, **kwargs: Any) -> str:
        if not query:
            return ""
        top_k = min(int(kwargs.pop("top_k", _DEFAULT_TOP_K) or _DEFAULT_TOP_K), _MAX_TOP_K)
        hits = await self.search(query, top_k=top_k, **kwargs)
        if not hits:
            return ""
        lines = [str(h.get("content", "")).strip() for h in hits if h.get("content")]
        lines = [ln for ln in lines if ln]
        if not lines:
            return ""
        return "## Jiuwen Memory\n" + "\n".join(f"- {ln}" for ln in lines)

    # -- write path -------------------------------------------------------- #

    async def add(
        self,
        content: str,
        *,
        infer: bool = False,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        """Store one content string. ``infer=True`` enables LLM extraction+dedup."""
        if not self._api or not self._is_initialized or not content:
            return None
        scope = self._resolve_scope(**kwargs)
        metadata: dict[str, str] = {}
        if infer:
            metadata["infer"] = "true"
        modality = self._modality_cls.TEXT
        # See search(): the sync LocalMemoryAPI.add asyncio.run()s internally,
        # so dispatch it to a worker thread from our running loop.
        try:
            security = self._security_ctx_factory(scope)
            units = await asyncio.to_thread(
                self._api.add,
                content,
                scope,
                modality,
                security=security,
                tags=tags,
                system_metadata=metadata or None,
            )
            if not units:
                return {"item_id": None}     # all deduped (infer path)
            u = units[0]
            return {
                "item_id": getattr(u, "id", ""),
                "item": {
                    "content": getattr(u, "content", ""),
                    "tier": getattr(getattr(u, "tier", None), "value", ""),
                },
            }
        except Exception as exc:
            logger.warning("[JiuwenMemoryProvider/sdk] add failed: %s", exc)
            return None

    async def sync_turn(
        self,
        user_msg: str,
        assistant_msg: str,
        *,
        infer: bool = True,
        save_assistant: bool = False,
        **kwargs: Any,
    ) -> None:
        """Persist a completed turn. Default: only the user turn."""
        if user_msg:
            await self.add(
                user_msg, infer=infer, tags=["conversation", "user"], **kwargs
            )
        if save_assistant and assistant_msg:
            await self.add(
                assistant_msg,
                infer=False,
                tags=["conversation", "assistant"],
                **kwargs,
            )

    # -- tool dispatch ----------------------------------------------------- #

    async def handle_tool_call(self, tool_name: str, args: dict) -> str:
        if not self._is_initialized:
            return json.dumps({"error": "Memory provider not initialized"})
        try:
            if tool_name == "mem2_search":
                query = args.get("query", "")
                if not query:
                    return json.dumps({"error": "Missing required parameter: query"})
                top_k = min(int(args.get("top_k", _DEFAULT_TOP_K) or _DEFAULT_TOP_K), _MAX_TOP_K)
                hits = await self.search(query, top_k=top_k)
                payload = [
                    {
                        "content": h.get("content", ""),
                        "item_id": h.get("item_id", ""),
                        "score": h.get("score", 0.0),
                    }
                    for h in hits
                ]
                return json.dumps(
                    {"results": payload, "count": len(payload)}, ensure_ascii=False
                )

            if tool_name == "mem2_add":
                content = args.get("content", "")
                if not content:
                    return json.dumps({"error": "Missing required parameter: content"})
                infer = bool(args.get("infer", False))
                tags = args.get("tags")
                data = await self.add(content, infer=infer, tags=tags)
                if data is None:
                    return json.dumps({"error": "Mem2 add failed (kernel error)."})
                item = data.get("item") or {}
                item_id = data.get("item_id")
                return json.dumps(
                    {
                        "result": "stored" if item_id else "deduped",
                        "item_id": item_id,
                        "content": item.get("content", ""),
                        "tier": item.get("tier", ""),
                    },
                    ensure_ascii=False,
                )

            return json.dumps({"error": f"Unknown tool: {tool_name}"})
        except Exception as exc:
            return json.dumps({"error": str(exc), "results": []})

    async def shutdown(self) -> None:
        # The in-process kernel has no external resources to close beyond
        # dropping the reference. Stores with close() (e.g. sqlite) are left
        # to GC; callers wanting clean teardown should run outside the provider.
        self._api = None
        self._is_initialized = False


# ---------------------------------------------------------------------------
# Public provider
# ---------------------------------------------------------------------------


class JiuwenMemoryProvider(MemoryProvider):
    """Jiuwen (JiuwenMemory) provider with two switchable backends.

    Args:
        mode: ``"server"`` (default) to call a remote JiuwenMemory HTTP service,
            or ``"sdk"`` to build a local JiuwenMemory kernel in-process via
            ``from api import assemble``. Fixed at construction; cannot switch
            afterwards. Case-insensitive.
        base_url: JiuwenMemory server URL. Server mode only.
        api_key: optional bearer token sent as ``Authorization``. Server mode
            only.
        config_dict: JiuwenMemory assembly config (two-level namespace dict).
            SDK mode only; None → built-in in-memory defaults. Runtime policy
            overrides go under ``config_dict["globals"]["policies"]``.
        tenant_id: the org axis of ``Scope``. Defaults to ``"default"``.
        user_id: the per-user/per-session axis (mem2 ``scope``).
        identity_org / identity_user: server mode only — the org/user of the
            server-side authenticated identity. The server requires the
            identity to cover the target scope, so these must match whoever
            the server authenticated (defaults match the dev authenticator's
            fixed ``local``/``developer``; set to the real identity in
            production). ``tenant_id``/``user_id`` isolate onto the
            ``agent``/``session`` axes underneath them.
        read_timeout / write_timeout: HTTP timeouts. Server mode only.
        infer_turns: whether ``sync_turn`` distills user turns into facts via
            the extraction+dedup path (default True). Set False to store raw
            conversation text verbatim.
        save_assistant_turns: whether ``sync_turn`` also persists the assistant
            reply (default False). Only the **user** turn is stored by default,
            since the assistant reply is derivable and low-value as a memory.
    """

    def __init__(
        self,
        *,
        mode: str = "server",
        base_url: str = "http://127.0.0.1:8137",
        api_key: str = "",
        tenant_id: str = "default",
        user_id: str = "",
        identity_org: str = "local",
        identity_user: str = "developer",
        read_timeout: float = _READ_TIMEOUT,
        write_timeout: float = _WRITE_TIMEOUT,
        config_dict: dict[str, Any] | None = None,
        infer_turns: bool = True,
        save_assistant_turns: bool = False,
    ):
        self._mode = mode.strip().lower()
        if self._mode not in ("server", "sdk"):
            raise ValueError(f"mode must be 'server' or 'sdk', got {mode!r}")
        self._infer_turns = infer_turns
        self._save_assistant_turns = save_assistant_turns

        if self._mode == "server":
            self._backend: _ServerBackend | _SDKBackend = _ServerBackend(
                base_url=base_url,
                api_key=api_key,
                tenant_id=tenant_id,
                user_id=user_id,
                identity_org=identity_org,
                identity_user=identity_user,
                read_timeout=read_timeout,
                write_timeout=write_timeout,
            )
        else:
            self._backend = _SDKBackend(
                config_dict=config_dict,
                tenant_id=tenant_id,
                user_id=user_id,
                infer_turns=infer_turns,
                save_assistant_turns=save_assistant_turns,
            )

    @property
    def name(self) -> str:
        return "jiuwen_memory"

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def is_initialized(self) -> bool:
        return self._backend.is_initialized

    def is_available(self) -> bool:
        return self._backend.is_available()

    async def initialize(self, **kwargs: Any) -> None:
        if "infer_turns" in kwargs:
            self._infer_turns = bool(kwargs["infer_turns"])
        if "save_assistant_turns" in kwargs:
            self._save_assistant_turns = bool(kwargs["save_assistant_turns"])
        await self._backend.initialize(**kwargs)

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        return [SEARCH_SCHEMA, ADD_SCHEMA]

    def system_prompt_block(self) -> str:
        return (
            "# Jiuwen Memory\n"
            f"Active. user={self._backend.user_id or '?'}, "
            f"tenant={self._backend.tenant_id}.\n"
            "Use mem2_search to recall memories, mem2_add to store a fact. "
            "Conversation turns are extracted into memories automatically."
        )

    async def prefetch(self, query: str, **kwargs: Any) -> str:
        return await self._backend.prefetch(query, **kwargs)

    async def sync_turn(
        self, user_msg: str, assistant_msg: str, **kwargs: Any
    ) -> None:
        infer = bool(kwargs.pop("infer", self._infer_turns))
        save_assistant = bool(
            kwargs.pop("save_assistant", self._save_assistant_turns)
        )
        await self._backend.sync_turn(
            user_msg,
            assistant_msg,
            infer=infer,
            save_assistant=save_assistant,
            **kwargs,
        )

    async def handle_tool_call(self, tool_name: str, args: dict) -> str:
        return await self._backend.handle_tool_call(tool_name, args)

    async def shutdown(self) -> None:
        await self._backend.shutdown()

    async def on_session_end(self, messages: list[dict[str, Any]]) -> None:
        # No server-side session lifecycle to close; nothing to do.
        return


__all__ = ["JiuwenMemoryProvider"]
