# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import os
from typing import Dict, List

import pytest
import pytest_asyncio

from openjiuwen.core.runner import Runner
from openjiuwen.core.sys_operation import OperationMode, SysOperationCard, SysOperation
from openjiuwen.core.sys_operation.result import ExecuteCodeResult
from openjiuwen.core.common.exception.codes import StatusCode


@pytest.mark.asyncio
class TestSysOperationExecuteCode:
    """Test suite for SysOperation.execute_code method"""

    @pytest_asyncio.fixture
    async def sys_op(self):
        """Fixture to setup and teardown Runner and SysOperation"""
        await Runner.start()
        card_id = "test_code_op"
        card = SysOperationCard(id=card_id, mode=OperationMode.LOCAL)

        add_res = Runner.resource_mgr.add_sys_operation(card)
        assert add_res.is_ok()

        op_instance = Runner.resource_mgr.get_sys_operation(card_id)
        yield op_instance

        Runner.resource_mgr.remove_sys_operation(sys_operation_id=card_id)
        await Runner.stop()

    async def test_execute_python_code_success(self, sys_op: SysOperation):
        """Test successful execution of valid Python code"""
        # Test data preparation
        code = "print('Hello, Python!'); x = 1 + 2; print(x)"
        expected_stdout = f"Hello, Python!{os.linesep}3{os.linesep}"

        # Execute target method
        result: ExecuteCodeResult = await sys_op.code().execute_code(code=code, language="python")

        # Assertions
        assert result.code == StatusCode.SUCCESS.code
        assert result.message == "Code executed successfully"
        assert result.data is not None
        assert result.data.code_content == code
        assert result.data.language == "python"
        assert result.data.exit_code == 0
        assert result.data.stdout.strip() == expected_stdout.strip()
        assert result.data.stderr == ""

    async def test_execute_javascript_code_success(self, sys_op: SysOperation):
        """Test successful execution of valid JavaScript code (requires Node.js installed)"""
        path_separator = ";" if os.name == "nt" else ":"
        path_dirs = os.environ.get("PATH", "").split(path_separator)
        node_exe = "node.exe" if os.name == "nt" else "node"
        node_found = False
        for dir_path in path_dirs:
            if not dir_path:
                continue
            node_path = os.path.join(dir_path.strip(), node_exe)
            if os.path.exists(node_path) and os.path.isfile(node_path) and os.access(node_path, os.X_OK):
                node_found = True
                break
        if not node_found:
            pytest.skip(
                f"Node.js not found in system PATH. "
                f"Please install Node.js and add it to your system environment variable PATH."
            )

        # Test data preparation
        code = "console.log('Hello, JavaScript!'); const x = 3 * 4; console.log(x)"
        expected_stdout = f"Hello, JavaScript!\n12\n"
        # Execute target method
        result: ExecuteCodeResult = await sys_op.code().execute_code(code=code, language="javascript")

        # Assertions
        assert result.code == StatusCode.SUCCESS.code
        assert result.message == "Code executed successfully"
        assert result.data.exit_code == 0
        assert result.data.stdout.strip() == expected_stdout.strip()
        assert result.data.stderr == ""

    async def test_execute_code_with_environment_vars(self, sys_op: SysOperation):
        """Test passing custom environment variables and using them in code"""
        # Test data preparation
        env_vars: Dict[str, str] = {"TEST_ENV": "pytest_test", "COUNT": "5"}
        code = """
import os
print(os.getenv('TEST_ENV'))
print(os.getenv('COUNT'))
        """
        expected_stdout = f"pytest_test{os.linesep}5{os.linesep}"

        # Execute target method
        result: ExecuteCodeResult = await sys_op.code().execute_code(
            code=code,
            language="python",
            environment=env_vars
        )

        # Assertions
        assert result.code == StatusCode.SUCCESS.code
        assert result.data.stdout.strip() == expected_stdout.strip()

    async def test_execute_code_with_custom_timeout(self, sys_op: SysOperation):
        """Test using custom timeout (short execution time, no timeout triggered)"""
        # Test data preparation (sleep 1s, timeout 2s - should not timeout)
        code = "import time; time.sleep(1); print('Timeout test pass')"

        # Execute target method
        result: ExecuteCodeResult = await sys_op.code().execute_code(
            code=code,
            language="python",
            time_out=2
        )

        # Assertions
        assert result.code == StatusCode.SUCCESS.code
        assert "pass" in result.data.stdout

    async def test_execute_empty_code(self, sys_op: SysOperation):
        """Test execution of empty code (including whitespace-only code)"""
        # Test multiple empty code scenarios
        empty_codes: List[str] = ["", "   ", "\n", "\t"]
        for code in empty_codes:
            result: ExecuteCodeResult = await sys_op.code().execute_code(code=code)

            assert result.code == StatusCode.SYS_OPERATION_CODE_EXECUTION_ERROR.code
            assert "code can not be empty" in result.message
            assert (result.data is None or
                    (result.data.code_content == code and result.data.language == "python"))

    async def test_execute_unsupported_language(self, sys_op: SysOperation):
        """Test execution of unsupported programming languages"""
        code = "print('test')"
        unsupported_langs: List[str] = ["java", "c++", "ruby", "go"]

        for lang in unsupported_langs:
            result: ExecuteCodeResult = await sys_op.code().execute_code(code=code, language=lang)

            assert result.code == StatusCode.SYS_OPERATION_CODE_EXECUTION_ERROR.code
            assert f"{lang} is not supported" in result.message
            assert result.data.code_content == code
            assert result.data.language == lang

    async def test_execute_python_code_with_syntax_error(self, sys_op: SysOperation):
        """Test execution of Python code with syntax errors"""
        code = "print('missing quote"  # Syntax error: missing closing quote
        result: ExecuteCodeResult = await sys_op.code().execute_code(code=code)

        assert result.code == StatusCode.SYS_OPERATION_CODE_EXECUTION_ERROR.code
        assert "execution failed" in result.message
        assert result.data.exit_code != 0
        assert "SyntaxError" in result.data.stderr
        assert result.data.code_content == code

    async def test_execute_code_timeout(self, sys_op: SysOperation):
        """Test code execution timeout"""
        # Test data preparation (sleep 3s, timeout 1s - should trigger timeout)
        code = "import time; time.sleep(3)"

        # Execute target method
        result: ExecuteCodeResult = await sys_op.code().execute_code(
            code=code,
            language="python",
            time_out=1
        )

        # Assertions
        assert result.code == StatusCode.SYS_OPERATION_CODE_EXECUTION_ERROR.code
        assert f"execution timeout after 1 seconds" in result.message
        assert result.data.exit_code == -1
        assert result.data.stderr == f"execution timeout after 1 seconds"

    async def test_execute_long_running_valid_code(self, sys_op: SysOperation):
        """Test execution of long-running but non-timeout code"""
        # Sleep 2s, timeout 3s - should complete successfully
        code = "import time; time.sleep(2); print('Long run success')"

        result: ExecuteCodeResult = await sys_op.code().execute_code(
            code=code,
            language="python",
            time_out=3
        )

        assert result.code == StatusCode.SUCCESS.code
        assert "success" in result.data.stdout.lower()

    async def test_execute_code_with_large_output(self, sys_op: SysOperation):
        """Test execution of code with large output volume"""
        # Generate 1000 lines of output
        code = "print('\\n'.join([f'Line {i}' for i in range(1000)]))"
        result: ExecuteCodeResult = await sys_op.code().execute_code(code=code)

        assert result.code == StatusCode.SUCCESS.code
        assert len(result.data.stdout.splitlines()) == 1000
        assert result.data.stderr == ""

    async def test_execute_code_with_special_characters(self, sys_op: SysOperation):
        """Test execution of code containing special characters (Chinese, emoji, symbols)"""
        code = """
print("Chinese test: 中文测试")
print("Special symbols: !@#$%^&*()_+-=[]{}|;:,.<>?")
        """
        result: ExecuteCodeResult = await sys_op.code().execute_code(code=code)

        assert result.code == StatusCode.SUCCESS.code
        assert "中文测试" in result.data.stdout
        assert "!@#$%^&*()" in result.data.stdout

    @pytest.mark.asyncio
    async def test_sys_op_fixture_reusability(self, sys_op: SysOperation):
        """Test reusability of sys_op fixture (execute code multiple times)"""
        # First execution
        result1 = await sys_op.code().execute_code(code="print(1)")
        assert result1.code == StatusCode.SUCCESS.code

        # Second execution with different code
        result2 = await sys_op.code().execute_code(code="print(2)")
        assert result2.code == StatusCode.SUCCESS.code
        assert "2" in result2.data.stdout
