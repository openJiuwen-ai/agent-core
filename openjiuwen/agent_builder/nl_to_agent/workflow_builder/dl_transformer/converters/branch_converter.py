# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from openjiuwen.agent_builder.nl_to_agent.workflow_builder.dl_transformer.converter_utils import ConverterUtils
from openjiuwen.agent_builder.nl_to_agent.workflow_builder.dl_transformer.converters.base import BaseConverter
from openjiuwen.agent_builder.nl_to_agent.workflow_builder.dl_transformer.models import SourceType, Edge


class BranchConverter(BaseConverter):
    BRANCH_OPERATOR_MAP = {
        "eq": "1",
        "not_eq": "2",
        "len_longer_than": "3",
        "len_longer_than_or_eq": "4",
        "len_shorter_than": "5",
        "len_shorter_than_or_eq": "6",
        "contain": "7",
        "not_contain": "8",
        "is_empty": "9",
        "is_not_empty": "10",
        "longer_than": "11",
        "longer_than_or_eq": "12",
        "short_than": "13",
        "short_than_or_eq": "14",
    }
    BRANCH_LOGIC_MAP = {
        "or": 1,
        "and": 2
    }
    
    @staticmethod
    def _convert_branches(conditions):
        branches = []
        for cond in conditions:
            if "expressions" in cond:
                branches.append(dict(
                    conditions=[BranchConverter._convert_expression(expr) for expr in cond["expressions"]],
                    logic=BranchConverter.BRANCH_LOGIC_MAP[cond["operator"]],
                    branchId=cond["branch"]
                ))
            elif cond["expression"] != "default":
                branches.append(dict(
                    conditions=[BranchConverter._convert_expression(cond["expression"])],
                    branchId=cond["branch"]
                ))
            else:
                branches.append(dict(conditions=[], branchId=cond["branch"]))
        return branches

    @staticmethod
    def _convert_expression(expression):
        operator = next((opt for opt in BranchConverter.BRANCH_OPERATOR_MAP if opt in expression), None)
        parts = expression.split(operator, 1)
        left_str = parts[0].strip() if parts else ""
        right_str = parts[1].strip() if len(parts) > 1 else ""
        left = BranchConverter._build_side(left_str)
        right = BranchConverter._build_side(right_str)
        if right:
            return dict(left=left, operator=BranchConverter.BRANCH_OPERATOR_MAP[operator], right=right)
        return dict(left=left, operator=BranchConverter.BRANCH_OPERATOR_MAP[operator])

    @staticmethod
    def _build_side(value_str):
        if not value_str:
            return None
        if "${" in value_str:
            return ConverterUtils.convert_ref_variable(value_str)
        return dict(
            type=SourceType.constant,
            content=value_str,
            schema=dict(
                type="string",
                extra={"weak": True}
            )
        )

    def _convert_specific_config(self):
        self.node.data.branches = self._convert_branches(self.node_data["parameters"]["conditions"])

    def _convert_edges(self):
        for cond in self.node_data["parameters"]["conditions"]:
            self.edges.append(
                Edge(sourceNodeID=self.node_data["id"], targetNodeID=cond["next"], sourcePortID=cond["branch"])
            )