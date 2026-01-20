# !/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from dataclasses import dataclass
from typing import Tuple
from openjiuwen.core.common.logging import logger
from openjiuwen.core.memory.config.config import AgentMemoryConfig
from openjiuwen.core.memory.process.extract.categorizer import Categorizer
from openjiuwen.core.memory.process.extract.memory_info import ExtractedData
from openjiuwen.core.memory.process.extract.user_profile_extractor import UserProfileExtractor
from openjiuwen.core.memory.process.extract.variable_extractor import ComprehensionExtractor
from openjiuwen.core.memory.manage.mem_model.memory_unit import MemoryType, BaseMemoryUnit, VariableUnit, \
    UserProfileUnit
from openjiuwen.core.foundation.llm.schema.message import BaseMessage
from openjiuwen.core.foundation.llm.model import Model

category_to_class = {
    "user_profile": MemoryType.USER_PROFILE
}


@dataclass
class ExtractMemoryParams:
    user_id: str
    scope_id: str
    messages: list[BaseMessage]
    history_messages: list[BaseMessage]
    base_chat_model: Tuple[str, Model]


async def _generate_extract(
        config: AgentMemoryConfig,
        history_messages: list[BaseMessage],
        messages: list[BaseMessage],
        base_chat_model: Tuple[str, Model]
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


class Generator:
    async def gen_all_memory(self, **kwargs) -> list[BaseMemoryUnit]:
        """Generate all memory units based on input"""
        messages = kwargs.get("messages")
        config = kwargs.get("config")
        model = kwargs.get("base_chat_model")
        user_id = kwargs.get("user_id")
        scope_id = kwargs.get("scope_id")
        history_messages = kwargs.get("history_messages")
        message_mem_id = kwargs.get("message_mem_id")
        if not all([messages, config, user_id, scope_id, model]):
            logger.error("messages, config, user_id, scope_id, model are required parameters")
            return []

        extract_memory_params = ExtractMemoryParams(
            user_id=user_id,
            scope_id=scope_id,
            messages=messages,
            history_messages=history_messages,
            base_chat_model=model
        )

        categorizer = Categorizer()
        all_memory_results = []
        variable_units = await self.gen_extracted_data(
            extract_memory_paras=extract_memory_params,
            config=config,
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

        try:
            merged_units = await self._categories_to_memory_unit(
                categories=categories,
                extract_memory_paras=extract_memory_params,
                message_mem_id=message_mem_id
            )
        except AttributeError as e:
            logger.debug(f"Get conflict info has attribute exception: {str(e)}")
            return all_memory_results
        except ValueError as e:
            logger.warning(f"Get conflict info has value exception: {str(e)}")
            return all_memory_results
        except BaseException as e:
            logger.warning(f"Get conflict info has exception: {str(e)}")
            return all_memory_results
        all_memory_results += merged_units
        return all_memory_results

    async def gen_extracted_data(
            self,
            extract_memory_paras: ExtractMemoryParams,
            config: AgentMemoryConfig,
    ) -> list[VariableUnit]:
        """Generate extracted variable memory units based on input"""
        extracted_data = await _generate_extract(
            config,
            extract_memory_paras.history_messages,
            extract_memory_paras.messages,
            extract_memory_paras.base_chat_model
        )
        variable_units = []
        for tmp_data in extracted_data:
            variable_units.append(VariableUnit(
                user_id=extract_memory_paras.user_id,
                scope_id=extract_memory_paras.scope_id,
                variable_name=tmp_data.key,
                variable_mem=tmp_data.value
            ))
        return variable_units

    async def gen_user_profile(
            self,
            extract_memory_paras: ExtractMemoryParams,
            message_mem_id: str,
            user_define: dict[str, str] = None
    ) -> list[UserProfileUnit]:
        """Generate user profile memory unit based on input"""
        user_profile_memory = await UserProfileExtractor.get_user_profile(
            messages=extract_memory_paras.messages,
            history_messages=extract_memory_paras.history_messages,
            base_chat_model=extract_memory_paras.base_chat_model,
            user_define=user_define)
        user_profile_data = []
        for profile_type, profile_list in user_profile_memory.items():
            if not isinstance(profile_list, list):
                logger.warning(f"User profile extractor output format error: {profile_list} is not a list")
                continue
            for profile in profile_list:
                user_profile_data.append(UserProfileUnit(
                    user_id=extract_memory_paras.user_id,
                    scope_id=extract_memory_paras.scope_id,
                    profile_type=profile_type,
                    profile_mem=profile,
                    message_mem_id=message_mem_id,
                ))
        return user_profile_data

    async def _categories_to_memory_unit(self,
                                         categories: list[str],
                                         extract_memory_paras: ExtractMemoryParams,
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
                    extract_memory_paras=extract_memory_paras,
                    message_mem_id=message_mem_id,
                    user_define=user_define
                )
                memory_units += user_profile_units
        return memory_units