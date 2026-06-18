from __future__ import annotations

import sys
from types import SimpleNamespace

from openjiuwen.core.context_engine.processor.offloader.rules import (
    ContentType,
    RuleContentRouter,
    RuleContext,
)
from openjiuwen.core.context_engine.processor.offloader.rules.html_compressor import (
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


def test_router_detects_structural_html_fragments_without_html_or_body_tags():
    fragment = (
        "<article><h1>Authentication</h1>"
        "<p>All API requests require an API key.</p>"
        "<script>track()</script></article>"
    )

    assert RuleContentRouter().detect(fragment) == ContentType.HTML
