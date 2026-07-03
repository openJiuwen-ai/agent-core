from __future__ import annotations

import sys
from types import SimpleNamespace

from openjiuwen.core.context_engine.processor.offloader.rule_compression import (
    ContentType,
    RuleContentRouter,
    RuleContext,
)
from openjiuwen.core.context_engine.processor.offloader.rule_compression.compressors.html_compressor import (
    HTMLExtractor,
    HTMLExtractorConfig,
    HtmlCompressor,
)


def _context() -> RuleContext:
    return RuleContext(
        max_tokens=1600,
        count_tokens=lambda text: max(len(text) // 3, 1),
        min_savings_ratio=0.0,
        html_min_content_chars=1,
    )


def test_html_extractor_delegates_body_and_metadata_extraction_to_trafilatura(monkeypatch):
    calls: dict[str, object] = {}

    def fake_extract(html: str, **kwargs):
        calls["extract_html"] = html
        calls["extract_kwargs"] = kwargs
        return "# Authentication\n\nUse an API key."

    def fake_extract_metadata(html: str, default_url=None):
        calls["metadata_html"] = html
        calls["metadata_url"] = default_url
        return SimpleNamespace(
            title="API Documentation - Authentication",
            author="OpenJiuwen",
            date="2026-06-17",
            sitename="Docs",
            description="Auth docs",
            categories=["api"],
            tags=["auth"],
        )

    monkeypatch.setitem(
        sys.modules,
        "trafilatura",
        SimpleNamespace(extract=fake_extract, extract_metadata=fake_extract_metadata),
    )

    html = "<html><head><title>x</title></head><body><main>body</main></body></html>"
    result = HTMLExtractor().extract(html, url="https://example.test/auth")

    assert result.extracted == "# Authentication\n\nUse an API key."
    assert result.original == html
    assert result.original_length == len(html)
    assert result.extracted_length == len(result.extracted)
    assert result.compression_ratio == result.extracted_length / result.original_length
    assert result.title == "API Documentation - Authentication"
    assert result.author == "OpenJiuwen"
    assert result.date == "2026-06-17"
    assert result.metadata == {
        "title": "API Documentation - Authentication",
        "author": "OpenJiuwen",
        "date": "2026-06-17",
        "sitename": "Docs",
        "description": "Auth docs",
        "categories": ["api"],
        "tags": ["auth"],
    }

    kwargs = calls["extract_kwargs"]
    assert kwargs["url"] == "https://example.test/auth"
    assert kwargs["output_format"] == "markdown"
    assert kwargs["include_links"] is True
    assert kwargs["include_images"] is False
    assert kwargs["include_tables"] is True
    assert kwargs["include_comments"] is False
    assert kwargs["include_formatting"] is True
    assert "config" in kwargs
    assert calls["metadata_url"] == "https://example.test/auth"


def test_html_extractor_converts_missing_extraction_to_empty_string(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "trafilatura",
        SimpleNamespace(extract=lambda html, **kwargs: None),
    )

    result = HTMLExtractor(HTMLExtractorConfig(extract_metadata=False)).extract("<p>short</p>")

    assert result.extracted == ""
    assert result.extracted_length == 0
    assert result.compression_ratio == 0
    assert result.reduction_percent == 100
    assert result.metadata == {}


def test_html_compressor_returns_trafilatura_markdown_when_it_saves_tokens(monkeypatch):
    def fake_extract(html: str, **kwargs):
        return "# Authentication\n\nAll API requests require an API key."

    monkeypatch.setitem(
        sys.modules,
        "trafilatura",
        SimpleNamespace(extract=fake_extract),
    )
    html = (
        "<!doctype html><html><head><title>Authentication</title>"
        "<script>track()</script></head><body><nav>Docs</nav>"
        "<main><article><h1>Authentication</h1>"
        "<p>All API requests require an API key.</p></article></main>"
        "<footer>Copyright</footer></body></html>"
    )

    result = HtmlCompressor().compress(html, _context())

    assert result.content == "# Authentication\n\nAll API requests require an API key."
    assert result.content_type == ContentType.HTML
    assert result.modified is True
    assert result.lossy is True
    assert result.details is not None
    assert result.details["extractor"] == "trafilatura"
    assert result.details["original_length"] == len(html)
    assert result.details["extracted_length"] == len(result.content)
    assert result.details["compression_ratio"] == len(result.content) / len(html)


def test_html_compressor_removes_style_and_script_before_trafilatura(monkeypatch):
    calls: dict[str, str] = {}

    def fake_extract(html: str, **kwargs):
        calls["html"] = html
        return "# Architecture\n\nCore design is preserved."

    monkeypatch.setitem(
        sys.modules,
        "trafilatura",
        SimpleNamespace(extract=fake_extract),
    )
    html = (
        "<!doctype html><html><head><title>Architecture</title>"
        "<style>.card { color: red; }</style>"
        "<script>track()</script></head><body>"
        "<main><h1>Architecture</h1><p>Core design is preserved.</p></main>"
        "</body></html>"
    )

    result = HtmlCompressor().compress(html, _context())

    assert result.modified is True
    assert ".card" not in calls["html"]
    assert "track()" not in calls["html"]
    assert "Core design is preserved." in calls["html"]


def test_router_detects_structural_html_fragments_without_html_or_body_tags():
    fragment = (
        "<article><h1>Authentication</h1>"
        "<p>All API requests require an API key.</p>"
        "<script>track()</script></article>"
    )

    assert RuleContentRouter().detect(fragment) == ContentType.HTML


def test_router_detects_numbered_html_fragment_that_starts_inside_css():
    fragment = "\n".join(
        [
            "     1\tbackground: #007bff;",
            "     2\tcolor: white;",
            "     3\tpadding: 10px 20px;",
            "     4\t}",
            "     5\t.flow-arrow {",
            "     6\tcolor: #007bff;",
            "     7\t}",
            "     8\t<strong>核心设计理念：</strong>",
            "     9\t<ul>",
            "    10\t<li><strong>两层架构</strong>：ContextEngine + SessionModelContext</li>",
            "    11\t<li><strong>插件化处理器</strong>：通过装饰器注册 ContextProcessor</li>",
            "    12\t</ul>",
        ]
    )

    assert RuleContentRouter().detect(fragment) == ContentType.HTML


def test_router_compresses_numbered_html_fragment_without_leading_css(monkeypatch):
    calls: dict[str, str] = {}

    def fake_extract(html: str, **kwargs):
        calls["html"] = html
        return "**核心设计理念：**\n\n- 两层架构"

    monkeypatch.setitem(
        sys.modules,
        "trafilatura",
        SimpleNamespace(extract=fake_extract),
    )
    fragment = "\n".join(
        [
            "     1\tbackground: #007bff;",
            "     2\tcolor: white;",
            "     3\tpadding: 10px 20px;",
            "     4\t}",
            "     5\ttable {",
            "     6\t  width: 100%;",
            "     7\t}",
            "     8\t<strong>核心设计理念：</strong>",
            "     9\t<ul>",
            "    10\t<li><strong>两层架构</strong>：ContextEngine + SessionModelContext</li>",
            "    11\t</ul>",
        ]
    )

    result = RuleContentRouter().compress(fragment, _context())

    assert result.content_type == ContentType.HTML
    assert result.modified is True
    assert "background:" not in calls["html"]
    assert "width: 100%" not in calls["html"]
    assert "<strong>核心设计理念" in calls["html"]


def test_router_detects_numbered_html_before_numbered_source():
    html = "\n".join(
        [
            "     1\t<!DOCTYPE html>",
            "     2\t<html>",
            "     3\t  <head><title>report</title></head>",
            "     4\t  <body>",
            "     5\t    <script>",
            "     6\t      import { html } from './report.js';",
            "     7\t      import { render } from './ui.js';",
            "     8\t      result = render(html);",
            "     9\t    </script>",
            "    10\t    <main><h1>Test report</h1></main>",
            "    11\t  </body>",
            "    12\t</html>",
        ]
    )

    assert RuleContentRouter().detect(html) == ContentType.HTML


def test_router_strips_numbered_prefixes_before_html_compression(monkeypatch):
    def fake_extract(html: str, **kwargs):
        assert html.startswith("<!DOCTYPE html>")
        assert "     1\t" not in html
        return "# Test report\n\nAll checks passed."

    monkeypatch.setitem(
        sys.modules,
        "trafilatura",
        SimpleNamespace(extract=fake_extract),
    )
    html = "\n".join(
        [
            "     1\t<!DOCTYPE html>",
            "     2\t<html>",
            "     3\t  <body><main><h1>Test report</h1><p>All checks passed.</p></main></body>",
            "     4\t</html>",
        ]
    )

    result = RuleContentRouter().compress(html, _context())

    assert result.content == "# Test report\n\nAll checks passed."


def test_html_compressor_preserves_pytest_html_report_tables_when_trafilatura_is_too_sparse(
    monkeypatch,
):
    monkeypatch.setitem(
        sys.modules,
        "trafilatura",
        SimpleNamespace(extract=lambda html, **kwargs: "Report generated."),
    )
    html = """
    <!doctype html>
    <html>
      <head><title>index.html</title></head>
      <body>
        <h1>index.html</h1>
        <h2>Environment</h2>
        <table id="environment">
          <tr><th>Package</th><th>Version</th></tr>
          <tr><td>pytest</td><td>8.3.5</td></tr>
        </table>
        <h2>Summary</h2>
        <p>7 tests took 00:00:08.</p>
        <table id="results-table">
          <tr><th>Result</th><th>Test</th><th>Duration</th></tr>
          <tr><td>Passed</td><td>tests/test_ok.py::test_ok</td><td>00:00:01</td></tr>
          <tr><td>Failed</td><td>tests/test_bad.py::test_bad</td><td>00:00:02</td></tr>
        </table>
        <script>window.pytest_html_data = {};</script>
      </body>
    </html>
    """

    result = HtmlCompressor().compress(html, _context())

    assert result.modified is True
    assert result.details["extractor"] == "beautifulsoup:report"
    assert "Report generated." not in result.content
    assert "# index.html" in result.content
    assert "## Environment" in result.content
    assert "| Package | Version |" in result.content
    assert "| pytest | 8.3.5 |" in result.content
    assert "## Summary" in result.content
    assert "7 tests took 00:00:08." in result.content
    assert "| Failed | tests/test_bad.py::test_bad | 00:00:02 |" in result.content
