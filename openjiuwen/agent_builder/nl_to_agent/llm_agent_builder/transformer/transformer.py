# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import json
import uuid
from datetime import datetime, timezone
from typing import List, Dict, Any

from openjiuwen.agent_builder.nl_to_agent.llm_agent_builder.transformer.llm_agent_template import LLM_AGENT_TEMPLATE

MS_PER_SECOND = 1000


class Transformer:
    @staticmethod
    def collect_plugin(tool_id_list: List[str],
                       plugin_dict: Dict[str, dict],
                       tool_id_map: Dict[str, str]) -> List[dict]:
        collected = []
        for tool_id in tool_id_list:
            if tool_id not in tool_id_map:
                continue

            plugin_id = tool_id_map[tool_id]
            plugin = plugin_dict.get(plugin_id, {})
            tool = plugin.get("tools", {}).get(tool_id, {})
            collected.append({
                "plugin_id": plugin_id,
                "plugin_name": plugin.get("plugin_name", ""),
                "tool_id": tool_id,
                "tool_name": tool.get("tool_name", ""),
            })
        return collected

    @staticmethod
    def collect_workflow(workflow_id_list: List[str],
                         workflow_dict: Dict[str, dict]) -> List[dict]:
        collected = []
        for workflow_id in workflow_id_list:
            workflow = workflow_dict.get(workflow_id, {})
            collected.append({
                "workflow_id": workflow_id,
                "workflow_name": workflow.get("workflow_name"),
                "workflow_version": workflow.get("workflow_version"),
                "description": workflow.get("workflow_desc"),
            })
        return collected

    def transform_to_dsl(self, agent_info: dict, resource: Dict[str, Any]) -> dict:
        dsl = LLM_AGENT_TEMPLATE.copy()
        dsl["agent_id"] = str(uuid.uuid4())
        dsl["name"] = agent_info.get("name", "")
        dsl["description"] = agent_info.get("description", "")
        dsl["configs"]["system_prompt"] = agent_info.get("prompt", "")
        dsl["opening_remarks"] = agent_info.get("opening_remarks", "")
        dsl["plugins"] = self.collect_plugin(
            agent_info.get("plugin", []), resource.get("plugin_dict"), resource.get("tool_id_map")
        )
        dsl["workflows"] = self.collect_workflow(
            agent_info.get("workflow", []), resource.get("workflow_dict")
        )

        now_ms_timestamp = int(datetime.now(timezone.utc).timestamp() * MS_PER_SECOND)
        dsl["create_time"] = now_ms_timestamp
        dsl["update_time"] = now_ms_timestamp
        return json.dumps(dsl, ensure_ascii=False)
