from problem import sort_numbers


def test_sort_numbers_handles_duplicates():
    assert sort_numbers([3, 1, 2, 1, 0]) == [0, 1, 1, 2, 3]


def test_sort_numbers_handles_empty_input():
    assert sort_numbers([]) == []
