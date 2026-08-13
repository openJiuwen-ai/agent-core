from problem import is_balanced


def test_is_balanced_accepts_nested_pairs():
    assert is_balanced("([]{})")


def test_is_balanced_rejects_mismatch():
    assert not is_balanced("([)]")
