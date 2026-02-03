# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import os
from typing import Dict, List, AsyncIterator

import pytest
import pytest_asyncio

from openjiuwen.core.runner import Runner
from openjiuwen.core.sys_operation import OperationMode, SysOperationCard, SysOperation
from openjiuwen.core.sys_operation.local.utils import StreamEventType
from openjiuwen.core.sys_operation.result import ExecuteCodeResult, ExecuteCodeStreamResult
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

    @staticmethod
    def check_node_executable(local_nodejs_path: str = "") -> bool:
        """
        Add the given local Node.js path to the system environment variable PATH,
        then check if an executable Node program exists in the entire system PATH.
        """
        path_separator = ";" if os.name == "nt" else ":"
        original_path = os.environ.get("PATH", "")

        if local_nodejs_path.strip():
            os.environ["PATH"] = original_path + path_separator + local_nodejs_path.strip()

        path_dirs = os.environ.get("PATH", "").split(path_separator)
        node_exe = "node.exe" if os.name == "nt" else "node"
        node_found = False

        for dir_path in path_dirs:
            if not dir_path.strip():
                continue
            node_full_path = os.path.join(dir_path.strip(), node_exe)
            if os.path.exists(node_full_path) and \
                    os.path.isfile(node_full_path) and \
                    os.access(node_full_path, os.X_OK):
                node_found = True
                break

        return node_found

    async def collect_stream_results(
            self,
            stream: AsyncIterator[ExecuteCodeStreamResult]
    ) -> list[ExecuteCodeStreamResult]:
        """Collect all results from async stream iterator for assertion"""
        results = []
        async for res in stream:
            results.append(res)
        return results

    # ------------------------------ execute_code Test Cases ------------------------------
    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
    async def test_execute_javascript_code_success(self, sys_op: SysOperation):
        """Test successful execution of valid JavaScript code (requires Node.js installed)"""
        node_found = self.check_node_executable(local_nodejs_path="")
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
        assert result.data is not None
        assert result.data.code_content == code
        assert result.data.language == "javascript"
        assert result.data.exit_code == 0
        assert result.data.stdout.strip() == expected_stdout.strip()
        assert result.data.stderr == ""

    @pytest.mark.asyncio
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
        assert result.data is not None
        assert result.data.exit_code == 0
        assert result.data.stdout.strip() == expected_stdout.strip()

    @pytest.mark.asyncio
    async def test_execute_code_with_custom_timeout(self, sys_op: SysOperation):
        """Test using custom timeout (short execution time, no timeout triggered)"""
        # Test data preparation (sleep 1s, timeout 2s - should not timeout)
        code = "import time; time.sleep(1); print('Timeout test pass')"

        # Execute target method
        result: ExecuteCodeResult = await sys_op.code().execute_code(
            code=code,
            language="python",
            timeout=2
        )

        # Assertions
        assert result.code == StatusCode.SUCCESS.code
        assert result.data is not None
        assert result.data.exit_code == 0
        assert "pass" in result.data.stdout

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
    async def test_execute_python_code_with_syntax_error(self, sys_op: SysOperation):
        """Test execution of Python code with syntax errors"""
        code = "print('missing quote"  # Syntax error: missing closing quote
        result: ExecuteCodeResult = await sys_op.code().execute_code(code=code)

        assert result.code == StatusCode.SUCCESS.code
        assert result.message == "Code executed successfully"
        assert result.data is not None
        assert result.data.code_content == code
        assert result.data.language == "python"
        assert result.data.exit_code != 0
        assert "SyntaxError" in result.data.stderr

    @pytest.mark.asyncio
    async def test_execute_code_timeout(self, sys_op: SysOperation):
        """Test code execution timeout"""
        # Test data preparation (sleep 3s, timeout 1s - should trigger timeout)
        code = "import time; time.sleep(3)"

        # Execute target method
        result: ExecuteCodeResult = await sys_op.code().execute_code(
            code=code,
            language="python",
            timeout=1
        )

        # Assertions
        assert result.code == StatusCode.SYS_OPERATION_CODE_EXECUTION_ERROR.code
        assert f"execution timeout after 1 seconds" in result.message
        assert result.data.exit_code != 0

    @pytest.mark.asyncio
    async def test_execute_long_running_valid_code(self, sys_op: SysOperation):
        """Test execution of long-running but non-timeout code"""
        # Sleep 2s, timeout 3s - should complete successfully
        code = "import time; time.sleep(2); print('Long run success')"

        result: ExecuteCodeResult = await sys_op.code().execute_code(
            code=code,
            language="python",
            timeout=3
        )

        assert result.code == StatusCode.SUCCESS.code
        assert "success" in result.data.stdout.lower()

    @pytest.mark.asyncio
    async def test_execute_code_with_large_output(self, sys_op: SysOperation):
        """Test execution of code with large output volume"""
        # Generate 1000 lines of output
        code = "print('\\n'.join([f'Line {i}' for i in range(1000)]))"
        result: ExecuteCodeResult = await sys_op.code().execute_code(code=code)

        assert result.code == StatusCode.SUCCESS.code
        assert len(result.data.stdout.splitlines()) == 1000
        assert result.data.stderr == ""

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
    async def test_execute_code_force_file_true_via_options(self, sys_op: SysOperation):
        """Test execute_code: pass force_file=True via options → force temp file + successful execution"""
        # Test Python code with output and simple logic
        test_code = """
print(f"Python Exec Mode: Temp File")
a, b = 50, 60
print(f"50 + 60 = {a + b}")
        """

        # Execute code with force_file=True in options
        result: ExecuteCodeResult = await sys_op.code().execute_code(
            code=test_code,
            language="python",
            options={"force_file": True, "encoding": "utf-8"}
        )

        # 1. Verify execution success (status code + message)
        assert result.code == StatusCode.SUCCESS.code
        assert result.message == "Code executed successfully"
        # 2. Verify process exit code is 0 (no runtime error)
        assert result.data.exit_code == 0
        # 3. Verify stdout contains correct output
        assert "50 + 60 = 110" in result.data.stdout
        # 4. Verify no error output in stderr
        assert result.data.stderr.strip() == ""

    @pytest.mark.asyncio
    async def test_execute_code_force_file_true_javascript(self, sys_op: SysOperation):
        """Test execute_code: force_file=True for JavaScript (cross-language support)"""
        node_found = self.check_node_executable(local_nodejs_path="")
        if not node_found:
            pytest.skip(
                f"Node.js not found in system PATH. "
                f"Please install Node.js and add it to your system environment variable PATH."
            )

        # Test JavaScript code with console output
        js_test_code = """
console.log("JS Exec Mode: Temp File");
const num1 = 15, num2 = 25;
console.log(`15 * 25 = ${num1 * num2}`);
        """

        # Execute JS code with force_file=True
        result: ExecuteCodeResult = await sys_op.code().execute_code(
            code=js_test_code,
            language="javascript",
            options={"force_file": True, "encoding": "utf-8"}
        )

        # 1. Verify success status
        assert result.code == StatusCode.SUCCESS.code
        assert result.data.exit_code == 0
        # 2. Verify correct stdout output
        assert "JS Exec Mode: Temp File" in result.data.stdout
        assert "15 * 25 = 375" in result.data.stdout
        # 3. Verify empty stderr
        assert result.data.stderr.strip() == ""

    @pytest.mark.asyncio
    async def test_execute_code_force_file_true_with_error(self, sys_op: SysOperation):
        """Test execute_code: force_file=True with ERROR code → correct error capture"""
        # Intentionally invalid Python code (undefined variable → runtime error)
        error_test_code = "print(undefined_variable_999)  # undefined variable"

        # Execute with force_file=True
        result: ExecuteCodeResult = await sys_op.code().execute_code(
            code=error_test_code,
            options={"force_file": True}
        )

        # 1. Verify execution success (status code + message)
        assert result.code == StatusCode.SUCCESS.code
        assert result.message == "Code executed successfully"
        # 2. Verify exit code is non-0 (error marker)
        assert result.data.exit_code != 0
        # 3. Verify stderr contains error detail (undefined variable)
        assert "undefined_variable_999" in result.data.stderr

    @pytest.mark.asyncio
    async def test_execute_code_force_file_true_timeout(self, sys_op: SysOperation):
        """Test execute_code: force_file=True with code exceeding timeout → timeout error"""
        # Code with sleep (exceed short timeout → trigger TimeoutError)
        timeout_test_code = """
import time
time.sleep(3)  # Sleep 3s, timeout set to 1s
print("This line should not be printed")
        """

        # Execute with force_file=True and 1s timeout
        result: ExecuteCodeResult = await sys_op.code().execute_code(
            code=timeout_test_code,
            options={"force_file": True},
            timeout=1
        )

        # 1. Verify error status code
        assert result.code == StatusCode.SYS_OPERATION_CODE_EXECUTION_ERROR.code
        # 2. Verify error message contains timeout info
        assert "timeout after 1 seconds" in result.message
        # 3. Verify exit code
        assert result.data.exit_code != 0

    # ------------------------------ execute_code_stream Test Cases ------------------------------
    @pytest.mark.asyncio
    async def test_execute_code_stream_empty_code(self, sys_op: SysOperation):
        """Test empty/blank code execution, expect execution error return"""
        # Test empty string code
        empty_code_results = await self.collect_stream_results(
            sys_op.code().execute_code_stream(code="")
        )
        assert len(empty_code_results) == 1
        empty_res = empty_code_results[0]
        assert empty_res.code == StatusCode.SYS_OPERATION_CODE_EXECUTION_ERROR.code
        assert "code can not be empty" in empty_res.message
        assert empty_res.data.exit_code != 0

        # Test pure blank code (spaces, newlines, tabs)
        blank_code_results = await self.collect_stream_results(
            sys_op.code().execute_code_stream(code="   \n\t")
        )
        assert len(blank_code_results) == 1
        assert "code can not be empty" in blank_code_results[0].message

    @pytest.mark.asyncio
    async def test_execute_code_stream_unsupported_language(self, sys_op: SysOperation):
        """Test execute code with unsupported language, expect execution error return"""
        unsupported_results = await self.collect_stream_results(
            sys_op.code().execute_code_stream(
                code="print(1)",
                language="java"
            )
        )
        assert len(unsupported_results) == 1
        unsup_res = unsupported_results[0]
        assert unsup_res.code == StatusCode.SYS_OPERATION_CODE_EXECUTION_ERROR.code
        assert "java is not supported" in unsup_res.message
        assert unsup_res.data.exit_code != 0

    @pytest.mark.asyncio
    async def test_execute_code_stream_python_normal(self, sys_op: SysOperation):
        """Test normal python code execution, expect stdout stream + exit event with exit code 0"""
        python_code = """
print("hello python")
print("stream test for python")
        """
        results = await self.collect_stream_results(
            sys_op.code().execute_code_stream(
                code=python_code,
                language="python",
                timeout=10
            )
        )
        # Assert at least contains stdout stream and normal exit event
        assert len(results) >= 2

        # Check if stdout stream contains expected content
        expected_text_1 = any(
            res.data.type == StreamEventType.STDOUT.value and "hello python" in res.data.text
            for res in results
        )
        assert expected_text_1

        expected_text_2 = any(
            res.data.type == StreamEventType.STDOUT.value and "stream test for python" in res.data.text
            for res in results
        )
        assert expected_text_2

        executed_res = results[-1]
        assert executed_res is not None
        assert executed_res.message == "Code executed successfully"
        assert executed_res.data.exit_code == 0

    @pytest.mark.asyncio
    async def test_execute_code_stream_python_stderr(self, sys_op: SysOperation):
        """Test error python code execution, expect stderr stream + error exit event with non-0 exit code"""
        # Python code with runtime error (undefined variable)
        error_python_code = "print(undefined_variable)  # Undefined variable will throw NameError"
        results = await self.collect_stream_results(
            sys_op.code().execute_code_stream(
                code=error_python_code,
                language="python",
                timeout=10
            )
        )
        assert len(results) >= 1

        # Check if stderr stream contains NameError
        has_stderr_error = any(
            res.data.type == StreamEventType.STDERR.value and "NameError" in res.data.text
            for res in results
        )
        assert has_stderr_error

        executed_res = results[-1]
        assert executed_res is not None
        assert executed_res.message == "Code executed successfully"
        assert executed_res.data.exit_code != 0

    @pytest.mark.asyncio
    async def test_execute_code_stream_javascript_normal(self, sys_op: SysOperation):
        """Test normal javascript code execution, expect stdout stream + exit event with exit code 0"""
        node_found = self.check_node_executable(local_nodejs_path="")
        if not node_found:
            pytest.skip(
                f"Node.js not found in system PATH. "
                f"Please install Node.js and add it to your system environment variable PATH."
            )

        # Normal javascript code with multiple console logs
        js_code = """
console.log("hello javascript");
console.log("stream test for js");
        """
        results = await self.collect_stream_results(
            sys_op.code().execute_code_stream(
                code=js_code,
                language="javascript",
                timeout=10
            )
        )
        assert len(results) >= 2

        # Check if stdout stream contains expected content
        expected_text_1 = any(
            res.data.type == StreamEventType.STDOUT.value and "hello javascript" in res.data.text
            for res in results
        )
        assert expected_text_1

        expected_text_2 = any(
            res.data.type == StreamEventType.STDOUT.value and "stream test for js" in res.data.text
            for res in results
        )
        assert expected_text_2

        executed_res = results[-1]
        assert executed_res is not None
        assert executed_res.message == "Code executed successfully"
        assert executed_res.data.exit_code == 0

    @pytest.mark.asyncio
    async def test_execute_code_stream_custom_options(self, sys_op: SysOperation):
        """Test execute code with custom options (chunk_size/encoding), expect options to take effect"""
        # Python code output 2048 'a' to test chunk split
        python_code = "print('a'*2048)  # Output 2048 characters for chunk test"
        results = await self.collect_stream_results(
            sys_op.code().execute_code_stream(
                code=python_code,
                language="python",
                options={"chunk_size": 512, "encoding": "utf-8"},  # 512 bytes per chunk
                timeout=10
            )
        )
        # Assert at least 4 chunks (2048/512) + 1 exit event
        assert len(results) >= 5

        # Verify total number of 'a' is 2048 (no garbled, no loss)
        total_a_count = sum(res.data.text.count('a') for res in results if res.data.text)
        assert total_a_count == 2048

    @pytest.mark.asyncio
    async def test_execute_code_stream_custom_environment(self, sys_op: SysOperation):
        """Test execute code with custom environment variables, expect code to read env vars correctly"""
        # Python code to read custom environment variables
        python_code = """
import os
print(os.getenv("TEST_ENV_KEY"))
print(os.getenv("TEST_ENV_VALUE"))
        """
        results = await self.collect_stream_results(
            sys_op.code().execute_code_stream(
                code=python_code,
                language="python",
                environment={"TEST_ENV_KEY": "python_test", "TEST_ENV_VALUE": "123456"},
                timeout=10
            )
        )
        # Collect all stdout content
        stdout_text = "".join([res.data.text for res in results if res.data.text])
        # Assert custom env vars are read correctly
        assert "python_test" in stdout_text
        assert "123456" in stdout_text

    @pytest.mark.asyncio
    async def test_execute_code_stream_timeout(self, sys_op: SysOperation):
        """Test long-running code with short timeout, expect execution timeout error"""
        # Python code with infinite loop (will trigger timeout)
        timeout_code = "while True: pass  # Infinite loop for timeout test"
        results = await self.collect_stream_results(
            sys_op.code().execute_code_stream(
                code=timeout_code,
                language="python",
                timeout=2  # Timeout after 2 seconds
            )
        )
        assert len(results) >= 1

        # Check if timeout error message exists
        has_timeout_error = any(
            "timeout" in res.message.lower() or "execution receive error" in res.message
            for res in results
        )
        assert has_timeout_error

    @pytest.mark.asyncio
    async def test_execute_code_stream_default_params(self, sys_op: SysOperation):
        """Test execute code with all default parameters, expect normal execution"""
        # Simple python code for default param test
        default_code = "print('default parameter test success')"
        results = await self.collect_stream_results(
            sys_op.code().execute_code_stream(code=default_code)
        )
        assert len(results) >= 2

        # Collect stdout content and verify
        stdout_text = "".join([res.data.text for res in results if res.data.text])
        assert "default parameter test success" in stdout_text
        # Assert normal exit event exists
        executed_res = results[-1]
        assert executed_res is not None
        assert executed_res.message == "Code executed successfully"
        assert executed_res.data.exit_code == 0
