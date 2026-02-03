# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from datetime import datetime, timezone
from typing import Any, List, Optional, Tuple

from pydantic import BaseModel, Field
from openjiuwen.core.foundation.llm import Model
from openjiuwen.core.memory.common.base import generate_idx_name, parse_memory_hit_infos
from openjiuwen.core.memory.manage.update.conflict_resolution import ConflictResolution
from openjiuwen.core.memory.manage.index.base_memory_manager import BaseMemoryManager
from openjiuwen.core.memory.manage.mem_model.data_id_manager import DataIdManager
from openjiuwen.core.memory.manage.mem_model.memory_unit import UserProfileUnit, MemoryType, ConflictType, \
    BaseMemoryUnit
from openjiuwen.core.memory.manage.mem_model.semantic_store import SemanticStore
from openjiuwen.core.memory.manage.mem_model.user_mem_store import UserMemStore
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import memory_logger
from openjiuwen.core.common.logging.events import LogEventType


class UserProfileSearchParams(BaseModel):
    is_implicit: bool = False
    mem_type: str = Field(default=MemoryType.USER_PROFILE.value)
    user_id: Optional[str] = None
    scope_id: Optional[str] = None
    profile_type: Optional[str] = None
    profile_mem: Optional[str] = None
    source_id: Optional[str] = None
    reasoning: Optional[str] = None
    context_summary: Optional[str] = ""


class UserProfileManager(BaseMemoryManager):
    CHECK_CONFLICT_OLD_MEMORY_NUM = 5

    def __init__(self,
                 semantic_recall_instance: SemanticStore,
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

    async def add(self, memory: BaseMemoryUnit, llm: Tuple[str, Model] | None = None):
        if not isinstance(memory, UserProfileUnit):
            raise build_error(
                StatusCode.MEMORY_ADD_MEMORY_EXECUTION_ERROR,
                memory_type="user profile",
                error_msg=f"user profile add Must pass UserProfileUnit class",
            )
        if not memory.user_id:
            raise build_error(
                StatusCode.MEMORY_ADD_MEMORY_EXECUTION_ERROR,
                memory_type="user profile",
                error_msg=f"user_profile_manager add operation must pass user_id",
            )
        if not memory.scope_id:
            raise build_error(
                StatusCode.MEMORY_ADD_MEMORY_EXECUTION_ERROR,
                memory_type="user profile",
                error_msg=f"user_profile_manager add operation must pass scope_id",
            )
        if not memory.profile_mem:
            raise build_error(
                StatusCode.MEMORY_ADD_MEMORY_EXECUTION_ERROR,
                memory_type="user profile",
                error_msg=f"user_profile_manager add operation must pass profile_mem",
            )
        if not memory.profile_type:
            raise build_error(
                StatusCode.MEMORY_ADD_MEMORY_EXECUTION_ERROR,
                memory_type="user profile",
                error_msg=f"user_profile_manager add operation must pass profile_type",
            )
        conflict_info = await self._get_conflict_info(memory=memory, llm=llm)
        for conflict in conflict_info:
            conf_id = conflict['id']
            conf_mem = conflict['text']
            conf_event = conflict['event']
            if not conf_mem or conf_mem == "":
                continue
            if conf_id == "-1" and conf_event == ConflictType.ADD.value:
                memory_logger.debug(
                    "Add conflict info",
                    memory_type="user_profile",
                    metadata={"conflict": conflict},
                    event_type=LogEventType.MEMORY_STORE,
                    user_id=memory.user_id,
                    scope_id=memory.scope_id
                )
                user_profile_search_param = UserProfileSearchParams(
                    user_id=memory.user_id,
                    scope_id=memory.scope_id,
                    profile_type=memory.profile_type,
                    profile_mem=conf_mem,
                    source_id=memory.message_mem_id
                )
                mem_id = await self._add_user_profile_memory(user_profile_search_param)
                vector_success = await self._add_vector_user_profile_memory(user_id=memory.user_id,
                                                                           scope_id=memory.scope_id,
                                                                           memory_id=mem_id,
                                                                           mem=conf_mem)
                if not vector_success:
                    await self.delete(user_id=memory.user_id, scope_id=memory.scope_id, mem_id=mem_id)
                    raise build_error(
                        StatusCode.MEMORY_ADD_MEMORY_EXECUTION_ERROR,
                        memory_type="user profile",
                        error_msg=f"user_profile_manager add vector store failed",
                    )
            elif conf_event == ConflictType.NONE.value:
                memory_logger.debug(
                    "None conflict info",
                    memory_type="user_profile",
                    metadata={"conflict": conflict, "new_profile": memory.profile_mem},
                    event_type=LogEventType.MEMORY_STORE,
                    user_id=memory.user_id,
                    scope_id=memory.scope_id
                )
            elif conf_event == ConflictType.UPDATE.value:
                memory_logger.debug(
                    "Update conflict info",
                    memory_type="user_profile",
                    metadata={"conflict": conflict, "new_profile": memory.profile_mem},
                    event_type=LogEventType.MEMORY_STORE,
                    user_id=memory.user_id,
                    scope_id=memory.scope_id
                )
                await self.update(memory.user_id, memory.scope_id, conf_id, memory.profile_mem)
            elif conf_event == ConflictType.DELETE.value:
                memory_logger.debug(
                    "Delete conflict info",
                    memory_type="user_profile",
                    metadata={"conflict": conflict, "new_profile": memory.profile_mem},
                    event_type=LogEventType.MEMORY_STORE,
                    user_id=memory.user_id,
                    scope_id=memory.scope_id
                )
                await self.delete(memory.user_id, memory.scope_id, conf_id)
            else:
                memory_logger.debug(
                    "Unknown conflict event",
                    memory_type="user_profile",
                    metadata={"conflict": conflict},
                    event_type=LogEventType.MEMORY_STORE,
                    user_id=memory.user_id,
                    scope_id=memory.scope_id
                )

    async def update(self, user_id: str, scope_id: str, mem_id: str, new_memory: str, **kwargs) -> bool:
        time = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        encrypt_new_memory = BaseMemoryManager.encrypt_memory_if_needed(key=self.crypto_key, plaintext=new_memory)
        new_data = {'mem': encrypt_new_memory, 'time': time}
        await self.mem_store.update(mem_id=mem_id, user_id=user_id, scope_id=scope_id, data=new_data)
        table_name = generate_idx_name(user_id, scope_id, MemoryType.USER_PROFILE.value)
        await self.semantic_recall.delete_docs([mem_id], table_name)
        # semantic memory embedding must not encrypt
        await self.semantic_recall.add_docs([(mem_id, new_memory)], table_name, scope_id=scope_id)
        return True

    async def search(self, user_id: str, scope_id: str, query: str, top_k: int, **kwargs):
        mem_type = kwargs.get("mem_type", MemoryType.USER_PROFILE.value)
        mem_ids, scores = await self._recall_by_vector(query, user_id, scope_id, top_k, mem_type)
        retrieve_res = await self.mem_store.batch_get(user_id=user_id, scope_id=scope_id, mem_ids=mem_ids)
        if retrieve_res is None:
            return None
        for item in retrieve_res:
            item["score"] = scores.get(item['id'], 0)
            item["mem"] = BaseMemoryManager.decrypt_memory_if_needed(key=self.crypto_key, ciphertext=item["mem"])
            item["context_summary"] = BaseMemoryManager.decrypt_memory_if_needed(key=self.crypto_key,
                                                                                 ciphertext=item["context_summary"])
        retrieve_res.sort(key=lambda x: scores.get(x["id"], 0), reverse=True)
        return retrieve_res

    async def get(self, user_id: str, scope_id: str, mem_id: str) -> dict[str, Any] | None:
        retrieve_res = await self.mem_store.get(user_id=user_id, scope_id=scope_id, mem_id=mem_id)
        retrieve_res["mem"] = BaseMemoryManager.decrypt_memory_if_needed(key=self.crypto_key,
                                                                         ciphertext=retrieve_res["mem"])
        retrieve_res["context_summary"] = BaseMemoryManager.decrypt_memory_if_needed(key=self.crypto_key,
                                                                                     ciphertext=retrieve_res[
                                                                                         "context_summary"])
        return retrieve_res

    async def delete(self, user_id: str, scope_id: str, mem_id: str, **kwargs):
        data = await self.mem_store.get(user_id=user_id, scope_id=scope_id, mem_id=mem_id)
        if data is None:
            memory_logger.error(
                "Delete user_profile in store failed",
                memory_type="user_profile",
                memory_id=[mem_id],
                event_type=LogEventType.MEMORY_STORE,
                user_id=user_id,
                scope_id=scope_id
            )
            return False
        mem_type = kwargs.get("mem_type", MemoryType.USER_PROFILE.value)
        await self.mem_store.delete(mem_id=mem_id, user_id=user_id, scope_id=scope_id)
        await self._delete_vector_user_profile_memory(memory_id=[mem_id], user_id=user_id,
                                                      scope_id=scope_id, mem_type=mem_type)
        return True

    async def delete_by_user_id(self, user_id: str, scope_id: str):
        data = await self.mem_store.get_all(user_id=user_id, scope_id=scope_id, mem_type=MemoryType.USER_PROFILE.value)
        if data is None:
            memory_logger.error(
                "Delete user_profile in store failed, the mem of user_id is not exist.",
                memory_type="user_profile",
                event_type=LogEventType.MEMORY_STORE,
                user_id=user_id,
                scope_id=scope_id
            )
            return False
        mem_ids = [item['id'] for item in data]
        await self.mem_store.batch_delete(user_id=user_id, scope_id=scope_id, mem_ids=mem_ids)
        await self._delete_vector_store_table(user_id=user_id, scope_id=scope_id,
                                              mem_type=MemoryType.USER_PROFILE.value)
        return True

    async def list_user_profile(self, user_id: str, scope_id: str, profile_type: Optional[str] = None,
                                mem_type=MemoryType.USER_PROFILE) -> list[dict[str, Any]]:
        datas = await self.mem_store.get_all(user_id=user_id, scope_id=scope_id, mem_type=mem_type.value)
        if not datas:
            memory_logger.debug(
                "End to get user profile, result is None.",
                memory_type=mem_type,
                event_type=LogEventType.MEMORY_STORE,
                user_id=user_id,
                scope_id=scope_id
            )
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

    async def _recall_by_vector(self, query: str, user_id: str, scope_id: str, top_k: int = 5,
                                mem_type=MemoryType.USER_PROFILE.value) -> tuple[List[str], dict[str, float]]:
        table_name = generate_idx_name(user_id, scope_id, mem_type)
        memory_hit_info = await self.semantic_recall.search(query=query, table_name=table_name, top_k=top_k)
        return parse_memory_hit_infos(memory_hit_info)

    async def _get_conflict_input(
            self,
            user_id: str,
            scope_id: str,
            new_memory: str
    ):
        historical_profiles = []
        search_results = await self.search(
            user_id=user_id,
            scope_id=scope_id,
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
                                 llm: Tuple[str, Model] | None,
                                 ) -> list[dict[str, Any]]:
        input_memories, input_memory_ids_map = await self._get_conflict_input(
            user_id=memory.user_id,
            scope_id=memory.scope_id,
            new_memory=memory.profile_mem
        )
        tmp_conflict_info = await ConflictResolution.check_conflict(old_messages=input_memories,
                                                                    new_message=memory.profile_mem,
                                                                    base_chat_model=llm)
        return UserProfileManager._process_conflict_info(tmp_conflict_info, input_memory_ids_map)


    async def _add_user_profile_memory(self, req: UserProfileSearchParams) -> str:
        mem_id = str(await self.date_user_profile_id.generate_next_id(user_id=req.user_id))

        time = datetime.now(timezone.utc)
        profile_mem = BaseMemoryManager.encrypt_memory_if_needed(key=self.crypto_key,
                                                                 plaintext=req.profile_mem)
        context_summary = BaseMemoryManager.encrypt_memory_if_needed(key=self.crypto_key,
                                                                     plaintext=req.context_summary)
        data = {
            'id': mem_id,
            'user_id': req.user_id or '',
            'scope_id': req.scope_id or '',
            'is_implicit': req.is_implicit,
            'profile_type': req.profile_type,
            'mem': profile_mem,
            'source_id': req.source_id,
            'reasoning': req.reasoning,
            'context_summary': context_summary,
            'mem_type': req.mem_type,
            'timestamp': time.strftime('%Y-%m-%d %H:%M:%S')
        }
        await self.mem_store.write(user_id=req.user_id, scope_id=req.scope_id, mem_id=mem_id, data=data)
        return mem_id

    async def _add_vector_user_profile_memory(
            self, user_id: str, scope_id: str, memory_id: str,
            mem: str, mem_type: str = MemoryType.USER_PROFILE.value) -> bool:
        if self.semantic_recall:
            table_name = generate_idx_name(user_id, scope_id, mem_type)
            return await self.semantic_recall.add_docs([(memory_id, mem)], table_name, scope_id=scope_id)
        else:
            raise build_error(
                StatusCode.MEMORY_ADD_MEMORY_EXECUTION_ERROR,
                memory_type="user profile",
                error_msg=f"vector store must not be None",
            )

    async def _delete_vector_user_profile_memory(
            self, user_id: str, scope_id: str,
            memory_id: List[str], mem_type: str = MemoryType.USER_PROFILE.value):
        if self.semantic_recall:
            table_name = generate_idx_name(user_id, scope_id, mem_type)
            await self.semantic_recall.delete_docs(memory_id, table_name)
        else:
            raise build_error(
                StatusCode.MEMORY_DELETE_MEMORY_EXECUTION_ERROR,
                memory_type="user profile",
                error_msg=f"vector store must not be None",
            )

    async def _delete_vector_store_table(self, user_id: str, scope_id: str, mem_type):
        if self.semantic_recall:
            table_name = generate_idx_name(usr_id=user_id, scope_id=scope_id, mem_type=mem_type)
            await self.semantic_recall.delete_table(table_name=table_name)
        else:
            raise build_error(
                StatusCode.MEMORY_DELETE_MEMORY_EXECUTION_ERROR,
                memory_type="user profile",
                error_msg=f"vector store must not be None",
            )