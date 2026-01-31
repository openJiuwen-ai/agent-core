# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from openjiuwen.core.memory.process.extract.common import ExtractMemoryParams
from openjiuwen.core.memory.process.extract.long_term_memory_extractor import LongTermMemoryExtractor
from openjiuwen.core.memory.manage.mem_model.memory_unit import MemoryType, BaseMemoryUnit, VariableUnit, \
    UserProfileUnit, SummaryUnit
from openjiuwen.core.memory.manage.mem_model.data_id_manager import DataIdManager
from openjiuwen.core.memory.process.extract.memory_analyzer import MemoryAnalyzer, VariableResult
from openjiuwen.core.common.logging import memory_logger
from openjiuwen.core.common.logging.events import LogEventType

category_to_class = {
    "user_profile": MemoryType.USER_PROFILE
}


class Generator:
    def __init__(self, data_id_generator: DataIdManager):
        self.data_id_generator = data_id_generator

    async def gen_all_memory(self, **kwargs) -> list[BaseMemoryUnit]:
        """Generate all memory units based on input"""
        messages = kwargs.get("messages")
        config = kwargs.get("config")
        model = kwargs.get("base_chat_model")
        user_id = kwargs.get("user_id")
        scope_id = kwargs.get("scope_id")
        history_messages = kwargs.get("history_messages")
        message_mem_id = kwargs.get("message_mem_id")
        timestamp = kwargs.get("timestamp")
        summary_max_token = kwargs.get("summary_max_token")
        if not all([messages, config, user_id, scope_id, model]):
            memory_logger.error(
                "Messages, config, user_id, scope_id, model are required parameters",
                event_type=LogEventType.MEMORY_PROCESS,
                user_id=user_id,
                scope_id=scope_id
            )
            return []

        extract_memory_params = ExtractMemoryParams(
            user_id=user_id,
            scope_id=scope_id,
            messages=messages,
            history_messages=history_messages,
            base_chat_model=model
        )

        all_memory_results = []
        memory_analyze_res = await MemoryAnalyzer.analyze(
            messages=messages,
            history_messages=history_messages,
            base_chat_model=model,
            memory_config=config,
            summary_max_token=summary_max_token
        )
        variable_units = self._process_extracted_data(
            user_id=user_id,
            scope_id=scope_id,
            variable_results=memory_analyze_res.variables,
        )
        all_memory_results += variable_units

        if not config.enable_long_term_mem:
            memory_logger.info(
                "Not enable long term memory",
                event_type=LogEventType.MEMORY_PROCESS,
                user_id=user_id,
                scope_id=scope_id,
            )
            return all_memory_results

        summary_unit = await self._process_summary_data(user_id=user_id,
                                                        scope_id=scope_id,
                                                        message_mem_id=message_mem_id,
                                                        summary=memory_analyze_res.summary,
                                                        timestamp=timestamp)
        all_memory_results.append(summary_unit)
        try:
            merged_units = await self._categories_to_memory_unit(
                categories=memory_analyze_res.categories,
                extract_memory_paras=extract_memory_params,
                message_mem_id=message_mem_id,
                timestamp=timestamp
            )
        except AttributeError as e:
            memory_logger.debug(
                "Get conflict info has attribute exception",
                event_type=LogEventType.MEMORY_PROCESS,
                user_id=user_id,
                scope_id=scope_id,
                exception=str(e)
            )
            return all_memory_results
        except ValueError as e:
            memory_logger.warning(
                "Get conflict info has value exception",
                event_type=LogEventType.MEMORY_PROCESS,
                user_id=user_id,
                scope_id=scope_id,
                exception=str(e)
            )
            return all_memory_results
        except BaseException as e:
            memory_logger.warning(
                "Get conflict info has exception",
                event_type=LogEventType.MEMORY_PROCESS,
                user_id=user_id,
                scope_id=scope_id,
                exception=str(e)
            )
            return all_memory_results
        all_memory_results += merged_units
        memory_logger.info(
            "Memory units generated successfully",
            event_type=LogEventType.MEMORY_PROCESS,
            user_id=user_id,
            scope_id=scope_id,
            metadata={"all_memory_result": all_memory_results}
        )
        return all_memory_results

    async def _categories_to_memory_unit(self,
                                         categories: list[str],
                                         extract_memory_paras: ExtractMemoryParams,
                                         message_mem_id: str,
                                         timestamp: str
                                         ) -> list[BaseMemoryUnit]:
        memory_units = []
        memory_dict = await LongTermMemoryExtractor.extract_long_term_memory(
            categories=categories,
            extract_memory_paras=extract_memory_paras,
            timestamp=timestamp,
        )
        memory_units.extend(await self._get_user_profile_unit(
            user_id=extract_memory_paras.user_id,
            scope_id=extract_memory_paras.scope_id,
            message_mem_id=message_mem_id,
            memory_dict=memory_dict,
            timestamp=timestamp,
        ))
        return memory_units

    @staticmethod
    def _process_extracted_data(
            user_id: str,
            scope_id: str,
            variable_results: list[VariableResult],
    ) -> list[VariableUnit]:
        variable_units = []
        for tmp_data in variable_results:
            if not tmp_data.variable_value:
                continue
            variable_units.append(VariableUnit(
                user_id=user_id,
                scope_id=scope_id,
                variable_name=tmp_data.variable_key,
                variable_mem=tmp_data.variable_value
            ))
        return variable_units

    async def _process_summary_data(
            self,
            user_id: str,
            scope_id: str,
            message_mem_id: str,
            summary: str,
            timestamp: str,
    ) -> SummaryUnit:
        mem_id = str(await self.data_id_generator.generate_next_id(user_id=user_id))
        return SummaryUnit(
            user_id=user_id,
            scope_id=scope_id,
            mem_id=mem_id,
            summary=summary,
            message_mem_id=message_mem_id,
            timestamp=timestamp,
        )

    async def _get_user_profile_unit(
            self,
            user_id: str,
            scope_id: str,
            message_mem_id: str,
            memory_dict: dict,
            timestamp: str
    ) -> list[UserProfileUnit]:
        """Generate user profile memory unit based on input"""
        user_profile_data = []
        user_profile_dict = memory_dict.get("user_profile", {})
        for profile_type, profile_list in user_profile_dict.items():
            if not isinstance(profile_list, list):
                memory_logger.warning(
                    "User profile extractor output format error: profile_list is not a list",
                    event_type=LogEventType.MEMORY_PROCESS,
                    user_id=user_id,
                    scope_id=scope_id,
                    metadata={"profile_list": profile_list}
                )
                continue
            for profile in profile_list:
                mem_id = str(await self.data_id_generator.generate_next_id(user_id=user_id))
                user_profile_data.append(UserProfileUnit(
                    user_id=user_id,
                    scope_id=scope_id,
                    profile_type=profile_type,
                    profile_mem=profile,
                    message_mem_id=message_mem_id,
                    timestamp=timestamp,
                    mem_id=mem_id,
                ))
        return user_profile_data
