from __future__ import annotations

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
