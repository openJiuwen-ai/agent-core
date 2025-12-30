# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import Any, Optional, Tuple

from openjiuwen.core.memory.manage.base_memory_manager import BaseMemoryManager
from openjiuwen.core.common.logging import logger
from openjiuwen.core.utils.llm.base import BaseModelClient
from openjiuwen.core.memory.mem_unit.memory_unit import VariableUnit
from openjiuwen.core.memory.store.base_kv_store import BaseKVStore


class VariableManager(BaseMemoryManager):
    SEPARATOR = "/"

    def __init__(self,
                 kv_store: BaseKVStore,
                 crypto_key: bytes):
        self.kv_store = kv_store
        self.crypto_key = crypto_key

    async def add(self, memory: VariableUnit, llm: Tuple[str, BaseModelClient] | None = None):
        """add Variable memory"""
        if self.kv_store is None:
            logger.error("kv_store cannot be None")
            return
        key, value = self._make_variable_pairs(
            memory.user_id,
            False,
            memory.group_id,
            memory.variable_name,
            None,
            memory.variable_mem,
            None
        )
        await self.kv_store.set(key, value)

    async def update(self, user_id: str, group_id: str, mem_id: str, new_memory: str, **kwargs):
        logger.warning("not implemented method update")
        pass

    async def update_user_variable(self, user_id: str, group_id: str, var_name: str, var_mem: str):
        if self.kv_store is None:
            logger.error("kv_store cannot be None")
            return
        existing_variable = await self.query_variable(user_id=user_id, group_id=group_id, name=var_name)
        if not VariableManager._check_exist(existing_variable, var_name):
            return
        key, value = self._make_variable_pairs(usr_id=user_id, for_deletion=False,
                                               group_id=group_id, var_name=var_name, user_var_value=var_mem)
        await self.kv_store.set(key, value)

    async def delete(self, user_id: str, group_id: str, mem_id: str, **kwargs):
        logger.warning("not implemented method delete")
        pass

    async def delete_by_user_id(self, user_id: str, group_id: str):
        if self.kv_store is None:
            logger.error("kv_store cannot be None")
            return
        user_prefix = f"user_var{self.SEPARATOR}{user_id}{self.SEPARATOR}{group_id}{self.SEPARATOR}"
        session_prefix = f"session_var{self.SEPARATOR}{user_id}{self.SEPARATOR}{group_id}{self.SEPARATOR}"
        await self.kv_store.delete_by_prefix(user_prefix)
        await self.kv_store.delete_by_prefix(session_prefix)

    async def delete_user_variable(self, user_id: str, group_id: str, var_name: str):
        if self.kv_store is None:
            logger.error("kv_store cannot be None")
            return
        key, _ = self._make_variable_pairs(usr_id=user_id, for_deletion=False, group_id=group_id, var_name=var_name)
        await self.kv_store.delete(key)

    async def get(self, user_id: str, group_id: str, mem_id: str) -> dict[str, Any] | None:
        logger.warning("not implemented method get")
        pass

    async def search(self, user_id: str, group_id: str, query: str, top_k: int, **kwargs):
        logger.warning("not implemented method search")
        pass

    async def query_variable(self, user_id: str, group_id: str, name: Optional[str] = None,
                             session_id: Optional[str] = None) -> dict[str, Any]:
        """query variable by user_id, group_id, variable_name return variable mem."""
        self._check_user_and_group_id(user_id, group_id, "Search")
        if not name or not name.strip():
            prefix_str = f"user_var{self.SEPARATOR}{user_id}{self.SEPARATOR}{group_id}{self.SEPARATOR}"
            kv_ret = await self.kv_store.get_by_prefix(prefix_str)
            result = {}
            for k, v in kv_ret.items():
                v = BaseMemoryManager.decrypt_memory_if_needed(key=self.crypto_key, ciphertext=v)
                result[k.split(f"{self.SEPARATOR}")[-1]] = v
            return result
        if session_id:
            key = (f"session_var{self.SEPARATOR}{user_id}{self.SEPARATOR}{group_id}{self.SEPARATOR}"
                   f"{session_id}{self.SEPARATOR}{name}")
        else:
            key = f"user_var{self.SEPARATOR}{user_id}{self.SEPARATOR}{group_id}{self.SEPARATOR}{name}"
        kv_ret = await self.kv_store.get(key)
        kv_ret = BaseMemoryManager.decrypt_memory_if_needed(key=self.crypto_key, ciphertext=kv_ret)
        return {name: kv_ret}

    def _make_variable_pairs(
            self,
            usr_id: str,
            for_deletion: bool,
            group_id: str,
            var_name: Optional[str] = None,
            session_id: Optional[str] = None,
            user_var_value: Optional[str] = None,
            session_var_value: Optional[str] = None
    ) -> Tuple[str, str]:
        key, value = "", ""
        user_var_value = BaseMemoryManager.encrypt_memory_if_needed(key=self.crypto_key,
                                                                    plaintext=user_var_value)
        session_var_value = BaseMemoryManager.encrypt_memory_if_needed(key=self.crypto_key,
                                                                       plaintext=session_var_value)
        if var_name is not None:
            # 1) user_var
            if session_id is None:
                key = (
                    f"user_var{VariableManager.SEPARATOR}{usr_id}"
                    f"{VariableManager.SEPARATOR}{group_id}"
                    f"{VariableManager.SEPARATOR}{var_name}"
                )
                value = None if for_deletion else user_var_value
            # 2) session_var
            else:
                key = (
                    f"session_var{VariableManager.SEPARATOR}{usr_id}"
                    f"{VariableManager.SEPARATOR}{group_id}"
                    f"{VariableManager.SEPARATOR}{session_id}"
                    f"{VariableManager.SEPARATOR}{var_name}"
                )
                value = None if for_deletion else session_var_value
        return key, value

    @staticmethod
    def _check_user_and_group_id(user_id, group_id, context="Operation"):
        if not user_id or not user_id.strip():
            logger.error(f"{context} failed, user ID is empty")
        if not group_id or not group_id.strip():
            logger.error(f"{context} failed, group ID is empty")

    @staticmethod
    def _check_exist(variable_dict: dict[str, Any], variable_name: str) -> bool:
        if not variable_dict:
            return False

        if variable_name not in variable_dict.keys():
            return False

        if not variable_dict[variable_name]:
            return False

        return True
