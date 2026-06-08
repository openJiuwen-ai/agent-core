# coding: utf-8

import json

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.harness.tools.web import (
    WebFetchWebpageTool,
    WebFreeSearchTool,
    WebPaidSearchTool,
    create_web_tools,
    is_free_search_enabled,
    is_paid_search_enabled,
)
from openjiuwen.harness.tools.web._common import _resolve_proxy
from openjiuwen.harness.tools.web._decode import _decode_response_text
from openjiuwen.harness.tools.web._http import _read_capped

_REQUEST_PATCH_TARGET = "openjiuwen.harness.tools.web._http._request"


def _resp(status=200, body=b"", headers=None, final_url="https://example.com", truncated=False):
    """Build a fake ``_request`` return tuple."""
    return (status, headers or {"Content-Type": "text/html; charset=utf-8"}, body, final_url, truncated)


class _RequestRecorder:
    """Async stand-in for ``_request`` that records calls and dispatches via a handler."""

    def __init__(self, handler):
        self.handler = handler
        self.calls: list[dict] = []

    async def __call__(self, session, method, url, *, headers=None, json_body=None, timeout_seconds, max_bytes=None):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers,
                "json_body": json_body,
                "timeout_seconds": timeout_seconds,
                "max_bytes": max_bytes,
            }
        )
        return self.handler(method, url, json_body)


@pytest.fixture(autouse=True)
def clear_search_env(monkeypatch):
    for key in (
        "FREE_SEARCH_DDG_ENABLED",
        "FREE_SEARCH_BING_ENABLED",
        "WEB_PROXY_URL",
        "FREE_SEARCH_PROXY_URL",
        "NO_PROXY",
        "no_proxy",
        "BOCHA_API_KEY",
        "PERPLEXITY_API_KEY",
        "SERPER_API_KEY",
        "JINA_API_KEY",
        "PAID_SEARCH_PROVIDER",
        "WEB_PAID_SEARCH_PROVIDER",
        "PPLX_MODEL",
        "JINA_MODEL",
    ):
        monkeypatch.delenv(key, raising=False)


def _patch_request(monkeypatch, handler):
    recorder = _RequestRecorder(handler)
    monkeypatch.setattr(_REQUEST_PATCH_TARGET, recorder)
    return recorder


# --------------------------------------------------------------------------- #
# Free search
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_free_invoke_empty_query():
    tool = WebFreeSearchTool(language="cn")
    result = await tool.invoke({"query": ""})
    assert "[ERROR]: query cannot be empty." in result


@pytest.mark.asyncio
async def test_free_invoke_duckduckgo_success(monkeypatch):
    monkeypatch.setenv("FREE_SEARCH_DDG_ENABLED", "true")
    monkeypatch.setenv("FREE_SEARCH_BING_ENABLED", "false")
    html = (
        '<a class="result__a" href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fpage1">'
        "Example Title 1</a>"
        '<a class="result__snippet" href="#">Example snippet text 1</a>'
    )
    _patch_request(monkeypatch, lambda method, url, body: _resp(200, html.encode("utf-8")))

    tool = WebFreeSearchTool(language="cn")
    result = await tool.invoke({"query": "test query", "max_results": 5})

    assert "Free search results (DuckDuckGo)" in result
    assert "Example Title 1" in result
    assert "https://example.com/page1" in result


@pytest.mark.asyncio
async def test_free_invoke_bing_fallback_success(monkeypatch):
    monkeypatch.setenv("FREE_SEARCH_DDG_ENABLED", "true")
    monkeypatch.setenv("FREE_SEARCH_BING_ENABLED", "true")
    bing_html = """
    <html><main aria-label="Search Results">
      <li class="b_algo">
        <h2><a href="https://example.com/page1">Bing Result 1</a></h2>
        <div class="b_caption"><p>Bing snippet 1</p></div>
      </li>
    </main></html>
    """

    def handler(method, url, body):
        if "r.jina.ai" in url:
            return _resp(500, b"")
        if "duckduckgo.com" in url:
            return _resp(500, b"")
        return _resp(200, bing_html.encode("utf-8"))

    _patch_request(monkeypatch, handler)
    tool = WebFreeSearchTool(language="cn")
    result = await tool.invoke({"query": "test query", "max_results": 5})

    assert "Free search results (Bing)" in result
    assert "Bing Result 1" in result


@pytest.mark.asyncio
async def test_free_ddg_toggle_disables_duckduckgo_engines(monkeypatch):
    monkeypatch.setenv("FREE_SEARCH_DDG_ENABLED", "false")
    monkeypatch.setenv("FREE_SEARCH_BING_ENABLED", "true")
    bing_html = """
    <html><main aria-label="Search Results">
      <li class="b_algo">
        <h2><a href="https://example.com/page1">Bing Result 1</a></h2>
        <div class="b_caption"><p>Bing snippet 1</p></div>
      </li>
    </main></html>
    """
    recorder = _patch_request(monkeypatch, lambda method, url, body: _resp(200, bing_html.encode("utf-8")))

    tool = WebFreeSearchTool(language="cn")
    result = await tool.invoke({"query": "test query", "max_results": 5})

    requested = [call["url"] for call in recorder.calls]
    assert all("duckduckgo.com" not in url for url in requested)
    assert all("r.jina.ai" not in url for url in requested)
    assert "Free search results (Bing)" in result


@pytest.mark.asyncio
async def test_free_all_engines_disabled_returns_error(monkeypatch):
    monkeypatch.setenv("FREE_SEARCH_DDG_ENABLED", "false")
    monkeypatch.setenv("FREE_SEARCH_BING_ENABLED", "false")
    tool = WebFreeSearchTool(language="cn")
    result = await tool.invoke({"query": "test query", "max_results": 5})
    assert "[ERROR]: free search failed:" in result
    assert "all free search engines are disabled" in result


@pytest.mark.asyncio
async def test_free_best_effort_returns_low_quality_bing_rows(monkeypatch):
    monkeypatch.setenv("FREE_SEARCH_DDG_ENABLED", "true")
    monkeypatch.setenv("FREE_SEARCH_BING_ENABLED", "true")
    bing_html = """
    <html><main aria-label="Search Results">
      <li class="b_algo">
        <h2><a href="https://www.zhihu.com/question/1">亚洲 - 知乎</a></h2>
        <div class="b_caption"><p>知乎页面</p></div>
      </li>
    </main></html>
    """

    def handler(method, url, body):
        if "r.jina.ai" in url:
            return _resp(500, b"")
        if "duckduckgo.com" in url:
            return _resp(500, b"")
        return _resp(200, bing_html.encode("utf-8"))

    _patch_request(monkeypatch, handler)
    tool = WebFreeSearchTool(language="cn")
    result = await tool.invoke({"query": "亚洲新闻 最新", "max_results": 5})

    assert "Free search results (Bing)" in result
    assert "亚洲 - 知乎" in result


@pytest.mark.asyncio
async def test_free_invoke_param_coercion(monkeypatch):
    monkeypatch.setenv("FREE_SEARCH_DDG_ENABLED", "true")
    monkeypatch.setenv("FREE_SEARCH_BING_ENABLED", "false")
    html = '<a class="result__a" href="https://example.com/x">Title</a>'
    _patch_request(monkeypatch, lambda method, url, body: _resp(200, html.encode("utf-8")))

    tool = WebFreeSearchTool(language="cn")
    # Float-like and junk values must not raise; they fall back to defaults.
    for bad in ("8.0", "abc", None, 0):
        result = await tool.invoke({"query": "test", "max_results": bad, "timeout_seconds": bad})
        assert "Title" in result


@pytest.mark.asyncio
async def test_free_stream_not_supported():
    tool = WebFreeSearchTool(language="cn")
    with pytest.raises(BaseError) as exc_info:
        async for _ in tool.stream({"query": "test"}):
            pass
    assert exc_info.value.status == StatusCode.TOOL_STREAM_NOT_SUPPORTED


def test_ddg_snippet_not_misaligned():
    from openjiuwen.harness.tools.web.free_search import _parse_ddg_html

    # Middle result has no snippet; it must not steal the next result's snippet.
    html = (
        '<a class="result__a" href="https://a.com">T1</a><a class="result__snippet">S1</a>'
        '<a class="result__a" href="https://b.com">T2</a>'
        '<a class="result__a" href="https://c.com">T3</a><a class="result__snippet">S3</a>'
    )
    rows = _parse_ddg_html(html, 10)
    assert [r["url"] for r in rows] == ["https://a.com", "https://b.com", "https://c.com"]
    assert rows[0]["snippet"] == "S1"
    assert rows[1]["snippet"] == ""
    assert rows[2]["snippet"] == "S3"


# --------------------------------------------------------------------------- #
# create_web_tools
# --------------------------------------------------------------------------- #
def test_create_web_tools_omits_free_search_by_default():
    tools = create_web_tools(language="cn")
    assert is_free_search_enabled() is False
    assert [tool.card.name for tool in tools] == ["fetch_webpage"]


def test_create_web_tools_restores_free_search_when_any_engine_enabled(monkeypatch):
    monkeypatch.setenv("FREE_SEARCH_DDG_ENABLED", "false")
    monkeypatch.setenv("FREE_SEARCH_BING_ENABLED", "true")
    tools = create_web_tools(language="cn")
    assert is_free_search_enabled() is True
    assert [tool.card.name for tool in tools] == ["free_search", "fetch_webpage"]


def test_create_web_tools_prioritizes_paid_search_when_configured(monkeypatch):
    monkeypatch.setenv("BOCHA_API_KEY", "test-key")
    monkeypatch.setenv("FREE_SEARCH_DDG_ENABLED", "false")
    monkeypatch.setenv("FREE_SEARCH_BING_ENABLED", "true")
    tools = create_web_tools(language="cn")
    assert is_paid_search_enabled() is True
    assert [tool.card.name for tool in tools] == ["paid_search", "free_search", "fetch_webpage"]


# --------------------------------------------------------------------------- #
# Proxy resolution
# --------------------------------------------------------------------------- #
def test_resolve_proxy_applies_configured_proxy(monkeypatch):
    proxy_url = "http://username:password@proxyhk.huawei.com:8080"
    monkeypatch.setenv("FREE_SEARCH_PROXY_URL", proxy_url)
    assert _resolve_proxy("https://www.bing.com/search?q=test") == proxy_url


def test_resolve_proxy_bypasses_no_proxy_hosts(monkeypatch):
    monkeypatch.setenv("FREE_SEARCH_PROXY_URL", "http://username:password@proxyhk.huawei.com:8080")
    assert _resolve_proxy("https://service.huawei.com/path") is None


def test_resolve_proxy_bypasses_cidr_network(monkeypatch):
    # NO_PROXY entries may be CIDR networks; IP-literal hosts inside them go direct.
    monkeypatch.setenv("WEB_PROXY_URL", "http://gw.example.com:8080")
    monkeypatch.setenv("NO_PROXY", "10.0.0.0/8")
    assert _resolve_proxy("http://10.1.2.3/api") is None
    assert _resolve_proxy("http://11.0.0.1/api") == "http://gw.example.com:8080"


def test_web_proxy_url_precedence_and_legacy_fallback(monkeypatch):
    # Legacy FREE_SEARCH_PROXY_URL still works; WEB_PROXY_URL wins when both set.
    monkeypatch.setenv("FREE_SEARCH_PROXY_URL", "http://legacy.example.com:8080")
    assert _resolve_proxy("https://example.com") == "http://legacy.example.com:8080"
    monkeypatch.setenv("WEB_PROXY_URL", "http://new.example.com:8080")
    assert _resolve_proxy("https://example.com") == "http://new.example.com:8080"


# --------------------------------------------------------------------------- #
# Paid search
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_paid_invoke_invalid_provider():
    tool = WebPaidSearchTool(language="cn")
    result = await tool.invoke({"query": "test", "provider": "invalid"})
    assert "[ERROR]: provider must be one of" in result


@pytest.mark.asyncio
async def test_paid_invoke_bocha_success(monkeypatch):
    monkeypatch.setenv("BOCHA_API_KEY", "test-bocha-key")
    body = json.dumps(
        {
            "data": {
                "summary": "Bocha summary answer.",
                "webPages": {"value": [{"url": "https://example.com/page1"}]},
            }
        }
    ).encode("utf-8")
    _patch_request(monkeypatch, lambda method, url, json_body: _resp(200, body))

    tool = WebPaidSearchTool(language="cn")
    result = await tool.invoke({"query": "test query", "provider": "bocha"})

    assert "Paid search provider: bocha" in result
    assert "Bocha summary answer." in result
    assert "https://example.com/page1" in result


@pytest.mark.asyncio
async def test_paid_auto_prefers_perplexity(monkeypatch):
    monkeypatch.setenv("PERPLEXITY_API_KEY", "test-key")
    body = json.dumps(
        {"choices": [{"message": {"content": "PPLX auto answer"}}], "citations": ["https://example.com/pplx"]}
    ).encode("utf-8")
    _patch_request(monkeypatch, lambda method, url, json_body: _resp(200, body))

    tool = WebPaidSearchTool(language="cn")
    result = await tool.invoke({"query": "test query", "provider": "auto"})
    assert "Paid search provider: perplexity" in result


@pytest.mark.asyncio
async def test_paid_auto_fallback_to_serper(monkeypatch):
    for key in ("PERPLEXITY_API_KEY", "BOCHA_API_KEY", "JINA_API_KEY", "SERPER_API_KEY"):
        monkeypatch.setenv(key, "x")
    serper_body = json.dumps({"organic": [{"link": "https://example.com/fallback"}]}).encode("utf-8")

    def handler(method, url, json_body):
        if "perplexity.ai" in url or "api.bocha.cn" in url or "deepsearch.jina.ai" in url:
            return _resp(500, b"err")
        return _resp(200, serper_body)

    _patch_request(monkeypatch, handler)
    tool = WebPaidSearchTool(language="cn")
    result = await tool.invoke({"query": "test query", "provider": "auto"})
    assert "Paid search provider: serper" in result


@pytest.mark.asyncio
async def test_paid_timeout_clamp(monkeypatch):
    monkeypatch.setenv("SERPER_API_KEY", "test-key")
    serper_body = json.dumps({"organic": [{"link": "https://example.com/serper"}]}).encode("utf-8")
    recorder = _patch_request(monkeypatch, lambda method, url, json_body: _resp(200, serper_body))

    tool = WebPaidSearchTool(language="cn")
    result = await tool.invoke({"query": "test query", "provider": "serper", "timeout_seconds": 999})

    assert "Paid search provider: serper" in result
    assert recorder.calls[0]["timeout_seconds"] == 300


@pytest.mark.asyncio
async def test_paid_serper_retries_minimal_payload(monkeypatch):
    monkeypatch.setenv("SERPER_API_KEY", "test-key")
    good_body = json.dumps({"organic": [{"link": "https://example.com/serper"}]}).encode("utf-8")

    def handler(method, url, json_body):
        if json_body and "num" in json_body:
            return _resp(400, b"")
        return _resp(200, good_body)

    recorder = _patch_request(monkeypatch, handler)
    tool = WebPaidSearchTool(language="cn")
    result = await tool.invoke({"query": "test query", "provider": "serper"})

    assert "Paid search provider: serper" in result
    assert recorder.calls[0]["json_body"] == {"q": "test query", "num": 8}
    assert recorder.calls[1]["json_body"] == {"q": "test query"}


@pytest.mark.asyncio
async def test_paid_perplexity_uses_safe_model(monkeypatch):
    monkeypatch.setenv("PERPLEXITY_API_KEY", "test-key")
    monkeypatch.setenv("PPLX_MODEL", "sonar-deep-research")
    body = json.dumps(
        {"choices": [{"message": {"content": "PPLX answer"}}], "citations": ["https://example.com/pplx"]}
    ).encode("utf-8")
    recorder = _patch_request(monkeypatch, lambda method, url, json_body: _resp(200, body))

    tool = WebPaidSearchTool(language="cn")
    result = await tool.invoke({"query": "test query", "provider": "perplexity"})

    payload = recorder.calls[0]["json_body"]
    assert "Paid search provider: perplexity" in result
    assert payload["model"] == "sonar-pro"
    assert "search_context_size" not in payload
    assert payload["stream"] is False


@pytest.mark.asyncio
async def test_paid_jina_uses_low_effort(monkeypatch):
    monkeypatch.setenv("JINA_API_KEY", "test-key")
    monkeypatch.setenv("JINA_MODEL", "unsupported-slow-model")
    body = json.dumps(
        {"choices": [{"message": {"content": "Jina answer https://example.com/jina"}}]}
    ).encode("utf-8")
    recorder = _patch_request(monkeypatch, lambda method, url, json_body: _resp(200, body))

    tool = WebPaidSearchTool(language="cn")
    result = await tool.invoke({"query": "test query", "provider": "jina"})

    payload = recorder.calls[0]["json_body"]
    assert "Paid search provider: jina" in result
    assert payload["model"] == "jina-deepsearch-v1"
    assert payload["reasoning_effort"] == "low"
    assert "budget_tokens" not in payload


@pytest.mark.asyncio
async def test_paid_stream_not_supported():
    tool = WebPaidSearchTool(language="cn")
    with pytest.raises(BaseError) as exc_info:
        async for _ in tool.stream({"query": "test"}):
            pass
    assert exc_info.value.status == StatusCode.TOOL_STREAM_NOT_SUPPORTED


# --------------------------------------------------------------------------- #
# Fetch webpage
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_fetch_basic_html_extracts_main_content(monkeypatch):
    html = (
        b"<html><title>Title</title><body><nav>menu</nav>"
        b"<main><p>Main content paragraph.</p></main></body></html>"
    )
    _patch_request(
        monkeypatch,
        lambda method, url, body: _resp(
            200, html, headers={"Content-Type": "text/html; charset=utf-8"}, final_url="https://example.com/article"
        ),
    )

    tool = WebFetchWebpageTool(language="cn")
    result = await tool.invoke({"url": "https://example.com/article"})

    assert "Title: Title" in result
    assert "Main content paragraph." in result
    assert "menu" not in result


@pytest.mark.asyncio
async def test_fetch_max_chars_zero(monkeypatch):
    _patch_request(
        monkeypatch,
        lambda method, url, body: _resp(200, b"abcdefghij", headers={"Content-Type": "text/plain"}),
    )
    tool = WebFetchWebpageTool(language="cn")
    result = await tool.invoke({"url": "https://example.com/article", "max_chars": 0})
    assert "abcdefghij" in result
    assert "[truncated]" not in result


@pytest.mark.asyncio
async def test_fetch_param_coercion(monkeypatch):
    _patch_request(
        monkeypatch,
        lambda method, url, body: _resp(200, b"hello world", headers={"Content-Type": "text/plain"}),
    )
    tool = WebFetchWebpageTool(language="cn")
    for bad in ("abc", "20000.0", None):
        result = await tool.invoke({"url": "https://example.com", "max_chars": bad, "timeout_seconds": bad})
        assert "hello world" in result


@pytest.mark.asyncio
async def test_fetch_byte_cap_truncation_surfaced(monkeypatch):
    _patch_request(
        monkeypatch,
        lambda method, url, body: _resp(
            200, b"partial body content", headers={"Content-Type": "text/plain"}, truncated=True
        ),
    )
    tool = WebFetchWebpageTool(language="cn")
    result = await tool.invoke({"url": "https://example.com"})
    assert "partial body content" in result
    assert "truncated: response exceeded byte limit" in result


@pytest.mark.asyncio
async def test_fetch_byte_cap_independent_of_max_chars(monkeypatch):
    # A small max_chars must NOT shrink the wire-read byte ceiling: max_chars
    # caps the extracted output text, while byte_cap only guards against OOM.
    # Coupling them would truncate the input before main-text extraction runs.
    recorder = _patch_request(
        monkeypatch,
        lambda method, url, body: _resp(
            200, b"<html><body><p>body</p></body></html>", headers={"Content-Type": "text/html"}
        ),
    )
    tool = WebFetchWebpageTool(language="cn")
    await tool.invoke({"url": "https://example.com", "max_chars": 500})
    assert recorder.calls[0]["max_bytes"] == 8_000_000


@pytest.mark.asyncio
async def test_fetch_byte_cap_env_override(monkeypatch):
    # The hard ceiling stays configurable via the byte-cap env var.
    monkeypatch.setenv("MCP_FETCH_WEBPAGE_MAX_BYTES", "1234567")
    recorder = _patch_request(
        monkeypatch,
        lambda method, url, body: _resp(
            200, b"<html><body><p>body</p></body></html>", headers={"Content-Type": "text/html"}
        ),
    )
    tool = WebFetchWebpageTool(language="cn")
    await tool.invoke({"url": "https://example.com"})
    assert recorder.calls[0]["max_bytes"] == 1234567


@pytest.mark.asyncio
async def test_fetch_http_error_uses_fetch_scoped_message(monkeypatch):
    # A 5xx (not 401/403/429) must surface a fetch-scoped error, never the
    # web-search-engine wording borrowed from the search path.
    _patch_request(
        monkeypatch,
        lambda method, url, body: _resp(500, b"upstream boom", headers={"Content-Type": "text/plain"}),
    )
    tool = WebFetchWebpageTool(language="cn")
    result = await tool.invoke({"url": "https://example.com/x"})
    assert "[ERROR]: failed to fetch webpage:" in result
    assert "web page fetch failed" in result
    assert "search engine" not in result


def test_raise_fetch_http_error_code_and_threshold():
    from openjiuwen.harness.tools.web.fetch_webpage import _raise_fetch_http_error

    with pytest.raises(BaseError) as exc_info:
        _raise_fetch_http_error("https://example.com/x", 500, b"boom")
    assert exc_info.value.status == StatusCode.TOOL_WEB_FETCH_EXECUTION_ERROR
    # status < 400 must not raise
    _raise_fetch_http_error("https://example.com/x", 200, b"")


def test_decode_response_text_prefers_non_mojibake():
    raw = "【杭州24小时天气查询】".encode("utf-8")
    decoded = _decode_response_text(raw, content_type="text/html")
    assert "杭州" in decoded


@pytest.mark.asyncio
async def test_fetch_stream_not_supported():
    tool = WebFetchWebpageTool(language="cn")
    with pytest.raises(BaseError) as exc_info:
        async for _ in tool.stream({"url": "https://example.com"}):
            pass
    assert exc_info.value.status == StatusCode.TOOL_STREAM_NOT_SUPPORTED


# --------------------------------------------------------------------------- #
# Capped streaming reader
# --------------------------------------------------------------------------- #
class _FakeContent:
    def __init__(self, chunks):
        self._chunks = chunks

    async def iter_chunked(self, n):
        for chunk in self._chunks:
            yield chunk


class _FakeResp:
    def __init__(self, chunks):
        self.content = _FakeContent(chunks)


@pytest.mark.asyncio
async def test_read_capped_stops_at_max_bytes():
    resp = _FakeResp([b"a" * 100, b"b" * 100, b"c" * 100])
    body, truncated = await _read_capped(resp, max_bytes=150)
    assert truncated is True
    assert len(body) == 200  # stopped after the second chunk crossed 150


@pytest.mark.asyncio
async def test_read_capped_reads_all_when_unbounded():
    resp = _FakeResp([b"x" * 10, b"y" * 5])
    body, truncated = await _read_capped(resp, max_bytes=None)
    assert truncated is False
    assert body == b"x" * 10 + b"y" * 5


# --------------------------------------------------------------------------- #
# _request transport contract (against a fake aiohttp session)
# --------------------------------------------------------------------------- #
class _FakeReqCM:
    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self._resp

    async def __aexit__(self, *exc):
        return False


class _FakeRespFull:
    def __init__(self, status, headers, chunks, url):
        self.status = status
        self.headers = headers
        self.url = url
        self.content = _FakeContent(chunks)


class _FakeSession:
    def __init__(self, resp):
        self._resp = resp
        self.last_kwargs: dict | None = None

    def request(self, method, url, *, headers=None, json=None, proxy=None, proxy_auth=None, timeout=None):
        self.last_kwargs = {
            "method": method,
            "url": url,
            "headers": headers,
            "json": json,
            "proxy": proxy,
            "proxy_auth": proxy_auth,
            "timeout": timeout,
        }
        return _FakeReqCM(self._resp)


@pytest.mark.asyncio
async def test_request_transport_contract(monkeypatch):
    import aiohttp

    from openjiuwen.harness.tools.web._http import _request

    resp = _FakeRespFull(200, {"Content-Type": "text/html"}, [b"hello"], "https://final.example/x")
    session = _FakeSession(resp)

    status, headers, body, final_url, truncated = await _request(
        session,
        "get",
        "https://example.com",
        headers={"X": "1"},
        json_body={"a": 1},
        timeout_seconds=5,
    )

    assert status == 200
    assert headers == {"Content-Type": "text/html"}
    assert body == b"hello"
    assert final_url == "https://final.example/x"
    assert truncated is False
    assert session.last_kwargs["method"] == "GET"
    assert session.last_kwargs["json"] == {"a": 1}
    assert session.last_kwargs["proxy"] is None
    assert isinstance(session.last_kwargs["timeout"], aiohttp.ClientTimeout)


@pytest.mark.asyncio
async def test_request_extracts_inline_proxy_auth(monkeypatch):
    # aiohttp drops inline proxy credentials; _request must surface them as an
    # explicit BasicAuth and strip the userinfo from the proxy URL it passes.
    import aiohttp

    from openjiuwen.harness.tools.web._http import _request

    monkeypatch.setenv("WEB_PROXY_URL", "http://puser:ppass@gw.example.com:8080")
    resp = _FakeRespFull(200, {"Content-Type": "text/html"}, [b"ok"], "https://example.com/x")
    session = _FakeSession(resp)
    await _request(session, "GET", "https://target.example.com", timeout_seconds=5)
    assert session.last_kwargs["proxy"] == "http://gw.example.com:8080"
    assert session.last_kwargs["proxy_auth"] == aiohttp.BasicAuth("puser", "ppass")
