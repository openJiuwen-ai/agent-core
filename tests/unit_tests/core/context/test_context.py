import unittest

from jiuwen.core.runtime.config import Config
from jiuwen.core.runtime.workflow import WorkflowRuntime, NodeRuntime
from jiuwen.core.runtime.state import ReadableStateLike
from jiuwen.core.runtime.workflow_state import InMemoryState
from jiuwen.core.runtime.utils import update_dict, get_by_schema, root_to_index


class ContextTest(unittest.TestCase):
    def assert_context(self, context: NodeRuntime, node_id: str, executable_id: str, parent_id: str):
        assert context.node_id() == node_id
        assert context.executable_id() == executable_id
        assert context.parent_id() == parent_id

    def test_basic(self):
        # Workflow context/
        context = WorkflowRuntime()
        context.state().commit_user_inputs({'a': 1, 'b': 2})
        assert context.state().get_global('a') == 1
        assert context.state().get_global('b') == 2

        # node1节点
        node1_context = NodeRuntime(context, "node1")
        assert node1_context.node_id() == "node1"
        assert node1_context.executable_id() == "node1"
        assert node1_context.parent_id() == ""
        assert node1_context.state().get_global('a') == 1
        assert node1_context.state().get_global('b') == 2
        # 通过input_schema获取inputs
        node1_input_schema = {"aa": "${a}", "bb": "${b}"}
        node1_input_schema2 = {"node_1_inputs": ["${a}", "${b}"]}
        assert node1_context.state().get_global(node1_input_schema) == {'aa': 1, 'bb': 2}
        assert node1_context.state().get_global(node1_input_schema2) == {"node_1_inputs": [1, 2]}

        # 通过transformer获取inputs
        def node1_transformer(state: ReadableStateLike):
            return state.get(node1_input_schema)

        assert node1_context.state().get_inputs_by_transformer(node1_transformer) == {'aa': 1, 'bb': 2}
        node1_context.state().update_global({"c": 3})
        node1_context.state().update({"url": "0.0.0.1"})
        node1_context.state().commit()
        assert node1_context.state().get_global('c') == 3
        assert node1_context.state().get('url') == '0.0.0.1'

        node2_context = NodeRuntime(context, "node2")
        assert node2_context.state().get_global('c') == 3
        assert node2_context.state().get('url') == None

        # 嵌套workflow
        sub_workflow_context = NodeRuntime(context, "sub_workflow1")
        sub_workflow_context.state().commit_user_inputs({'a': 11, 'b': 12})
        sub_workflow_context.state().commit()

        sub_node1_context = NodeRuntime(sub_workflow_context, "node1")
        assert sub_node1_context.node_id() == "node1"
        assert sub_node1_context.parent_id() == "sub_workflow1"
        assert sub_node1_context.executable_id() == "sub_workflow1.node1"
        assert sub_node1_context.state().get_global(node1_input_schema) == {'aa': 11, 'bb': 12}
        sub_node1_context.state().update_global({"c": 4})
        sub_node1_context.state().update({"url": "0.0.0.2"})
        sub_node1_context.state().commit()
        assert sub_node1_context.state().get_global('c') == 4
        assert sub_node1_context.state().get('url') == '0.0.0.2'

    def test_context_state(self):
        source = {}
        # 增加a.b: nums属性
        update_dict({"a.b.nums": [1, 2, 3]}, source)
        assert source == {'a': {'b': {'nums': [1, 2, 3]}}}
        # 增加a.b: name属性
        update_dict({
            "a.b.name": "shanghai"
        }, source)
        assert source == {'a': {'b': {'nums': [1, 2, 3], 'name': 'shanghai'}}}
        # 增加a.b: class属性
        update_dict({"a.b": {"class": "hha"}}, source)
        assert source == {'a': {'b': {'nums': [1, 2, 3], 'name': 'shanghai', 'class': 'hha'}}}
        # 覆盖a.b所有$ok
        update_dict({"a.b": [1, 2, 3]}, source)
        assert source == {'a': {'b': [1, 2, 3]}}
        assert get_by_schema("a", data=source) == {'b': [1, 2, 3]}
        assert get_by_schema({"a": "b"}, data=source) == {"a": "b"}
        assert get_by_schema({"result": "${a.b}"}, data=source) == {'result': [1, 2, 3]}
        assert get_by_schema({"result": ["abc", "${a}"]}, data=source) == {"result": ["abc", {'b': [1, 2, 3]}]}
        assert get_by_schema({"result": ["abc", "cde"]}, data=source) == {"result": ["abc", "cde"]}
        assert get_by_schema({"result": {"abc": "cde", "result": "${1}"}}, data=source) == {
            "result": {"abc": "cde", "result": None}}

        assert get_by_schema({"result": ["${abc}", "cde"]}, data=source) == {"result": [None, "cde"]}
        assert get_by_schema({"result": {"abc": "cde", "result": "${a}"}}, data=source) == {
            "result": {"abc": "cde", "result": {'b': [1, 2, 3]}}}

    def test_clean_non_value(self):
        data = {"a": {"a1": 1, "a2": 2}, "b": {"b1": {"b11": "1", "b12": [1, 2, None], "b13": "2"}}, "c": 2}
        update = {"c": None}
        update_dict(update, data)
        assert data == {"a": {"a1": 1, "a2": 2}, "b": {"b1": {"b11": "1", "b12": [1, 2, None], "b13": "2"}}}
        update = {"a.a1": None}
        update_dict(update, data)
        assert data == {"a": {"a2": 2}, "b": {"b1": {"b11": "1", "b12": [1, 2, None], "b13": "2"}}}

    def test_root_to_index(self):
        # Test 1: Basic creation with multiple levels
        source = []
        root_to_index([1, 2, 3], source, create_if_absent=True)
        print(source)
        assert source[1][2][3] == {}
        assert source == [None, [None, None, [None, None, None, {}]]]
        result = root_to_index([1, 2, 3], source)
        assert result[1][result[0]] == source[1][2][3]

        # Test 2: Navigation in existing complex structure
        source = [1, [2, (2, [3, 4, 5, [7, 8, 9]])]]
        assert root_to_index([1, 1, 1, 3, 2], source=source) == (2, source[1][1][1][3])

        # Test 3: Negative index access
        print(root_to_index([-1], [1, 2, 3]))
        assert root_to_index([-1], [1, 2, 3]) == (2, [1, 2, 3])

        # Test 4: Negative index out of bounds
        assert root_to_index([-5], [1, 2, 3]) == (None, None)

        # Test 5: Single level access with creation
        source = []
        result = root_to_index([0], source, create_if_absent=True)
        assert result == (0, source)
        assert source == [{}]

        # Test 6: Two level access with creation
        source = []
        result = root_to_index([0, 1], source, create_if_absent=True)
        assert result == (1, source[0])
        assert source == [[None, {}]]

        # Test 7: Access without creation (should fail)
        source = []
        result = root_to_index([0, 1], source, create_if_absent=False)
        assert result == (None, None)
        assert source == []  # Source should remain unchanged

        # Test 8: Tuple immutability test
        source = (1, [2, 3])
        result = root_to_index([1, 5], source, create_if_absent=True)
        assert result == (5, [2, 3, None, None, None, {}])  # Should fail because source is tuple

        # Test 9: Mixed list and tuple navigation
        source = [1, (2, [3, 4, 5]), 6]
        result = root_to_index([1, 1, 0], source)
        assert result == (0, source[1][1])
        assert result[1][result[0]] == 3

        # Test 10: Maximum depth test
        source = []
        deep_path = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0]  # 10 levels - exactly at limit
        result = root_to_index(deep_path, source, create_if_absent=True)
        assert result[0] == 0

        # Test 11: Exceed maximum depth (should raise ValueError)
        try:
            over_deep_path = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]  # 11 levels
            root_to_index(over_deep_path, source)
            assert False, "Should have raised ValueError for exceeding depth limit"
        except ValueError as e:
            assert "Nesting level too deep" in str(e)

        # Test 12: Large index within bounds
        source = []
        result = root_to_index([100], source, create_if_absent=True)
        assert result[0] == 100
        assert len(source) == 101
        assert source[100] == {}

        # Test 13: Large index out of bounds (should raise ValueError)
        try:
            root_to_index([10001], [])
            assert False, "Should have raised ValueError for large index"
        except ValueError as e:
            assert "Index must be between" in str(e)

        # Test 14: Complex negative index chain
        source = [[1, 2, 3], [4, 5, 6], [7, 8, 9]]
        result = root_to_index([-1, -1], source)
        assert result == (2, source[2])  # source[2][2] = 9
        assert result[1][result[0]] == 9

        # Test 15: Empty source and indexes
        assert root_to_index([], [1, 2, 3]) == (None, None)
        assert root_to_index([0], None) == (None, None)

        # Test 16: Data integrity - verify original data not modified when create_if_absent=False
        original_data = [1, [2, 3]]
        original_copy = [1, [2, 3]]  # Make a copy for comparison
        result = root_to_index([1, 5], original_data, create_if_absent=False)
        assert result == (None, None)
        assert original_data == original_copy  # Data should remain unchanged

        # Test 17: Multiple negative indexes in path
        source = [1, [2, 3, [4, 5, 6]], 7]
        result = root_to_index([1, -1, -1], source)
        assert result == (2, source[1][2])  # source[1][2][2] = 6
        assert result[1][result[0]] == 6

        # Test 18: Boundary case - index 0 with empty list
        source = []
        result = root_to_index([0], source, create_if_absent=True)
        assert result == (0, source)
        assert source == [{}]

        # Test 19: Boundary case - index 10000 (maximum allowed)
        source = []
        result = root_to_index([10000], source, create_if_absent=True)
        assert result[0] == None

        # Test 20: Verify intermediate containers are lists, not dicts
        source = []
        root_to_index([1, 2, 3], source, create_if_absent=True)
        assert isinstance(source[1], list)
        assert isinstance(source[1][2], list)
        assert isinstance(source[1][2][3], dict)  # Only the final container should be dict

        # Test 21: Access existing nested structure without modification
        source = [1, [2, [3, 4]], 5]
        result = root_to_index([1, 1, 0], source)
        assert result == (0, source[1][1])
        assert result[1][result[0]] == 3
        # Verify source structure unchanged
        assert source == [1, [2, [3, 4]], 5]

        # Test 22: Complex real-world like structure
        filesystem_like = [
            "root",
            [
                "users",
                [
                    "alice", ["docs", "pics", "music"],
                    "bob", ["work", "personal"]
                ]
            ],
            ["system", ["config", "logs"]]
        ]
        # Navigate to alice's music folder
        result = root_to_index([1, 1, 1, 2], filesystem_like)
        assert result == (2, filesystem_like[1][1][1])
        assert result[1][result[0]] == "music"

        # Test 23: Partial path creation
        source = [1, [2, 3]]  # Existing structure
        result = root_to_index([1, 5, 2], source, create_if_absent=True)
        assert result == (2, source[1][5])
        # Verify the created structure
        assert source[1][5] == [None, None, {}]
        assert source[1][3] is None  # Filled with None
        assert source[1][4] is None  # Filled with None
