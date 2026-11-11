# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""End-to-end tests for the yuanrong code-execution paths.

Gated on ``RUN_YUANRONG_TEST=1``.
"""
from __future__ import annotations

import os
import uuid
from typing import AsyncIterator, Dict, List, Optional

import pytest
import pytest_asyncio

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.runner import Runner
from openjiuwen.core.sys_operation import OperationMode, SandboxGatewayConfig, SysOperation, SysOperationCard
from openjiuwen.core.sys_operation.config import ContainerScope, PreDeployLauncherConfig, SandboxIsolationConfig
from openjiuwen.core.sys_operation.result import ExecuteCodeResult, ExecuteCodeStreamResult
from openjiuwen.core.sys_operation.sandbox.gateway.gateway_client import SandboxGatewayClient

requires_yuanrong = pytest.mark.skipif(
    os.environ.get("RUN_YUANRONG_TEST") != "1",
    reason="Requires running YuanRong cluster",
)


def _stdout_tail(stdout: Optional[str], n: int = 1) -> str:
    """YuanRong execute may prepend banner lines; use trailing non-empty lines as payload."""
    lines = [line for line in (stdout or "").splitlines() if line.strip()]
    if not lines:
        return ""
    return "\n".join(lines[-n:]) if n > 1 else lines[-1]


def _base_url() -> str:
    return os.environ.get("YUANRONG_TEST_BASE_URL", "http://127.0.0.1:8080")


def _executor() -> str:
    value = os.environ.get("YUANRONG_TEST_EXECUTOR", "default").strip().lower()
    return value if value in {"default", "docker"} else "default"


def _extra_params() -> dict:
    params: dict = {"executor": _executor()}
    if _executor() == "docker":
        image = os.environ.get("YUANRONG_TEST_IMAGE")
        if image:
            params["image"] = image
    return params


async def _remove_sys_operation_with_sandbox_release(sys_operation_id: str) -> None:
    sys_op = Runner.resource_mgr.get_sys_operation(sys_operation_id)
    if sys_op is not None and sys_op.isolation_key_template:
        try:
            await SandboxGatewayClient.release(sys_op.isolation_key_template, on_stop="delete")
        except Exception as exc:
            if "not found" not in str(exc).lower():
                raise
    Runner.resource_mgr.remove_sys_operation(sys_operation_id=sys_operation_id)


@pytest.mark.asyncio
class TestYuanrongCodeOperation:
    @pytest_asyncio.fixture
    async def sys_op(self) -> AsyncIterator[SysOperation]:
        await Runner.start()
        card_id = f"yuanrong_code_op_{uuid.uuid4().hex[:8]}"
        card = SysOperationCard(
            id=card_id,
            mode=OperationMode.SANDBOX,
            gateway_config=SandboxGatewayConfig(
                isolation=SandboxIsolationConfig(
                    container_scope=ContainerScope.CUSTOM,
                    custom_id=card_id,
                ),
                launcher_config=PreDeployLauncherConfig(
                    base_url=_base_url(),
                    sandbox_type="yuanrong",
                    idle_ttl_seconds=600,
                    extra_params=_extra_params(),
                ),
                timeout_seconds=30,
            ),
        )
        add_res = Runner.resource_mgr.add_sys_operation(card)
        assert add_res.is_ok()
        try:
            yield Runner.resource_mgr.get_sys_operation(card_id)
        finally:
            await _remove_sys_operation_with_sandbox_release(card_id)
            await Runner.stop()

    async def _sandbox_has_node(self, sys_op: SysOperation) -> bool:
        result = await sys_op.shell().execute_cmd("node --version")
        return result.code == StatusCode.SUCCESS.code and result.data.exit_code == 0

    async def collect_stream_results(
        self,
        stream: AsyncIterator[ExecuteCodeStreamResult],
    ) -> list[ExecuteCodeStreamResult]:
        results = []
        async for res in stream:
            results.append(res)
        return results

    @requires_yuanrong
    async def test_execute_python_code_success(self, sys_op: SysOperation):
        code = "print('Hello, Python!'); x = 1 + 2; print(x)"
        result: ExecuteCodeResult = await sys_op.code().execute_code(code=code, language="python")

        assert result.code == StatusCode.SUCCESS.code
        assert result.message == "Code executed successfully"
        assert result.data is not None
        assert result.data.code_content == code
        assert result.data.language == "python"
        assert result.data.exit_code == 0
        assert _stdout_tail(result.data.stdout, 2) == "Hello, Python!\n3"
        assert result.data.stderr == ""

    @requires_yuanrong
    async def test_execute_javascript_code_success(self, sys_op: SysOperation):
        if not await self._sandbox_has_node(sys_op):
            pytest.skip("Node.js not found in yuanrong sandbox")

        code = "console.log('Hello, JavaScript!'); const x = 3 * 4; console.log(x)"
        result: ExecuteCodeResult = await sys_op.code().execute_code(code=code, language="javascript")

        assert result.code == StatusCode.SUCCESS.code
        assert result.message == "Code executed successfully"
        assert result.data is not None
        assert result.data.exit_code == 0
        assert _stdout_tail(result.data.stdout, 2) == "Hello, JavaScript!\n12"

    @requires_yuanrong
    async def test_execute_code_with_environment_vars(self, sys_op: SysOperation):
        env_vars: Dict[str, str] = {"TEST_ENV": "pytest_test", "COUNT": "5"}
        code = """
import os
print(os.getenv('TEST_ENV'))
print(os.getenv('COUNT'))
        """
        result: ExecuteCodeResult = await sys_op.code().execute_code(
            code=code,
            language="python",
            environment=env_vars,
        )

        assert result.code == StatusCode.SUCCESS.code
        assert result.data is not None
        assert result.data.exit_code == 0
        assert _stdout_tail(result.data.stdout, 2) == "pytest_test\n5"

    @requires_yuanrong
    async def test_execute_empty_code(self, sys_op: SysOperation):
        empty_codes: List[str] = ["", "   ", "\n", "\t"]
        for code in empty_codes:
            result: ExecuteCodeResult = await sys_op.code().execute_code(code=code)
            assert result.code == StatusCode.SYS_OPERATION_CODE_EXECUTION_ERROR.code
            assert "code can not be empty" in result.message

    @requires_yuanrong
    async def test_execute_unsupported_language(self, sys_op: SysOperation):
        code = "print('test')"
        for lang in ["java", "c++", "ruby", "go"]:
            result: ExecuteCodeResult = await sys_op.code().execute_code(code=code, language=lang)
            assert result.code == StatusCode.SYS_OPERATION_CODE_EXECUTION_ERROR.code
            assert f"{lang} is not supported" in result.message

    @requires_yuanrong
    async def test_execute_python_code_with_syntax_error(self, sys_op: SysOperation):
        code = "print('missing quote"
        result: ExecuteCodeResult = await sys_op.code().execute_code(code=code)

        assert result.code == StatusCode.SUCCESS.code
        assert result.data is not None
        assert result.data.exit_code != 0

    @requires_yuanrong
    async def test_execute_code_timeout(self, sys_op: SysOperation):
        code = "import time; time.sleep(3)"
        result: ExecuteCodeResult = await sys_op.code().execute_code(code=code, language="python", timeout=1)

        assert result.code == StatusCode.SYS_OPERATION_CODE_EXECUTION_ERROR.code
        assert "execution timeout after 1 seconds" in result.message
        assert result.data.exit_code != 0

    @requires_yuanrong
    async def test_execute_code_force_file_true_via_options(self, sys_op: SysOperation):
        test_code = """
print(f"Python Exec Mode: Temp File")
a, b = 50, 60
print(f"50 + 60 = {a + b}")
        """
        result: ExecuteCodeResult = await sys_op.code().execute_code(
            code=test_code,
            language="python",
            options={"force_file": True},
        )

        assert result.code == StatusCode.SUCCESS.code
        assert result.data.exit_code == 0
        assert "50 + 60 = 110" in result.data.stdout

    @requires_yuanrong
    async def test_code_stream_python_success(self, sys_op: SysOperation):
        results: list[ExecuteCodeStreamResult] = []
        async for item in sys_op.code().execute_code_stream(code="print('stream-ok')", language="python"):
            results.append(item)

        assert results
        assert results[-1].code == StatusCode.SUCCESS.code
        assert results[-1].data.exit_code == 0
        assert "stream-ok" in "".join(item.data.text or "" for item in results if item.data)

    @requires_yuanrong
    async def test_execute_code_stream_empty_code(self, sys_op: SysOperation):
        empty_code_results = await self.collect_stream_results(sys_op.code().execute_code_stream(code=""))
        assert len(empty_code_results) == 1
        assert "code can not be empty" in empty_code_results[0].message

    @requires_yuanrong
    async def test_execute_code_stream_unsupported_language(self, sys_op: SysOperation):
        unsupported_results = await self.collect_stream_results(
            sys_op.code().execute_code_stream(code="print(1)", language="java")
        )
        assert len(unsupported_results) == 1
        assert "java is not supported" in unsupported_results[0].message
