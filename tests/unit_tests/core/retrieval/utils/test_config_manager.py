# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
"""
Config manager test cases
"""

import json
import os
import tempfile

import pytest

from openjiuwen.core.retrieval import ConfigManager
from openjiuwen.core.retrieval import KnowledgeBaseConfig
from openjiuwen.core.common.exception.errors import BaseError


class TestConfigManager:
    """Config manager tests"""

    @staticmethod
    def test_init_with_path():
        """测试使用路径初始化"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            config_data = {
                "kb_id": "test_kb",
                "index_type": "vector",
                "use_graph": False,
                "chunk_size": 512,
                "chunk_overlap": 50,
            }
            json.dump(config_data, f)
            temp_path = f.name

        try:
            manager = ConfigManager(config_path=temp_path)
            config = manager.get_knowledge_base_config()
            assert config.kb_id == "test_kb"
            assert config.index_type == "vector"
        finally:
            os.unlink(temp_path)

    @staticmethod
    def test_load_from_file_json():
        """测试从 JSON 文件加载配置"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            config_data = {
                "kb_id": "test_kb",
                "index_type": "hybrid",
            }
            json.dump(config_data, f)
            temp_path = f.name

        try:
            manager = ConfigManager()
            manager.load_from_file(temp_path)
            config = manager.get_knowledge_base_config()
            assert config.kb_id == "test_kb"
            assert config.index_type == "hybrid"
        finally:
            os.unlink(temp_path)

    @staticmethod
    def test_load_from_file_yaml():
        """测试从 YAML 文件加载配置"""
        try:
            import yaml
        except ImportError:
            pytest.skip("PyYAML not installed")

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            config_data = {
                "kb_id": "test_kb",
                "index_type": "vector",
            }
            yaml.dump(config_data, f)
            temp_path = f.name

        try:
            manager = ConfigManager()
            manager.load_from_file(temp_path)
            config = manager.get_knowledge_base_config()
            assert config.kb_id == "test_kb"
            assert config.index_type == "vector"
        finally:
            os.unlink(temp_path)

    @staticmethod
    def test_load_from_file_not_found():
        """测试加载不存在的文件"""
        manager = ConfigManager()
        with pytest.raises(BaseError):
            manager.load_from_file("nonexistent.json")

    @staticmethod
    def test_get_config():
        """测试获取配置"""
        manager = ConfigManager()
        config = KnowledgeBaseConfig(kb_id="test_kb")
        manager.update_config(config)

        retrieved_config = manager.get_config(KnowledgeBaseConfig)
        assert retrieved_config is not None
        assert retrieved_config.kb_id == "test_kb"

    @staticmethod
    def test_get_config_not_found():
        """测试获取不存在的配置"""
        manager = ConfigManager()
        config = manager.get_config(KnowledgeBaseConfig)
        assert config is None
