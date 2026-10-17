from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from openjiuwen.core.context_engine import ContextEngine
from openjiuwen.core.context_engine.processor.forked.offloader.message_offloader import (
    MessageSummaryOffloader,
    MessageSummaryOffloaderConfig,
)
from openjiuwen.core.context_engine.processor.forked.offloader.rule_compression import (
    RuleContentRouter,
    RuleContext,
)
from openjiuwen.core.context_engine.processor.forked.offloader.rule_compression.compressors import html_compressor
from openjiuwen.core.context_engine.schema.config import ContextEngineConfig

HTML = "<html><body><article><p>Useful article text.</p></article></body></html>"


def context():
    return RuleContext(max_tokens=1600, min_savings_ratio=0.0, html_min_content_chars=1)


def extractor():
    return Mock(
        extract=Mock(
            return_value=SimpleNamespace(
                extracted="Useful text.",
                extracted_length=12,
                title=None,
                author=None,
                date=None,
                metadata={},
            )
        )
    )


def test_offloader_creation_and_non_html_do_not_create_extractor(monkeypatch):
    factory = Mock(side_effect=AssertionError("unused HTML extractor initialized"))
    monkeypatch.setattr(html_compressor, "HTMLExtractor", factory)
    MessageSummaryOffloader(MessageSummaryOffloaderConfig())
    result = RuleContentRouter().compress("short plain text", context())
    assert result.content == "short plain text"
    factory.assert_not_called()


def test_html_first_use_creates_one_extractor_and_reuses_it(monkeypatch):
    instance = extractor()
    factory = Mock(return_value=instance)
    monkeypatch.setattr(html_compressor, "HTMLExtractor", factory)
    compressor = html_compressor.HtmlCompressor()
    factory.assert_not_called()
    assert compressor.compress(HTML, context()).content == "Useful text."
    assert compressor.compress(HTML, context()).content == "Useful text."
    factory.assert_called_once_with()
    assert instance.extract.call_count == 2


def test_report_html_does_not_create_unused_extractor(monkeypatch):
    factory = Mock(side_effect=AssertionError("report uses BeautifulSoup"))
    monkeypatch.setattr(html_compressor, "HTMLExtractor", factory)
    html = "<html><body><h1>pytest-html report</h1><p>All tests passed.</p></body></html>"
    result = html_compressor.HtmlCompressor().compress(html, context())
    assert result.details["extractor"] == "beautifulsoup:report"
    assert "All tests passed." in result.content
    factory.assert_not_called()


def test_injected_extractor_is_preserved(monkeypatch):
    factory = Mock(side_effect=AssertionError("injected extractor must be used"))
    monkeypatch.setattr(html_compressor, "HTMLExtractor", factory)
    instance = extractor()
    compressor = html_compressor.HtmlCompressor(extractor=instance)
    assert compressor.compress(HTML, context()).content == "Useful text."
    instance.extract.assert_called_once()
    factory.assert_not_called()


class _FalsyBoolExtractor(SimpleNamespace):
    def __bool__(self):
        return False


class _FalsyLenExtractor(SimpleNamespace):
    def __len__(self):
        return 0


@pytest.mark.parametrize("extractor_type", [_FalsyBoolExtractor, _FalsyLenExtractor], ids=["bool", "len"])
def test_falsy_injected_extractor_is_used_and_reused(monkeypatch, extractor_type):
    instance = extractor_type(extract=extractor().extract)
    assert not instance
    factory = Mock(side_effect=AssertionError("falsy injected extractor must not be replaced"))
    monkeypatch.setattr(html_compressor, "HTMLExtractor", factory)

    compressor = html_compressor.HtmlCompressor(extractor=instance)
    assert compressor.compress(HTML, context()).content == "Useful text."
    assert compressor.compress(HTML, context()).content == "Useful text."
    assert instance.extract.call_count == 2
    factory.assert_not_called()


def test_concurrent_first_use_initializes_only_once(monkeypatch):
    barrier = Barrier(4)
    instance = extractor()

    def slow_factory():
        # Simulate an import that releases the GIL during initialization.
        Event().wait(0.02)
        return instance

    factory = Mock(side_effect=slow_factory)
    monkeypatch.setattr(html_compressor, "HTMLExtractor", factory)
    compressor = html_compressor.HtmlCompressor()
    factory.assert_not_called()

    def compress():
        barrier.wait(timeout=5)
        return compressor.compress(HTML, context()).content

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(lambda _: compress(), range(4))) == ["Useful text."] * 4
    factory.assert_called_once_with()


@pytest.mark.parametrize("error", [ModuleNotFoundError("trafilatura"), ValueError("extraction failed")])
def test_extraction_failure_keeps_existing_fallback(monkeypatch, error):
    instance = extractor()
    instance.extract.side_effect = error
    monkeypatch.setattr(html_compressor, "HTMLExtractor", Mock(return_value=instance))
    result = html_compressor.HtmlCompressor().compress(HTML, context())
    if isinstance(error, ModuleNotFoundError):
        assert result.details["extractor"] == "beautifulsoup"
        assert "Useful article text." in result.content
    else:
        assert result.content == HTML
        assert result.modified is False


@pytest.mark.parametrize("error_type", [ModuleNotFoundError, ValueError])
def test_extractor_construction_failure_falls_back_then_retries(monkeypatch, error_type):
    instance = extractor()
    factory = Mock(side_effect=[error_type("extractor initialization failed"), instance])
    monkeypatch.setattr(html_compressor, "HTMLExtractor", factory)
    compressor = html_compressor.HtmlCompressor()
    factory.assert_not_called()

    result = compressor.compress(HTML, context())
    factory.assert_called_once_with()
    instance.extract.assert_not_called()
    if error_type is ModuleNotFoundError:
        assert result.details["extractor"] == "beautifulsoup"
        assert "Useful article text." in result.content
    else:
        assert result.content == HTML
        assert result.modified is False

    # Failed construction must release the lock and allow a later successful retry.
    assert compressor.compress(HTML, context()).content == "Useful text."
    assert compressor.compress(HTML, context()).content == "Useful text."
    assert factory.call_count == 2
    assert instance.extract.call_count == 2


@pytest.mark.asyncio
async def test_real_context_creation_does_not_initialize_html(monkeypatch):
    factory = Mock(side_effect=AssertionError("context creation must not initialize HTML"))
    monkeypatch.setattr(html_compressor, "HTMLExtractor", factory)
    monkeypatch.setitem(ContextEngine._PROCESSOR_MAP, "MessageSummaryOffloader", MessageSummaryOffloader)
    engine = ContextEngine(ContextEngineConfig(enable_openrouter_model_context_window_tokens=False))
    created = await engine.create_context(
        "lazy-html",
        processors=[("MessageSummaryOffloader", MessageSummaryOffloaderConfig())],
        token_counter=Mock(),
    )
    assert created is not None
    factory.assert_not_called()
