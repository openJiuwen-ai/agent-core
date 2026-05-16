# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Optional, Tuple

from openjiuwen.core.foundation.llm import Model
from openjiuwen.core.memory.manage.search.search_manager import SearchManager, SearchParams
from openjiuwen.core.memory.prompts.prompt_applier import PromptApplier
from openjiuwen.core.memory.process.extract.common import ExtractMemoryParams, MemoryOperationParams
from openjiuwen.core.memory.process.extract.long_term_memory_extractor import LongTermMemoryExtractor
from openjiuwen.core.memory.manage.mem_model.memory_unit import MemoryType, BaseMemoryUnit, \
    VariableUnit, FragmentMemoryUnit, SummaryUnit, OperationType
from openjiuwen.core.memory.manage.mem_model.data_id_manager import DataIdManager
from openjiuwen.core.memory.process.extract.memory_analyzer import MemoryAnalyzer, VariableResult
from openjiuwen.core.common.logging import memory_logger
from openjiuwen.core.common.logging.events import LogEventType
from openjiuwen.core.memory.config.config import MemoryScopeConfig

category_to_class = {
    "user_profile": MemoryType.USER_PROFILE,
    "semantic_memory": MemoryType.SEMANTIC_MEMORY,
    "episodic_memory": MemoryType.EPISODIC_MEMORY,
}
operation_str_to_enum = {op.value: op for op in OperationType}


class Generator:
    def __init__(self, data_id_generator: DataIdManager, search_manager: Optional[SearchManager] = None):
        self.data_id_generator = data_id_generator
        self.search_manager = search_manager

    async def gen_all_memory(self, **kwargs) -> dict[str, list[BaseMemoryUnit]]:
        """Generate all memory units based on input"""
        messages = kwargs.get("messages")
        config = kwargs.get("config")
        model = kwargs.get("base_chat_model")
        user_id = kwargs.get("user_id")
        scope_id = kwargs.get("scope_id")
        history_messages = kwargs.get("history_messages")
        forbidden_variables = kwargs.get("forbidden_variables")
        message_mem_id = kwargs.get("message_mem_id")
        timestamp = kwargs.get("timestamp")
        summary_max_token = kwargs.get("summary_max_token")
        scope_config = kwargs.get("scope_config")
        semantic_store = kwargs.get("semantic_store")
        if not all([messages, config, user_id, scope_id, model]):
            memory_logger.error(
                "Messages, config, user_id, scope_id, model are required parameters",
                event_type=LogEventType.MEMORY_PROCESS,
                user_id=user_id,
                scope_id=scope_id
            )
            return {}

        extract_memory_params = ExtractMemoryParams(
            user_id=user_id,
            scope_id=scope_id,
            messages=messages,
            history_messages=history_messages,
            base_chat_model=model
        )

        all_memory_results = {}
        memory_analyze_res = await MemoryAnalyzer.analyze(
            messages=messages,
            history_messages=history_messages,
            base_chat_model=model,
            memory_config=config,
            summary_max_token=summary_max_token,
            scope_config=scope_config,
            forbidden_variables=forbidden_variables
        )
        variable_units = self._process_extracted_data(
            variable_results=memory_analyze_res.variables,
        )
        for unit in variable_units:
            mem_type = unit.mem_type.value
            if mem_type not in all_memory_results:
                all_memory_results[mem_type] = []
            all_memory_results[mem_type].append(unit)

        if not config.enable_long_term_mem:
            memory_logger.info(
                "Not enable long term memory",
                event_type=LogEventType.MEMORY_PROCESS,
                user_id=user_id,
                scope_id=scope_id,
            )
            return all_memory_results

        if config.enable_summary_memory:
            summary_unit = await self._process_summary_data(
                user_id=user_id,
                message_mem_id=message_mem_id,
                summary=memory_analyze_res.summary,
                timestamp=timestamp
            )
            summary_type = summary_unit.mem_type.value
            if summary_type not in all_memory_results:
                all_memory_results[summary_type] = []
            all_memory_results[summary_type].append(summary_unit)

        if not memory_analyze_res.has_key_information:
            return all_memory_results
        fragment_enable = {
            MemoryType.USER_PROFILE.value: config.enable_user_profile,
            MemoryType.SEMANTIC_MEMORY.value: config.enable_semantic_memory,
            MemoryType.EPISODIC_MEMORY.value: config.enable_episodic_memory,
        }

        try:
            merged_units = await self._categories_to_memory_unit(
                extract_memory_paras=extract_memory_params,
                message_mem_id=message_mem_id,
                timestamp=timestamp,
                scope_config=scope_config,
                semantic_store=semantic_store
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
        for unit in merged_units:
            mem_type = unit.mem_type.value
            if mem_type not in all_memory_results:
                all_memory_results[mem_type] = []
            if fragment_enable.get(mem_type, False):
                all_memory_results[mem_type].append(unit)
        memory_logger.info(
            "Memory units generated successfully",
            event_type=LogEventType.MEMORY_PROCESS,
            user_id=user_id,
            scope_id=scope_id,
            metadata={"all_memory_result": all_memory_results}
        )
        return all_memory_results

    async def _categories_to_memory_unit(
        self,
        extract_memory_paras: ExtractMemoryParams,
        message_mem_id: str,
        timestamp: str,
        scope_config: MemoryScopeConfig,
        semantic_store
    ) -> list[BaseMemoryUnit]:
        memory_units = []
        memory_dict = await LongTermMemoryExtractor.extract_long_term_memory(
            extract_memory_paras=extract_memory_paras,
            timestamp=timestamp,
            scope_config=scope_config
        )
        if memory_dict.get("has_explict_instruct", False):
            instruct_memories = memory_dict.get("instruct_memories", [])
            memory_operation_params = MemoryOperationParams(
                user_id=extract_memory_paras.user_id,
                scope_id=extract_memory_paras.scope_id,
                message_mem_id=message_mem_id,
                timestamp=timestamp,
                base_chat_model=extract_memory_paras.base_chat_model,
                semantic_store=semantic_store
            )
            memory_units.extend(await self._handle_memory_with_instruct(
                memory_operation_params=memory_operation_params,
                memory_list=instruct_memories
            ))

        memory_units.extend(await self._get_fragment_memory_unit(
            user_id=extract_memory_paras.user_id,
            message_mem_id=message_mem_id,
            memory_dict=memory_dict,
            timestamp=timestamp,
        ))
        return memory_units

    @staticmethod
    def _process_extracted_data(variable_results: list[VariableResult]) -> list[VariableUnit]:
        variable_units = []
        for tmp_data in variable_results:
            if not tmp_data.variable_value:
                continue
            variable_units.append(VariableUnit(
                variable_name=tmp_data.variable_key,
                variable_mem=tmp_data.variable_value
            ))
        return variable_units

    async def _process_summary_data(
            self,
            user_id: str,
            message_mem_id: str,
            summary: str,
            timestamp: str,
    ) -> SummaryUnit:
        mem_id = str(await self.data_id_generator.generate_next_id(user_id=user_id))
        return SummaryUnit(
            mem_id=mem_id,
            summary=summary,
            message_mem_id=message_mem_id,
            timestamp=timestamp,
        )

    async def _get_fragment_memory_unit(
            self,
            user_id: str,
            message_mem_id: str,
            memory_dict: dict,
            timestamp: str
    ) -> list[FragmentMemoryUnit]:
        """Generate fragment memory unit based on input"""
        fragment_mem_units = []
        for fragment_type, fragment_memories in memory_dict.items():
            mem_type = category_to_class.get(fragment_type, None)
            if not mem_type:
                continue
            for mem_content in fragment_memories:
                if not isinstance(mem_content, str):
                    content = mem_content.get("content")
                    if content:
                        mem_content = content
                    else:
                        mem_content = str(mem_content)
                mem_id = str(await self.data_id_generator.generate_next_id(user_id=user_id))
                fragment_mem_units.append(FragmentMemoryUnit(
                    mem_type=mem_type,
                    content=mem_content,
                    message_mem_id=message_mem_id,
                    timestamp=timestamp,
                    mem_id=mem_id,
                    operation_type=OperationType.ADD
                ))
        return fragment_mem_units

    async def _process_proactive_memory_data(
            self,
            user_id: str,
            message_mem_id: str,
            memory_list: list,
            timestamp: str
    ) -> list[FragmentMemoryUnit]:
        """Generate fragment memory unit from proactive memory list"""
        fragment_mem_units = []
        for mem_dict in memory_list:
            if not isinstance(mem_dict, dict):
                continue
            mem_instruct = str(mem_dict.get("mem_instruct", "")).lower()
            operation_type = operation_str_to_enum.get(mem_instruct, None)
            if operation_type != OperationType.ADD:
                continue
            fragment_type = mem_dict.get("mem_type")
            mem_type = category_to_class.get(fragment_type, None)
            if not mem_type:
                continue
            mem_content = mem_dict.get("mem_content")
            if not mem_content:
                continue
            if not isinstance(mem_content, str):
                content = mem_content.get("mem_content")
                if content:
                    mem_content = content
                else:
                    mem_content = str(mem_content)
            mem_id = str(await self.data_id_generator.generate_next_id(user_id=user_id))
            fragment_mem_units.append(FragmentMemoryUnit(
                mem_type=mem_type,
                content=mem_content,
                message_mem_id=message_mem_id,
                timestamp=timestamp,
                mem_id=mem_id,
                operation_type=operation_type
            ))
        return fragment_mem_units

    async def _semantic_validation(
            self,
            obtained_mems: list[dict],
            old_mem: str,
            base_chat_model: Model,
    ) -> list[tuple[str, str]]:
        """Validate semantic consistency between target and reference information"""
        ret_ids = []
        for obtained_mem in obtained_mems:
            prompt_content = PromptApplier().apply(
                "semantic_validation",
                {
                    "obtained_mem": obtained_mem.get("mem", ""),
                    "old_mem": old_mem,
                },
            )
            model_input = [{"role": "user", "content": prompt_content}]
            response = await base_chat_model.invoke(messages=model_input)
            if "CORRECT" in response.content.upper() and "WRONG" not in response.content.upper():
                memory_logger.debug(
                    f"semantic_validate_result: old_mem:{old_mem}, obtained_mem:{obtained_mem['mem']}, result: CORRECT",
                    event_type=LogEventType.MEMORY_PROCESS,
                )
                ret_ids.append((obtained_mem["id"], obtained_mem["mem"]))
            else:
                memory_logger.debug(
                    f"semantic_validate_result: old_mem:{old_mem}, obtained_mem:{obtained_mem['mem']}, result: WRONG",
                    event_type=LogEventType.MEMORY_PROCESS,
                )
        return ret_ids

    async def _handle_memory_with_instruct(
            self,
            memory_operation_params: MemoryOperationParams,
            memory_list: list
    ) -> list[FragmentMemoryUnit]:
        update_memories = []
        delete_memories = []
        for mem_dict in memory_list:
            if not isinstance(mem_dict, dict):
                continue
            mem_instruct = str(mem_dict.get("mem_instruct", "")).lower()
            if operation_str_to_enum.get(mem_instruct) == OperationType.UPDATE:
                update_memories.append(mem_dict)
            elif operation_str_to_enum.get(mem_instruct) == OperationType.DELETE:
                delete_memories.append(mem_dict)
        ret_memories = []

        ret_memories.extend(await self._process_memory_operations(
            memory_operation_params=memory_operation_params,
            memory_dicts=update_memories,
            operation_type=OperationType.UPDATE
        ))

        ret_memories.extend(await self._process_memory_operations(
            memory_operation_params=memory_operation_params,
            memory_dicts=delete_memories,
            operation_type=OperationType.DELETE
        ))

        return ret_memories

    async def _process_memory_operations(
            self,
            memory_operation_params: MemoryOperationParams,
            memory_dicts: list,
            operation_type: OperationType
    ) -> list[FragmentMemoryUnit]:
        """Process memory update or delete operations with semantic validation."""
        ret_memories = []
        for mem_dict in memory_dicts:
            old_mem = mem_dict.get("old_mem")
            if not old_mem:
                continue
            params = SearchParams(
                query=old_mem,
                scope_id=memory_operation_params.scope_id,
                top_k=1,
                user_id=memory_operation_params.user_id,
            )
            fragment_type = [MemoryType.USER_PROFILE.value, MemoryType.EPISODIC_MEMORY.value,
                             MemoryType.SEMANTIC_MEMORY.value]
            params.search_type = fragment_type
            search_data = await self.search_manager.search(params,
                                        semantic_store=memory_operation_params.semantic_store)
            search_data = sorted(search_data, key=lambda x: x.get("score", 0.0), reverse=True)
            obtained_mem = [search_data[0]] if search_data else []
            if not obtained_mem:
                continue
            mem_ids = await self._semantic_validation(
                obtained_mems=obtained_mem,
                old_mem=old_mem,
                base_chat_model=memory_operation_params.base_chat_model
            )
            for mem_id in mem_ids:
                mem_type_str = str(mem_dict.get("mem_type")).lower()
                ret_memories.append(FragmentMemoryUnit(
                    mem_type=category_to_class.get(mem_type_str),
                    content=str(mem_dict.get("mem_content")),
                    message_mem_id=memory_operation_params.message_mem_id,
                    timestamp=memory_operation_params.timestamp,
                    mem_id=mem_id[0],
                    operation_type=operation_type
                ))
        return ret_memories
