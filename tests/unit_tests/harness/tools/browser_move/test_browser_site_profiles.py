from __future__ import annotations

import json
import logging
from pathlib import Path

from openjiuwen.harness.tools.browser_move.playwright_runtime.site_profiles import (
    BrowserSelectorCache,
    builtin_site_profiles,
    domain_from_url,
    normalize_route_signature,
)


def test_builtin_site_profiles_include_books_to_scrape() -> None:
    profiles = builtin_site_profiles()

    books = [item for item in profiles if item.get("id") == "books_to_scrape"]
    assert books

    profile = books[0]
    assert "books.toscrape.com" in profile["domains"]
    assert "article.product_pod" in profile["card_container_selectors"]
    assert "h3 a[title]" in profile["title_selectors"]
    assert ".price_color" in profile["price_selectors"]


def test_normalize_route_signature_generalizes_numeric_paths() -> None:
    assert (
        normalize_route_signature(
            "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html"
        )
        == "/catalogue/a-light-in-the-attic_*/index.html"
    )


def test_domain_from_url_extracts_hostname() -> None:
    assert domain_from_url("https://books.toscrape.com/") == "books.toscrape.com"


def test_selector_cache_records_card_probe_result(tmp_path: Path) -> None:
    cache = BrowserSelectorCache(tmp_path / "selector_cache.json")

    cache.record_card_probe_result(
        {
            "ok": True,
            "url": "https://books.toscrape.com/",
            "cards": [
                {
                    "title": "A Light in the Attic",
                    "price": "51.77",
                    "rating": "Three stars",
                    "availability": "In stock",
                    "author": "Oscar Wilde",
                    "source": "books.toscrape.com",
                    "summary": "A compact book listing with title, price, rating, availability, and actions.",
                    "has_image": True,
                    "primary_link": "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html",
                    "text_preview": "A Light in the Attic Three stars 51.77 In stock Add to basket",
                    "selector_hint": (
                        "ol:nth-of-type(1) > li.col-xs-6:nth-of-type(1) > "
                        "article.product_pod:nth-of-type(1)"
                    ),
                    "title_selector_hint": "article.product_pod:nth-of-type(1) > h3:nth-of-type(1) > a:nth-of-type(1)",
                    "price_selector_hint": "article.product_pod:nth-of-type(1) > div.product_price:nth-of-type(2) > p.price_color:nth-of-type(1)",
                    "rating_selector_hint": "article.product_pod:nth-of-type(1) > p.star-rating:nth-of-type(1)",
                    "author_selector_hint": "article.product_pod:nth-of-type(1) > p.author:nth-of-type(1)",
                    "source_selector_hint": "article.product_pod:nth-of-type(1) > span.source:nth-of-type(1)",
                    "summary_selector_hint": "article.product_pod:nth-of-type(1) > p.description:nth-of-type(1)",
                    "primary_link_selector_hint": "article.product_pod:nth-of-type(1) > h3:nth-of-type(1) > a:nth-of-type(1)",
                    "buttons": [
                        {
                            "selector_hint": (
                                "article.product_pod:nth-of-type(1) > "
                                "div.product_price:nth-of-type(2) > form:nth-of-type(1) > "
                                "button.btn.btn-primary:nth-of-type(1)"
                            )
                        }
                    ],
                },
                {
                    "title": "Tipping the Velvet",
                    "price": "53.74",
                    "rating": "One star",
                    "availability": "In stock",
                    "author": "Virginia Woolf",
                    "source": "books.toscrape.com",
                    "summary": "Another compact listing with enough metadata to cache article-style selectors.",
                    "has_image": True,
                    "primary_link": "https://books.toscrape.com/catalogue/tipping-the-velvet_999/index.html",
                    "text_preview": "Tipping the Velvet One star 53.74 In stock Add to basket",
                    "selector_hint": (
                        "ol:nth-of-type(1) > li.col-xs-6:nth-of-type(2) > "
                        "article.product_pod:nth-of-type(1)"
                    ),
                    "title_selector_hint": "article.product_pod:nth-of-type(1) > h3:nth-of-type(1) > a:nth-of-type(1)",
                    "price_selector_hint": "article.product_pod:nth-of-type(1) > div.product_price:nth-of-type(2) > p.price_color:nth-of-type(1)",
                    "rating_selector_hint": "article.product_pod:nth-of-type(1) > p.star-rating:nth-of-type(1)",
                    "author_selector_hint": "article.product_pod:nth-of-type(1) > p.author:nth-of-type(1)",
                    "source_selector_hint": "article.product_pod:nth-of-type(1) > span.source:nth-of-type(1)",
                    "summary_selector_hint": "article.product_pod:nth-of-type(1) > p.description:nth-of-type(1)",
                    "primary_link_selector_hint": "article.product_pod:nth-of-type(1) > h3:nth-of-type(1) > a:nth-of-type(1)",
                    "buttons": [
                        {
                            "selector_hint": (
                                "article.product_pod:nth-of-type(1) > "
                                "div.product_price:nth-of-type(2) > form:nth-of-type(1) > "
                                "button.btn.btn-primary:nth-of-type(1)"
                            )
                        }
                    ],
                },
            ],
        }
    )

    exported = cache.export_for_probe()

    assert len(exported) == 1
    assert exported[0]["domain"] == "books.toscrape.com"
    assert exported[0]["kind"] == "card_probe"
    assert exported[0]["quality_score"] > 0
    assert exported[0]["sample_card_count"] == 2

    selectors = exported[0]["selectors"]
    assert "article.product_pod" in selectors["card_container_selectors"]
    assert "p.price_color" in selectors["price_selectors"]
    assert "p.star-rating" in selectors["rating_selectors"]
    assert "p.author" in selectors["author_selectors"]
    assert "span.source" in selectors["source_selectors"]
    assert "p.description" in selectors["summary_selectors"]


def test_selector_cache_does_not_record_navigation_only_cards(tmp_path: Path) -> None:
    cache = BrowserSelectorCache(tmp_path / "selector_cache.json")

    cache.record_card_probe_result(
        {
            "ok": True,
            "url": "https://www.amazon.sg/s?k=wireless+mouse",
            "cards": [
                {
                    "title": "Fresh & Fast",
                    "selector_hint": "li.nav-li:nth-of-type(1)",
                    "text_preview": "Fresh & Fast",
                    "primary_link": "https://www.amazon.sg/fresh",
                    "buttons": [{"selector_hint": "li.nav-li > a"}],
                },
                {
                    "title": "Sell",
                    "selector_hint": "li.nav-li:nth-of-type(2)",
                    "text_preview": "Sell",
                    "primary_link": "https://www.amazon.sg/sell",
                    "buttons": [{"selector_hint": "li.nav-li > a"}],
                },
            ],
        }
    )

    assert cache.export_for_probe() == []


def test_selector_cache_records_only_high_quality_cards(tmp_path: Path) -> None:
    cache = BrowserSelectorCache(tmp_path / "selector_cache.json")

    cache.record_card_probe_result(
        {
            "ok": True,
            "url": "https://www.amazon.sg/s?k=wireless+mouse",
            "cards": [
                {
                    "title": "Wireless Mouse Example",
                    "selector_hint": "div.s-result-item:nth-of-type(1)",
                    "title_selector_hint": "h2 a span",
                    "price_selector_hint": ".a-price .a-offscreen",
                    "rating_selector_hint": "span.a-icon-alt",
                    "primary_link_selector_hint": "h2 a",
                    "price": "S$20.00",
                    "rating": "4.5 out of 5 stars",
                    "review_count": "1,000 ratings",
                    "has_image": True,
                    "text_preview": "Wireless Mouse Example S$20.00 4.5 out of 5 stars 1,000 ratings",
                    "buttons": [{"selector_hint": "button[name='submit.addToCart']"}],
                },
                {
                    "title": "Another Wireless Mouse",
                    "selector_hint": "div.s-result-item:nth-of-type(2)",
                    "title_selector_hint": "h2 a span",
                    "price_selector_hint": ".a-price .a-offscreen",
                    "rating_selector_hint": "span.a-icon-alt",
                    "primary_link_selector_hint": "h2 a",
                    "price": "S$25.00",
                    "rating": "4.3 out of 5 stars",
                    "review_count": "500 ratings",
                    "has_image": True,
                    "text_preview": "Another Wireless Mouse S$25.00 4.3 out of 5 stars 500 ratings",
                    "buttons": [{"selector_hint": "button[name='submit.addToCart']"}],
                },
            ],
        }
    )

    exported = cache.export_for_probe()

    assert len(exported) == 1
    assert exported[0]["domain"] == "www.amazon.sg"
    assert exported[0]["quality_score"] > 0
    assert "div.s-result-item" in exported[0]["selectors"]["card_container_selectors"]
    assert "li.nav-li" not in str(exported[0]["selectors"])


def test_selector_cache_loads_utf8_bom_file(tmp_path: Path) -> None:
    cache_path = tmp_path / "selector_cache.json"
    cache_data = {
        "version": 1,
        "records": [
            {
                "domain": "books.toscrape.com",
                "route_signature": "/",
                "kind": "card_probe",
                "selectors": {
                    "card_container_selectors": ["article.product_pod"],
                },
                "success_count": 1,
                "failure_count": 0,
                "quality_score": 0.9,
            }
        ],
    }
    cache_path.write_bytes(
        b"\xef\xbb\xbf" + json.dumps(cache_data).encode("utf-8")
    )

    exported = BrowserSelectorCache(cache_path).export_for_probe()

    assert len(exported) == 1
    assert exported[0]["domain"] == "books.toscrape.com"
    assert exported[0]["selectors"]["card_container_selectors"] == [
        "article.product_pod"
    ]


def test_selector_cache_logs_warning_for_invalid_json(
    tmp_path: Path, caplog
) -> None:
    cache_path = tmp_path / "selector_cache.json"
    cache_path.write_text("{not valid json", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        exported = BrowserSelectorCache(cache_path).export_for_probe()

    assert exported == []
    assert "Failed to load browser selector cache" in caplog.text
    assert str(cache_path) in caplog.text


def test_selector_cache_records_rejected_card_probe_attempt(tmp_path: Path) -> None:
    cache_path = tmp_path / "selector_cache.json"
    cache_path.write_text(
        json.dumps(
            {
                "version": 1,
                "records": [
                    {
                        "domain": "www.amazon.sg",
                        "route_signature": "/s",
                        "kind": "card_probe",
                        "selectors": {
                            "card_container_selectors": ["div.s-result-item"],
                            "title_selectors": ["h2 a span"],
                        },
                        "success_count": 3,
                        "failure_count": 0,
                        "quality_score": 0.9,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    cache = BrowserSelectorCache(cache_path)

    cache.record_card_probe_cache_rejection(
        {
            "ok": True,
            "url": "https://www.amazon.sg/s?k=wireless+mouse",
            "cache_records_used": 1,
            "cache_accepted": False,
            "selector_source": "generic",
        }
    )

    exported = cache.export_for_probe()
    assert len(exported) == 1
    assert exported[0]["success_count"] == 3
    assert exported[0]["failure_count"] == 1
    assert exported[0]["last_failure_at"] > 0
    assert exported[0]["last_failure_reason"] == "generic"


def test_selector_cache_does_not_record_accepted_cache_as_failure(tmp_path: Path) -> None:
    cache_path = tmp_path / "selector_cache.json"
    cache_path.write_text(
        json.dumps(
            {
                "version": 1,
                "records": [
                    {
                        "domain": "www.amazon.sg",
                        "route_signature": "/s",
                        "kind": "card_probe",
                        "selectors": {
                            "card_container_selectors": ["div.s-result-item"],
                        },
                        "success_count": 3,
                        "failure_count": 0,
                        "quality_score": 0.9,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    cache = BrowserSelectorCache(cache_path)

    cache.record_card_probe_cache_rejection(
        {
            "ok": True,
            "url": "https://www.amazon.sg/s?k=wireless+mouse",
            "cache_records_used": 1,
            "cache_accepted": True,
            "selector_source": "cache",
        }
    )

    exported = cache.export_for_probe()
    assert exported[0]["failure_count"] == 0
    assert "last_failure_at" not in exported[0]


def test_selector_cache_does_not_record_generic_toolbar_false_positives(
    tmp_path: Path,
) -> None:
    cache = BrowserSelectorCache(tmp_path / "selector_cache.json")

    cache.record_card_probe_result(
        {
            "ok": True,
            "url": "https://so.csdn.net/so/search?q=fastapi%20tutorial&t=blog",
            "selector_source": "generic",
            "cards": [
                {
                    "title": "--",
                    "selector_hint": "div.toolbar-container-right",
                    "summary": "toolbar member center message creation center profile links",
                    "author": "member center",
                    "source": "mp.csdn.net",
                    "primary_link": "https://mp.csdn.net/",
                    "text_preview": "member center message creation center profile links",
                    "buttons": [{"selector_hint": "#toolbar-remind"}],
                    "has_image": True,
                },
                {
                    "title": "right panel",
                    "selector_hint": "div.main-rt",
                    "summary": "right sidebar recommendations and unrelated navigation entries",
                    "primary_link": "https://blog.csdn.net/example",
                    "text_preview": "right sidebar recommendations and unrelated navigation entries",
                    "buttons": [{"selector_hint": "li > a"}],
                },
            ],
        }
    )

    assert cache.export_for_probe() == []


def test_selector_cache_records_generic_result_rows_with_enough_samples(
    tmp_path: Path,
) -> None:
    cache = BrowserSelectorCache(tmp_path / "selector_cache.json")

    cache.record_card_probe_result(
        {
            "ok": True,
            "url": "https://search.douban.com/book/subject_search?search_text=python",
            "selector_source": "generic",
            "cards": [
                {
                    "title": f"Python Result {index}",
                    "selector_hint": f"div.item-root:nth-of-type({index})",
                    "title_selector_hint": "div.item-root > div.detail > div.title",
                    "summary_selector_hint": "div.item-root > div.detail > div.meta.abstract",
                    "primary_link_selector_hint": "div.item-root > a.cover-link",
                    "summary": "A real result row with enough summary text and metadata to cache.",
                    "primary_link": f"https://example.com/book/{index}",
                    "text_preview": "Python Result summary metadata link",
                    "buttons": [{"selector_hint": "div.title > a.title-text"}],
                }
                for index in range(1, 4)
            ],
        }
    )

    exported = cache.export_for_probe()
    assert len(exported) == 1
    assert exported[0]["domain"] == "search.douban.com"
    assert exported[0]["sample_card_count"] == 3
    assert "div.item-root" in exported[0]["selectors"]["card_container_selectors"]


def test_selector_cache_demotes_csdn_author_links_from_title_and_primary_link(
    tmp_path: Path,
) -> None:
    cache = BrowserSelectorCache(tmp_path / "selector_cache.json")

    cache.record_card_probe_result(
        {
            "ok": True,
            "url": "https://so.csdn.net/so/search?q=fastapi%20tutorial&t=blog",
            "selector_source": "generic",
            "cards": [
                {
                    "title": f"Author Name {index}",
                    "selector_hint": f"div.so-items-normal.baidu:nth-of-type({index})",
                    "title_selector_hint": "a.user > span.name-text",
                    "author_selector_hint": "div.btm-rt > a.user",
                    "summary_selector_hint": "div.bdc-lt > div.nm-rt-desc.row2",
                    "summary": "FastAPI article result summary with enough text to be considered cacheable.",
                    "primary_link": f"https://blog.csdn.net/user{index}",
                    "primary_link_selector_hint": "div.btm-rt > a.user",
                    "text_preview": "FastAPI article result summary author metadata article link",
                    "buttons": [
                        {
                            "selector_hint": "h3.title.substr > a.block-title.so-item-report"
                        }
                    ],
                }
                for index in range(1, 4)
            ],
        }
    )

    exported = cache.export_for_probe()
    assert len(exported) == 1

    selectors = exported[0]["selectors"]
    serialized = json.dumps(selectors, ensure_ascii=False)

    assert "a.user" not in selectors.get("title_selectors", [])
    assert "span.name-text" not in serialized
    assert "div.btm-rt > a.user" not in selectors.get("primary_link_selectors", [])
    assert "a.block-title.so-item-report" in selectors["primary_link_selectors"]
    assert "a.block-title.so-item-report" in selectors["title_selectors"]
    assert "a.user" in serialized
    assert "author_selectors" in selectors
