# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for graph_memory local_storage"""

import os

from openjiuwen.core.memory.graph.graph_memory.local_storage import DEFAULT_GRAPH_STORAGE_DIR


class TestDefaultGraphStorageDir:
    """Tests for DEFAULT_GRAPH_STORAGE_DIR"""

    @staticmethod
    def test_default_is_directory():
        """DEFAULT_GRAPH_STORAGE_DIR is a non-empty path string"""
        assert isinstance(DEFAULT_GRAPH_STORAGE_DIR, str)
        assert len(DEFAULT_GRAPH_STORAGE_DIR) > 0

    @staticmethod
    def test_default_resolves_to_local_storage_package_dir():
        """DEFAULT_GRAPH_STORAGE_DIR equals dirname of local_storage __file__"""
        import openjiuwen.core.memory.graph.graph_memory.local_storage as mod

        expected = os.path.dirname(os.path.abspath(mod.__file__))
        assert os.path.normpath(DEFAULT_GRAPH_STORAGE_DIR) == os.path.normpath(expected)
