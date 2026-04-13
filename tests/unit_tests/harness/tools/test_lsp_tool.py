# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""单元测试：LSP Tool — 8种 LSP 操作"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from openjiuwen.harness.tools.lsp_tool._tool import (
    build_lsp_tool,
    _resolve_path,
    _operation_to_method,
    _needs_gitignore_filter,
)
from openjiuwen.harness.tools.lsp_tool._schemas import (
    LspOperation,
    LspToolInput,
    GoToDefinitionInput,
    FindReferencesInput,
    DocumentSymbolInput,
    WorkspaceSymbolInput,
    PrepareCallHierarchyInput,
    IncomingCallsInput,
    OutgoingCallsInput,
)
from openjiuwen.harness.tools.lsp_tool._formatter import (
    format_result,
    format_location,
    format_go_to_definition,
    format_find_references,
    format_document_symbol,
    format_workspace_symbol,
    format_prepare_call_hierarchy,
    format_incoming_calls,
    format_outgoing_calls,
    format_uri,
    SYMBOL_KIND_MAP,
)


# ============================================================================
# 1. build_lsp_tool() 测试
# ============================================================================

class TestBuildLspTool:
    """测试 LSP Tool 定义构建"""

    def test_build_lsp_tool_returns_dict(self):
        """build_lsp_tool() 应返回 dict 类型"""
        result = build_lsp_tool()
        assert isinstance(result, dict)

    def test_build_lsp_tool_has_required_fields(self):
        """返回的 dict 应包含 name, description, input_schema"""
        result = build_lsp_tool()
        assert "name" in result
        assert "description" in result
        assert "input_schema" in result

    def test_build_lsp_tool_name_is_lsp(self):
        """工具名称应为 'lsp'"""
        result = build_lsp_tool()
        assert result["name"] == "lsp"

    def test_build_lsp_tool_input_schema_has_operation_enum(self):
        """input_schema 中 operation 字段应有正确的 enum 值"""
        result = build_lsp_tool()
        schema = result["input_schema"]
        op_enum = schema["properties"]["operation"]["enum"]
        assert "goToDefinition" in op_enum
        assert "findReferences" in op_enum
        assert "documentSymbol" in op_enum
        assert "workspaceSymbol" in op_enum
        assert "goToImplementation" in op_enum
        assert "prepareCallHierarchy" in op_enum
        assert "incomingCalls" in op_enum
        assert "outgoingCalls" in op_enum
        assert len(op_enum) == 8

    def test_build_lsp_tool_required_fields(self):
        """input_schema 中 operation 和 file_path 应为必填字段"""
        result = build_lsp_tool()
        schema = result["input_schema"]
        assert "operation" in schema["required"]
        assert "file_path" in schema["required"]


# ============================================================================
# 2. LspOperation 枚举测试
# ============================================================================

class TestLspOperationEnum:
    """测试 LspOperation 枚举值"""

    def test_all_8_operations_present(self):
        """8种操作应全部存在"""
        assert LspOperation.GO_TO_DEFINITION.value == "goToDefinition"
        assert LspOperation.FIND_REFERENCES.value == "findReferences"
        assert LspOperation.DOCUMENT_SYMBOL.value == "documentSymbol"
        assert LspOperation.WORKSPACE_SYMBOL.value == "workspaceSymbol"
        assert LspOperation.GO_TO_IMPLEMENTATION.value == "goToImplementation"
        assert LspOperation.PREPARE_CALL_HIERARCHY.value == "prepareCallHierarchy"
        assert LspOperation.INCOMING_CALLS.value == "incomingCalls"
        assert LspOperation.OUTGOING_CALLS.value == "outgoingCalls"


# ============================================================================
# 3. Pydantic Schema 输入验证测试
# ============================================================================

class TestGoToDefinitionInput:
    """测试 goToDefinition 输入验证"""

    def test_valid_input(self):
        """有效输入应通过验证"""
        data = {
            "operation": "goToDefinition",
            "file_path": "/path/to/file.py",
            "line": 10,
            "character": 5,
        }
        result = GoToDefinitionInput.model_validate(data)
        assert result.operation == LspOperation.GO_TO_DEFINITION
        assert result.file_path == "/path/to/file.py"
        assert result.line == 10
        assert result.character == 5

    def test_default_operation_auto_filled(self):
        """operation 字段应有默认值"""
        data = {"file_path": "/path/to/file.py", "line": 1, "character": 1}
        result = GoToDefinitionInput.model_validate(data)
        assert result.operation == LspOperation.GO_TO_DEFINITION

    def test_line_must_be_ge_1(self):
        """line 必须 >= 1"""
        data = {
            "operation": "goToDefinition",
            "file_path": "/path/to/file.py",
            "line": 0,
            "character": 1,
        }
        with pytest.raises(Exception):
            GoToDefinitionInput.model_validate(data)

    def test_character_must_be_ge_1(self):
        """character 必须 >= 1"""
        data = {
            "operation": "goToDefinition",
            "file_path": "/path/to/file.py",
            "line": 1,
            "character": 0,
        }
        with pytest.raises(Exception):
            GoToDefinitionInput.model_validate(data)


class TestFindReferencesInput:
    """测试 findReferences 输入验证"""

    def test_valid_input(self):
        """有效输入应通过验证"""
        data = {
            "operation": "findReferences",
            "file_path": "/path/to/file.py",
            "line": 5,
            "character": 10,
        }
        result = FindReferencesInput.model_validate(data)
        assert result.operation == LspOperation.FIND_REFERENCES
        assert result.include_declaration is True  # 默认值

    def test_include_declaration_false(self):
        """include_declaration 可设置为 False"""
        data = {
            "operation": "findReferences",
            "file_path": "/path/to/file.py",
            "line": 5,
            "character": 10,
            "include_declaration": False,
        }
        result = FindReferencesInput.model_validate(data)
        assert result.include_declaration is False


class TestDocumentSymbolInput:
    """测试 documentSymbol 输入验证"""

    def test_valid_input(self):
        """仅需要 file_path"""
        data = {"operation": "documentSymbol", "file_path": "/path/to/file.py"}
        result = DocumentSymbolInput.model_validate(data)
        assert result.operation == LspOperation.DOCUMENT_SYMBOL
        assert result.file_path == "/path/to/file.py"

    def test_file_path_required(self):
        """file_path 是必填字段"""
        data = {"operation": "documentSymbol"}
        with pytest.raises(Exception):
            DocumentSymbolInput.model_validate(data)


class TestWorkspaceSymbolInput:
    """测试 workspaceSymbol 输入验证"""

    def test_valid_input(self):
        """query 字段有默认值"""
        data = {"operation": "workspaceSymbol", "query": "my_function"}
        result = WorkspaceSymbolInput.model_validate(data)
        assert result.query == "my_function"

    def test_empty_query_allowed(self):
        """空查询字符串应被允许"""
        data = {"operation": "workspaceSymbol", "query": ""}
        result = WorkspaceSymbolInput.model_validate(data)
        assert result.query == ""


class TestCallHierarchyInputs:
    """测试调用层次结构相关输入"""

    def test_prepare_call_hierarchy_valid(self):
        data = {
            "operation": "prepareCallHierarchy",
            "file_path": "/path/to/file.py",
            "line": 20,
            "character": 15,
        }
        result = PrepareCallHierarchyInput.model_validate(data)
        assert result.operation == LspOperation.PREPARE_CALL_HIERARCHY

    def test_incoming_calls_valid(self):
        data = {
            "operation": "incomingCalls",
            "file_path": "/path/to/file.py",
            "line": 20,
            "character": 15,
        }
        result = IncomingCallsInput.model_validate(data)
        assert result.operation == LspOperation.INCOMING_CALLS

    def test_outgoing_calls_valid(self):
        data = {
            "operation": "outgoingCalls",
            "file_path": "/path/to/file.py",
            "line": 20,
            "character": 15,
        }
        result = OutgoingCallsInput.model_validate(data)
        assert result.operation == LspOperation.OUTGOING_CALLS


class TestLspToolInputDiscriminatedUnion:
    """测试 LspToolInput discriminated union"""

    def test_discriminated_union_go_to_definition(self):
        """GoToDefinitionInput 应能通过 TypeAdapter(LspToolInput) 验证"""
        from pydantic import TypeAdapter

        data = {
            "operation": "goToDefinition",
            "file_path": "/path/to/file.py",
            "line": 10,
            "character": 5,
        }
        result = TypeAdapter(LspToolInput).validate_python(data)
        assert isinstance(result, GoToDefinitionInput)
        assert result.operation == LspOperation.GO_TO_DEFINITION

    def test_discriminated_union_workspace_symbol(self):
        """WorkspaceSymbolInput 应能通过 TypeAdapter(LspToolInput) 验证"""
        from pydantic import TypeAdapter

        data = {"operation": "workspaceSymbol", "query": "test"}
        result = TypeAdapter(LspToolInput).validate_python(data)
        assert isinstance(result, WorkspaceSymbolInput)
        assert result.operation == LspOperation.WORKSPACE_SYMBOL


# ============================================================================
# 4. 操作方法映射测试
# ============================================================================

class TestOperationToMethod:
    """测试操作枚举到 LSP 方法名的映射"""

    def test_go_to_definition(self):
        assert _operation_to_method(LspOperation.GO_TO_DEFINITION) == "textDocument/definition"

    def test_find_references(self):
        assert _operation_to_method(LspOperation.FIND_REFERENCES) == "textDocument/references"

    def test_document_symbol(self):
        assert _operation_to_method(LspOperation.DOCUMENT_SYMBOL) == "textDocument/documentSymbol"

    def test_workspace_symbol(self):
        assert _operation_to_method(LspOperation.WORKSPACE_SYMBOL) == "workspace/symbol"

    def test_go_to_implementation(self):
        assert _operation_to_method(LspOperation.GO_TO_IMPLEMENTATION) == "textDocument/implementation"

    def test_prepare_call_hierarchy(self):
        assert (
            _operation_to_method(LspOperation.PREPARE_CALL_HIERARCHY)
            == "textDocument/prepareCallHierarchy"
        )

    def test_incoming_calls(self):
        assert _operation_to_method(LspOperation.INCOMING_CALLS) == "callHierarchy/incomingCalls"

    def test_outgoing_calls(self):
        assert _operation_to_method(LspOperation.OUTGOING_CALLS) == "callHierarchy/outgoingCalls"


# ============================================================================
# 5. gitignore 过滤判断测试
# ============================================================================

class TestNeedsGitignoreFilter:
    """测试哪些操作需要 gitignore 过滤"""

    def test_needs_filter(self):
        """findReferences, goToDefinition, goToImplementation, workspaceSymbol 需要过滤"""
        assert _needs_gitignore_filter(LspOperation.FIND_REFERENCES) is True
        assert _needs_gitignore_filter(LspOperation.GO_TO_DEFINITION) is True
        assert _needs_gitignore_filter(LspOperation.GO_TO_IMPLEMENTATION) is True
        assert _needs_gitignore_filter(LspOperation.WORKSPACE_SYMBOL) is True

    def test_no_filter(self):
        """documentSymbol, prepareCallHierarchy, incomingCalls, outgoingCalls 不需要过滤"""
        assert _needs_gitignore_filter(LspOperation.DOCUMENT_SYMBOL) is False
        assert _needs_gitignore_filter(LspOperation.PREPARE_CALL_HIERARCHY) is False
        assert _needs_gitignore_filter(LspOperation.INCOMING_CALLS) is False
        assert _needs_gitignore_filter(LspOperation.OUTGOING_CALLS) is False


# ============================================================================
# 6. 格式化器测试
# ============================================================================

class TestSymbolKindMap:
    """测试 SymbolKind 映射表"""

    def test_symbol_kind_map_has_all_25_kinds(self):
        """应有 25 种 SymbolKind"""
        assert len(SYMBOL_KIND_MAP) == 25
        assert SYMBOL_KIND_MAP[1] == "File"
        assert SYMBOL_KIND_MAP[5] == "Class"
        assert SYMBOL_KIND_MAP[12] == "Function"
        assert SYMBOL_KIND_MAP[25] == "TypeParameter"


class TestFormatLocation:
    """测试 Location 格式化"""

    def test_format_location_basic(self):
        """基本 Location 格式化"""
        loc = {
            "uri": "file:///path/to/file.py",
            "range": {"start": {"line": 4, "character": 2}, "end": {"start": {"line": 4, "character": 10}}},
        }
        result = format_location(loc)
        assert "/path/to/file.py" in result
        assert ":5:" in result  # 0-indexed 转 1-indexed (line 4 -> 5)
        # 格式为 "path:line:character"，character 2 -> 3
        assert "3" in result  # character 2 转 3


class TestFormatUri:
    """测试 URI 格式化"""

    def test_file_uri_decoded(self):
        """file:///path 应解码为 /path"""
        result = format_uri("file:///path/to/file.py")
        assert result == "/path/to/file.py"

    def test_percent_encoded_decoded(self):
        """percent-encoded 字符应被解码"""
        result = format_uri("file:///path/with%20space/file.py")
        assert "with space" in result


class TestFormatGoToDefinition:
    """测试 goToDefinition 结果格式化"""

    def test_no_result(self):
        """无结果时应返回 'No definition found.'"""
        assert format_go_to_definition(None) == "No definition found."
        assert format_go_to_definition({}) == "No definition found."

    def test_with_location(self):
        """有 Location 时应返回 'Defined in path:line:char'"""
        result = format_go_to_definition({
            "uri": "file:///path/to/file.py",
            "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 5}},
        })
        assert "Defined in" in result
        assert "/path/to/file.py" in result


class TestFormatFindReferences:
    """测试 findReferences 结果格式化"""

    def test_no_result(self):
        """无结果时应返回 'No references found.'"""
        assert format_find_references([]) == "No references found."

    def test_with_locations(self):
        """有多个位置时应按文件分组"""
        result = format_find_references([
            {"uri": "file:///a.py", "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 5}}},
            {"uri": "file:///a.py", "range": {"start": {"line": 1, "character": 0}, "end": {"line": 1, "character": 5}}},
            {"uri": "file:///b.py", "range": {"start": {"line": 2, "character": 0}, "end": {"line": 2, "character": 5}}},
        ])
        assert "/a.py" in result
        assert "/b.py" in result
        assert "1:1" in result  # 行号 0 转 1
        assert "3:1" in result  # 行号 2 转 3


class TestFormatDocumentSymbol:
    """测试 documentSymbol 结果格式化"""

    def test_no_result(self):
        """无结果时应返回 'No symbols found.'"""
        assert format_document_symbol([]) == "No symbols found."

    def test_flat_symbols(self):
        """平面符号列表应正确格式化"""
        result = format_document_symbol([
            {
                "name": "MyClass",
                "kind": 5,  # Class
                "location": {
                    "uri": "file:///path/to/file.py",
                    "range": {"start": {"line": 0, "character": 0}, "end": {"line": 5, "character": 0}},
                },
                "containerName": "",
            }
        ])
        assert "Class" in result
        assert "MyClass" in result

    def test_symbol_tree(self):
        """符号树应正确格式化（含缩进）"""
        result = format_document_symbol([
            {
                "name": "MyClass",
                "kind": 5,
                "detail": "public",
                "children": [
                    {"name": "my_method", "kind": 6, "detail": "", "children": []}  # Method
                ],
            }
        ])
        assert "Class" in result
        assert "my_method" in result
        assert "Method" in result


class TestFormatWorkspaceSymbol:
    """测试 workspaceSymbol 结果格式化"""

    def test_no_result(self):
        """无结果时应返回 'No symbols found.'"""
        assert format_workspace_symbol([]) == "No symbols found."

    def test_with_symbols(self):
        """符号结果应按文件分组"""
        result = format_workspace_symbol([
            {
                "name": "my_func",
                "kind": 12,  # Function
                "location": {
                    "uri": "file:///path/to/file.py",
                    "range": {"start": {"line": 3, "character": 0}, "end": {"line": 3, "character": 10}},
                },
                "containerName": "MyClass",
            }
        ])
        assert "Function" in result
        assert "my_func" in result
        assert "MyClass" in result


class TestFormatPrepareCallHierarchy:
    """测试 prepareCallHierarchy 结果格式化"""

    def test_no_result(self):
        """无结果时应返回 'No call hierarchy available.'"""
        assert format_prepare_call_hierarchy([]) == "No call hierarchy available."

    def test_single_item(self):
        """单个结果应显示为 'path:line: name'"""
        result = format_prepare_call_hierarchy([
            {
                "name": "my_func",
                "uri": "file:///path/to/file.py",
                "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 10}},
                "originSelectionRange": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 10}},
            }
        ])
        assert "my_func" in result
        assert "1:" in result


class TestFormatIncomingCalls:
    """测试 incomingCalls 结果格式化"""

    def test_no_result(self):
        """无结果时应返回 'No incoming calls found.'"""
        assert format_incoming_calls([]) == "No incoming calls found."


class TestFormatOutgoingCalls:
    """测试 outgoingCalls 结果格式化"""

    def test_no_result(self):
        """无结果时应返回 'No outgoing calls found.'"""
        assert format_outgoing_calls([]) == "No outgoing calls found."


class TestFormatResultRouter:
    """测试 format_result 根据操作类型路由到正确的格式化器"""

    def test_routes_all_8_operations(self):
        """所有 8 种操作都应有对应的格式化器"""
        for op in LspOperation:
            result = format_result(op, None)
            assert isinstance(result, str)


# ============================================================================
# 7. 路径解析测试
# ============================================================================

class TestResolvePath:
    """测试路径解析"""

    def test_absolute_path_unchanged(self, tmp_path):
        """绝对路径应保持不变"""
        abs_path = str(tmp_path / "test.py")
        result = _resolve_path(abs_path)
        assert result == abs_path

    def test_relative_path_resolved(self, tmp_path, monkeypatch):
        """相对路径应被解析为绝对路径"""
        monkeypatch.chdir(tmp_path)
        result = _resolve_path("test.py")
        assert result.endswith("test.py")
        assert result.startswith(str(tmp_path))


# ============================================================================
# 8. 集成测试（模拟 LSP 服务器）
# ============================================================================

@pytest.mark.asyncio
class TestLspToolIntegration:
    """集成测试：模拟 LSP 服务器响应"""

    async def test_call_lsp_tool_go_to_definition(self, tmp_path):
        """测试 goToDefinition 操作模拟调用"""
        from openjiuwen.harness.tools.lsp_tool._tool import call_lsp_tool

        # 创建测试文件
        test_file = tmp_path / "test.py"
        test_file.write_text("def foo(): pass\ndef bar(): pass\n")

        # 模拟 LSP 服务器响应
        mock_result = {
            "uri": f"file://{test_file}",
            "range": {"start": {"line": 0, "character": 4}, "end": {"line": 0, "character": 7}},
        }

        mock_instance = MagicMock()
        mock_instance.send_request = AsyncMock(return_value=mock_result)
        mock_instance.name = "pyright"

        mock_manager = MagicMock()
        mock_manager.get_or_start_server = AsyncMock(return_value=mock_instance)
        mock_manager._extension_map = {".py": ["pyright"]}

        with patch(
            "openjiuwen.harness.tools.lsp_tool._tool.LSPServerManager.get_instance",
            return_value=mock_manager,
        ), patch("openjiuwen.harness.tools.lsp_tool._tool.filter_git_ignored_locations", new_callable=AsyncMock, side_effect=lambda locs, cwd: locs):
            result = await call_lsp_tool({
                "operation": "goToDefinition",
                "file_path": str(test_file),
                "line": 3,
                "character": 1,
            })

        assert result["success"] is True
        assert result["data"]["operation"] == "goToDefinition"
        assert "Defined in" in result["data"]["result"]

    async def test_call_lsp_tool_find_references(self, tmp_path):
        """测试 findReferences 操作模拟调用"""
        from openjiuwen.harness.tools.lsp_tool._tool import call_lsp_tool

        test_file = tmp_path / "test.py"
        test_file.write_text("x = 1\ny = x + 1\nz = x * 2\n")

        mock_result = [
            {"uri": f"file://{test_file}", "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 1}}},
            {"uri": f"file://{test_file}", "range": {"start": {"line": 1, "character": 4}, "end": {"line": 1, "character": 5}}},
            {"uri": f"file://{test_file}", "range": {"start": {"line": 2, "character": 4}, "end": {"line": 2, "character": 5}}},
        ]

        mock_instance = MagicMock()
        mock_instance.send_request = AsyncMock(return_value=mock_result)
        mock_instance.name = "pyright"

        mock_manager = MagicMock()
        mock_manager.get_or_start_server = AsyncMock(return_value=mock_instance)
        mock_manager._extension_map = {".py": ["pyright"]}

        with patch(
            "openjiuwen.harness.tools.lsp_tool._tool.LSPServerManager.get_instance",
            return_value=mock_manager,
        ), patch("openjiuwen.harness.tools.lsp_tool._tool.filter_git_ignored_locations", new_callable=AsyncMock, return_value=mock_result):
            result = await call_lsp_tool({
                "operation": "findReferences",
                "file_path": str(test_file),
                "line": 1,
                "character": 5,
            })

        assert result["success"] is True
        assert result["data"]["operation"] == "findReferences"

    async def test_call_lsp_tool_document_symbol(self, tmp_path):
        """测试 documentSymbol 操作模拟调用"""
        from openjiuwen.harness.tools.lsp_tool._tool import call_lsp_tool

        test_file = tmp_path / "test.py"
        test_file.write_text("class Foo:\n    pass\ndef bar():\n    pass\n")

        mock_result = [
            {
                "name": "Foo",
                "kind": 5,  # Class
                "location": {"uri": f"file://{test_file}", "range": {"start": {"line": 0, "character": 0}, "end": {"line": 1, "character": 0}}},
            },
            {
                "name": "bar",
                "kind": 12,  # Function
                "location": {"uri": f"file://{test_file}", "range": {"start": {"line": 2, "character": 0}, "end": {"line": 3, "character": 0}}},
            },
        ]

        mock_instance = MagicMock()
        mock_instance.send_request = AsyncMock(return_value=mock_result)
        mock_instance.name = "pyright"

        mock_manager = MagicMock()
        mock_manager.get_or_start_server = AsyncMock(return_value=mock_instance)
        mock_manager._extension_map = {".py": ["pyright"]}

        with patch(
            "openjiuwen.harness.tools.lsp_tool._tool.LSPServerManager.get_instance",
            return_value=mock_manager,
        ):
            result = await call_lsp_tool({
                "operation": "documentSymbol",
                "file_path": str(test_file),
            })

        assert result["success"] is True
        assert result["data"]["operation"] == "documentSymbol"
        assert "Foo" in result["data"]["result"]
        assert "bar" in result["data"]["result"]

    async def test_call_lsp_tool_workspace_symbol(self, tmp_path):
        """测试 workspaceSymbol 操作模拟调用"""
        from openjiuwen.harness.tools.lsp_tool._tool import call_lsp_tool

        test_file = tmp_path / "test.py"
        test_file.write_text("def search():\n    pass\n")

        mock_result = [
            {
                "name": "search",
                "kind": 12,  # Function
                "location": {"uri": f"file://{test_file}", "range": {"start": {"line": 0, "character": 0}, "end": {"line": 1, "character": 0}}},
            }
        ]

        mock_instance = MagicMock()
        mock_instance.send_request = AsyncMock(return_value=mock_result)
        mock_instance.name = "pyright"

        mock_manager = MagicMock()
        mock_manager.get_or_start_server = AsyncMock(return_value=mock_instance)
        mock_manager._extension_map = {".py": ["pyright"]}

        with patch(
            "openjiuwen.harness.tools.lsp_tool._tool.LSPServerManager.get_instance",
            return_value=mock_manager,
        ), patch("openjiuwen.harness.tools.lsp_tool._tool.filter_git_ignored_locations", new_callable=AsyncMock, return_value=mock_result):
            result = await call_lsp_tool({
                "operation": "workspaceSymbol",
                "query": "search",
            })

        assert result["success"] is True
        assert result["data"]["operation"] == "workspaceSymbol"

    async def test_call_lsp_tool_prepare_call_hierarchy(self, tmp_path):
        """测试 prepareCallHierarchy 操作模拟调用"""
        from openjiuwen.harness.tools.lsp_tool._tool import call_lsp_tool

        test_file = tmp_path / "test.py"
        test_file.write_text("def my_func():\n    pass\n")

        mock_result = [
            {
                "name": "my_func",
                "uri": f"file://{test_file}",
                "range": {"start": {"line": 0, "character": 4}, "end": {"line": 0, "character": 11}},
                "originSelectionRange": {"start": {"line": 0, "character": 4}, "end": {"line": 0, "character": 11}},
            }
        ]

        mock_instance = MagicMock()
        mock_instance.send_request = AsyncMock(return_value=mock_result)
        mock_instance.name = "pyright"

        mock_manager = MagicMock()
        mock_manager.get_or_start_server = AsyncMock(return_value=mock_instance)
        mock_manager._extension_map = {".py": ["pyright"]}

        with patch(
            "openjiuwen.harness.tools.lsp_tool._tool.LSPServerManager.get_instance",
            return_value=mock_manager,
        ):
            result = await call_lsp_tool({
                "operation": "prepareCallHierarchy",
                "file_path": str(test_file),
                "line": 1,
                "character": 5,
            })

        assert result["success"] is True
        assert result["data"]["operation"] == "prepareCallHierarchy"

    async def test_call_lsp_tool_incoming_calls(self, tmp_path):
        """测试 incomingCalls 操作模拟调用"""
        from openjiuwen.harness.tools.lsp_tool._tool import call_lsp_tool

        test_file = tmp_path / "test.py"
        test_file.write_text("def caller():\n    my_func()\ndef my_func():\n    pass\n")

        mock_result = [
            {
                "from": {"uri": f"file://{test_file}", "range": {"start": {"line": 0, "character": 0}, "end": {"line": 1, "character": 0}}},
                "fromRanges": [{"start": {"line": 1, "character": 4}, "end": {"line": 1, "character": 11}}],
            }
        ]

        mock_instance = MagicMock()
        mock_instance.send_request = AsyncMock(return_value=mock_result)
        mock_instance.name = "pyright"

        mock_manager = MagicMock()
        mock_manager.get_or_start_server = AsyncMock(return_value=mock_instance)
        mock_manager._extension_map = {".py": ["pyright"]}

        with patch(
            "openjiuwen.harness.tools.lsp_tool._tool.LSPServerManager.get_instance",
            return_value=mock_manager,
        ):
            result = await call_lsp_tool({
                "operation": "incomingCalls",
                "file_path": str(test_file),
                "line": 3,
                "character": 5,
            })

        assert result["success"] is True
        assert result["data"]["operation"] == "incomingCalls"

    async def test_call_lsp_tool_outgoing_calls(self, tmp_path):
        """测试 outgoingCalls 操作模拟调用"""
        from openjiuwen.harness.tools.lsp_tool._tool import call_lsp_tool

        test_file = tmp_path / "test.py"
        test_file.write_text("def my_func():\n    helper()\ndef helper():\n    pass\n")

        mock_result = [
            {
                "from": {"uri": f"file://{test_file}", "range": {"start": {"line": 0, "character": 0}, "end": {"line": 1, "character": 0}}},
                "fromRanges": [{"start": {"line": 1, "character": 4}, "end": {"line": 1, "character": 10}}],
            }
        ]

        mock_instance = MagicMock()
        mock_instance.send_request = AsyncMock(return_value=mock_result)
        mock_instance.name = "pyright"

        mock_manager = MagicMock()
        mock_manager.get_or_start_server = AsyncMock(return_value=mock_instance)
        mock_manager._extension_map = {".py": ["pyright"]}

        with patch(
            "openjiuwen.harness.tools.lsp_tool._tool.LSPServerManager.get_instance",
            return_value=mock_manager,
        ):
            result = await call_lsp_tool({
                "operation": "outgoingCalls",
                "file_path": str(test_file),
                "line": 1,
                "character": 5,
            })

        assert result["success"] is True
        assert result["data"]["operation"] == "outgoingCalls"

    async def test_call_lsp_tool_invalid_input(self):
        """测试无效输入应返回错误"""
        from openjiuwen.harness.tools.lsp_tool._tool import call_lsp_tool

        result = await call_lsp_tool({"operation": "goToDefinition"})
        assert result["success"] is False
        assert "error" in result

    async def test_call_lsp_tool_manager_not_initialized(self):
        """测试 Manager 未初始化时应返回错误"""
        from openjiuwen.harness.tools.lsp_tool._tool import call_lsp_tool

        with patch("openjiuwen.harness.tools.lsp_tool._tool.LSPServerManager.get_instance", return_value=None):
            result = await call_lsp_tool({
                "operation": "goToDefinition",
                "file_path": "/path/to/file.py",
                "line": 1,
                "character": 1,
            })
        assert result["success"] is False
        assert "not initialized" in result["error"]

    async def test_call_lsp_tool_no_server(self):
        """测试没有可用服务器时应返回错误"""
        from openjiuwen.harness.tools.lsp_tool._tool import call_lsp_tool

        mock_manager = MagicMock()
        mock_manager.get_or_start_server = AsyncMock(return_value=None)
        mock_manager._extension_map = {}

        with patch(
            "openjiuwen.harness.tools.lsp_tool._tool.LSPServerManager.get_instance",
            return_value=mock_manager,
        ):
            result = await call_lsp_tool({
                "operation": "goToDefinition",
                "file_path": "/path/to/unknown.xyz",
                "line": 1,
                "character": 1,
            })

        assert result["success"] is False
        assert "No LSP server" in result["error"]

    async def test_call_lsp_tool_lsp_request_error(self, tmp_path):
        """测试 LSP 请求失败时应返回错误"""
        from openjiuwen.harness.tools.lsp_tool._tool import call_lsp_tool

        test_file = tmp_path / "test.py"
        test_file.write_text("x = 1\n")

        mock_instance = MagicMock()
        mock_instance.send_request = AsyncMock(side_effect=RuntimeError("Server error"))
        mock_instance.name = "pyright"

        mock_manager = MagicMock()
        mock_manager.get_or_start_server = AsyncMock(return_value=mock_instance)
        mock_manager._extension_map = {".py": ["pyright"]}

        with patch(
            "openjiuwen.harness.tools.lsp_tool._tool.LSPServerManager.get_instance",
            return_value=mock_manager,
        ):
            result = await call_lsp_tool({
                "operation": "goToDefinition",
                "file_path": str(test_file),
                "line": 1,
                "character": 1,
            })

        assert result["success"] is False
        assert "error" in result
