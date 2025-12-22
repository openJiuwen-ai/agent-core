#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved
import json
import unittest

from openjiuwen.agent_builder.nl_to_agent.workflow_builder.dl_reflector.dl_reflector import Reflector, \
    extract_placeholder_content


class TestExtractPlaceholderContent(unittest.TestCase):

    def test_extract_placeholder_content_with_placeholder(self):
        input_str = "Hello ${name}, welcome to ${place}"
        has_placeholder, matches = extract_placeholder_content(input_str)

        self.assertTrue(has_placeholder)
        self.assertEqual(matches, ['name', 'place'])

    def test_extract_placeholder_content_without_placeholder(self):
        input_str = "Hello world, welcome to the test"
        has_placeholder, matches = extract_placeholder_content(input_str)

        self.assertFalse(has_placeholder)
        self.assertEqual(matches, [])

    def test_extract_placeholder_content_empty_string(self):
        input_str = ""
        has_placeholder, matches = extract_placeholder_content(input_str)

        self.assertFalse(has_placeholder)
        self.assertEqual(matches, [])

    def test_extract_placeholder_content_nested_braces(self):
        input_str = "Test ${outer{inner}} value"
        has_placeholder, matches = extract_placeholder_content(input_str)

        self.assertTrue(has_placeholder)
        self.assertEqual(matches, ['outer{inner'])

    def test_extract_placeholder_content_special_characters(self):
        input_str = "Test ${var.name} and ${var_name-1}"
        has_placeholder, matches = extract_placeholder_content(input_str)

        self.assertTrue(has_placeholder)
        self.assertEqual(matches, ['var.name', 'var_name-1'])

    def test_extract_placeholder_content_edge_positions(self):
        input_str1 = "${start} is at the beginning"
        has_placeholder1, matches1 = extract_placeholder_content(input_str1)
        self.assertTrue(has_placeholder1)
        self.assertEqual(matches1, ['start'])
        
        input_str2 = "The end is at ${end}"
        has_placeholder2, matches2 = extract_placeholder_content(input_str2)
        self.assertTrue(has_placeholder2)
        self.assertEqual(matches2, ['end'])
        
        input_str3 = "${only}"
        has_placeholder3, matches3 = extract_placeholder_content(input_str3)
        self.assertTrue(has_placeholder3)
        self.assertEqual(matches3, ['only'])


class TestReflectorBasic(unittest.TestCase):

    def setUp(self):
        self.reflector = Reflector()

    def tearDown(self):
        self.reflector.reset()

    def test_initial_state(self):
        self.assertEqual(self.reflector.available_node_types, {
            'Start', 'End', 'Output', 'LLM', 'Questioner', 'Plugin',
            'Code', 'Branch', 'IntentDetection'
        })
        self.assertEqual(self.reflector.available_node_outputs, set())
        self.assertEqual(self.reflector.node_ids, [])
        self.assertEqual(self.reflector.node_ids_of_next, set())
        self.assertEqual(self.reflector.errors, [])

    def test_reset_method(self):
        self.reflector.available_node_outputs.add("test.output")
        self.reflector.node_ids.append("test_id")
        self.reflector.node_ids_of_next.add("next_id")
        self.reflector.errors.append("test error")

        self.reflector.reset()

        self.assertEqual(self.reflector.available_node_outputs, set())
        self.assertEqual(self.reflector.node_ids, [])
        self.assertEqual(self.reflector.node_ids_of_next, set())
        self.assertEqual(self.reflector.errors, [])
        
    def test_check_next_missing(self):
        node_missing_next = {
            "id": "node1",
            "type": "LLM",
            "description": "test node",
            "parameters": {}
        }
        self.reflector._check_next_missing(node_missing_next)
        self.assertTrue(any("缺失'next'属性" in error for error in self.reflector.errors))
        self.reflector.reset()

        node_with_next = {
            "id": "node1",
            "type": "LLM",
            "description": "test node",
            "parameters": {},
            "next": "next_node"
        }
        self.reflector._check_next_missing(node_with_next)
        self.assertEqual(len(self.reflector.errors), 0)
        self.assertIn("next_node", self.reflector.node_ids_of_next)
        
    def test_check_configs(self):
        node_missing_configs = {
            "id": "node1",
            "type": "LLM",
            "description": "test node",
            "parameters": {}
        }
        self.reflector._check_configs(node_missing_configs, ["key1"])
        self.assertTrue(any("缺失'configs'属性" in error for error in self.reflector.errors))
        self.reflector.reset()

        node_invalid_configs = {
            "id": "node1",
            "type": "LLM",
            "description": "test node",
            "parameters": {
                "configs": "not a dict"
            }
        }
        self.reflector._check_configs(node_invalid_configs, ["key1"])
        self.assertTrue(any("configs'属性必须为字典类型" in error for error in self.reflector.errors))
        self.reflector.reset()

        node_missing_key = {
            "id": "node1",
            "type": "LLM",
            "description": "test node",
            "parameters": {
                "configs": {"key2": "value2"}
            }
        }
        self.reflector._check_configs(node_missing_key, ["key1", "key2"])
        self.assertTrue(any("缺失'key1'属性" in error for error in self.reflector.errors))
        self.reflector.reset()
        
        node_valid = {
            "id": "node1",
            "type": "LLM",
            "description": "test node",
            "parameters": {
                "configs": {
                    "key1": "value1",
                    "key2": "value2"
                }
            }
        }
        self.reflector._check_configs(node_valid, ["key1", "key2"])
        self.assertEqual(len(self.reflector.errors), 0)


class TestReflectorCheckFormat(unittest.TestCase):

    def setUp(self):
        self.reflector = Reflector()

    def tearDown(self):
        self.reflector.reset()

    def test_invalid_json_format(self):
        invalid_json = "invalid json content"
        self.reflector.check_format(invalid_json)

        self.assertEqual(len(self.reflector.errors), 1)
        self.assertTrue("JSON格式错误" in self.reflector.errors[0])

    def test_empty_json_array(self):
        empty_json = "[]"
        self.reflector.check_format(empty_json)

        self.assertEqual(len(self.reflector.errors), 0)

    def test_node_not_dict(self):
        invalid_dl = '[{"id": "node1", "type": "Start", "description": "test", "parameters": {}}, "invalid_node"]'
        self.reflector.check_format(invalid_dl)

        self.assertTrue(any("必须为字典类型" in error for error in self.reflector.errors))


class TestReflectorBasicCheck(unittest.TestCase):

    def setUp(self):
        self.reflector = Reflector()

    def tearDown(self):
        self.reflector.reset()

    def test_basic_check_valid_node(self):
        valid_node = {
            "id": "node1",
            "type": "Start",
            "description": "test node",
            "parameters": {}
        }
        result = self.reflector._basic_check(valid_node, 0)

        self.assertFalse(result)
        self.assertEqual(len(self.reflector.errors), 0)
        self.assertEqual(self.reflector.node_ids, ["node1"])

    def test_basic_check_missing_key(self):
        invalid_node = {
            "id": "node1",
            "type": "Start",
        }
        result = self.reflector._basic_check(invalid_node, 0)

        self.assertTrue(result)
        self.assertTrue(any("缺失" in error for error in self.reflector.errors))

    def test_basic_check_duplicate_id(self):
        node1 = {
            "id": "node1",
            "type": "Start",
            "description": "test node 1",
            "parameters": {}
        }
        node2 = {
            "id": "node1",
            "type": "End",
            "description": "test node 2",
            "parameters": {}
        }

        result1 = self.reflector._basic_check(node1, 0)
        self.assertFalse(result1)

        result2 = self.reflector._basic_check(node2, 1)
        self.assertTrue(result2)
        self.assertTrue(any("已存在" in error for error in self.reflector.errors))

    def test_basic_check_invalid_type(self):
        invalid_node = {
            "id": "node1",
            "type": "InvalidType",
            "description": "test node",
            "parameters": {}
        }
        result = self.reflector._basic_check(invalid_node, 0)

        self.assertTrue(result)
        self.assertTrue(any("不在可用节点类型中" in error for error in self.reflector.errors))


class TestReflectorNodeSpecificChecks(unittest.TestCase):

    def setUp(self):
        self.reflector = Reflector()

    def tearDown(self):
        self.reflector.reset()

    def test_check_start_node_valid(self):
        start_node = {
            "id": "start1",
            "type": "Start",
            "description": "start node",
            "parameters": {
                "outputs": [
                    {"name": "query", "description": "用户输入"}
                ]
            },
            "next": "next_node"
        }

        self.reflector._check_start_node(start_node)
        self.assertEqual(len(self.reflector.errors), 0)

    def test_check_start_node_missing_query_output(self):
        start_node = {
            "id": "start1",
            "type": "Start",
            "description": "start node",
            "parameters": {
                "outputs": [
                    {"name": "other", "description": "其他输出"}
                ]
            },
            "next": "next_node"
        }

        self.reflector._check_start_node(start_node)
        self.assertTrue(any("必须包含" in error for error in self.reflector.errors))

    def test_check_end_node_valid(self):
        end_node = {
            "id": "end1",
            "type": "End",
            "description": "end node",
            "parameters": {
                "inputs": [],
                "configs": {
                    "template": "Result: ${input}"
                }
            }
        }

        self.reflector._check_end_node(end_node)
        self.assertEqual(len(self.reflector.errors), 0)

    def test_check_llm_node_valid(self):
        llm_node = {
            "id": "llm1",
            "type": "LLM",
            "description": "llm node",
            "parameters": {
                "inputs": [],
                "outputs": [{"name": "response", "description": "LLM响应"}],
                "configs": {
                    "system_prompt": "You are a helpful assistant",
                    "user_prompt": "Answer the question: ${input}"
                }
            },
            "next": "next_node"
        }

        self.reflector._check_llm_node(llm_node)
        self.assertEqual(len(self.reflector.errors), 0)
        self.assertIn("llm1.response", self.reflector.available_node_outputs)

    def test_check_branch_node_valid(self):
        self.reflector.available_node_outputs.add("previous_node.output")

        branch_node = {
            "id": "branch1",
            "type": "Branch",
            "description": "branch node",
            "parameters": {
                "conditions": [
                    {
                        "branch": "condition1",
                        "description": "第一个条件",
                        "expression": "${previous_node.output} eq 'value1'",
                        "next": "node1"
                    },
                    {
                        "branch": "default",
                        "description": "默认分支",
                        "expression": "default",
                        "next": "default_node"
                    }
                ]
            }
        }

        self.reflector._check_branch_node(branch_node)
        self.assertEqual(len(self.reflector.errors), 0)

    def test_check_branch_node_missing_default(self):
        branch_node = {
            "id": "branch1",
            "type": "Branch",
            "description": "branch node",
            "parameters": {
                "conditions": [
                    {
                        "branch": "condition1",
                        "description": "第一个条件",
                        "expression": "${var} == 'value'",
                        "next": "node1"
                    }
                ]
            }
        }

        self.reflector._check_branch_node(branch_node)
        self.assertTrue(any("缺少default分支" in error for error in self.reflector.errors))

    def test_check_intent_detection_node_valid(self):
        intent_node = {
            "id": "intent1",
            "type": "IntentDetection",
            "description": "intent detection node",
            "parameters": {
                "inputs": [],
                "configs": {
                    "prompt": "Detect intent from: ${input}"
                },
                "conditions": [
                    {
                        "branch": "intent1",
                        "description": "第一个意图",
                        "expression": "${intent1.rawOutput} contain 'weather'",
                        "next": "weather_node"
                    },
                    {
                        "branch": "default",
                        "description": "默认意图",
                        "expression": "default",
                        "next": "default_node"
                    }
                ]
            }
        }

        self.reflector._check_intent_detection_node(intent_node)
        self.assertEqual(len(self.reflector.errors), 0)
        
    def test_check_output_node_valid(self):
        self.reflector.available_node_outputs.add("previous_node.output")
        
        output_node = {
            "id": "output1",
            "type": "Output",
            "description": "output node",
            "parameters": {
                "inputs": [
                    {"name": "input1", "value": "${previous_node.output}"}
                ],
                "configs": {
                    "template": "Result: ${previous_node.output}"
                }
            },
            "next": "next_node"
        }

        self.reflector._check_output_node(output_node)
        self.assertEqual(len(self.reflector.errors), 0)
        self.assertIn("next_node", self.reflector.node_ids_of_next)
        
    def test_check_output_node_missing_template(self):
        output_node = {
            "id": "output1",
            "type": "Output",
            "description": "output node",
            "parameters": {
                "inputs": [],
                "configs": {}
            },
            "next": "next_node"
        }

        self.reflector._check_output_node(output_node)
        self.assertTrue(any("缺失'template'属性" in error for error in self.reflector.errors))
        
    def test_check_plugin_node_valid(self):
        plugin_node = {
            "id": "plugin1",
            "type": "Plugin",
            "description": "plugin node",
            "parameters": {
                "inputs": [],
                "outputs": [{"name": "result", "description": "plugin result"}],
                "configs": {
                    "tool_id": "plugin_tool_id"
                }
            },
            "next": "next_node"
        }

        self.reflector._check_plugin_node(plugin_node)
        self.assertEqual(len(self.reflector.errors), 0)
        self.assertIn("plugin1.result", self.reflector.available_node_outputs)
        self.assertIn("next_node", self.reflector.node_ids_of_next)
        
    def test_check_plugin_node_missing_tool_id(self):
        plugin_node = {
            "id": "plugin1",
            "type": "Plugin",
            "description": "plugin node",
            "parameters": {
                "inputs": [],
                "outputs": [],
                "configs": {}
            },
            "next": "next_node"
        }

        self.reflector._check_plugin_node(plugin_node)
        self.assertTrue(any("缺失'tool_id'属性" in error for error in self.reflector.errors))
        
    def test_check_code_node_valid(self):
        code_node = {
            "id": "code1",
            "type": "Code",
            "description": "code node",
            "parameters": {
                "inputs": [],
                "outputs": [{"name": "result", "description": "code result"}],
                "configs": {
                    "code": """def process():
    return 'processed result'"""
                }
            },
            "next": "next_node"
        }

        self.reflector._check_code_node(code_node)
        self.assertEqual(len(self.reflector.errors), 0)
        self.assertIn("code1.result", self.reflector.available_node_outputs)
        self.assertIn("next_node", self.reflector.node_ids_of_next)
        
    def test_check_code_node_missing_code(self):
        code_node = {
            "id": "code1",
            "type": "Code",
            "description": "code node",
            "parameters": {
                "inputs": [],
                "outputs": [],
                "configs": {}
            },
            "next": "next_node"
        }

        self.reflector._check_code_node(code_node)
        self.assertTrue(any("缺失'code'属性" in error for error in self.reflector.errors))


class TestReflectorInputOutputChecks(unittest.TestCase):

    def setUp(self):
        self.reflector = Reflector()

    def tearDown(self):
        self.reflector.reset()

    def test_check_inputs_valid_reference(self):
        self.reflector.available_node_outputs.add("previous_node.output")

        node = {
            "id": "test_node",
            "type": "LLM",
            "description": "test node",
            "parameters": {
                "inputs": [
                    {
                        "name": "input1",
                        "value": "${previous_node.output}"
                    }
                ],
                "outputs": [],
                "configs": {
                    "system_prompt": "test",
                    "user_prompt": "test"
                }
            },
            "next": "next_node"
        }

        self.reflector._check_llm_node(node)
        self.assertEqual(len(self.reflector.errors), 0)

    def test_check_inputs_invalid_reference(self):
        node = {
            "id": "test_node",
            "type": "LLM",
            "description": "test node",
            "parameters": {
                "inputs": [
                    {
                        "name": "input1",
                        "value": "${nonexistent_node.output}"
                    }
                ]
            }
        }

        self.reflector._check_llm_node(node)
        self.assertTrue(any("引用了不存在的变量" in error for error in self.reflector.errors))

    def test_check_outputs_adds_to_available(self):
        node = {
            "id": "test_node",
            "type": "LLM",
            "description": "test node",
            "parameters": {
                "outputs": [
                    {
                        "name": "output1",
                        "description": "测试输出"
                    }
                ]
            }
        }

        self.reflector._check_llm_node(node)
        self.assertIn("test_node.output1", self.reflector.available_node_outputs)


class TestReflectorIntegration(unittest.TestCase):

    def setUp(self):
        self.reflector = Reflector()

    def tearDown(self):
        self.reflector.reset()

    def test_valid_workflow(self):
        workflow = [
            {
                "id": "start1",
                "type": "Start",
                "description": "开始节点",
                "parameters": {
                    "outputs": [
                        {"name": "query", "description": "用户输入"}
                    ]
                },
                "next": "llm1"
            },
            {
                "id": "llm1",
                "type": "LLM",
                "description": "LLM处理",
                "parameters": {
                    "inputs": [
                        {"name": "input", "value": "${start1.query}"}
                    ],
                    "outputs": [
                        {"name": "response", "description": "LLM响应"}
                    ],
                    "configs": {
                        "system_prompt": "You are helpful",
                        "user_prompt": "Process: ${start1.query}"
                    }
                },
                "next": "end1"
            },
            {
                "id": "end1",
                "type": "End",
                "description": "结束节点",
                "parameters": {
                    "inputs": [
                        {"name": "input", "value": "${llm1.response}"}
                    ],
                    "configs": {
                        "template": "Result: ${llm1.response}"
                    }
                }
            }
        ]

        workflow_json = json.dumps(workflow)
        self.reflector.check_format(workflow_json)

        if self.reflector.errors:
            print("Errors found:", self.reflector.errors)

        self.assertEqual(len(self.reflector.errors), 0)

    def test_invalid_next_reference(self):
        workflow = [
            {
                "id": "start1",
                "type": "Start",
                "description": "开始节点",
                "parameters": {
                    "outputs": [
                        {"name": "query", "description": "用户输入"}
                    ]
                },
                "next": "nonexistent_node"
            },
            {
                "id": "end1",
                "type": "End",
                "description": "结束节点",
                "parameters": {
                    "inputs": [],
                    "configs": {"template": "Test"}
                }
            }
        ]

        workflow_json = json.dumps(workflow)
        self.reflector.check_format(workflow_json)

        self.assertTrue(any("不存在" in error for error in self.reflector.errors))


class TestReflectorEdgeCases(unittest.TestCase):

    def setUp(self):
        self.reflector = Reflector()

    def tearDown(self):
        self.reflector.reset()

    def test_empty_inputs_outputs(self):
        node = {
            "id": "test_node",
            "type": "LLM",
            "description": "test node",
            "parameters": {
                "inputs": [],
                "outputs": [],
                "configs": {
                    "system_prompt": "test",
                    "user_prompt": "test"
                }
            },
            "next": "next_node"
        }

        self.reflector._check_llm_node(node)
        self.assertEqual(len(self.reflector.errors), 0)

    def test_missing_configs_keys(self):
        node = {
            "id": "test_node",
            "type": "LLM",
            "description": "test node",
            "parameters": {
                "inputs": [],
                "outputs": [],
                "configs": {
                    "system_prompt": "test"
                }
            },
            "next": "next_node"
        }

        self.reflector._check_llm_node(node)
        self.assertTrue(any("缺失" in error for error in self.reflector.errors))
        
    def test_inputs_with_duplicate_names(self):
        node = {
            "id": "test_node",
            "type": "LLM",
            "description": "test node",
            "parameters": {
                "inputs": [
                    {"name": "input1", "value": "value1"},
                    {"name": "input1", "value": "value2"}
                ],
                "outputs": [],
                "configs": {
                    "system_prompt": "test",
                    "user_prompt": "test"
                }
            },
            "next": "next_node"
        }

        self.reflector._check_llm_node(node)
        self.assertTrue(any("'name'属性值必须唯一" in error for error in self.reflector.errors))
        
    def test_outputs_with_duplicate_names(self):
        node = {
            "id": "test_node",
            "type": "LLM",
            "description": "test node",
            "parameters": {
                "inputs": [],
                "outputs": [
                    {"name": "output1", "description": "描述1"},
                    {"name": "output1", "description": "描述2"}
                ],
                "configs": {
                    "system_prompt": "test",
                    "user_prompt": "test"
                }
            },
            "next": "next_node"
        }

        self.reflector._check_llm_node(node)
        self.assertTrue(any("'name'属性值必须唯一" in error for error in self.reflector.errors))
        
    def test_input_with_multiple_references(self):
        self.reflector.available_node_outputs.add("node1.output")
        self.reflector.available_node_outputs.add("node2.output")
        
        node = {
            "id": "test_node",
            "type": "LLM",
            "description": "test node",
            "parameters": {
                "inputs": [
                    {"name": "input1", "value": "${node1.output} ${node2.output}"}
                ],
                "outputs": [],
                "configs": {
                    "system_prompt": "test",
                    "user_prompt": "test"
                }
            },
            "next": "next_node"
        }

        self.reflector._check_llm_node(node)
        self.assertTrue(any("有多个引用变量" in error for error in self.reflector.errors))
        
    def test_input_with_invalid_reference_name(self):
        node = {
            "id": "test_node",
            "type": "LLM",
            "description": "test node",
            "parameters": {
                "inputs": [
                    {"name": "input1", "value": "${invalid variable name}"}
                ],
                "outputs": [],
                "configs": {
                    "system_prompt": "test",
                    "user_prompt": "test"
                }
            },
            "next": "next_node"
        }

        self.reflector._check_llm_node(node)
        self.assertTrue(len(self.reflector.errors) > 0)
        
    def test_branch_node_with_expressions_array(self):
        self.reflector.available_node_outputs.add("node1.output")
        
        branch_node = {
            "id": "branch1",
            "type": "Branch",
            "description": "branch node",
            "parameters": {
                "conditions": [
                    {
                        "branch": "condition1",
                        "description": "条件1",
                        "expressions": [
                            "${node1.output} longer_than 10",
                            "${node1.output} short_than 100"
                        ],
                        "next": "node1"
                    },
                    {
                        "branch": "default",
                        "description": "默认分支",
                        "expression": "default",
                        "next": "default_node"
                    }
                ]
            }
        }

        self.reflector._check_branch_node(branch_node)
        self.assertEqual(len(self.reflector.errors), 0)
        
    def test_questioner_node_valid(self):
        questioner_node = {
            "id": "questioner1",
            "type": "Questioner",
            "description": "questioner node",
            "parameters": {
                "inputs": [],
                "outputs": [{"name": "answer", "description": "用户回答"}],
                "configs": {
                    "prompt": "Please enter your name:"
                }
            },
            "next": "next_node"
        }

        self.reflector._check_questioner_node(questioner_node)
        self.assertEqual(len(self.reflector.errors), 0)
        self.assertIn("questioner1.answer", self.reflector.available_node_outputs)
        
    def test_intent_detection_invalid_expression(self):
        intent_node = {
            "id": "intent1",
            "type": "IntentDetection",
            "description": "intent node",
            "parameters": {
                "inputs": [],
                "configs": {
                    "prompt": "test"
                },
                "conditions": [
                    {
                        "branch": "test",
                        "description": "test",
                        "expression": "invalid_expression",
                        "next": "next_node"
                    },
                    {
                        "branch": "default",
                        "description": "default",
                        "expression": "default",
                        "next": "default_node"
                    }
                ]
            }
        }

        self.reflector._check_intent_detection_node(intent_node)
        self.assertTrue(any("表达式变量错误" in error for error in self.reflector.errors))


if __name__ == '__main__':
    unittest.main()
