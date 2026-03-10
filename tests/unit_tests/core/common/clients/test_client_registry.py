# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from unittest.mock import Mock
import pytest
from openjiuwen.core.common.clients.client_registry import ClientRegistry


class TestClientRegistry:
    @pytest.fixture
    def registry(self):
        return ClientRegistry()

    def test_register_client_decorator(self, registry):
        @registry.register_client('test_client', client_type='test')
        def create_test_client(**kwargs):
            return Mock()

        assert 'test_test_client' in registry.list_clients()

    def test_register_class(self, registry):
        class TestClient:
            __client_name__ = 'mysql'
            __client_type__ = 'database'

            def __init__(self, **kwargs):
                self.config = kwargs

        registry.register_class(TestClient)

        full_name = 'database_mysql'
        assert full_name in registry.list_clients()
        assert full_name in registry.list_clients()

        instance = registry.get_client('database_mysql', host='localhost')
        assert instance.config['host'] == 'localhost'

    def test_get_client_by_name(self, registry):
        mock_instance = Mock()

        @registry.register_client('redis', client_type='cache')
        def create_redis(**kwargs):
            return mock_instance

        result = registry.get_client('redis', client_type='cache')
        assert result == mock_instance

    def test_get_client_without_client_type(self, registry):
        mock_instance = Mock()

        @registry.register_client('default_client', client_type=None)
        def create_default(**kwargs):
            return mock_instance

        result = registry.get_client('default_client')
        assert result == mock_instance

    def test_get_client_empty_name(self, registry):
        with pytest.raises(ValueError, match="cannot be empty"):
            registry.get_client('')

    def test_get_client_unknown(self, registry):
        with pytest.raises(ValueError, match="Unknown client type"):
            registry.get_client('unknown')

    def test_get_client_creation_failure(self, registry):
        @registry.register_client('failing', client_type='test')
        def failing_factory(**kwargs):
            raise Exception("Creation failed")

        with pytest.raises(RuntimeError, match="Failed to create client"):
            registry.get_client('failing', client_type='test')

    def test_unregister(self, registry):
        @registry.register_client('redis', client_type='cache')
        def create_redis(**kwargs):
            return Mock()

        registry.unregister('redis', client_type='cache')

        assert 'cache_redis' not in registry.list_clients()

    def test_unregister_not_found(self, registry):
        with pytest.raises(ValueError, match="not registered"):
            registry.unregister('nonexistent')

    def test_list_clients(self, registry):
        @registry.register_client('client1', client_type='type1')
        def factory1(**kwargs):
            return Mock()

        @registry.register_client('client2', client_type='type2')
        def factory2(**kwargs):
            return Mock()

        clients = registry.list_clients()
        assert set(clients) == {'type1_client1', 'type2_client2'}
