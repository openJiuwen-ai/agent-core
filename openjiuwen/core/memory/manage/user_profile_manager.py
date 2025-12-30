# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from datetime import datetime, timezone
from typing import Any, List, Optional, Tuple

from openjiuwen.core.common.logging import logger
from openjiuwen.core.utils.llm.base import BaseModelClient
from openjiuwen.core.memory.store.base_semantic_store import BaseSemanticStore
from openjiuwen.core.memory.common.base import generate_idx_name, parse_memory_hit_infos
from openjiuwen.core.memory.generation.conflict_resolution import ConflictResolution
from openjiuwen.core.memory.manage.base_memory_manager import BaseMemoryManager
from openjiuwen.core.memory.manage.data_id_manager import DataIdManager
from openjiuwen.core.memory.mem_unit.memory_unit import UserProfileUnit, MemoryType, ConflictType, BaseMemoryUnit
from openjiuwen.core.memory.store.user_mem_store import UserMemStore


class UserProfileManager(BaseMemoryManager):
    CHECK_CONFLICT_OLD_MEMORY_NUM = 5

    def __init__(self,
                 semantic_recall_instance: BaseSemanticStore,
                 user_mem_store: UserMemStore,
                 data_id_generator: DataIdManager,
                 crypto_key: bytes):
        self.mem_store = user_mem_store
        self.semantic_recall = semantic_recall_instance
        self.date_user_profile_id = data_id_generator
        self.crypto_key = crypto_key

    @staticmethod
    def _process_conflict_info(conflict_info: list[dict], input_memory_ids_map: dict[int, str]) -> list[dict]:
        process_conflict_info = []
        for conflict in conflict_info:
            conf_id = int(conflict['id'])
            conf_mem = conflict['text']
            conf_event = conflict['event']
            if conf_id == 0:
                process_conflict_info.append({
                    "id": '-1',
                    "text": conf_mem,
                    "event": conf_event
                })
                continue
            map_id = input_memory_ids_map[conf_id]
            process_conflict_info.append({
                "id": map_id,
                "text": conf_mem,
                "event": conf_event
            })
        return process_conflict_info

    async def add(self, memory: BaseMemoryUnit, llm: Tuple[str, BaseModelClient] | None = None):
        if not isinstance(memory, UserProfileUnit):
            raise ValueError('user profile add Must pass UserProfileUnit class.')
        if not memory.user_id:
            raise ValueError('user_profile_manager add operation must pass user_id')
        if not memory.group_id:
            raise ValueError('user_profile_manager add operation must pass group_id')
        if not memory.profile_mem:
            raise ValueError('user_profile_manager add operation must pass profile_mem')
        if not memory.profile_type:
            raise ValueError('user_profile_manager add operation must pass profile_type')
        conflict_info = await self._get_conflict_info(memory=memory, llm=llm)
        for conflict in conflict_info:
            conf_id = conflict['id']
            conf_mem = conflict['text']
            conf_event = conflict['event']
            if not conf_mem or conf_mem == "":
                continue
            if conf_id == "-1" and conf_event == ConflictType.ADD.value:
                logger.debug(f"add conflict info: {conflict}")
                mem_id = await self._add_user_profile_memory(user_id=memory.user_id,
                                                             group_id=memory.group_id,
                                                             profile_type=memory.profile_type,
                                                             profile_mem=conf_mem,
                                                             source_id=memory.message_mem_id)
                await self._add_vector_user_profile_memory(user_id=memory.user_id,
                                                           group_id=memory.group_id,
                                                           memory_id=mem_id,
                                                           mem=conf_mem)
            elif conf_event == ConflictType.NONE.value:
                logger.debug(f"none conflict info: {conflict}, new_profile: {memory.profile_mem}")
            elif conf_event == ConflictType.UPDATE.value:
                logger.debug(f"update conflict info: {conflict}, update_profile: {memory.profile_mem}")
                await self.update(memory.user_id, memory.group_id, conf_id, memory.profile_mem)
            elif conf_event == ConflictType.DELETE.value:
                logger.debug(f"delete conflict info: {conflict}, new_profile: {memory.profile_mem}")
                await self.delete(memory.user_id, memory.group_id, conf_id)
            else:
                logger.debug(f"unknown conflict event: {conflict}")

    async def update(self, user_id: str, group_id: str, mem_id: str, new_memory: str, **kwargs) -> bool:
        time = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        encrypt_new_memory = BaseMemoryManager.encrypt_memory_if_needed(key=self.crypto_key, plaintext=new_memory)
        new_data = {'mem': encrypt_new_memory, 'time': time}
        await self.mem_store.update(mem_id=mem_id, user_id=user_id, group_id=group_id, data=new_data)
        table_name = generate_idx_name(user_id, group_id, MemoryType.USER_PROFILE.value)
        await self.semantic_recall.delete_docs([mem_id], table_name)
        # semantic memory embedding must not encrypt
        await self.semantic_recall.add_docs([(mem_id, new_memory)], table_name)
        return True

    async def search(self, user_id: str, group_id: str, query: str, top_k: int, **kwargs):
        mem_type = kwargs.get("mem_type", MemoryType.USER_PROFILE.value)
        mem_ids, scores = await self._recall_by_vector(query, user_id, group_id, top_k, mem_type)
        retrieve_res = await self.mem_store.batch_get(user_id=user_id, group_id=group_id, mem_ids=mem_ids)
        if retrieve_res is None:
            return None
        for item in retrieve_res:
            item["score"] = scores.get(item['id'], 0)
            item["mem"] = BaseMemoryManager.decrypt_memory_if_needed(key=self.crypto_key, ciphertext=item["mem"])
            item["context_summary"] = BaseMemoryManager.decrypt_memory_if_needed(key=self.crypto_key,
                                                                                 ciphertext=item["context_summary"])
        retrieve_res.sort(key=lambda x: scores.get(x["id"], 0), reverse=True)
        return retrieve_res

    async def get(self, user_id: str, group_id: str, mem_id: str) -> dict[str, Any] | None:
        retrieve_res = await self.mem_store.get(user_id=user_id, group_id=group_id, mem_id=mem_id)
        retrieve_res["mem"] = BaseMemoryManager.decrypt_memory_if_needed(key=self.crypto_key,
                                                                         ciphertext=retrieve_res["mem"])
        retrieve_res["context_summary"] = BaseMemoryManager.decrypt_memory_if_needed(key=self.crypto_key,
                                                                                     ciphertext=retrieve_res[
                                                                                         "context_summary"])
        return retrieve_res

    async def delete(self, user_id: str, group_id: str, mem_id: str, **kwargs):
        data = await self.mem_store.get(user_id=user_id, group_id=group_id, mem_id=mem_id)
        if data is None:
            logger.error(f"Delete user_profile in store failed, the mem of mem_id({mem_id}) is not exist.")
            return False
        mem_type = kwargs.get("mem_type", MemoryType.USER_PROFILE.value)
        await self.mem_store.delete(mem_id=mem_id, user_id=user_id, group_id=group_id)
        await self._delete_vector_user_profile_memory(memory_id=[mem_id], user_id=user_id,
                                                      group_id=group_id, mem_type=mem_type)
        return True

    async def delete_by_user_id(self, user_id: str, group_id: str):
        data = await self.mem_store.get_all(user_id=user_id, group_id=group_id, mem_type=MemoryType.USER_PROFILE.value)
        if data is None:
            logger.error(f"Delete user_profile in store failed, the mem of user_id({user_id}) is not exist.")
            return False
        mem_ids = [item['id'] for item in data]
        await self.mem_store.batch_delete(user_id=user_id, group_id=group_id, mem_ids=mem_ids)
        await self._delete_vector_user_profile_memory(memory_id=mem_ids, user_id=user_id,
                                                      group_id=group_id, mem_type=MemoryType.USER_PROFILE.value)
        return True

    async def list_user_profile(self, user_id: str, group_id: str, profile_type: Optional[str] = None,
                                mem_type=MemoryType.USER_PROFILE) -> list[dict[str, Any]]:
        datas = await self.mem_store.get_all(user_id=user_id, group_id=group_id, mem_type=mem_type.value)
        if not datas:
            logger.debug(f"End to get user profile, result is None, "
                         f"params user_id:{user_id}, group_id:{group_id}, mem_type:{mem_type}")
            return []
        new_datas = []
        if profile_type is not None:
            for data in datas:
                if data['profile_type'] == profile_type:
                    new_datas.append(data)
        else:
            new_datas = datas
        for data in new_datas:
            data["mem"] = BaseMemoryManager.decrypt_memory_if_needed(key=self.crypto_key,
                                                                     ciphertext=data["mem"])
            data["context_summary"] = BaseMemoryManager.decrypt_memory_if_needed(key=self.crypto_key,
                                                                                 ciphertext=data["context_summary"])
        new_datas.sort(key=lambda x: (x['mem'], x['timestamp']), reverse=True)
        return new_datas

    async def _recall_by_vector(self, query: str, user_id: str, group_id: str, top_k: int = 5,
                                mem_type=MemoryType.USER_PROFILE.value) -> tuple[List[str], dict[str, float]]:
        table_name = generate_idx_name(user_id, group_id, mem_type)
        memory_hit_info = await self.semantic_recall.search(query, table_name, top_k)
        return parse_memory_hit_infos(memory_hit_info)

    async def _get_conflict_input(
            self,
            user_id: str,
            group_id: str,
            new_memory: str
    ):
        historical_profiles = []
        search_results = await self.search(
            user_id=user_id,
            group_id=group_id,
            query=new_memory,
            top_k=UserProfileManager.CHECK_CONFLICT_OLD_MEMORY_NUM
        )
        for search_result in search_results:
            historical_profiles.append((
                search_result['id'],
                search_result['mem'],
                search_result['score']
            ))
        input_memory_ids_map: dict[int, str] = {}
        input_memories: list[str] = []
        i = 1
        for historical in historical_profiles:
            mem_id, mem_content, _ = historical
            input_memories.append(mem_content)
            input_memory_ids_map[i] = mem_id
            i += 1
        return input_memories, input_memory_ids_map

    async def _get_conflict_info(self,
                                 memory: UserProfileUnit,
                                 llm: Tuple[str, BaseModelClient],
                                 ) -> list[dict[str, Any]]:
        input_memories, input_memory_ids_map = await self._get_conflict_input(
            user_id=memory.user_id,
            group_id=memory.group_id,
            new_memory=memory.profile_mem
        )
        tmp_conflict_info = await ConflictResolution.check_conflict(old_messages=input_memories,
                                                                    new_message=memory.profile_mem,
                                                                    base_chat_model=llm)
        return UserProfileManager._process_conflict_info(tmp_conflict_info, input_memory_ids_map)

    async def _add_user_profile_memory(
            self,
            is_implicit: bool = False,
            mem_type: str = MemoryType.USER_PROFILE.value,
            user_id: Optional[str] = None,
            group_id: Optional[str] = None,
            profile_type: Optional[str] = None,
            profile_mem: Optional[str] = None,
            source_id: Optional[str] = None,
            reasoning: Optional[str] = None,
            context_summary: Optional[str] = ""
    ) -> str:
        mem_id = str(await self.date_user_profile_id.generate_next_id(user_id=user_id))
        time = datetime.now(timezone.utc)
        profile_mem = BaseMemoryManager.encrypt_memory_if_needed(key=self.crypto_key,
                                                                 plaintext=profile_mem)
        context_summary = BaseMemoryManager.encrypt_memory_if_needed(key=self.crypto_key,
                                                                     plaintext=context_summary)
        data = {
            'id': mem_id,
            'user_id': user_id or '',
            'group_id': group_id or '',
            'is_implicit': is_implicit,
            'profile_type': profile_type,
            'mem': profile_mem,
            'source_id': source_id,
            'reasoning': reasoning,
            'context_summary': context_summary,
            'mem_type': mem_type,
            'timestamp': time.strftime('%Y-%m-%d %H:%M:%S')
        }
        await self.mem_store.write(user_id=user_id, group_id=group_id, mem_id=mem_id, data=data)
        return mem_id

    async def _add_vector_user_profile_memory(
            self, user_id: str, group_id: str, memory_id: str,
            mem: str, mem_type: str = MemoryType.USER_PROFILE.value):
        if self.semantic_recall:
            table_name = generate_idx_name(user_id, group_id, mem_type)
            await self.semantic_recall.add_docs([(memory_id, mem)], table_name)
        else:
            raise ValueError('vector store must not be None')

    async def _delete_vector_user_profile_memory(
            self, user_id: str, group_id: str,
            memory_id: List[str], mem_type: str = MemoryType.USER_PROFILE.value):
        if self.semantic_recall:
            table_name = generate_idx_name(user_id, group_id, mem_type)
            await self.semantic_recall.delete_docs(memory_id, table_name)
        else:
            raise ValueError('vector store must not be None')
