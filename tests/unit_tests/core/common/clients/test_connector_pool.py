# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from unittest.mock import patch
import pytest

from openjiuwen.core.common.clients.connector_pool import ConnectorPool, ConnectorPoolConfig, ConnectorPoolManager, \
    TcpConnectorPool


class TestConnectorPool:

    @pytest.fixture
    def concrete_pool(self):
        class MockConnectorPool(ConnectorPool):
            def __init__(self, config):
                super().__init__(config)

            def set_created_at(self, created_at):
                self._created_at = created_at

            def conn(self):
                return "connector"

            async def _do_close(self):
                self.closed_flag = True

        pool = MockConnectorPool(ConnectorPoolConfig(ttl=10, max_idle_time=5))
        pool.closed_flag = False
        return pool

    def test_stat(self, concrete_pool):
        with patch('time.time', return_value=200):
            concrete_pool.set_created_at(100)

            stats = concrete_pool.stat()
            assert not stats['closed']
            assert stats['ref_detail']['created_at'] == 100


@pytest.mark.asyncio
class TestTcpConnectorPoolIntegration:
    async def test_with_connector_pool_manager(self):
        manager = ConnectorPoolManager(max_pools=5)
        config = ConnectorPoolConfig(limit=50, limit_per_host=10)
        pool = await manager.get_connector_pool(config=config)
        assert isinstance(pool, TcpConnectorPool)
        assert pool.conn() is not None
        print(pool.get_stats())
        await manager.release_connector_pool(config=config)
        await manager.close_connector_pool(config=config)
        print(pool.get_stats())

    async def test_multiple_pools_with_manager(self):
        manager = ConnectorPoolManager(max_pools=3)

        configs = [
            ConnectorPoolConfig(limit=10),
            ConnectorPoolConfig(limit=20),
            ConnectorPoolConfig(limit=30),
        ]

        pools = []
        for config in configs:
            pool = await manager.get_connector_pool(config=config)
            pools.append(pool)

        for pool in pools:
            assert isinstance(pool, TcpConnectorPool)
        print(manager.get_stats())
        assert manager.get_stats().get("total_connector_pools") == 3

        await manager.close_connector_pool(config=configs[0])
        await manager.close_connector_pool(config=configs[1])
        await manager.close_connector_pool(config=configs[2])


    async def test_reuse_same_config(self):
        manager = ConnectorPoolManager()
        config = ConnectorPoolConfig(limit=100)
        pool1 = await manager.get_connector_pool(config=config)
        pool2 = await manager.get_connector_pool(config=config)
        assert pool1 is pool2
        assert pool1.ref_count == 2  # 初始1 + 1次获取
        await manager.release_connector_pool(config=config)
        await manager.release_connector_pool(config=config)
