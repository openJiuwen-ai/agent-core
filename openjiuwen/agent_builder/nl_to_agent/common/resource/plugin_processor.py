# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import List, Dict, Tuple


class PluginProcessor:

    @staticmethod
    def preprocess(raw_plugins: List[dict]) -> Tuple[dict, dict]:
        def format_params(params):
            return [
                {"name": p.get("name", ""), "description": p.get("description", "")}
                for p in (params or [])
            ]

        plugin_dict = {}
        tool_plugin_id_map = {}
        for plugin in raw_plugins or []:
            plugin_id = plugin.get("plugin_id", "")
            if not plugin_id:
                continue

            formatted_tools = {}
            for tool in plugin.get("tools", []):
                tool_id = tool.get("tool_id")
                if not tool_id:
                    continue

                tool_plugin_id_map[tool_id] = plugin_id
                input_params = tool.get("input_parameters", [])
                output_params = tool.get("output_parameters", [])
                tool_dict = {
                    "tool_id": tool_id,
                    "tool_name": tool.get("tool_name", ""),
                    "tool_desc": tool.get("desc", ""),
                    "ori_inputs": input_params,
                    "ori_outputs": output_params,
                    "inputs_for_dl_gen": format_params(input_params),
                    "outputs_for_dl_gen": format_params(output_params)
                }
                formatted_tools[tool_id] = tool_dict

            plugin_dict[plugin_id] = {
                "plugin_id": plugin_id,
                "plugin_name": plugin.get("plugin_name", ""),
                "plugin_desc": plugin.get("plugin_desc", ""),
                "tools": formatted_tools,
            }

        return plugin_dict, tool_plugin_id_map
    
    @staticmethod
    def format_for_prompt(plugin_dict: Dict[str, dict]) -> List[dict]:
        result = []
        for plugin in plugin_dict.values():
            tools_brief = [
                {"tool_id": t["tool_id"], "tool_name": t["tool_name"], "tool_desc": t["tool_desc"]}
                for t in plugin.get("tools", {}).values()
            ]
            result.append({
                "plugin_id": plugin["plugin_id"],
                "plugin_name": plugin["plugin_name"],
                "plugin_desc": plugin["plugin_desc"],
                "tools": tools_brief,
            })
        return result
    
    @staticmethod
    def get_retrieved_info(tool_id_list: List[str],
                           plugin_dict: Dict[str, dict],
                           tool_plugin_id_map: Dict[str, str],
                           need_inputs_outputs: bool = True) -> List[dict]:
        tool_detail_list = []
        retrieved_plugin_dict = {}
        retrieved_tool_id_map = {}
        for tool_id in tool_id_list:
            if tool_id not in tool_plugin_id_map:
                continue

            plugin_id = tool_plugin_id_map[tool_id]
            tool_info = plugin_dict[plugin_id]["tools"][tool_id]
            tool_detail = {
                "tool_id": tool_id,
                "tool_name": tool_info.get("tool_name", ""),
                "tool_desc": tool_info.get("tool_desc", "")
            }
            if need_inputs_outputs:
                tool_detail.update({
                    "inputs": tool_info.get("inputs_for_dl_gen", ""),
                    "outputs": tool_info.get("outputs_for_dl_gen", "")
                })
            tool_detail_list.append(tool_detail)
            retrieved_plugin_dict.update({plugin_id: plugin_dict[plugin_id]})
            retrieved_tool_id_map.update({tool_id: plugin_id})
        return tool_detail_list, retrieved_plugin_dict, retrieved_tool_id_map
