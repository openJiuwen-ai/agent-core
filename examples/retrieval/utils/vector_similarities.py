# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""
Utility to calculate vector similarities
"""

import numpy as np
from numpy.typing import ArrayLike


def cosine_similarity(vec1: ArrayLike, vec2: ArrayLike) -> float:
    """Calculate cosine similarity between two vectors"""
    vec1 = np.asarray(vec1)
    vec2 = np.asarray(vec2)
    dot_product = np.dot(vec1, vec2)
    norm1 = np.linalg.norm(vec1)
    norm2 = np.linalg.norm(vec2)
    return float(dot_product / (norm1 * norm2))


def euclidean_distance(vec1: ArrayLike, vec2: ArrayLike) -> float:
    """Calculate euclidean distance between two vectors"""
    vec1 = np.asarray(vec1)
    vec2 = np.asarray(vec2)
    return float(np.linalg.norm(vec1 - vec2))
