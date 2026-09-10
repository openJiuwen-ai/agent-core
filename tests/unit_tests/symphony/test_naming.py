from openjiuwen.symphony import normalize_name_key as public_normalize_name_key
from openjiuwen.symphony.shared import fuzzy_name_distance, normalize_name_key


def test_normalize_name_key_preserves_unicode_name_content() -> None:
    assert normalize_name_key("ppt") == "ppt"
    assert normalize_name_key("ppt大师") == "ppt大师"
    assert normalize_name_key("ＰＰＴ-大师") == "ppt大师"
    assert normalize_name_key("PPT _ - 大师") == "ppt大师"
    assert normalize_name_key("A\u20dd / 大+师!") == "a\u20dd大师"


def test_fuzzy_name_distance_keeps_distinct_unicode_names_separate() -> None:
    assert fuzzy_name_distance("ppt", "ppt大师") is None


def test_normalize_name_key_is_exported_from_public_symphony_api() -> None:
    assert public_normalize_name_key is normalize_name_key
