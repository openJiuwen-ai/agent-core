"""Pin the OCR rule-baseline text heuristics.

Every assertion here fails against the pre-fix regexes, which were written as
raw strings with a doubled escape (r"\\b..." instead of r"\b..."). That made
_DOMAIN_RE / _EMAIL_RE / _MONEY_RE match a literal backslash rather than a word
boundary, so they never matched anything and _detail_description always fell
through to the generic "Visible screen text" branch.
"""

from video_memory.memory.ocr_generator import (
    _detail_description,
    _meaningful_lines,
    _merge_split_lines,
    _remove_prefix,
    _summary_description,
    _DOMAIN_RE,
    _EMAIL_RE,
    _MONEY_RE,
)


def test_regexes_match_real_screen_text() -> None:
    assert _DOMAIN_RE.search("edition.cnn.com")
    assert _EMAIL_RE.search("tau.irisbrennan.1654798856749@gmail.com")
    assert _MONEY_RE.findall("$299.00 and $1,800") == ["$299.00", "$1,800"]


def test_detail_description_picks_the_account_branch_on_an_email() -> None:
    description = _detail_description(["Sync settings", "iris.brennan@gmail.com"])
    assert description.startswith("Account/sync page text; iris.brennan@gmail.com")


def test_detail_description_picks_the_shopping_branch_on_a_price() -> None:
    description = _detail_description(["2021 Apple iPad 10.2-inch", "$299.00"])
    assert description.startswith("Shopping/product page text")


def test_detail_description_picks_the_article_branch_and_keeps_the_domain() -> None:
    description = _detail_description(
        [
            "edition.cnn.com",
            "Russia-Ukraine live updates: Missiles strike Zaporizhzhia",
            "Last Updated: October 9, 2022, 7:07 AM ET",
        ]
    )
    assert description.startswith("Article/page text; edition.cnn.com")


def test_detail_description_falls_back_to_the_generic_branch() -> None:
    assert _detail_description(["Alarm", "8:30 AM"]).startswith("Visible screen text")


def test_merge_split_lines_rejoins_an_email_broken_across_two_ocr_lines() -> None:
    assert _merge_split_lines(["tau.irisbrennan.1654798856749@", "gmail.com"]) == [
        "tau.irisbrennan.1654798856749@gmail.com"
    ]


def test_remove_prefix_strips_the_branch_label() -> None:
    assert _remove_prefix("Shopping/product page text; $299.00") == "$299.00"


def test_summary_description_drops_the_branch_labels() -> None:
    summary = _summary_description(["Visible screen text; Alarm 8:30 AM"])
    assert summary == "Window summary: Alarm 8:30 AM"


def test_meaningful_lines_drops_navigation_noise_and_stubs() -> None:
    assert _meaningful_lines(["Search", "", "  Home  ", "ok", "CNN Breaking News"]) == [
        "CNN Breaking News"
    ]
