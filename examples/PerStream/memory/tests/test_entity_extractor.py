import pytest

from video_memory.memory.entity_extractor import SpacyEntityExtractor


def test_missing_spacy_model_raises_instead_of_returning_nothing() -> None:
    """A missing model must not degrade into silent zero-entity extraction.

    Previously the constructor swallowed the load error and extract() fell back
    to a capitalisation regex labelled FALLBACK, which the shipped config then
    blocked — so every call returned []. The memory graph came out with no
    entity edges and graph propagation did nothing, with nothing reported.
    """
    with pytest.raises(RuntimeError, match="python -m spacy download"):
        SpacyEntityExtractor("en_core_web_definitely_not_installed")


def test_allowed_labels_are_kept_and_blocklisted_text_is_dropped() -> None:
    extractor = SpacyEntityExtractor(
        "en_core_web_sm",
        allowed_labels=["ORG", "PERSON", "GPE"],
        blocklist=["search"],
    )
    mentions = extractor.extract("CNN reported that Putin visited Moscow.")
    assert [mention.text for mention in mentions] == ["CNN", "Putin", "Moscow"]


def test_labels_outside_allowed_and_conditional_are_dropped() -> None:
    extractor = SpacyEntityExtractor("en_core_web_sm", allowed_labels=["PERSON"])
    mentions = extractor.extract("CNN reported that Putin visited Moscow.")
    assert [mention.text for mention in mentions] == ["Putin"]


def test_conditional_numbers_need_supporting_context() -> None:
    """Bare CARDINALs are noise; the same number near a price/rating is not."""
    extractor = SpacyEntityExtractor(
        "en_core_web_sm",
        allowed_labels=["ORG"],
        conditional_labels=["CARDINAL"],
    )
    assert extractor.extract("There were 30 of them.") == []
    assert [m.text for m in extractor.extract("The price is 30.")] == ["30"]
