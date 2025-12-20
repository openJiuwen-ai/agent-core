# !/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Tuple
from openjiuwen.core.memory.generation.memory_info import ExtractedData
from openjiuwen.core.memory.generation.variable_extractor import ComprehensionExtractor
from openjiuwen.core.memory.search.search_manager.search_manager import SearchManager
from openjiuwen.core.memory.mem_unit.memory_unit import MemoryType, BaseMemoryUnit, VariableUnit, UserProfileUnit
from openjiuwen.core.memory.generation.categorizer import Categorizer
from openjiuwen.core.memory.generation.user_profile_extractor import UserProfileExtractor
from openjiuwen.core.memory.generation.conflict_resolution import ConflictResolution
from openjiuwen.core.common.logging import logger
from openjiuwen.core.memory.config.config import MemoryConfig
from openjiuwen.core.utils.llm.base import BaseModelClient
from openjiuwen.core.utils.llm.messages import BaseMessage

category_to_class = {
    "user_profile": MemoryType.USER_PROFILE
}


async def _generate_extract(
        config: MemoryConfig,
        history_messages: list[BaseMessage],
        messages: list[BaseMessage],
        base_chat_model: Tuple[str, BaseModelClient]
) -> list[ExtractedData]:
    history_summary = ""
    for msg in history_messages:
        history_summary += f"{msg.role}: {msg.content}\n"
    return await ComprehensionExtractor.extract(
        messages,
        BaseMessage(content=history_summary, role=""),
        base_chat_model,
        config
    )


async def _generate_user_profile(
        history_messages: list[BaseMessage],
        messages: list[BaseMessage],
        base_chat_model: Tuple[str, BaseModelClient],
        user_define: dict[str, str] = None
) -> dict[str, str]:
    return await UserProfileExtractor.get_user_profile(
        messages,
        history_messages,
        base_chat_model,
        user_define
    )


async def _get_conflict_input(
        user_id: str,
        group_id: str,
        new_message: str,
        search_manager: SearchManager
):
    historical_profiles = []
    search_results = await search_manager.search(
        user_id=user_id,
        group_id=group_id,
        query=new_message,
        top_k=5,
        search_type=MemoryType.USER_PROFILE.value
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


class Generator:
    def __init__(self, search_manager: SearchManager) -> None:
        self._search_manager = search_manager

    async def gen_all_memory(self, **kwargs) -> list[BaseMemoryUnit]:
        """Generate all memory units based on input"""
        messages = kwargs.get("messages")
        config = kwargs.get("config")
        model = kwargs.get("base_chat_model")
        user_id = kwargs.get("user_id")
        group_id = kwargs.get("group_id")
        history_messages = kwargs.get("history_messages")
        message_mem_id = kwargs.get("message_mem_id")
        if not all([messages, config, user_id, group_id, model]):
            logger.error("messages, config, user_id, group_id, model are required parameters")
        categorizer = Categorizer()
        all_memory_results = []
        variable_units = await self.gen_extracted_data(
            messages=messages,
            user_id=user_id,
            group_id=group_id,
            history_messages=history_messages,
            config=config,
            base_chat_model=model
        )
        all_memory_results += variable_units
        if not config.enable_long_term_mem:
            logger.info("Not enable long term memory")
            return all_memory_results
        categories = await categorizer.get_categories(
            messages,
            history_messages,
            model,
        )
        merged_units = await self._categories_to_memory_unit(
            categories=categories,
            history_messages=history_messages,
            messages=messages,
            user_id=user_id,
            group_id=group_id,
            base_chat_model=model,
            message_mem_id=message_mem_id
        )
        all_memory_results += merged_units
        return all_memory_results

    async def gen_extracted_data(
            self,
            user_id: str,
            group_id: str,
            messages: list[BaseMessage],
            history_messages: list[BaseMessage],
            config: MemoryConfig,
            base_chat_model: Tuple[str, BaseModelClient]
    ) -> list[VariableUnit]:
        """Generate extracted variable memory units based on input"""
        extracted_data = await _generate_extract(
            config,
            history_messages,
            messages,
            base_chat_model
        )
        variable_units = []
        for tmp_data in extracted_data:
            variable_units.append(VariableUnit(
                user_id=user_id,
                group_id=group_id,
                mem_type=MemoryType.VARIABLE,
                variable_name=tmp_data.key,
                variable_mem=tmp_data.value
            ))
        return variable_units

    async def gen_user_profile(
            self,
            user_id: str,
            group_id: str,
            messages: list[BaseMessage],
            history_messages: list[BaseMessage],
            base_chat_model: Tuple[str, BaseModelClient],
            message_mem_id: str,
            user_define: dict[str, str] = None
    ) -> list[UserProfileUnit]:
        """Generate user profile memory unit based on input"""
        user_profile_memory = await _generate_user_profile(
            history_messages,
            messages,
            base_chat_model,
            user_define
        )
        user_profile_data = []
        for profile_type, profile_list in user_profile_memory.items():
            if not isinstance(profile_list, list):
                logger.warning(f"User profile extractor output format error: {profile_list} is not a list")
                continue
            for profile in profile_list:
                input_memories, input_memory_ids_map = await _get_conflict_input(
                    user_id,
                    group_id,
                    profile,
                    self._search_manager
                )
                tmp_conflict_info = await ConflictResolution.check_conflict(old_messages=input_memories,
                                                                            new_message=profile,
                                                                            base_chat_model=base_chat_model)
                conflict_info = _process_conflict_info(tmp_conflict_info, input_memory_ids_map)
                user_profile_data.append(UserProfileUnit(
                    user_id=user_id,
                    group_id=group_id,
                    profile_type=profile_type,
                    profile_mem=profile,
                    conflict_info=conflict_info,
                    mem_type=MemoryType.USER_PROFILE,
                    message_mem_id=message_mem_id
                ))
        return user_profile_data

    async def _categories_to_memory_unit(self,
                                         categories: list[str],
                                         history_messages: list[BaseMessage],
                                         messages: list[BaseMessage],
                                         user_id: str,
                                         group_id: str,
                                         base_chat_model: Tuple[str, BaseModelClient],
                                         message_mem_id: str,
                                         user_define: dict[str, str] = None
                                         ) -> list[BaseMemoryUnit]:
        memory_units = []
        for category in categories:
            if category not in category_to_class.keys():
                logger.warning(f"Unsupported memory category: {category}, skipped.")
                continue
            mem_class = category_to_class[category]
            if mem_class == MemoryType.USER_PROFILE:
                user_profile_units = await self.gen_user_profile(
                    user_id=user_id,
                    group_id=group_id,
                    history_messages=history_messages,
                    messages=messages,
                    base_chat_model=base_chat_model,
                    message_mem_id=message_mem_id,
                    user_define=user_define
                )
                memory_units += user_profile_units
        return memory_units
