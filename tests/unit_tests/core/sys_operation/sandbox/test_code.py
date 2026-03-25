# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from typing import AsyncIterator

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.sys_operation import OperationMode
from openjiuwen.core.sys_operation.local.utils import StreamEventType
from openjiuwen.core.sys_operation.registry import OperationRegistry
from openjiuwen.core.sys_operation.result import ExecuteCodeResult, ExecuteCodeStreamResult


async def _collect_stream_results(
        stream: AsyncIterator[ExecuteCodeStreamResult]
) -> list[ExecuteCodeStreamResult]:
    results = []
    async for res in stream:
        results.append(res)
    return results


@pytest.mark.asyncio
async def test_execute_python_code_success(local_op):
    """Test successful execution of valid Python code."""
    code = 'print("Hello, Python!")\nprint("3")'
    result: ExecuteCodeResult = await local_op.code().execute_code(code=code, language="python")

    assert result.code == StatusCode.SUCCESS.code
    assert result.message == "Code executed successfully"
    assert result.data is not None
    assert result.data.code_content == code
    assert result.data.language == "python"
    assert result.data.exit_code == 0
    assert "Hello, Python!" in result.data.stdout
    assert "3" in result.data.stdout
    assert result.data.stderr == ""


@pytest.mark.asyncio
async def test_execute_javascript_code_success(local_op):
    """Test successful execution of valid JavaScript code with the offline local sandbox provider."""
    code = 'print("Hello, JavaScript!")\nprint("12")'
    result: ExecuteCodeResult = await local_op.code().execute_code(code=code, language="javascript")

    assert result.code == StatusCode.SUCCESS.code
    assert result.data is not None
    assert result.data.language == "javascript"
    assert result.data.exit_code == 0
    assert "Hello, JavaScript!" in result.data.stdout
    assert "12" in result.data.stdout


@pytest.mark.asyncio
async def test_execute_code_with_environment_vars(local_op):
    """Test environment variable propagation for code execution."""
    code = """
import os
print(os.getenv("TEST_ENV"))
print(os.getenv("COUNT"))
    """
    result: ExecuteCodeResult = await local_op.code().execute_code(
        code=code,
        language="python",
        environment={"TEST_ENV": "pytest_test", "COUNT": "5"},
    )

    assert result.code == StatusCode.SUCCESS.code
    assert result.data is not None
    assert result.data.exit_code == 0
    assert result.data.stdout.strip().splitlines() == ["pytest_test", "5"]


@pytest.mark.asyncio
async def test_execute_code_with_custom_timeout(local_op):
    """Test non-timeout execution when timeout is sufficient."""
    code = 'print("Timeout test pass")'
    result: ExecuteCodeResult = await local_op.code().execute_code(code=code, language="python", timeout=2)

    assert result.code == StatusCode.SUCCESS.code
    assert result.data is not None
    assert result.data.exit_code == 0
    assert "Timeout test pass" in result.data.stdout


@pytest.mark.asyncio
async def test_execute_empty_code(local_op):
    """Test empty code validation."""
    for code in ("", "   ", "\n", "\t"):
        result: ExecuteCodeResult = await local_op.code().execute_code(code=code)
        assert result.code == StatusCode.SYS_OPERATION_CODE_EXECUTION_ERROR.code
        assert "code can not be empty" in result.message
        assert result.data is not None
        assert result.data.exit_code == -1


@pytest.mark.asyncio
async def test_execute_unsupported_language(local_op):
    """Test unsupported language validation."""
    for language in ("java", "c++", "ruby", "go"):
        result: ExecuteCodeResult = await local_op.code().execute_code(code="print('test')", language=language)
        assert result.code == StatusCode.SYS_OPERATION_CODE_EXECUTION_ERROR.code
        assert f"{language} is not supported" in result.message
        assert result.data is not None
        assert result.data.language == language


@pytest.mark.asyncio
async def test_execute_python_code_with_syntax_error(local_op):
    """Test syntax error propagation in execute_code."""
    code = "print('missing quote"
    result: ExecuteCodeResult = await local_op.code().execute_code(code=code)

    assert result.code == StatusCode.SUCCESS.code
    assert result.data is not None
    assert result.data.exit_code != 0
    assert "SyntaxError" in result.data.stderr


@pytest.mark.asyncio
async def test_execute_code_timeout(local_op):
    """Test timeout errors for execute_code."""
    code = "import time; time.sleep(3)"
    result: ExecuteCodeResult = await local_op.code().execute_code(code=code, language="python", timeout=1)

    assert result.code == StatusCode.SYS_OPERATION_CODE_EXECUTION_ERROR.code
    assert "execution timeout after 1 seconds" in result.message
    assert result.data is not None
    assert result.data.exit_code != 0


@pytest.mark.asyncio
async def test_execute_long_running_valid_code(local_op):
    """Test valid code that should complete before timeout."""
    code = 'print("Long run success")'
    result: ExecuteCodeResult = await local_op.code().execute_code(code=code, language="python", timeout=3)

    assert result.code == StatusCode.SUCCESS.code
    assert result.data is not None
    assert "Long run success" in result.data.stdout


@pytest.mark.asyncio
async def test_execute_code_with_special_characters(local_op):
    """Test code with non-ASCII and symbols."""
    code = """
print("Chinese test: 中文测试")
print("Special symbols: !@#$%^&*()_+-=[]{}|;:,.<>?")
    """
    result: ExecuteCodeResult = await local_op.code().execute_code(code=code)

    assert result.code == StatusCode.SUCCESS.code
    assert result.data is not None
    assert "Chinese test: 中文测试" in result.data.stdout
    assert "!@#$%^&*()" in result.data.stdout


@pytest.mark.asyncio
async def test_sys_op_fixture_reusability(local_op):
    """Test repeated code execution through the same fixture instance."""
    result1 = await local_op.code().execute_code(code='print("1")')
    result2 = await local_op.code().execute_code(code='print("2")')

    assert result1.code == StatusCode.SUCCESS.code
    assert result2.code == StatusCode.SUCCESS.code
    assert result2.data is not None
    assert "2" in result2.data.stdout


@pytest.mark.asyncio
async def test_execute_code_force_file_true_via_options(local_op):
    """Test that force_file-style options do not break offline routed execution."""
    code = """
print("Python Exec Mode: Temp File")
print("50 + 60 = 110")
    """
    result: ExecuteCodeResult = await local_op.code().execute_code(
        code=code,
        language="python",
        options={"force_file": True, "encoding": "utf-8"},
    )

    assert result.code == StatusCode.SUCCESS.code
    assert result.data is not None
    assert result.data.exit_code == 0
    assert "Python Exec Mode: Temp File" in result.data.stdout
    assert "50 + 60 = 110" in result.data.stdout
    assert result.data.stderr.strip() == ""


@pytest.mark.asyncio
async def test_execute_code_force_file_true_javascript(local_op):
    """Test force_file-style options with JavaScript language path."""
    code = """
print("JS Exec Mode: Temp File")
print("15 * 25 = 375")
    """
    result: ExecuteCodeResult = await local_op.code().execute_code(
        code=code,
        language="javascript",
        options={"force_file": True, "encoding": "utf-8"},
    )

    assert result.code == StatusCode.SUCCESS.code
    assert result.data is not None
    assert result.data.exit_code == 0
    assert "JS Exec Mode: Temp File" in result.data.stdout
    assert "15 * 25 = 375" in result.data.stdout
    assert result.data.stderr.strip() == ""


@pytest.mark.asyncio
async def test_execute_code_force_file_true_with_error(local_op):
    """Test error capture under force_file-style options."""
    code = "print(undefined_variable_999)"
    result: ExecuteCodeResult = await local_op.code().execute_code(code=code, options={"force_file": True})

    assert result.code == StatusCode.SUCCESS.code
    assert result.data is not None
    assert result.data.exit_code != 0
    assert "undefined_variable_999" in result.data.stderr


@pytest.mark.asyncio
async def test_execute_code_force_file_true_timeout(local_op):
    """Test timeout under force_file-style options."""
    code = """
import time
time.sleep(3)
print("This line should not be printed")
    """
    result: ExecuteCodeResult = await local_op.code().execute_code(
        code=code,
        options={"force_file": True},
        timeout=1,
    )

    assert result.code == StatusCode.SYS_OPERATION_CODE_EXECUTION_ERROR.code
    assert "timeout after 1 seconds" in result.message
    assert result.data is not None
    assert result.data.exit_code != 0


@pytest.mark.asyncio
async def test_execute_code_stream_empty_code(local_op):
    """Test empty code validation for stream execution."""
    results = await _collect_stream_results(local_op.code().execute_code_stream(code=""))
    assert len(results) == 1
    assert results[0].code == StatusCode.SYS_OPERATION_CODE_EXECUTION_ERROR.code
    assert "code can not be empty" in results[0].message
    assert results[0].data.exit_code != 0


@pytest.mark.asyncio
async def test_execute_code_stream_unsupported_language(local_op):
    """Test unsupported language validation for stream execution."""
    results = await _collect_stream_results(local_op.code().execute_code_stream(code="print(1)", language="java"))

    assert len(results) == 1
    assert results[0].code == StatusCode.SYS_OPERATION_CODE_EXECUTION_ERROR.code
    assert "java is not supported" in results[0].message
    assert results[0].data.exit_code != 0


@pytest.mark.asyncio
async def test_execute_code_stream_python_normal(local_op):
    """Test normal Python stream execution."""
    code = """
print("hello python")
print("stream test for python")
    """
    results = await _collect_stream_results(local_op.code().execute_code_stream(code=code,
                                                                                language="python", timeout=10))

    assert len(results) >= 3
    assert any(res.data.type == StreamEventType.STDOUT.value and "hello python" in res.data.text for res in results)
    assert any(
        res.data.type == StreamEventType.STDOUT.value and "stream test for python" in res.data.text for res in results
    )
    assert results[-1].message == "Code executed successfully"
    assert results[-1].data.exit_code == 0


@pytest.mark.asyncio
async def test_execute_code_stream_python_stderr(local_op):
    """Test stderr streaming for runtime errors."""
    code = "print(undefined_variable)"
    results = await _collect_stream_results(local_op.code().execute_code_stream(code=code,
                                                                                language="python", timeout=10))

    assert len(results) >= 2
    assert any(res.data.type == StreamEventType.STDERR.value and "NameError" in res.data.text for res in results)
    assert results[-1].message == "Code executed successfully"
    assert results[-1].data.exit_code != 0


@pytest.mark.asyncio
async def test_execute_code_stream_javascript_normal(local_op):
    """Test normal JavaScript stream execution through the offline local provider."""
    code = """
print("hello javascript")
print("stream test for js")
    """
    results = await _collect_stream_results(
        local_op.code().execute_code_stream(code=code, language="javascript", timeout=10)
    )

    assert len(results) >= 3
    assert any(res.data.type == StreamEventType.STDOUT.value and "hello javascript" in res.data.text for res in results)
    assert any(
        res.data.type == StreamEventType.STDOUT.value and "stream test for js" in res.data.text for res in results)
    assert results[-1].message == "Code executed successfully"
    assert results[-1].data.exit_code == 0


@pytest.mark.asyncio
async def test_execute_code_stream_custom_options(local_op):
    """Test that custom stream options are accepted without breaking routing."""
    code = 'print("chunk-size-option-test")'
    results = await _collect_stream_results(
        local_op.code().execute_code_stream(
            code=code,
            language="python",
            options={"chunk_size": 512, "encoding": "utf-8"},
            timeout=10,
        )
    )

    stdout_text = "".join(res.data.text for res in results if res.data.text)
    assert "chunk-size-option-test" in stdout_text
    assert results[-1].data.exit_code == 0


@pytest.mark.asyncio
async def test_execute_code_stream_custom_environment(local_op):
    """Test environment propagation during stream execution."""
    code = """
import os
print(os.getenv("TEST_ENV_KEY"))
print(os.getenv("TEST_ENV_VALUE"))
    """
    results = await _collect_stream_results(
        local_op.code().execute_code_stream(
            code=code,
            language="python",
            environment={"TEST_ENV_KEY": "python_test", "TEST_ENV_VALUE": "123456"},
            timeout=10,
        )
    )

    stdout_text = "".join(res.data.text for res in results if res.data.text)
    assert "python_test" in stdout_text
    assert "123456" in stdout_text


@pytest.mark.asyncio
async def test_execute_code_stream_timeout(local_op):
    """Test timeout handling for stream execution."""
    results = await _collect_stream_results(
        local_op.code().execute_code_stream(code="while True: pass", language="python", timeout=2)
    )

    assert len(results) == 1
    assert results[0].code == StatusCode.SYS_OPERATION_CODE_EXECUTION_ERROR.code
    assert "timeout" in results[0].message.lower()


@pytest.mark.asyncio
async def test_execute_code_stream_default_params(local_op):
    """Test stream execution with all default parameters."""
    results = await _collect_stream_results(
        local_op.code().execute_code_stream(code='print("default parameter test success")'))

    assert len(results) >= 2
    stdout_text = "".join(res.data.text for res in results if res.data.text)
    assert "default parameter test success" in stdout_text
    assert results[-1].message == "Code executed successfully"
    assert results[-1].data.exit_code == 0


def test_sandbox_discovery():
    """Test that sandbox operations are correctly discovered."""
    fs_op = OperationRegistry.get_operation_info("fs", OperationMode.SANDBOX)
    assert fs_op is not None
    assert fs_op.name == "fs"
    assert fs_op.mode == OperationMode.SANDBOX

    shell_op = OperationRegistry.get_operation_info("shell", OperationMode.SANDBOX)
    assert shell_op is not None

    code_op = OperationRegistry.get_operation_info("code", OperationMode.SANDBOX)
    assert code_op is not None
