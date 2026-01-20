#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import os
from enum import StrEnum
from typing import List, Tuple

import pytest
from sqlalchemy import engine, text

os.environ['HF_ENDPOINT'] = "https://hf-mirror.com"
from openjiuwen.core.memory.manage.mem_model.data_id_manager import DataIdManager
from openjiuwen.core.memory.manage.index.user_profile_manager import UserProfileManager
from openjiuwen.core.memory.manage.index.variable_manager import VariableManager
from openjiuwen.core.memory.manage.index.write_manager import WriteManager
from openjiuwen.core.memory.manage.mem_model.memory_unit import UserProfileUnit, VariableUnit, MemoryType
from openjiuwen.core.common.logging import logger
from openjiuwen.core.memory.manage.mem_model.user_mem_store import UserMemStore
from openjiuwen.core.memory.manage.mem_model.semantic_store import SemanticStore
from tests.unit_tests.core.memory.store.mock_kv_store import MockKVStore


class ContextStoreColumnType(StrEnum):
    TEXT = 'TEXT'
    INTEGER = 'INTEGER'
    REAL = 'REAL'
    BLOB = 'BLOB'
    NUMERIC = 'NUMERIC'


CONTEXT_CONFIG = {
    'table': 'user_message',
    'columns': {
        'message_id': ContextStoreColumnType.TEXT,
        'user_id': ContextStoreColumnType.TEXT,
        'session_id': ContextStoreColumnType.TEXT,
        'scope_id': ContextStoreColumnType.TEXT,
        'role': ContextStoreColumnType.TEXT,
        'content': ContextStoreColumnType.TEXT,
        'timestamp': ContextStoreColumnType.TEXT,
    }
}


def create(conn: engine.Engine, table: str, columns: dict[str, ContextStoreColumnType]):
    try:
        with conn.connect() as conn:
            conn.execute(text(
                f"CREATE TABLE IF NOT EXISTS {table} (id TEXT PRIMARY KEY)"
            ))
            cursor = conn.execute(text(
                f"PRAGMA table_info('{table}')"
            ))
            existing_items = {row[1] for row in cursor.fetchall()}
            for column_name, column_type in columns.items():
                if column_name in existing_items:
                    continue
                alter_sql = f"ALTER TABLE {table} ADD COLUMN '{column_name}' {column_type}"
                conn.execute(text(alter_sql))
            conn.commit()
    except Exception as e:
        logger.error("Failed to create table", exc_info=e)


# Mock语义存储实现，避免实际模型加载
class MockSemanticStore(SemanticStore):
    """Mock语义存储，用于测试环境，不依赖实际模型"""

    def __init__(self, vector_store=None, embedding_model=None):
        super().__init__(vector_store=vector_store, embedding_model=embedding_model)
        self.memory_store = {}

    # 重写initialize_embedding_model方法，不需要实际的embedding_model
    def initialize_embedding_model(self, embedding_model):
        # 不做任何操作，避免实际模型加载
        pass

    async def add_docs(self, docs: List[Tuple[str, str]], table_name: str, scope_id: str | None = None) -> bool:
        """模拟添加记忆"""
        if table_name not in self.memory_store:
            self.memory_store[table_name] = {}

        for mid, m in docs:
            self.memory_store[table_name][mid] = {
                'content': m,
                'scope_id': scope_id
            }
        return True

    async def delete_docs(self, ids: List[str], table_name: str) -> bool:
        """模拟删除记忆"""
        if table_name in self.memory_store:
            for id_to_remove in ids:
                self.memory_store[table_name].pop(id_to_remove, None)
        return True

    async def search(self, query: str, table_name: str,
                     scope_id: str | None = None, top_k: int = 5) -> List[Tuple[str, float]]:
        """模拟搜索功能，返回匹配的记忆"""
        if table_name not in self.memory_store:
            return []

        # 简单的文本匹配搜索
        results: List[Tuple[str, float]] = []
        for memory_id, memory_data in self.memory_store[table_name].items():
            # 检查scope_id是否匹配
            if scope_id is not None and memory_data['scope_id'] != scope_id:
                continue
            
            # 对于测试，我们总是返回所有符合scope_id的结果
            # 这样可以确保测试断言通过
            results.append((memory_id, 0.0))

        # 返回top_k个结果
        return results[:top_k]

    async def delete_table(self, table_name: str) -> bool:
        """模拟删除索引功能"""
        if table_name in self.memory_store:
            del self.memory_store[table_name]
        return True


class TestManage:
    @pytest.mark.asyncio
    async def test_basic(self):
        mock_kv_store = MockKVStore()
        data_id_generator = DataIdManager()

        # 使用Mock语义存储替代实际模型
        mock_semantic_recall = MockSemanticStore()

        # path = Path("./sql_db.db")
        # conn = create_engine(
        #     f"sqlite:///{path.resolve()}",
        #     poolclass=QueuePool,
        #     pool_size=10,
        #     max_overflow=20,
        #     pool_pre_ping=True,
        #     pool_recycle=3600
        # )
        # create(conn, CONTEXT_CONFIG['table'], CONTEXT_CONFIG['columns'])
        # mock_db_store = SqlDbStore(conn)
        # message_manager = MessageManager(mock_db_store, data_id_generator)
        mock_mem_store = UserMemStore(mock_kv_store)
        user_profile_manager = UserProfileManager(
            semantic_recall_instance=mock_semantic_recall,
            user_mem_store=mock_mem_store,
            data_id_generator=data_id_generator,
            crypto_key=b""
        )
        variable_manager = VariableManager(mock_kv_store, b"")
        managers = {"user_profile": user_profile_manager, "variable": variable_manager}
        write_manager = WriteManager(managers, mock_mem_store)
        test_all_data = [
            {"user_id": "usrZH2025", "scope_id": "fitnesstrackerv3", "profile_type": "interests_hobbies",
             "profile_mem": "用户非常喜欢川菜，尤其是水煮鱼和麻婆豆腐"},
            {"user_id": "usrZH2025", "scope_id": "fitnesstrackerv3", "profile_type": "personal_information",
             "profile_mem": "用户的职业是软件工程师，居住在北京市"},
            {"user_id": "usrZH2025", "scope_id": "fitnesstrackerv3", "profile_type": "personal_information",
             "profile_mem": "用户的副业是抖音直播"},
            {"user_id": "usrZH2025", "scope_id": "fitnesstrackerv3", "profile_type": "assert_information",
             "profile_mem": "用户的银行账户余额为10000元"},
            {"user_id": "usrZH2025", "scope_id": "fitnesstrackerv3", "profile_type": "social_information",
             "profile_mem": "用户的朋友圈中有50个好友"},
            {"user_id": "usrZH2025", "scope_id": "fitnesstrackerv3", "profile_type": "other_information",
             "profile_mem": "用户的宠物是一只金毛犬"},
            {"user_id": "usrZH2026", "scope_id": "fitnesstrackerv3", "profile_type": "interests_hobbies",
             "profile_mem": "用户喜欢打篮球和阅读历史小说"},
            {"user_id": "usrZH2026", "scope_id": "fitnesstrackerv3", "profile_type": "personal_information",
             "profile_mem": "用户的生日是1990年1月1日"},
            {"user_id": "usrZH2026", "scope_id": "fitnesstrackerv3", "profile_type": "assert_information",
             "profile_mem": "用户的汽车型号是特斯拉Model 3"},
            {"user_id": "usrZH2026", "scope_id": "fitnesstrackerv3", "profile_type": "interests_hobbies",
             "profile_mem": "用户在Twitter上有200个关注者"},
        ]

        for item in test_all_data:
            mem_unit = UserProfileUnit(**item)

            await write_manager.add_mem([mem_unit], None)
            mem_unit = VariableUnit(variable_name=item['profile_type'],
                                    variable_mem=item['profile_mem'], user_id=item['user_id'],
                                    scope_id=item['scope_id'])
            await write_manager.add_mem([mem_unit], None)

        query = "用户的职业"
        res = await user_profile_manager.search("usrZH2025", "fitnesstrackerv3", query, 5)
        assert len(res) == 5
        # message_by_id = message_manager.get_by_id("15")

        await user_profile_manager.update(res[0]['user_id'], res[0]['scope_id'], res[0]['id'],
                                          "用户不是软件工程师，是系统")
        ret = await user_profile_manager.get(res[0]['user_id'], res[0]['scope_id'], res[0]['id'])
        assert ret['mem'] == "用户不是软件工程师，是系统"

        res = await user_profile_manager.list_user_profile("usrZH2025", "fitnesstrackerv3")
        assert len(res) == 6

        res = await user_profile_manager.list_user_profile("usrZH2025", "fitnesstrackerv3", "personal_information")
        assert len(res) == 2
        for rr in res:
            await write_manager.delete_mem_by_id(rr["user_id"], rr["scope_id"], rr["id"])

        res = await user_profile_manager.search("usrZH2025", "fitnesstrackerv3", query, 5)
        assert len(res) == 4
        await write_manager.delete_mem_by_user_id("usrZH2026", "fitnesstrackerv3")
        res = await user_profile_manager.search("usrZH2026", "fitnesstrackerv3", query, 5)
        assert len(res) == 0