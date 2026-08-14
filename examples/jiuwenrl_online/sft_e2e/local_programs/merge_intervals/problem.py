from __future__ import annotations


def merge_intervals(intervals: list[list[int]]) -> list[list[int]]:
    if not intervals:
        return []
    merged: list[list[int]] = [list(intervals[0])]
    for start, end in intervals[1:]:
        last = merged[-1]
        if start > last[1]:
            merged.append([start, end])
        else:
            last[1] = max(last[1], end)
    return merged
