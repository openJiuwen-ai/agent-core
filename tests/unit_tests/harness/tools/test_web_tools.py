# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from unittest.mock import patch, MagicMock
import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.harness.tools.web_tools import (
    WebFreeSearchTool,
    WebPaidSearchTool,
    WebFetchWebpageTool,
)


class TestWebFreeSearchTool:
    """Test cases for WebFreeSearchTool."""

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
        mock_response.text = '''
        <html>
        <a class="result__a" href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fpage1">Example Title 1</a>
        <a class="result__snippet" href="#">Example snippet text 1</a>
        <a class="result__a" href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fpage2">Example Title 2</a>
        <a class="result__snippet" href="#">Example snippet text 2</a>
        </html>
        '''
        mock_response.raise_for_status = MagicMock()

        with patch("openjiuwen.harness.tools.web_tools._http_request", return_value=mock_response):
            result = await tool.invoke({"query": "test query", "max_results": 5})

        assert "Free search results" in result
        assert "test query" in result
        assert "Example Title 1" in result

    @pytest.mark.asyncio
    async def test_invoke_duckduckgo_challenge_page(self, tool):
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.text = '<html><script src="/anomaly.js"></script></html>'

        with patch("openjiuwen.harness.tools.web_tools._http_request", return_value=mock_response):
            result = await tool.invoke({"query": "test query"})

        assert "[ERROR]" in result

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
        bing_response.text = '''
        <html>
        <li class="b_algo">
            <h2><a href="https://example.com/page1">Bing Result 1</a></h2>
            <p>Bing snippet 1</p>
        </li>
        <li class="b_algo">
            <h2><a href="https://example.com/page2">Bing Result 2</a></h2>
            <p>Bing snippet 2</p>
        </li>
        </html>
        '''
        bing_response.raise_for_status = MagicMock()

        call_count = [0]

        def mock_http_request(method, url, **kwargs):
            call_count[0] += 1
            if "duckduckgo.com" in url:
                return ddg_response
            elif "r.jina.ai" in url:
                return jina_response
            elif "bing.com" in url:
                return bing_response
            return MagicMock(status_code=404)

        with patch("openjiuwen.harness.tools.web_tools._http_request", side_effect=mock_http_request):
            result = await tool.invoke({"query": "test query", "max_results": 5})

        assert "Free search results" in result
        assert "Bing" in result

    @pytest.mark.asyncio
    async def test_invoke_all_engines_failed(self, tool):
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = ""
        mock_response.raise_for_status = MagicMock(side_effect=Exception("Network error"))

        with patch("openjiuwen.harness.tools.web_tools._http_request", return_value=mock_response):
            result = await tool.invoke({"query": "test query"})

        assert "[ERROR]" in result

    @pytest.mark.asyncio
    async def test_invoke_max_results_boundary(self, tool):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '<html></html>'
        mock_response.raise_for_status = MagicMock()

        with patch("openjiuwen.harness.tools.web_tools._http_request", return_value=mock_response):
            result = await tool.invoke({"query": "test", "max_results": 25})

        assert "No search results" in result or "[ERROR]" in result

    @pytest.mark.asyncio
    async def test_invoke_timeout_boundary(self, tool):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '<html></html>'
        mock_response.raise_for_status = MagicMock()

        with patch("openjiuwen.harness.tools.web_tools._http_request", return_value=mock_response):
            result = await tool.invoke({"query": "test", "timeout_seconds": 100})

        assert "No search results" in result or "[ERROR]" in result

    @pytest.mark.asyncio
    async def test_stream_not_supported(self, tool):
        with pytest.raises(BaseError) as exc_info:
            async for _ in tool.stream({"query": "test"}):
                pass
        assert exc_info.value.status == StatusCode.TOOL_STREAM_NOT_SUPPORTED


class TestWebPaidSearchTool:
    """Test cases for WebPaidSearchTool."""

    @pytest.fixture
    def tool(self):
        return WebPaidSearchTool(language="cn")

    @pytest.mark.asyncio
    async def test_invoke_empty_query(self, tool):
        result = await tool.invoke({"query": ""})
        assert "[ERROR]: query cannot be empty." in result

    @pytest.mark.asyncio
    async def test_invoke_invalid_provider(self, tool):
        result = await tool.invoke({"query": "test", "provider": "invalid"})
        assert "[ERROR]: provider must be one of" in result

    @pytest.mark.asyncio
    async def test_invoke_jina_success(self, tool):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": "This is the answer from Jina. Visit https://example.com for more info."
                    }
                }
            ]
        }
        mock_response.raise_for_status = MagicMock()

        with patch.dict("os.environ", {"JINA_API_KEY": "test-jina-key"}):
            with patch("openjiuwen.harness.tools.web_tools._http_request", return_value=mock_response):
                result = await tool.invoke({"query": "test query", "provider": "jina"})

        assert "Paid search provider: jina" in result
        assert "This is the answer from Jina" in result

    @pytest.mark.asyncio
    async def test_invoke_jina_no_api_key(self, tool):
        with patch.dict("os.environ", {}, clear=True):
            with patch.dict("os.environ", {"JINA_API_KEY": ""}):
                result = await tool.invoke({"query": "test query", "provider": "jina"})

        assert "[ERROR]" in result

    @pytest.mark.asyncio
    async def test_invoke_serper_success(self, tool):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "organic": [
                {"link": "https://example.com/page1"},
                {"link": "https://example.com/page2"},
            ]
        }
        mock_response.raise_for_status = MagicMock()

        with patch.dict("os.environ", {"SERPER_API_KEY": "test-serper-key"}):
            with patch("openjiuwen.harness.tools.web_tools._http_request", return_value=mock_response):
                result = await tool.invoke({"query": "test query", "provider": "serper", "max_results": 5})

        assert "Paid search provider: serper" in result
        assert "https://example.com/page1" in result

    @pytest.mark.asyncio
    async def test_invoke_serper_no_api_key(self, tool):
        with patch.dict("os.environ", {}, clear=True):
            with patch.dict("os.environ", {"SERPER_API_KEY": ""}):
                result = await tool.invoke({"query": "test query", "provider": "serper"})

        assert "[ERROR]" in result

    @pytest.mark.asyncio
    async def test_invoke_perplexity_success(self, tool):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": "This is the answer from Perplexity."
                    }
                }
            ],
            "citations": [
                "https://example.com/citation1",
                "https://example.com/citation2",
            ]
        }
        mock_response.raise_for_status = MagicMock()

        with patch.dict("os.environ", {"PERPLEXITY_API_KEY": "test-perplexity-key"}):
            with patch("openjiuwen.harness.tools.web_tools._http_request", return_value=mock_response):
                result = await tool.invoke({"query": "test query", "provider": "perplexity", "max_results": 5})

        assert "Paid search provider: perplexity" in result
        assert "This is the answer from Perplexity" in result
        assert "https://example.com/citation1" in result

    @pytest.mark.asyncio
    async def test_invoke_perplexity_no_api_key(self, tool):
        with patch.dict("os.environ", {}, clear=True):
            with patch.dict("os.environ", {"PERPLEXITY_API_KEY": ""}):
                result = await tool.invoke({"query": "test query", "provider": "perplexity"})

        assert "[ERROR]" in result

    @pytest.mark.asyncio
    async def test_invoke_auto_provider_fallback(self, tool):
        perplexity_response = MagicMock()
        perplexity_response.status_code = 500
        perplexity_response.raise_for_status = MagicMock(side_effect=Exception("API error"))

        serper_response = MagicMock()
        serper_response.status_code = 200
        serper_response.json.return_value = {
            "organic": [{"link": "https://example.com/fallback"}]
        }
        serper_response.raise_for_status = MagicMock()

        call_count = [0]

        def mock_http_request(method, url, **kwargs):
            call_count[0] += 1
            if "perplexity.ai" in url:
                return perplexity_response
            elif "serper.dev" in url:
                return serper_response
            return MagicMock(status_code=404)

        with patch.dict("os.environ", {
            "PERPLEXITY_API_KEY": "test-key",
            "SERPER_API_KEY": "test-key"
        }):
            with patch("openjiuwen.harness.tools.web_tools._http_request", side_effect=mock_http_request):
                result = await tool.invoke({"query": "test query", "provider": "auto"})

        assert "Paid search provider: serper" in result

    @pytest.mark.asyncio
    async def test_invoke_all_providers_failed(self, tool):
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.raise_for_status = MagicMock(side_effect=Exception("Network error"))

        with patch.dict("os.environ", {
            "PERPLEXITY_API_KEY": "test-key",
            "SERPER_API_KEY": "test-key",
            "JINA_API_KEY": "test-key"
        }):
            with patch("openjiuwen.harness.tools.web_tools._http_request", return_value=mock_response):
                result = await tool.invoke({"query": "test query", "provider": "auto"})

        assert "[ERROR]: paid search failed" in result

    @pytest.mark.asyncio
    async def test_invoke_max_results_boundary(self, tool):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"organic": []}
        mock_response.raise_for_status = MagicMock()

        with patch.dict("os.environ", {"SERPER_API_KEY": "test-key"}):
            with patch("openjiuwen.harness.tools.web_tools._http_request", return_value=mock_response):
                result = await tool.invoke({"query": "test", "provider": "serper", "max_results": 25})

        assert "No usable result payload" in result

    @pytest.mark.asyncio
    async def test_stream_not_supported(self, tool):
        with pytest.raises(BaseError) as exc_info:
            async for _ in tool.stream({"query": "test"}):
                pass
        assert exc_info.value.status == StatusCode.TOOL_STREAM_NOT_SUPPORTED


class TestWebFetchWebpageTool:
    """Test cases for WebFetchWebpageTool."""

    @pytest.fixture
    def tool(self):
        return WebFetchWebpageTool(language="cn")

    @pytest.mark.asyncio
    async def test_invoke_empty_url(self, tool):
        result = await tool.invoke({"url": ""})
        assert "[ERROR]: url cannot be empty." in result

    @pytest.mark.asyncio
    async def test_invoke_success(self, tool):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.url = "https://example.com/page"
        mock_response.content = b"<html><head><title>Test Page</title></head><body><p>Hello World</p></body></html>"
        mock_response.text = "<html><head><title>Test Page</title></head><body><p>Hello World</p></body></html>"
        mock_response.headers = {"Content-Type": "text/html; charset=utf-8"}
        mock_response.encoding = "utf-8"
        mock_response.apparent_encoding = "utf-8"
        mock_response.raise_for_status = MagicMock()

        with patch("openjiuwen.harness.tools.web_tools._http_request", return_value=mock_response):
            result = await tool.invoke({"url": "https://example.com/page"})

        assert "URL: https://example.com/page" in result
        assert "Status: 200" in result
        assert "Title: Test Page" in result
        assert "Hello World" in result

    @pytest.mark.asyncio
    async def test_invoke_with_redirect_to_jina(self, tool):
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.headers = {"Content-Type": "text/html"}
        mock_response.raise_for_status = MagicMock()

        jina_response = MagicMock()
        jina_response.status_code = 200
        jina_response.content = b"Content from Jina Reader"
        jina_response.text = "Content from Jina Reader"
        jina_response.headers = {"Content-Type": "text/plain"}
        jina_response.encoding = "utf-8"
        jina_response.apparent_encoding = "utf-8"
        jina_response.raise_for_status = MagicMock()

        call_count = [0]

        def mock_http_request(method, url, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return mock_response
            return jina_response

        with patch("openjiuwen.harness.tools.web_tools._http_request", side_effect=mock_http_request):
            result = await tool.invoke({"url": "https://example.com/protected"})

        assert "Content from Jina Reader" in result

    @pytest.mark.asyncio
    async def test_invoke_non_html_content(self, tool):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.url = "https://example.com/data.json"
        mock_response.content = b'{"key": "value"}'
        mock_response.text = '{"key": "value"}'
        mock_response.headers = {"Content-Type": "application/json"}
        mock_response.encoding = "utf-8"
        mock_response.apparent_encoding = "utf-8"
        mock_response.raise_for_status = MagicMock()

        with patch("openjiuwen.harness.tools.web_tools._http_request", return_value=mock_response):
            result = await tool.invoke({"url": "https://example.com/data.json"})

        assert "URL: https://example.com/data.json" in result
        assert '{"key": "value"}' in result

    @pytest.mark.asyncio
    async def test_invoke_with_charset_detection(self, tool):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.url = "https://example.com/gb2312"
        mock_response.content = "中文内容".encode("gb2312")
        mock_response.text = "中文内容"
        mock_response.headers = {"Content-Type": "text/html; charset=gb2312"}
        mock_response.encoding = "gb2312"
        mock_response.apparent_encoding = "gb2312"
        mock_response.raise_for_status = MagicMock()

        with patch("openjiuwen.harness.tools.web_tools._http_request", return_value=mock_response):
            result = await tool.invoke({"url": "https://example.com/gb2312"})

        assert "Status: 200" in result

    @pytest.mark.asyncio
    async def test_invoke_request_failure(self, tool):
        with patch("openjiuwen.harness.tools.web_tools._http_request",
                   side_effect=Exception("Connection timeout")):
            result = await tool.invoke({"url": "https://example.com/timeout"})

        assert "[ERROR]: failed to fetch webpage" in result
        assert "Connection timeout" in result

    @pytest.mark.asyncio
    async def test_invoke_max_chars_boundary(self, tool):
        long_content = "x" * 60000
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.url = "https://example.com/long"
        mock_response.content = long_content.encode("utf-8")
        mock_response.text = long_content
        mock_response.headers = {"Content-Type": "text/plain"}
        mock_response.encoding = "utf-8"
        mock_response.apparent_encoding = "utf-8"
        mock_response.raise_for_status = MagicMock()

        with patch("openjiuwen.harness.tools.web_tools._http_request", return_value=mock_response):
            result = await tool.invoke({"url": "https://example.com/long", "max_chars": 500})

        assert "[truncated]" in result

    @pytest.mark.asyncio
    async def test_invoke_timeout_boundary(self, tool):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.url = "https://example.com/page"
        mock_response.content = b"content"
        mock_response.text = "content"
        mock_response.headers = {"Content-Type": "text/plain"}
        mock_response.encoding = "utf-8"
        mock_response.apparent_encoding = "utf-8"
        mock_response.raise_for_status = MagicMock()

        with patch("openjiuwen.harness.tools.web_tools._http_request", return_value=mock_response):
            result = await tool.invoke({"url": "https://example.com/page", "timeout_seconds": 150})

        assert "Status: 200" in result

    @pytest.mark.asyncio
    async def test_stream_not_supported(self, tool):
        with pytest.raises(BaseError) as exc_info:
            async for _ in tool.stream({"url": "https://example.com/page"}):
                pass
        assert exc_info.value.status == StatusCode.TOOL_STREAM_NOT_SUPPORTED
