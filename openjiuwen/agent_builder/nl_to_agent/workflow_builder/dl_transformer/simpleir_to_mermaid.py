# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import re
from collections import Counter
from typing import List, Dict


class SimpleIrToMermaid:

    @staticmethod
    def _edge_transform(nodes: List[Dict]) -> List[Dict]:
        edges = []
        for node in nodes:
            if "next" in node and node["next"]:
                edges_item = {
                    "来源": node["id"],
                    "去向": node["next"],
                }
                edges.append(edges_item)

            else:
                if node.get("type") != "End":
                    conditions = node.get("parameters", {}).get("conditions", [])
                    for con in conditions:
                        if "next" in con and con["next"]:
                            target_node = next((n for n in nodes if n["id"] == con.get("next", "")), None)
                            con_desc = con.get("description", "")
                            edges_item = {
                                "来源": node["id"],
                                "去向": con["next"],
                                "分支": con.get("branch", ""),
                                "描述": con_desc
                            }
                            edges.append(edges_item)
        return edges

    @staticmethod
    def _trans_to_mermaid(data: Dict) -> str:
        nodes = data.get("nodes", [])
        edges = data.get("edges", [])

        id_to_desc = {n["id"]: n.get("description", n["id"]) for n in nodes}
        count_edges = Counter(e["来源"] for e in edges)

        lines = ["graph TD"]

        for node_id, desc in id_to_desc.items():
            label = desc.replace('`', "'").replace('"', "'")
            lines.append(f"  {node_id}[节点{node_id}: {label}]")

        for e in edges:
            src, dst = e["来源"], e["去向"]
            edges_desc = e.get("描述", "").replace('`', "'").replace('"', "'")

            pattern = r"^当满足条件[(?P<cond>.+?)]"
            match = re.search(pattern, edges_desc)

            if count_edges[src] > 1 and edges_desc:
                label = match.group("cond") if match else edges_desc
                lines.append(f"  {src} -- {label} --> {dst}")
            else:
                lines.append(f"  {src} --> {dst}")

        return "\n".join(lines)

    @staticmethod
    def transform_to_mermaid(json_data: List[Dict]) -> str:
        edges = SimpleIrToMermaid._edge_transform(json_data)
        return SimpleIrToMermaid._trans_to_mermaid({"nodes": json_data, "edges": edges})
