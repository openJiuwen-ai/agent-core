from problem import merge_intervals


def test_merge_intervals_sorts_before_merging():
    assert merge_intervals([[5, 7], [1, 3], [2, 4], [8, 10]]) == [[1, 4], [5, 7], [8, 10]]


def test_merge_intervals_accepts_touching_ranges():
    assert merge_intervals([[1, 2], [2, 5]]) == [[1, 5]]
