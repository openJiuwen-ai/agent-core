# coding: utf-8

from unittest.mock import MagicMock, patch

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.harness.tools.web_tools import (
    WebFetchWebpageTool,
    WebFreeSearchTool,
    WebPaidSearchTool,
    _http_request,
    create_web_tools,
    is_free_search_enabled,
)


class TestWebFreeSearchTool:
    @pytest.fixture
    def tool(self):
        return WebFreeSearchTool(language="cn")

    @pytest.mark.asyncio
    async def test_invoke_empty_query(self, tool):
        result = await tool.invoke({"query": ""})
        assert "[ERROR]: query cannot be empty." in result

    @pytest.mark.asyncio
    async def test_invoke_duckduckgo_success(self, tool):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = """
        <a class="result__a" href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fpage1">Example Title 1</a>
        <a class="result__snippet" href="#">Example snippet text 1</a>
        """
        mock_response.raise_for_status = MagicMock()

        with patch("openjiuwen.harness.tools.web_tools._http_request", return_value=mock_response):
            result = await tool.invoke({"query": "test query", "max_results": 5})

        assert "Free search results (DuckDuckGo)" in result
        assert "Example Title 1" in result

    @pytest.mark.asyncio
    async def test_invoke_bing_fallback_success(self, tool):
        ddg_response = MagicMock()
        ddg_response.status_code = 500
        ddg_response.text = ""

        jina_response = MagicMock()
        jina_response.status_code = 500
        jina_response.text = ""

        bing_response = MagicMock()
        bing_response.status_code = 200
        bing_response.text = """
        <html>
        <main aria-label="Search Results">
          <li class="b_algo">
            <h2><a href="https://example.com/page1">Bing Result 1</a></h2>
            <div class="b_caption"><p>Bing snippet 1</p></div>
          </li>
        </main>
        </html>
        """
        bing_response.raise_for_status = MagicMock()

        def mock_http_request(method, url, **kwargs):
            if "r.jina.ai" in url:
                return jina_response
            if "duckduckgo.com" in url:
                return ddg_response
            return bing_response

        with patch("openjiuwen.harness.tools.web_tools._http_request", side_effect=mock_http_request):
            result = await tool.invoke({"query": "test query", "max_results": 5})

        assert "Free search results (Bing)" in result
        assert "Bing Result 1" in result

    @pytest.mark.asyncio
    async def test_ddg_toggle_disables_duckduckgo_engines(self, tool, monkeypatch):
        monkeypatch.setenv("FREE_SEARCH_DDG_ENABLED", "false")
        monkeypatch.setenv("FREE_SEARCH_BING_ENABLED", "true")

        bing_response = MagicMock()
        bing_response.status_code = 200
        bing_response.text = """
        <html>
        <main aria-label="Search Results">
          <li class="b_algo">
            <h2><a href="https://example.com/page1">Bing Result 1</a></h2>
            <div class="b_caption"><p>Bing snippet 1</p></div>
          </li>
        </main>
        </html>
        """
        bing_response.raise_for_status = MagicMock()

        with patch("openjiuwen.harness.tools.web_tools._http_request", return_value=bing_response) as mock_request:
            result = await tool.invoke({"query": "test query", "max_results": 5})

        requested_urls = [call.args[1] for call in mock_request.call_args_list]
        assert all("duckduckgo.com" not in url for url in requested_urls)
        assert all("r.jina.ai" not in url for url in requested_urls)
        assert "Free search results (Bing)" in result

    @pytest.mark.asyncio
    async def test_all_free_search_engines_disabled_returns_error(self, tool, monkeypatch):
        monkeypatch.setenv("FREE_SEARCH_DDG_ENABLED", "false")
        monkeypatch.setenv("FREE_SEARCH_BING_ENABLED", "false")

        result = await tool.invoke({"query": "test query", "max_results": 5})

        assert "[ERROR]: free search failed:" in result
        assert "all free search engines are disabled" in result

    def test_create_web_tools_omits_free_search_when_all_engines_disabled(self, monkeypatch):
        monkeypatch.setenv("FREE_SEARCH_DDG_ENABLED", "false")
        monkeypatch.setenv("FREE_SEARCH_BING_ENABLED", "false")

        tools = create_web_tools(language="cn")

        assert is_free_search_enabled() is False
        assert [tool.card.name for tool in tools] == ["fetch_webpage"]

    def test_create_web_tools_restores_free_search_when_any_engine_enabled(self, monkeypatch):
        monkeypatch.setenv("FREE_SEARCH_DDG_ENABLED", "false")
        monkeypatch.setenv("FREE_SEARCH_BING_ENABLED", "true")

        tools = create_web_tools(language="cn")

        assert is_free_search_enabled() is True
        assert [tool.card.name for tool in tools] == ["free_search", "fetch_webpage"]

    @pytest.mark.asyncio
    async def test_best_effort_returns_low_quality_bing_rows(self, tool):
        ddg_response = MagicMock()
        ddg_response.status_code = 500
        ddg_response.text = ""

        jina_response = MagicMock()
        jina_response.status_code = 500
        jina_response.text = ""

        bing_response = MagicMock()
        bing_response.status_code = 200
        bing_response.text = """
        <html>
        <main aria-label="Search Results">
          <li class="b_algo">
            <h2><a href="https://www.zhihu.com/question/1">亚洲 - 知乎</a></h2>
            <div class="b_caption"><p>知乎页面</p></div>
          </li>
        </main>
        </html>
        """
        bing_response.raise_for_status = MagicMock()

        def mock_http_request(method, url, **kwargs):
            if "r.jina.ai" in url:
                return jina_response
            if "duckduckgo.com" in url:
                return ddg_response
            return bing_response

        with patch("openjiuwen.harness.tools.web_tools._http_request", side_effect=mock_http_request):
            result = await tool.invoke({"query": "亚洲新闻 最新", "max_results": 5})

        assert "Free search results (Bing)" in result
        assert "亚洲 - 知乎" in result

    @pytest.mark.asyncio
    async def test_stream_not_supported(self, tool):
        with pytest.raises(BaseError) as exc_info:
            async for _ in tool.stream({"query": "test"}):
                pass
        assert exc_info.value.status == StatusCode.TOOL_STREAM_NOT_SUPPORTED

    def test_http_request_applies_configured_search_proxy(self, monkeypatch):
        proxy_url = "http://username:password@proxyhk.huawei.com:8080"
        monkeypatch.setenv("FREE_SEARCH_PROXY_URL", proxy_url)
        monkeypatch.delenv("NO_PROXY", raising=False)
        monkeypatch.delenv("no_proxy", raising=False)
        response = MagicMock()

        with patch("requests.get", return_value=response) as mock_get:
            assert _http_request("GET", "https://www.bing.com/search?q=test") is response

        assert mock_get.call_args.kwargs["proxies"] == {"http": proxy_url, "https": proxy_url}

    def test_http_request_bypasses_configured_search_proxy_for_no_proxy_hosts(self, monkeypatch):
        monkeypatch.setenv("FREE_SEARCH_PROXY_URL", "http://username:password@proxyhk.huawei.com:8080")
        monkeypatch.delenv("NO_PROXY", raising=False)
        monkeypatch.delenv("no_proxy", raising=False)
        response = MagicMock()

        with patch("requests.get", return_value=response) as mock_get:
            assert _http_request("GET", "https://service.huawei.com/path") is response

        assert "proxies" not in mock_get.call_args.kwargs


class TestWebPaidSearchTool:
    @pytest.fixture
    def tool(self):
        return WebPaidSearchTool(language="cn")

    @pytest.mark.asyncio
    async def test_invoke_invalid_provider(self, tool):
        result = await tool.invoke({"query": "test", "provider": "invalid"})
        assert "[ERROR]: provider must be one of" in result

    @pytest.mark.asyncio
    async def test_invoke_bocha_success(self, tool):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": {
                "summary": "Bocha summary answer.",
                "webPages": {"value": [{"url": "https://example.com/page1"}]},
            }
        }
        mock_response.raise_for_status = MagicMock()

        with patch.dict("os.environ", {"BOCHA_API_KEY": "test-bocha-key"}):
            with patch("openjiuwen.harness.tools.web_tools._http_request", return_value=mock_response):
                result = await tool.invoke({"query": "test query", "provider": "bocha"})

        assert "Paid search provider: bocha" in result
        assert "Bocha summary answer." in result
        assert "https://example.com/page1" in result

    @pytest.mark.asyncio
    async def test_invoke_auto_provider_prefers_bocha(self, tool):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": {"summary": "Bocha auto summary.", "webPages": {"value": [{"url": "https://example.com/bocha"}]}}}
        mock_response.raise_for_status = MagicMock()

        with patch.dict("os.environ", {"BOCHA_API_KEY": "test-key"}):
            with patch("openjiuwen.harness.tools.web_tools._http_request", return_value=mock_response):
                result = await tool.invoke({"query": "test query", "provider": "auto"})

        assert "Paid search provider: bocha" in result

    @pytest.mark.asyncio
    async def test_invoke_auto_provider_fallback(self, tool):
        bocha_response = MagicMock()
        bocha_response.raise_for_status = MagicMock(side_effect=Exception("Bocha error"))

        perplexity_response = MagicMock()
        perplexity_response.raise_for_status = MagicMock(side_effect=Exception("PPLX error"))

        serper_response = MagicMock()
        serper_response.status_code = 200
        serper_response.json.return_value = {"organic": [{"link": "https://example.com/fallback"}]}
        serper_response.raise_for_status = MagicMock()

        def mock_http_request(method, url, **kwargs):
            if "api.bocha.cn" in url:
                return bocha_response
            if "perplexity.ai" in url:
                return perplexity_response
            return serper_response

        with patch.dict("os.environ", {"BOCHA_API_KEY": "x", "PERPLEXITY_API_KEY": "x", "SERPER_API_KEY": "x"}):
            with patch("openjiuwen.harness.tools.web_tools._http_request", side_effect=mock_http_request):
                result = await tool.invoke({"query": "test query", "provider": "auto"})

        assert "Paid search provider: serper" in result


class TestWebFetchWebpageTool:
    @pytest.fixture
    def tool(self):
        return WebFetchWebpageTool(language="cn")

    @pytest.mark.asyncio
    async def test_invoke_basic_html_extracts_main_content(self, tool):
        response = MagicMock()
        response.status_code = 200
        response.url = "https://example.com/article"
        response.headers = {"Content-Type": "text/html; charset=utf-8"}
        response.content = b"<html><title>Title</title><body><nav>menu</nav><main><p>Main content paragraph.</p></main></body></html>"
        response.encoding = "utf-8"
        response.apparent_encoding = "utf-8"
        response.raise_for_status = MagicMock()

        with patch("openjiuwen.harness.tools.web_tools._http_request", return_value=response):
            result = await tool.invoke({"url": "https://example.com/article"})

        assert "Title: Title" in result
        assert "Main content paragraph." in result
        assert "menu" not in result

    @pytest.mark.asyncio
    async def test_invoke_max_chars_zero_disables_clipping(self, tool):
        response = MagicMock()
        response.status_code = 200
        response.url = "https://example.com/article"
        response.headers = {"Content-Type": "text/plain"}
        response.content = b"abcdefghij"
        response.encoding = "utf-8"
        response.apparent_encoding = "utf-8"
        response.raise_for_status = MagicMock()

        with patch("openjiuwen.harness.tools.web_tools._http_request", return_value=response):
            result = await tool.invoke({"url": "https://example.com/article", "max_chars": 0})

        assert "abcdefghij" in result
        assert "[truncated]" not in result

    def test_decode_response_text_prefers_non_mojibake(self):
        response = MagicMock()
        text = "【杭州24小时天气查询】".encode("utf-8")
        response.content = text
        response.headers = {"Content-Type": "text/html"}
        response.encoding = "cp1252"
        response.apparent_encoding = "cp1252"

        decoded = WebFetchWebpageTool._decode_response_text(response)
        assert "杭州" in decoded

    @pytest.mark.asyncio
    async def test_stream_not_supported(self, tool):
        with pytest.raises(BaseError) as exc_info:
            async for _ in tool.stream({"url": "https://example.com"}):
                pass
        assert exc_info.value.status == StatusCode.TOOL_STREAM_NOT_SUPPORTED
