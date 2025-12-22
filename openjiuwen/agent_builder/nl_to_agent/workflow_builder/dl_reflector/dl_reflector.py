# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import json
import re


def extract_placeholder_content(input_str: str) -> tuple[bool, list[str]]:
    pattern = r'\$\{([^}]+)\}'
    matches = re.findall(pattern, input_str)    
    has_placeholder = len(matches) > 0
    return has_placeholder, matches


class Reflector:
    def __init__(self):
        self.available_node_types = {'Start', 'End', 'Output', 'LLM', 'Questioner',
                                     'Plugin', 'Code', 'Branch', 'IntentDetection'}
        self.available_variable_types = {'String', 'Integer', 'Number', 'Boolean', 'Object',
                               'Array<String>', 'Array<Integer>', 'Array<Number>', 'Array<Boolean>', 'Array<Object>'}
        self.available_condition_operators = {"eq", "not_eq", "contain", "not_contain", "longer_than",
                                "longer_than_or_eq", "short_than", "short_than_or_eq", "is_empty", "is_not_empty"}
        self.available_node_outputs = set()
        self.node_ids = []
        self.node_ids_of_next = set()

        self.errors = []
        self.check_functions = {
            'Start': self._check_start_node,
            'End': self._check_end_node,
            'Output': self._check_output_node,
            'LLM': self._check_llm_node,
            'Questioner': self._check_questioner_node,
            'Plugin': self._check_plugin_node,
            'Code': self._check_code_node,
            'Branch': self._check_branch_node,
            'IntentDetection': self._check_intent_detection_node
        }

    def check_format(self, generated_dl: str):
        try:
            generated_dl_dict = json.loads(generated_dl)
        except Exception as e:
            self.errors.append(f"JSON格式错误: {str(e)}")
            return

        # check each node
        for node_index, node_content in enumerate(generated_dl_dict):
            basic_has_error = self._basic_check(node_content, node_index)
            if basic_has_error:
                continue
            self.check_functions[node_content["type"]](node_content)

        # check next
        for node_id in self.node_ids_of_next:
            if node_id not in self.node_ids:
                self.errors.append(f"节点ID错误: {node_id} 不存在")

    def reset(self):
        self.available_node_outputs = set()
        self.node_ids = []
        self.node_ids_of_next = set()
        self.errors = []

    def _basic_check(self, node_content: dict, node_index: int) -> bool:
        """
        Check the generated node: 
        1. The type is correct 
        2. No missing key
        3. The id is unique
        4. The type is in the available node types
        """
        if not isinstance(node_content, dict):
            self.errors.append(f"第{node_index+1}个节点类型错误: 必须为字典类型!")
            return True
        for key_item in ['id', 'type', 'description', 'parameters']:
            if key_item not in node_content:
                self.errors.append(f"第{node_index+1}个节点中缺失'{key_item}'属性")
                return True
        if node_content["id"] in self.node_ids:
            self.errors.append(f"第{node_index+1}个节点ID错误: {node_content['id']} 已存在")
            return True
        self.node_ids.append(node_content["id"])
        if node_content["type"] not in self.available_node_types:
            self.errors.append(f"第{node_index+1}个节点类型错误: {node_content['type']} 不在可用节点类型中")
            return True
        return False

    def _check_start_node(self, node_content: dict):
        self._check_outputs_list(node_content)
        if not self.errors and {'name': 'query', 'description': '用户输入'} not in node_content['parameters']['outputs']:
            self.errors.append("Start节点的'parameters'中的'outputs'列表中必须包含{'name': 'query', 'description': '用户输入'}")
        self._check_next_missing(node_content)

    def _check_end_node(self, node_content: dict):
        self._check_inputs_list(node_content)
        self._check_configs(node_content, keys=['template'])

    def _check_output_node(self, node_content: dict):
        self._check_inputs_list(node_content)
        self._check_configs(node_content, keys=['template'])
        self._check_next_missing(node_content)

    def _check_llm_node(self, node_content: dict):
        self._check_inputs_list(node_content)
        self._check_outputs_list(node_content)
        self._check_configs(node_content, keys=['system_prompt', 'user_prompt'])
        self._check_next_missing(node_content)

    def _check_questioner_node(self, node_content: dict):
        self._check_inputs_list(node_content)
        self._check_outputs_list(node_content)
        self._check_configs(node_content, keys=['prompt'])
        self._check_next_missing(node_content)

    def _check_plugin_node(self, node_content: dict):
        """
        TODO: need check with the original plugin info.
        """
        self._check_inputs_list(node_content)
        self._check_outputs_list(node_content)
        self._check_configs(node_content, keys=['tool_id'])
        self._check_next_missing(node_content)

    def _check_code_node(self, node_content: dict):
        self._check_inputs_list(node_content)
        self._check_outputs_list(node_content)
        self._check_configs(node_content, keys=['code'])
        self._check_next_missing(node_content)

    def _check_intent_detection_node(self, node_content: dict):
        self._check_inputs_list(node_content)
        self._check_configs(node_content, keys=['prompt'])
        self._check_intent_conditions_list(node_content)

    def _check_intent_conditions_list(self, node_content: dict):
        if 'conditions' not in node_content['parameters']:
            self.errors.append(f"{node_content['id']}节点, 类型为{node_content['type']}, 'parameters'中缺失'conditions'属性")
            return
        if not isinstance(node_content['parameters']['conditions'], list):
            self.errors.append(f"{node_content['id']}节点, 类型为{node_content['type']}, 'parameters'中的'conditions'属性必须为列表类型")
            return
        node_id = node_content['id']
        has_default_branch = False
        for condition in node_content['parameters']['conditions']:
            if not isinstance(condition, dict):
                self.errors.append(f"{node_content['id']}节点, 类型为{node_content['type']}, 'parameters'中的'conditions'列表中的元素必须为字典类型")
                return
            # check branch and description val
            for key in ['branch', 'description']:
                if key not in condition:
                    self.errors.append(f"{node_content['id']}节点, 类型为{node_content['type']}, 'parameters'中的'conditions'列表中的元素缺失'{key}'属性")
            # check next
            if 'next' not in condition:
                self.errors.append(
                    f"{node_content['id']}节点, 类型为{node_content['type']}, 'parameters'中的'conditions'列表中的元素缺失'next'属性")
            else:
                self.node_ids_of_next.add(condition["next"])
            # check expression
            if 'expression' not in condition:
                self.errors.append(f"{node_content['id']}节点, 类型为{node_content['type']}, 'parameters'中的'conditions'列表中的元素缺失'expression'属性")
            else:
                expression = condition['expression']
                if not isinstance(expression, str):
                    self.errors.append(f"{node_content['id']}节点, 类型为{node_content['type']}, 'parameters'中的'conditions'列表中的元素的'expression'属性必须为字符串类型")
                    return
                if expression == 'default':
                    has_default_branch = True
                else:
                    left_val = "${" + node_id + ".rawOutput}"
                    if left_val not in expression:
                        self.errors.append(f"{node_content['id']}节点, 类型为{node_content['type']}, 'parameters'中的'conditions'列表中的元素的'expression'的表达式变量错误")
                    if "contain" not in expression:
                        self.errors.append(f"{node_content['id']}节点, 类型为{node_content['type']}, 'parameters'中的'conditions'列表中的元素的'expression'的表达式必须使用contain")
        if not has_default_branch:
            self.errors.append(f"{node_content['id']}节点, 类型为{node_content['type']}, 'parameters'中的'conditions'列表中缺少default分支")

    def _check_branch_node(self, node_content: dict):
        if 'conditions' not in node_content['parameters']:
            self.errors.append(f"{node_content['id']}节点, 类型为{node_content['type']}, 'parameters'中缺失'conditions'属性")
            return
        if not isinstance(node_content['parameters']['conditions'], list):
            self.errors.append(f"{node_content['id']}节点, 类型为{node_content['type']}, 'parameters'中的'conditions'属性必须为列表类型")
            return
        has_default_branch = False
        for condition in node_content['parameters']['conditions']:
            if not isinstance(condition, dict):
                self.errors.append(f"{node_content['id']}节点, 类型为{node_content['type']}, 'parameters'中的'conditions'列表中的元素必须为字典类型")
                return
            # check branch and description val
            for key in ['branch', 'description']:
                if key not in condition:
                    self.errors.append(f"{node_content['id']}节点, 类型为{node_content['type']}, 'parameters'中的'conditions'列表中的元素缺失'{key}'属性")
            # check expression
            if condition.get('expression') == 'default':
                has_default_branch = True
            else:
                self._check_branch_expression(condition, node_content)
            # check next
            if 'next' not in condition:
                self.errors.append(f"{node_content['id']}节点, 类型为{node_content['type']}, 'parameters'中的'conditions'列表中的元素缺失'next'属性")
            else:
                self.node_ids_of_next.add(condition["next"])
        if not has_default_branch:
            self.errors.append(
                f"{node_content['id']}节点, 类型为{node_content['type']}, 'parameters'中的'conditions'列表中缺少default分支")

    def _check_branch_expression(self, condition_branch: dict, node_content: dict):
        if 'expression' in condition_branch:
            expression = condition_branch['expression']
            if not isinstance(expression, str):
                self.errors.append(f"{node_content['id']}节点, 类型为{node_content['type']}, 'parameters'中的'conditions'列表中的元素的'expression'属性必须为字符串类型")
            else:
                self._check_branch_operator(expression, node_content)
                _, placeholder_content = extract_placeholder_content(expression)
                for content in placeholder_content:
                    if content not in self.available_node_outputs:
                        self.errors.append(f"{node_content['id']}节点, 类型为{node_content['type']}, 'parameters'中的'conditions'列表中的元素的'expression'的表达式中引用了不存在的变量")
        elif 'expressions' in condition_branch:
            expressions = condition_branch['expressions']
            if not isinstance(expressions, list):
                self.errors.append(f"{node_content['id']}节点, 类型为{node_content['type']}, 'parameters'中的'conditions'列表中的元素的'expression'属性必须为列表类型")
            else:
                for expression in expressions:
                    self._check_branch_operator(expression, node_content)
                    _, placeholder_content = extract_placeholder_content(expression)
                    for content in placeholder_content:
                        if content not in self.available_node_outputs:
                            self.errors.append(f"{node_content['id']}节点, 类型为{node_content['type']}, 'parameters'中的'conditions'列表中的元素的'expression'的表达式中引用了不存在的变量")
        else:
            self.errors.append(f"{node_content['id']}节点, 类型为{node_content['type']}, 'parameters'中的'conditions'列表中的元素缺失'expression'或'expressions'属性")

    def _check_branch_operator(self, expression: str, node_content: dict):
        expression_list = expression.strip().split(" ")
        if expression_list[1] not in self.available_condition_operators:
            self.errors.append(
                f"{node_content['id']}节点, 类型为{node_content['type']}, 'parameters'中的'conditions'列表中的元素的'expression'的表达式使用了不支持的关系运算符")

    def _check_inputs_list(self, node_content: dict, check_type: bool = False):
        """
        Check the 'inputs' list in the 'parameters' of a node.
        1. The 'inputs' must exist
        2. The 'inputs' attribute must be a list.
        3. Each item in the 'inputs' list must be a dictionary.
        4. Each item in the 'inputs' list must have a 'name' attribute.
        5. The 'name' attribute of each item in the 'inputs' list must be unique.
        6. Each item in the 'inputs' list must have a 'value' attribute.
        7. If the 'value' attribute refers other node's output, the referred node and output must exist.
        8. The 'type' attribute of each item in the 'inputs' list must be in the available variable types.
        """
        if 'inputs' not in node_content['parameters']:
            self.errors.append(f"{node_content['id']}节点, 类型为{node_content['type']}, 'parameters'中缺失'inputs'属性")
            return
        if not isinstance(node_content['parameters']['inputs'], list):
            self.errors.append(f"{node_content['id']}节点, 类型为{node_content['type']}, 'parameters'中的'inputs'属性必须为列表类型")
            return
        input_names = set()
        # check each item in the 'inputs' list
        for input_item in node_content['parameters']['inputs']:
            if not isinstance(input_item, dict):
                self.errors.append(f"{node_content['id']}节点, 类型为{node_content['type']}, 'parameters'中的'inputs'列表中的元素必须为字典类型")
                return

            if 'name' not in input_item:
                self.errors.append(f"{node_content['id']}节点, 类型为{node_content['type']}, 'parameters'中的'inputs'列表中的元素缺失'name'属性")
            else:
                if input_item['name'] in input_names:
                    self.errors.append(f"{node_content['id']}节点, 类型为{node_content['type']}, 'parameters'中的'inputs'列表中的元素'name'属性值必须唯一")
                input_names.add(input_item['name'])
            if 'value' not in input_item:
                self.errors.append(f"{node_content['id']}节点, 类型为{node_content['type']}, 'parameters'中的'inputs'列表中的元素缺失'value'属性")
            else:
                value = input_item['value']
                has_placeholder, placeholder_content = extract_placeholder_content(value)
                if has_placeholder:
                    if len(placeholder_content) > 1:
                        self.errors.append(f"{node_content['id']}节点, 类型为{node_content['type']}, 'parameters'中的'inputs'列表中的元素'value'属性值中有多个引用变量")
                    elif len(placeholder_content) == 1 and placeholder_content[0] not in self.available_node_outputs:
                        self.errors.append(f"{node_content['id']}节点, 类型为{node_content['type']}, 'parameters'中的'inputs'列表中的元素'value'属性引用了不存在的变量")
                    elif len(placeholder_content) == 1 and len(placeholder_content[0]) != len(value.strip()[2:-1]) :
                        self.errors.append(f"{node_content['id']}节点, 类型为{node_content['type']}, 'parameters'中的'inputs'列表中的元素'value'属性引用格式错误")
            if check_type:
                if 'type' not in input_item:
                    self.errors.append(f"{node_content['id']}节点, 类型为{node_content['type']}, 'parameters'中的'inputs'列表中的元素缺失'type'属性")
                else:
                    if input_item['type'] not in self.available_variable_types:
                        self.errors.append(f"{node_content['id']}节点, 类型为{node_content['type']}, 'parameters'中的'inputs'列表中的元素'type'属性值必须为{self.available_variable_types}中的一个")

    def _check_outputs_list(self, node_content: dict, check_type: bool = False):
        """
        Check the 'outputs' list in the 'parameters' of the node.
        1. The 'outputs' must exist
        2. The 'outputs' must be a list
        3. Each item in the 'outputs' list must be a dictionary
        4. Each item in the 'outputs' list must have a 'name' key, and the value must be unique
        5. Each item in the 'outputs' list must have a 'description' key
        6. Each item in the 'outputs' list must have a 'type' key, and the value must be in the variable types list
        """
        if 'outputs' not in node_content['parameters']:
            self.errors.append(f"{node_content['id']}节点, 类型为{node_content['type']}, 'parameters'中缺失'outputs'属性")
            return
        if not isinstance(node_content['parameters']['outputs'], list):
            self.errors.append(f"{node_content['id']}节点, 类型为{node_content['type']}, 'parameters'中的'outputs'属性必须为列表类型")
            return
        output_names = set()
        # check each item in the 'outputs' list
        for output_item in node_content['parameters']['outputs']:
            if not isinstance(output_item, dict):
                self.errors.append(f"{node_content['id']}节点, 类型为{node_content['type']}, 'parameters'中的'outputs'列表中的元素必须为字典类型")
                return

            if 'name' not in output_item:
                self.errors.append(f"{node_content['id']}节点, 类型为{node_content['type']}, 'parameters'中的'outputs'列表中的元素缺失'name'属性")
            else:
                if output_item['name'] in output_names:
                    self.errors.append(f"{node_content['id']}节点, 类型为{node_content['type']}, 'parameters'中的'outputs'列表中的元素'name'属性值必须唯一")
                output_names.add(output_item['name'])
                self.available_node_outputs.add(f"{node_content['id']}.{output_item['name']}")
            if 'description' not in output_item:
                self.errors.append(f"{node_content['id']}节点, 类型为{node_content['type']}, 'parameters'中的'outputs'列表中的元素缺失'description'属性")
            if check_type:
                if 'type' not in output_item:
                    self.errors.append(f"{node_content['id']}节点, 类型为{node_content['type']}, 'parameters'中的'outputs'列表中的元素缺失'type'属性")
                else:
                    if output_item['type'] not in self.available_variable_types:
                        self.errors.append(f"{node_content['id']}节点, 类型为{node_content['type']}, 'parameters'中的'outputs'列表中的元素'type'属性值必须为{self.available_variable_types}中的一个")

    def _check_configs(self, node_content: dict, keys: list):
        if 'configs' not in node_content['parameters']:
            self.errors.append(f"{node_content['id']}节点, 类型为{node_content['type']}, 'parameters'中缺失'configs'属性")
            return
        if not isinstance(node_content['parameters']['configs'], dict):
            self.errors.append(f"{node_content['id']}节点, 类型为{node_content['type']}, 'parameters'中的'configs'属性必须为字典类型")
            return
        configs_keys = node_content['parameters']['configs'].keys()
        for key in keys:
            if key not in configs_keys:
                self.errors.append(f"{node_content['id']}节点, 类型为{node_content['type']}, 'parameters'中的'configs'字典中缺失'{key}'属性")

    def _check_next_missing(self, node_content: dict):
        if 'next' not in node_content:
            self.errors.append(f"{node_content['id']}节点, 类型为{node_content['type']}, 缺失'next'属性")
        else:
            self.node_ids_of_next.add(node_content['next'])
