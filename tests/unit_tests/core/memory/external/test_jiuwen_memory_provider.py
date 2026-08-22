# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for JiuwenMemoryProvider — server mode, httpx fully mocked.

The provider talks to a remote agent-memory server over httpx
(``POST /v1/<verb>`` + ``GET /healthz``). These tests replace
``httpx.AsyncClient`` with a fake so no network is touched — they exercise the
provider's own logic (request shaping, response parsing, error mapping, circuit
breaker, lifecycle) rather than the live server.

These tests only touch ``JiuwenMemoryProvider``'s public API. The fake HTTP
client is injected by patching the third-party ``httpx.AsyncClient`` symbol
(which ``initialize()`` consumes as a public dependency); no private members
or module-private constants of the provider are imported or accessed. The
fake client is the very ``MagicMock`` the test builds and passes into
``initialize()`` — asserting on ``fake.post`` / ``fake.get`` is asserting on
the test's own object, not on provider internals.

Coverage:
- construction / metadata (name, availability, schemas, system prompt)
- initialize (client creation, optional healthz, api_key → Authorization header,
  health failure is non-fatal)
- write path (add payload: content / tags / metadata.infer / tenant+scope)
- read path (search payload: query / k / tenant+scope; prefetch formatting)
- sync_turn (default: user only; ``save_assistant=True`` also stores assistant)
- handle_tool_call (search / add result shapes; missing-arg / unknown-tool
  errors)
- circuit breaker (consecutive failures short-circuit later calls; a success
  resets the failure counter so subsequent calls resume)
- lifecycle (shutdown closes the client)
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.memory.external import JiuwenMemoryProvider


# ---------------------------------------------------------------------------
# httpx fakes
# ---------------------------------------------------------------------------


def _mock_response(payload: dict, status_code: int = 200):
    """An httpx.Response stand-in carrying a JSON payload."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = payload
    if status_code >= 400:
        resp.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    else:
        resp.raise_for_status = MagicMock()
    return resp


def _fake_http(
    get_side_effect=None,
    post_value=None,
    post_side_effect=None,
):
    """A MagicMock standing in for httpx.AsyncClient.

    - ``GET`` defaults to a successful healthz (initialize probes it).
    - ``POST`` defaults to an empty ``{}`` success body.
    """
    http = MagicMock()
    if get_side_effect is not None:
        http.get = AsyncMock(side_effect=get_side_effect)
    else:
        http.get = AsyncMock(return_value=_mock_response({"status": "ok"}))
    if post_side_effect is not None:
        http.post = AsyncMock(side_effect=post_side_effect)
    else:
        http.post = AsyncMock(
            return_value=post_value if post_value is not None else _mock_response({})
        )
    http.aclose = AsyncMock()
    return http


_PATCH = "httpx.AsyncClient"


async def _init(provider: JiuwenMemoryProvider, fake_http) -> JiuwenMemoryProvider:
    """initialize() under a patched httpx.AsyncClient returning fake_http.

    ``patch(..., return_value=fake_http)`` makes ``httpx.AsyncClient(...)``
    return ``fake_http``, so the provider ends up using exactly this object.
    The test then asserts on ``fake_http`` directly (its own mock).
    """
    with patch(_PATCH, return_value=fake_http):
        await provider.initialize()
    return provider


# ---------------------------------------------------------------------------
# Construction / metadata
# ---------------------------------------------------------------------------


def test_name_is_mem2():
    assert JiuwenMemoryProvider().name == "jiuwen_memory"


def test_is_available_with_base_url():
    assert JiuwenMemoryProvider(base_url="http://localhost:8137").is_available() is True


def test_not_available_without_base_url():
    assert JiuwenMemoryProvider(base_url="").is_available() is False


def test_not_initialized_by_default():
    assert JiuwenMemoryProvider(base_url="http://localhost:8137").is_initialized is False


def test_tool_schemas_have_expected_names():
    schemas = JiuwenMemoryProvider().get_tool_schemas()
    names = {s["name"] for s in schemas}
    assert {"mem2_search", "mem2_add"} <= names
    for s in schemas:
        assert "parameters" in s and "description" in s


@pytest.mark.asyncio
async def test_system_prompt_block_mentions_tools():
    provider = JiuwenMemoryProvider(tenant_id="t1", user_id="u1")
    with patch(_PATCH, return_value=_fake_http()):
        await provider.initialize()
    block = provider.system_prompt_block()
    assert "Jiuwen Memory" in block
    assert "mem2_search" in block and "mem2_add" in block
    assert "u1" in block and "t1" in block


# ---------------------------------------------------------------------------
# initialize
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_initialize_creates_client_and_probes_healthz():
    provider = JiuwenMemoryProvider(base_url="http://localhost:8137")
    fake = _fake_http()
    with patch(_PATCH, return_value=fake):
        await provider.initialize()
    assert provider.is_initialized is True
    fake.get.assert_awaited_once_with("/healthz")


@pytest.mark.asyncio
async def test_initialize_health_failure_is_non_fatal():
    """An unreachable server must not block initialization."""
    provider = JiuwenMemoryProvider(base_url="http://nowhere:9999")
    fake = _fake_http(get_side_effect=ConnectionError("refused"))
    with patch(_PATCH, return_value=fake):
        await provider.initialize()
    assert provider.is_initialized is True


@pytest.mark.asyncio
async def test_initialize_requires_base_url():
    provider = JiuwenMemoryProvider(base_url="")
    with pytest.raises(ValueError):
        await provider.initialize()


@pytest.mark.asyncio
async def test_api_key_flows_into_authorization_header():
    provider = JiuwenMemoryProvider(api_key="secret-token")
    with patch(_PATCH) as client_cls:
        await provider.initialize()
    _, kwargs = client_cls.call_args
    assert kwargs["headers"]["Authorization"] == "Bearer secret-token"


# ---------------------------------------------------------------------------
# Write path — add via handle_tool_call (public entry point)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_posts_to_v1_add_with_full_payload():
    fake = _fake_http()
    provider = await _init(
        JiuwenMemoryProvider(tenant_id="org1", user_id="alice"), fake
    )
    fake.post = AsyncMock(
        return_value=_mock_response({"ok": True, "item_id": "id-1", "item": {}})
    )
    await provider.handle_tool_call(
        "mem2_add", {"content": "a fact", "infer": True, "tags": ["x", "y"]}
    )

    fake.post.assert_awaited_once()
    args, kwargs = fake.post.call_args
    assert args[0] == "/v1/add"
    body = kwargs["json"]
    assert body["content"] == "a fact"
    assert body["tags"] == ["x", "y"]
    assert body["metadata"] == {"infer": "true"}  # infer flag translated to metadata
    assert body["tenant_id"] == "org1"
    assert body["scope"] == "alice"
    # writes use the larger write timeout
    assert kwargs["timeout"] >= 60.0


@pytest.mark.asyncio
async def test_add_without_infer_omits_infer_metadata():
    fake = _fake_http()
    provider = await _init(
        JiuwenMemoryProvider(tenant_id="org1", user_id="alice"), fake
    )
    fake.post = AsyncMock(return_value=_mock_response({"ok": True, "item_id": "id-1"}))
    await provider.handle_tool_call("mem2_add", {"content": "raw text", "infer": False})
    body = fake.post.call_args.kwargs["json"]
    assert body["metadata"] == {}  # no infer flag when infer=False


@pytest.mark.asyncio
async def test_add_returns_error_when_unavailable():
    """A failed POST surfaces an error JSON via the tool-call entry (degrade, don't raise)."""
    fake = _fake_http()
    provider = await _init(
        JiuwenMemoryProvider(tenant_id="org1", user_id="alice"), fake
    )
    fake.post = AsyncMock(side_effect=Exception("boom"))
    out = await provider.handle_tool_call("mem2_add", {"content": "x", "infer": False})
    assert "error" in json.loads(out)


# ---------------------------------------------------------------------------
# Read path — search via handle_tool_call / prefetch (public entry points)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_posts_to_v1_search_with_query_k_and_scope():
    fake = _fake_http()
    provider = await _init(
        JiuwenMemoryProvider(tenant_id="org1", user_id="alice"), fake
    )
    fake.post = AsyncMock(
        return_value=_mock_response(
            {"hits": [{"item_id": "h1", "content": "c1", "score": 0.9}]}
        )
    )
    out = await provider.handle_tool_call("mem2_search", {"query": "Python", "top_k": 5})
    data = json.loads(out)
    assert data["count"] == 1
    assert data["results"][0]["content"] == "c1"
    body = fake.post.call_args.kwargs["json"]
    assert body["query"] == "Python"
    assert body["k"] == 5
    assert body["tenant_id"] == "org1" and body["scope"] == "alice"


@pytest.mark.asyncio
async def test_search_top_k_capped_to_max():
    fake = _fake_http()
    provider = await _init(JiuwenMemoryProvider(), fake)
    fake.post = AsyncMock(return_value=_mock_response({"hits": []}))
    await provider.handle_tool_call("mem2_search", {"query": "q", "top_k": 9999})
    assert fake.post.call_args.kwargs["json"]["k"] == 50  # server-side cap


@pytest.mark.asyncio
async def test_search_top_k_none_falls_back_to_default():
    """Regression: an explicit ``top_k: None`` (LLMs do this) must not crash
    ``int(None)``. It should fall back to the default and still POST."""
    fake = _fake_http()
    provider = await _init(JiuwenMemoryProvider(), fake)
    fake.post = AsyncMock(return_value=_mock_response({"hits": []}))
    out = await provider.handle_tool_call("mem2_search", {"query": "q", "top_k": None})
    data = json.loads(out)
    assert "error" not in data
    assert fake.post.call_args.kwargs["json"]["k"] == 10  # falls back to default


@pytest.mark.asyncio
async def test_search_empty_query_returns_error_without_posting():
    """Empty query is treated as missing → error, no HTTP call."""
    fake = _fake_http()
    provider = await _init(JiuwenMemoryProvider(), fake)
    out = await provider.handle_tool_call("mem2_search", {"query": ""})
    data = json.loads(out)
    assert "error" in data  # empty query == missing query
    fake.post.assert_not_awaited()


@pytest.mark.asyncio
async def test_prefetch_formats_marked_block():
    fake = _fake_http()
    provider = await _init(JiuwenMemoryProvider(), fake)
    fake.post = AsyncMock(
        return_value=_mock_response(
            {"hits": [{"content": "likes Python", "score": 0.8}]}
        )
    )
    block = await provider.prefetch("Python")
    assert block.startswith("## Jiuwen Memory")
    assert "- likes Python" in block


@pytest.mark.asyncio
async def test_prefetch_empty_hits_returns_empty_string():
    fake = _fake_http()
    provider = await _init(JiuwenMemoryProvider(), fake)
    fake.post = AsyncMock(return_value=_mock_response({"hits": []}))
    assert await provider.prefetch("Python") == ""


@pytest.mark.asyncio
async def test_prefetch_pops_top_k_before_delegating_to_search():
    """Regression: prefetch used to pass top_k twice (in kwargs + explicit)."""
    fake = _fake_http()
    provider = await _init(JiuwenMemoryProvider(), fake)
    fake.post = AsyncMock(return_value=_mock_response({"hits": []}))
    await provider.prefetch("q", top_k=3)
    assert fake.post.call_args.kwargs["json"]["k"] == 3


# ---------------------------------------------------------------------------
# sync_turn — user-only by default, assistant when opted in
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_turn_stores_only_user_by_default():
    fake = _fake_http()
    provider = await _init(
        JiuwenMemoryProvider(tenant_id="org1", user_id="alice", infer_turns=False),
        fake,
    )
    fake.post = AsyncMock(return_value=_mock_response({"ok": True, "item_id": "i"}))
    await provider.sync_turn("user says", "assistant says")

    # Exactly one POST — only the user turn.
    assert fake.post.await_count == 1
    body = fake.post.call_args.kwargs["json"]
    assert body["content"] == "user says"
    assert "user" in body["tags"]


@pytest.mark.asyncio
async def test_sync_turn_saves_assistant_when_enabled():
    fake = _fake_http()
    provider = await _init(
        JiuwenMemoryProvider(
            tenant_id="org1",
            user_id="alice",
            infer_turns=False,
            save_assistant_turns=True,
        ),
        fake,
    )
    fake.post = AsyncMock(return_value=_mock_response({"ok": True, "item_id": "i"}))
    await provider.sync_turn("user says", "assistant says")

    assert fake.post.await_count == 2
    bodies = [c.kwargs["json"] for c in fake.post.call_args_list]
    assert bodies[0]["content"] == "user says"
    assert bodies[1]["content"] == "assistant says"
    assert "assistant" in bodies[1]["tags"]


@pytest.mark.asyncio
async def test_sync_turn_infer_flag_respected():
    fake = _fake_http()
    provider = await _init(
        JiuwenMemoryProvider(tenant_id="org1", user_id="alice", infer_turns=False),
        fake,
    )
    fake.post = AsyncMock(return_value=_mock_response({"ok": True, "item_id": "i"}))
    await provider.sync_turn("user says", "assistant says", infer=True)
    assert fake.post.call_args.kwargs["json"]["metadata"] == {"infer": "true"}


# ---------------------------------------------------------------------------
# handle_tool_call
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_tool_call_search_returns_results():
    fake = _fake_http()
    provider = await _init(JiuwenMemoryProvider(tenant_id="t1", user_id="u1"), fake)
    fake.post = AsyncMock(
        return_value=_mock_response(
            {"hits": [{"item_id": "h1", "content": "c1", "score": 0.9}]}
        )
    )
    out = await provider.handle_tool_call("mem2_search", {"query": "Python", "top_k": 3})
    data = json.loads(out)
    assert data["count"] == 1
    assert data["results"][0]["content"] == "c1"


@pytest.mark.asyncio
async def test_handle_tool_call_add_returns_stored():
    fake = _fake_http()
    provider = await _init(JiuwenMemoryProvider(tenant_id="t1", user_id="u1"), fake)
    fake.post = AsyncMock(
        return_value=_mock_response(
            {"item_id": "id-1", "item": {"content": "a fact", "tier": "semantic"}}
        )
    )
    out = await provider.handle_tool_call(
        "mem2_add", {"content": "a fact", "infer": True}
    )
    data = json.loads(out)
    assert data["result"] == "stored"
    assert data["item_id"] == "id-1"
    assert data["tier"] == "semantic"


@pytest.mark.asyncio
async def test_handle_tool_call_add_deduped_when_no_item_id():
    fake = _fake_http()
    provider = await _init(JiuwenMemoryProvider(tenant_id="t1", user_id="u1"), fake)
    fake.post = AsyncMock(return_value=_mock_response({"item_id": None}))
    out = await provider.handle_tool_call("mem2_add", {"content": "dup"})
    data = json.loads(out)
    assert data["result"] == "deduped"


@pytest.mark.asyncio
async def test_handle_tool_call_search_missing_query_errors():
    fake = _fake_http()
    provider = await _init(JiuwenMemoryProvider(tenant_id="t1", user_id="u1"), fake)
    out = await provider.handle_tool_call("mem2_search", {})
    assert "error" in json.loads(out)


@pytest.mark.asyncio
async def test_handle_tool_call_add_missing_content_errors():
    fake = _fake_http()
    provider = await _init(JiuwenMemoryProvider(tenant_id="t1", user_id="u1"), fake)
    out = await provider.handle_tool_call("mem2_add", {})
    assert "error" in json.loads(out)


@pytest.mark.asyncio
async def test_handle_tool_call_unknown_tool_errors():
    fake = _fake_http()
    provider = await _init(JiuwenMemoryProvider(tenant_id="t1", user_id="u1"), fake)
    out = await provider.handle_tool_call("mem2_bogus", {})
    assert "Unknown tool" in json.loads(out)["error"]


@pytest.mark.asyncio
async def test_handle_tool_call_uninitialized_errors():
    provider = JiuwenMemoryProvider(tenant_id="t1", user_id="u1")  # not initialized
    out = await provider.handle_tool_call("mem2_search", {"query": "x"})
    assert "not initialized" in json.loads(out)["error"]


# ---------------------------------------------------------------------------
# Circuit breaker — observed purely through HTTP call counts (no private state)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_breaker_opens_after_repeated_failures():
    """Once the breaker trips, further calls must NOT reach http again.

    The threshold is an implementation detail, so we drive failures until the
    call count stops growing (the breaker short-circuits) or we hit a safe
    upper bound. No private constants or fields are read.
    """
    fake = _fake_http(post_side_effect=Exception("server down"))
    provider = await _init(JiuwenMemoryProvider(), fake)

    upper = 20  # well above any plausible threshold; guards against infinite loop
    for _ in range(upper):
        await provider.handle_tool_call("mem2_search", {"query": "q"})

    before = fake.post.await_count
    # The breaker is open by now (threshold is far below ``upper``); subsequent
    # calls must short-circuit without a new POST.
    await provider.handle_tool_call("mem2_search", {"query": "q"})
    assert fake.post.await_count == before


@pytest.mark.asyncio
async def test_breaker_success_resets_counter():
    """After a success, the failure counter resets: subsequent calls resume
    hitting http (the breaker is closed again)."""
    fake = _fake_http()
    provider = await _init(JiuwenMemoryProvider(), fake)

    # Two failures (below threshold) then a success.
    fake.post = AsyncMock(
        side_effect=[Exception("x"), Exception("x"), _mock_response({"hits": []})]
    )
    await provider.handle_tool_call("mem2_search", {"query": "q"})  # fail
    await provider.handle_tool_call("mem2_search", {"query": "q"})  # fail
    out = await provider.handle_tool_call("mem2_search", {"query": "q"})  # success
    assert json.loads(out)["count"] == 0

    # Counter reset: a fresh success call still reaches http (breaker closed),
    # and returns its payload — proving we did not short-circuit.
    fake.post = AsyncMock(
        return_value=_mock_response(
            {"hits": [{"item_id": "h1", "content": "c1", "score": 0.9}]}
        )
    )
    out = await provider.handle_tool_call("mem2_search", {"query": "q"})
    assert json.loads(out)["count"] == 1


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shutdown_closes_client_and_clears_state():
    fake = _fake_http()
    provider = await _init(JiuwenMemoryProvider(), fake)
    await provider.shutdown()
    fake.aclose.assert_awaited_once()
    assert provider.is_initialized is False


# ===========================================================================
# SDK mode — agent-memory kernel fully mocked (no real engine, no HTTP)
# ===========================================================================
#
# SDK backend does ``from api import assemble`` plus a few type imports at
# initialize() time. We inject fakes into ``sys.modules`` so no real
# agent-memory install is needed — the backend exercises its own logic (scope
# mapping, write/recall argument shaping, result parsing, sync_turn behavior,
# error mapping) against a mock LocalMemoryAPI.

import asyncio
import sys
import types

from dataclasses import dataclass, field


# ---- fake agent-memory type surface -------------------------------------- #


@dataclass
class _FakeScope:
    org: str = ""
    space: str = ""
    user: str = ""
    agent: str = ""
    session: str = ""


class _FakeModality:
    TEXT = "text"


@dataclass
class _FakeContext:
    scope: _FakeScope = field(default_factory=_FakeScope)
    extensions: dict = field(default_factory=dict)


class _FakeDisclosureLevel:
    L0 = "l0"
    L1 = "l1"
    L2 = "l2"
    ADAPTIVE = "adaptive"


@dataclass
class _FakeRetrievedItem:
    unit_id: str = ""
    content: str = ""
    score: float = 0.0


@dataclass
class _FakeRetrievalResult:
    items: list = field(default_factory=list)


@dataclass
class _FakeMemoryUnit:
    id: str = ""
    content: str = ""
    tier: Any = None


class _FakeTier:
    def __init__(self, value):
        self.value = value


class _FakeConfig:
    """Stand-in for agent-memory's config.Config."""
    @classmethod
    def from_dict(cls, data):
        return cls()


def _build_fake_mem_modules(local_api):
    """Build fake jiuwen_memory.* modules for sys.modules injection.

    Mirrors the real package layout: top-level ``jiuwen_memory`` package with
    ``jiuwen_memory.api`` / ``jiuwen_memory.common.type_def`` /
    ``jiuwen_memory.retrieval`` / ``jiwen_memory.config`` submodules.
    ``jiuwen_memory.api.assemble`` returns ``local_api`` (the fake
    LocalMemoryAPI); tests configure local_api's add/search and assert on
    call_args.
    """
    api_mod = types.ModuleType("jiuwen_memory.api")
    api_mod.assemble = MagicMock(return_value=local_api)

    ctd_pkg = types.ModuleType("jiuwen_memory.common.type_def")
    ctd_pkg.Scope = _FakeScope
    ctd_pkg.Modality = _FakeModality
    ctd_pkg.Context = _FakeContext
    ctd_pkg.MemoryUnit = _FakeMemoryUnit

    common_pkg = types.ModuleType("jiuwen_memory.common")
    common_pkg.__path__ = []  # mark as package

    retrieval_pkg = types.ModuleType("jiuwen_memory.retrieval.types")
    retrieval_pkg.__path__ = []
    retrieval_pkg.DisclosureLevel = _FakeDisclosureLevel
    retrieval_pkg.RetrievedItem = _FakeRetrievedItem
    retrieval_pkg.RetrievalResult = _FakeRetrievalResult

    config_mod = types.ModuleType("jiuwen_memory.config")
    config_mod.Config = _FakeConfig

    top_pkg = types.ModuleType("jiuwen_memory")
    top_pkg.__path__ = []

    return {
        "jiuwen_memory": top_pkg,
        "jiuwen_memory.api": api_mod,
        "jiuwen_memory.common": common_pkg,
        "jiuwen_memory.common.type_def": ctd_pkg,
        "jiuwen_memory.retrieval.types": retrieval_pkg,
        "jiuwen_memory.config": config_mod,
    }


@pytest.fixture
def fake_kernel():
    """Yield (api_mod, local_api) with agent-memory modules injected.

    ``api_mod.assemble`` is the MagicMock the backend calls via
    ``from api import assemble``; it returns ``local_api`` (the fake
    LocalMemoryAPI). Tests configure local_api.add/search and assert on
    call_args, and can assert on api_mod.assemble to check config flow.
    """
    local_api = MagicMock()
    local_api.add = MagicMock(return_value=[])
    local_api.search = MagicMock(return_value=_FakeRetrievalResult(items=[]))
    modules = _build_fake_mem_modules(local_api)
    api_mod = modules["jiuwen_memory.api"]
    saved = {k: sys.modules.get(k) for k in modules}
    sys.modules.update(modules)
    try:
        yield api_mod, local_api
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


# ---- construction & mode ------------------------------------------------ #


def test_sdk_mode_selected():
    provider = JiuwenMemoryProvider(mode="sdk", tenant_id="t", user_id="u")
    assert provider.mode == "sdk"
    assert provider.is_available() is True     # SDK always available in principle


def test_sdk_mode_case_insensitive():
    assert JiuwenMemoryProvider(mode="SDK").mode == "sdk"


def test_invalid_mode_raises():
    with pytest.raises(ValueError):
        JiuwenMemoryProvider(mode="bogus")


# ---- initialize --------------------------------------------------------- #


@pytest.mark.asyncio
async def test_sdk_initialize_calls_assemble_with_config(fake_kernel):
    api_mod, local_api = fake_kernel
    provider = JiuwenMemoryProvider(
        mode="sdk", tenant_id="t1", user_id="u1", config_dict={"globals": {}}
    )
    await provider.initialize()
    assert provider.is_initialized is True
    api_mod.assemble.assert_called_once()
    _, kwargs = api_mod.assemble.call_args
    assert "config" in kwargs


@pytest.mark.asyncio
async def test_sdk_initialize_without_config_uses_default(fake_kernel):
    api_mod, local_api = fake_kernel
    provider = JiuwenMemoryProvider(mode="sdk", tenant_id="t1", user_id="u1")
    await provider.initialize()
    assert provider.is_initialized is True
    assert api_mod.assemble.call_args.kwargs.get("config") is None


async def _init_sdk(provider, fake_kernel):
    """Initialize an SDK provider under the fake agent-memory modules."""
    api_mod, local_api = fake_kernel
    await provider.initialize()
    return provider


# ---- write path (add) --------------------------------------------------- #


@pytest.mark.asyncio
async def test_sdk_add_passes_content_scope_tags_metadata(fake_kernel):
    api_mod, local_api = fake_kernel
    local_api.add = MagicMock(
        return_value=[_FakeMemoryUnit(id="id-1", content="a fact", tier=_FakeTier("semantic"))]
    )
    provider = await _init_sdk(
        JiuwenMemoryProvider(mode="sdk", tenant_id="org1", user_id="alice", infer_turns=False),
        fake_kernel,
    )

    out = await provider.handle_tool_call("mem2_add", {"content": "a fact", "tags": ["x"], "infer": True})
    data = json.loads(out)
    assert data["result"] == "stored"
    assert data["item_id"] == "id-1"
    assert data["tier"] == "semantic"

    args, kwargs = local_api.add.call_args
    assert args[0] == "a fact"                       # content
    scope = args[1]
    assert scope.org == "org1" and scope.user == "alice"
    assert kwargs["tags"] == ["x"]
    assert kwargs["metadata"] == {"infer": "true"}   # infer flag → metadata
    assert kwargs["identity"] is scope               # identity == target scope


@pytest.mark.asyncio
async def test_sdk_add_without_infer_omits_metadata(fake_kernel):
    api_mod, local_api = fake_kernel
    local_api.add = MagicMock(return_value=[_FakeMemoryUnit(id="id-1")])
    provider = await _init_sdk(
        JiuwenMemoryProvider(mode="sdk", tenant_id="t", user_id="u", infer_turns=False),
        fake_kernel,
    )
    await provider.handle_tool_call("mem2_add", {"content": "raw text"})
    assert local_api.add.call_args.kwargs["metadata"] is None


@pytest.mark.asyncio
async def test_sdk_add_deduped_when_write_returns_empty(fake_kernel):
    api_mod, local_api = fake_kernel
    local_api.add = MagicMock(return_value=[])     # all deduped (infer path)
    provider = await _init_sdk(
        JiuwenMemoryProvider(mode="sdk", tenant_id="t", user_id="u", infer_turns=False),
        fake_kernel,
    )
    out = await provider.handle_tool_call("mem2_add", {"content": "dup"})
    data = json.loads(out)
    assert data["result"] == "deduped"
    assert data["item_id"] is None


@pytest.mark.asyncio
async def test_sdk_add_missing_content_errors(fake_kernel):
    api_mod, local_api = fake_kernel
    provider = await _init_sdk(
        JiuwenMemoryProvider(mode="sdk", tenant_id="t", user_id="u", infer_turns=False),
        fake_kernel,
    )
    out = await provider.handle_tool_call("mem2_add", {})
    assert "error" in json.loads(out)


# ---- read path (search) ------------------------------------------------- #


@pytest.mark.asyncio
async def test_sdk_search_passes_query_scope_top_k_disclosure(fake_kernel):
    api_mod, local_api = fake_kernel
    local_api.search = MagicMock(
        return_value=_FakeRetrievalResult(items=[
            _FakeRetrievedItem(unit_id="h1", content="c1", score=0.9),
        ])
    )
    provider = await _init_sdk(
        JiuwenMemoryProvider(mode="sdk", tenant_id="org1", user_id="alice", infer_turns=False),
        fake_kernel,
    )
    out = await provider.handle_tool_call("mem2_search", {"query": "Python", "top_k": 5})
    data = json.loads(out)
    assert data["count"] == 1
    assert data["results"][0]["content"] == "c1"
    assert data["results"][0]["item_id"] == "h1"

    args, kwargs = local_api.search.call_args
    assert args[0] == "Python"                       # query
    ctx = args[1]
    assert ctx.scope.org == "org1" and ctx.scope.user == "alice"
    assert kwargs["identity"] is ctx.scope
    assert kwargs["top_k"] == 5
    assert kwargs["disclosure"] == _FakeDisclosureLevel.L2   # full content parity


@pytest.mark.asyncio
async def test_sdk_search_top_k_capped(fake_kernel):
    api_mod, local_api = fake_kernel
    local_api.search = MagicMock(return_value=_FakeRetrievalResult(items=[]))
    provider = await _init_sdk(
        JiuwenMemoryProvider(mode="sdk", infer_turns=False), fake_kernel
    )
    await provider.handle_tool_call("mem2_search", {"query": "q", "top_k": 9999})
    assert local_api.search.call_args.kwargs["top_k"] == 50   # _MAX_TOP_K


@pytest.mark.asyncio
async def test_sdk_search_empty_query_errors_without_recall(fake_kernel):
    api_mod, local_api = fake_kernel
    provider = await _init_sdk(
        JiuwenMemoryProvider(mode="sdk", infer_turns=False), fake_kernel
    )
    out = await provider.handle_tool_call("mem2_search", {"query": ""})
    assert "error" in json.loads(out)
    local_api.search.assert_not_called()


@pytest.mark.asyncio
async def test_sdk_search_failure_returns_empty(fake_kernel):
    api_mod, local_api = fake_kernel
    local_api.search = MagicMock(side_effect=RuntimeError("boom"))
    provider = await _init_sdk(
        JiuwenMemoryProvider(mode="sdk", infer_turns=False), fake_kernel
    )
    out = await provider.handle_tool_call("mem2_search", {"query": "q"})
    assert json.loads(out)["count"] == 0


# ---- prefetch ----------------------------------------------------------- #


@pytest.mark.asyncio
async def test_sdk_prefetch_formats_marked_block(fake_kernel):
    api_mod, local_api = fake_kernel
    local_api.search = MagicMock(
        return_value=_FakeRetrievalResult(items=[
            _FakeRetrievedItem(content="likes Python", score=0.8),
        ])
    )
    provider = await _init_sdk(
        JiuwenMemoryProvider(mode="sdk", infer_turns=False), fake_kernel
    )
    block = await provider.prefetch("Python")
    assert block.startswith("## Jiuwen Memory")
    assert "- likes Python" in block


# ---- sync_turn ---------------------------------------------------------- #


@pytest.mark.asyncio
async def test_sdk_sync_turn_stores_only_user_by_default(fake_kernel):
    api_mod, local_api = fake_kernel
    local_api.add = MagicMock(return_value=[_FakeMemoryUnit(id="i")])
    provider = await _init_sdk(
        JiuwenMemoryProvider(mode="sdk", tenant_id="t", user_id="u", infer_turns=False),
        fake_kernel,
    )
    await provider.sync_turn("user says", "assistant says")
    assert local_api.add.call_count == 1          # only user turn
    assert local_api.add.call_args.args[0] == "user says"


@pytest.mark.asyncio
async def test_sdk_sync_turn_saves_assistant_when_enabled(fake_kernel):
    api_mod, local_api = fake_kernel
    local_api.add = MagicMock(return_value=[_FakeMemoryUnit(id="i")])
    provider = await _init_sdk(
        JiuwenMemoryProvider(
            mode="sdk", tenant_id="t", user_id="u",
            infer_turns=False, save_assistant_turns=True,
        ),
        fake_kernel,
    )
    await provider.sync_turn("user says", "assistant says")
    assert local_api.add.call_count == 2
    contents = [c.args[0] for c in local_api.add.call_args_list]
    assert contents == ["user says", "assistant says"]


@pytest.mark.asyncio
async def test_sdk_sync_turn_infer_flag_respected(fake_kernel):
    api_mod, local_api = fake_kernel
    local_api.add = MagicMock(return_value=[_FakeMemoryUnit(id="i")])
    provider = await _init_sdk(
        JiuwenMemoryProvider(mode="sdk", tenant_id="t", user_id="u", infer_turns=False),
        fake_kernel,
    )
    await provider.sync_turn("user says", "assistant says", infer=True)
    assert local_api.add.call_args.kwargs["metadata"] == {"infer": "true"}


# ---- tool dispatch edges ------------------------------------------------ #


@pytest.mark.asyncio
async def test_sdk_unknown_tool_errors(fake_kernel):
    api_mod, local_api = fake_kernel
    provider = await _init_sdk(
        JiuwenMemoryProvider(mode="sdk", infer_turns=False), fake_kernel
    )
    out = await provider.handle_tool_call("mem2_bogus", {})
    assert "Unknown tool" in json.loads(out)["error"]


@pytest.mark.asyncio
async def test_sdk_uninitialized_errors(fake_kernel):
    provider = JiuwenMemoryProvider(mode="sdk", tenant_id="t", user_id="u")  # not initialized
    out = await provider.handle_tool_call("mem2_search", {"query": "x"})
    assert "not initialized" in json.loads(out)["error"]


# ---- lifecycle ---------------------------------------------------------- #


@pytest.mark.asyncio
async def test_sdk_shutdown_clears_state(fake_kernel):
    api_mod, local_api = fake_kernel
    provider = await _init_sdk(
        JiuwenMemoryProvider(mode="sdk", infer_turns=False), fake_kernel
    )
    await provider.shutdown()
    assert provider.is_initialized is False
