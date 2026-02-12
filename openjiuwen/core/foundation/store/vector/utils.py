# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Conversion functions for Vector Store distance / similarity scores to normalized similarity [0, 1].
"""


def convert_l2_squared(raw_score: float, max_dist: float = 4.0) -> float:
    """
    Convert squared L2 distance to normalized similarity in [0, 1].
    Works for both Milvus and Chroma.

    Args:
        raw_score: Raw L2 distance score
        max_dist: Maximum distance (defaults to 4 for unit vectors)

    Returns:
        Normalized similarity score in [0, 1]
    """
    return max(0.0, (max_dist - raw_score) / max_dist)


def convert_cosine_similarity(raw_score: float) -> float:
    """
    Convert cosine similarity to normalized similarity in [0, 1].
    Works for Milvus.

    Args:
        raw_score: Raw cosine similarity (range [-1, 1])

    Returns:
        Normalized similarity score in [0, 1]
    """
    return (raw_score + 1.0) / 2.0


def convert_cosine_distance(raw_score: float) -> float:
    """
    Convert cosine distance to normalized cosine similarity in [0, 1].
    Works for Chroma.

    Args:
        raw_score: Raw cosine distance (range [0, 2])

    Returns:
        Normalized similarity score in [0, 1]
    """
    return (2.0 - raw_score) / 2.0


def convert_ip_similarity(raw_score: float) -> float:
    """
    Convert raw inner product to normalized similarity in [0, 1].
    Works for Milvus.

    Args:
        raw_score: Raw inner product

    Returns:
        Normalized similarity score in [0, 1]
    """
    return max(0.0, min(1.0, (raw_score + 1.0) / 2.0))


def convert_ip_distance(raw_score: float) -> float:
    """
    Convert inner product in distance form to normalized similarity in [0, 1].
    Works for Chroma, whose IP is a distance: d = 1 - dot (range [0, 2]).

    Args:
        raw_score: IP distance from Chroma (range [0, 2])

    Returns:
        Normalized similarity score in [0, 1]
    """
    return max(0.0, min(1.0, (2.0 - raw_score) / 2.0))
