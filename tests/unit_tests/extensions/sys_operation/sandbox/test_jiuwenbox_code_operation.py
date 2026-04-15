# coding: utf-8
from __future__ import annotations

import os
from pathlib import Path
from typing import AsyncIterator, Dict, List
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio

from openjiuwen.core.common.exception.codes import StatusCode  # noqa: E402
from openjiuwen.core.runner import Runner  # noqa: E402
from openjiuwen.core.sys_operation import OperationMode, SandboxGatewayConfig, SysOperation, SysOperationCard  # noqa: E402
from openjiuwen.core.sys_operation.config import ContainerScope, PreDeployLauncherConfig, SandboxIsolationConfig  # noqa: E402
from openjiuwen.core.sys_operation.local.utils import StreamEventType  # noqa: E402
from openjiuwen.core.sys_operation.result import ExecuteCodeResult, ExecuteCodeStreamResult  # noqa: E402


LONG_RUNNING_COMMAND = ["/usr/bin/python3", "-c", "import time; time.sleep(3600)"]


def _normalize_endpoint(endpoint: str) -> str:
    return endpoint if "://" in endpoint else f"http://{endpoint}"


@pytest.fixture
def server_endpoint() -> str:
    return os.environ.get("JIUWENBOX_TEST_SERVER", "127.0.0.1:8321")


@pytest.mark.asyncio
class TestJiuwenboxCodeOperation:
    @pytest_asyncio.fixture
    async def sys_op(self, server_endpoint, monkeypatch) -> AsyncIterator[SysOperation]:
        base_url = _normalize_endpoint(server_endpoint)
        with httpx.Client(base_url=base_url, timeout=30.0) as client:
            create_resp = client.post("/api/v1/sandboxes", json={"command": LONG_RUNNING_COMMAND})
            assert create_resp.status_code == 201, create_resp.text
            sandbox_id = create_resp.json()["id"]

            monkeypatch.setenv("JIUWENBOX_SANDBOX_ID", sandbox_id)
            await Runner.start()
            card_id = f"jiuwenbox_op_{uuid4().hex[:8]}"
            card = SysOperationCard(
                id=card_id,
                mode=OperationMode.SANDBOX,
                gateway_config=SandboxGatewayConfig(
                    isolation=SandboxIsolationConfig(
                        container_scope=ContainerScope.CUSTOM,
                        custom_id=sandbox_id,
                    ),
                    launcher_config=PreDeployLauncherConfig(
                        base_url=base_url,
                        sandbox_type="jiuwenbox",
                        idle_ttl_seconds=600,
                    ),
                    timeout_seconds=30,
                ),
            )

            add_res = Runner.resource_mgr.add_sys_operation(card)
            assert add_res.is_ok()
            try:
                yield Runner.resource_mgr.get_sys_operation(card_id)
            finally:
                Runner.resource_mgr.remove_sys_operation(sys_operation_id=card_id)
                await Runner.stop()
                client.delete(f"/api/v1/sandboxes/{sandbox_id}")
                monkeypatch.delenv("JIUWENBOX_SANDBOX_ID", raising=False)

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

    @pytest.mark.skipif(os.environ.get("RUN_JIUWENBOX_TEST") != "1", reason="Requires running Jiuwenbox sandbox")
    async def test_shell_execute_cmd_success(self, sys_op: SysOperation):
        result = await sys_op.shell().execute_cmd(
            "printf 'out'; printf 'err' >&2",
            cwd="/tmp",
            environment={"JIUWENBOX_ADAPTER_TEST": "ok"},
        )

        assert result.code == StatusCode.SUCCESS.code
        assert result.data.exit_code == 0
        assert result.data.stdout == "out"
        assert result.data.stderr == "err"

    @pytest.mark.skipif(os.environ.get("RUN_JIUWENBOX_TEST") != "1", reason="Requires running Jiuwenbox sandbox")
    async def test_execute_python_code_success(self, sys_op: SysOperation):
        code = "print('Hello, Python!'); x = 1 + 2; print(x)"
        result: ExecuteCodeResult = await sys_op.code().execute_code(code=code, language="python")

        assert result.code == StatusCode.SUCCESS.code
        assert result.message == "Code executed successfully"
        assert result.data is not None
        assert result.data.code_content == code
        assert result.data.language == "python"
        assert result.data.exit_code == 0
        assert result.data.stdout.strip() == "Hello, Python!\n3".strip()
        assert result.data.stderr == ""

    @pytest.mark.skipif(os.environ.get("RUN_JIUWENBOX_TEST") != "1", reason="Requires running Jiuwenbox sandbox")
    async def test_execute_javascript_code_success(self, sys_op: SysOperation):
        if not await self._sandbox_has_node(sys_op):
            pytest.skip("Node.js not found in jiuwenbox sandbox")

        code = "console.log('Hello, JavaScript!'); const x = 3 * 4; console.log(x)"
        result: ExecuteCodeResult = await sys_op.code().execute_code(code=code, language="javascript")

        assert result.code == StatusCode.SUCCESS.code
        assert result.message == "Code executed successfully"
        assert result.data is not None
        assert result.data.code_content == code
        assert result.data.language == "javascript"
        assert result.data.exit_code == 0
        assert result.data.stdout.strip() == "Hello, JavaScript!\n12".strip()
        assert result.data.stderr == ""

    @pytest.mark.skipif(os.environ.get("RUN_JIUWENBOX_TEST") != "1", reason="Requires running Jiuwenbox sandbox")
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
        assert result.data.stdout.strip() == "pytest_test\n5".strip()

    @pytest.mark.skipif(os.environ.get("RUN_JIUWENBOX_TEST") != "1", reason="Requires running Jiuwenbox sandbox")
    async def test_execute_code_with_custom_timeout(self, sys_op: SysOperation):
        code = "import time; time.sleep(1); print('Timeout test pass')"
        result: ExecuteCodeResult = await sys_op.code().execute_code(code=code, language="python", timeout=2)

        assert result.code == StatusCode.SUCCESS.code
        assert result.data is not None
        assert result.data.exit_code == 0
        assert "pass" in result.data.stdout

    @pytest.mark.skipif(os.environ.get("RUN_JIUWENBOX_TEST") != "1", reason="Requires running Jiuwenbox sandbox")
    async def test_execute_empty_code(self, sys_op: SysOperation):
        empty_codes: List[str] = ["", "   ", "\n", "\t"]
        for code in empty_codes:
            result: ExecuteCodeResult = await sys_op.code().execute_code(code=code)
            assert result.code == StatusCode.SYS_OPERATION_CODE_EXECUTION_ERROR.code
            assert "code can not be empty" in result.message
            assert result.data is not None
            assert result.data.code_content == code
            assert result.data.language == "python"

    @pytest.mark.skipif(os.environ.get("RUN_JIUWENBOX_TEST") != "1", reason="Requires running Jiuwenbox sandbox")
    async def test_execute_unsupported_language(self, sys_op: SysOperation):
        code = "print('test')"
        unsupported_langs: List[str] = ["java", "c++", "ruby", "go"]

        for lang in unsupported_langs:
            result: ExecuteCodeResult = await sys_op.code().execute_code(code=code, language=lang)
            assert result.code == StatusCode.SYS_OPERATION_CODE_EXECUTION_ERROR.code
            assert f"{lang} is not supported" in result.message
            assert result.data.code_content == code
            assert result.data.language == lang

    @pytest.mark.skipif(os.environ.get("RUN_JIUWENBOX_TEST") != "1", reason="Requires running Jiuwenbox sandbox")
    async def test_execute_python_code_with_syntax_error(self, sys_op: SysOperation):
        code = "print('missing quote"
        result: ExecuteCodeResult = await sys_op.code().execute_code(code=code)

        assert result.code == StatusCode.SUCCESS.code
        assert result.message == "Code executed successfully"
        assert result.data is not None
        assert result.data.code_content == code
        assert result.data.language == "python"
        assert result.data.exit_code != 0

    @pytest.mark.skipif(os.environ.get("RUN_JIUWENBOX_TEST") != "1", reason="Requires running Jiuwenbox sandbox")
    async def test_execute_code_timeout(self, sys_op: SysOperation):
        code = "import time; time.sleep(3)"
        result: ExecuteCodeResult = await sys_op.code().execute_code(code=code, language="python", timeout=1)

        assert result.code == StatusCode.SYS_OPERATION_CODE_EXECUTION_ERROR.code
        assert "execution timeout after 1 seconds" in result.message
        assert result.data.exit_code != 0

    @pytest.mark.skipif(os.environ.get("RUN_JIUWENBOX_TEST") != "1", reason="Requires running Jiuwenbox sandbox")
    async def test_execute_long_running_valid_code(self, sys_op: SysOperation):
        code = "import time; time.sleep(2); print('Long run success')"
        result: ExecuteCodeResult = await sys_op.code().execute_code(code=code, language="python", timeout=3)

        assert result.code == StatusCode.SUCCESS.code
        assert "success" in result.data.stdout.lower()

    @pytest.mark.skipif(os.environ.get("RUN_JIUWENBOX_TEST") != "1", reason="Requires running Jiuwenbox sandbox")
    async def test_execute_code_with_large_output(self, sys_op: SysOperation):
        code = "print('\\n'.join([f'Line {i}' for i in range(1000)]))"
        result: ExecuteCodeResult = await sys_op.code().execute_code(code=code)

        assert result.code == StatusCode.SUCCESS.code
        assert len(result.data.stdout.splitlines()) == 1000
        assert result.data.stderr == ""

    @pytest.mark.skipif(os.environ.get("RUN_JIUWENBOX_TEST") != "1", reason="Requires running Jiuwenbox sandbox")
    async def test_execute_code_with_special_characters(self, sys_op: SysOperation):
        code = r"""
print("Unicode escape test: \u4e2d\u6587")
print("Special symbols: !@#$%^&*()_+-=[]{}|;:,.<>?")
        """
        result: ExecuteCodeResult = await sys_op.code().execute_code(code=code)

        assert result.code == StatusCode.SUCCESS.code
        assert "\u4e2d\u6587" in result.data.stdout
        assert "!@#$%^&*()" in result.data.stdout

    @pytest.mark.skipif(os.environ.get("RUN_JIUWENBOX_TEST") != "1", reason="Requires running Jiuwenbox sandbox")
    async def test_sys_op_fixture_reusability(self, sys_op: SysOperation):
        result1 = await sys_op.code().execute_code(code="print(1)")
        assert result1.code == StatusCode.SUCCESS.code

        result2 = await sys_op.code().execute_code(code="print(2)")
        assert result2.code == StatusCode.SUCCESS.code
        assert "2" in result2.data.stdout

    @pytest.mark.skipif(os.environ.get("RUN_JIUWENBOX_TEST") != "1", reason="Requires running Jiuwenbox sandbox")
    async def test_execute_code_force_file_true_via_options(self, sys_op: SysOperation):
        test_code = """
print(f"Python Exec Mode: Temp File")
a, b = 50, 60
print(f"50 + 60 = {a + b}")
        """
        result: ExecuteCodeResult = await sys_op.code().execute_code(
            code=test_code,
            language="python",
            options={"force_file": True, "encoding": "utf-8"},
        )

        assert result.code == StatusCode.SUCCESS.code
        assert result.message == "Code executed successfully"
        assert result.data.exit_code == 0
        assert "50 + 60 = 110" in result.data.stdout
        assert result.data.stderr.strip() == ""

    @pytest.mark.skipif(os.environ.get("RUN_JIUWENBOX_TEST") != "1", reason="Requires running Jiuwenbox sandbox")
    async def test_execute_code_force_file_true_javascript(self, sys_op: SysOperation):
        if not await self._sandbox_has_node(sys_op):
            pytest.skip("Node.js not found in jiuwenbox sandbox")

        js_test_code = """
console.log("JS Exec Mode: Temp File");
const num1 = 15, num2 = 25;
console.log(`15 * 25 = ${num1 * num2}`);
        """
        result: ExecuteCodeResult = await sys_op.code().execute_code(
            code=js_test_code,
            language="javascript",
            options={"force_file": True, "encoding": "utf-8"},
        )

        assert result.code == StatusCode.SUCCESS.code
        assert result.data.exit_code == 0
        assert "JS Exec Mode: Temp File" in result.data.stdout
        assert "15 * 25 = 375" in result.data.stdout
        assert result.data.stderr.strip() == ""

    @pytest.mark.skipif(os.environ.get("RUN_JIUWENBOX_TEST") != "1", reason="Requires running Jiuwenbox sandbox")
    async def test_execute_code_force_file_true_with_error(self, sys_op: SysOperation):
        error_test_code = "print(undefined_variable_999)"
        result: ExecuteCodeResult = await sys_op.code().execute_code(
            code=error_test_code,
            options={"force_file": True},
        )

        assert result.code == StatusCode.SUCCESS.code
        assert result.message == "Code executed successfully"
        assert result.data.exit_code != 0

    @pytest.mark.skipif(os.environ.get("RUN_JIUWENBOX_TEST") != "1", reason="Requires running Jiuwenbox sandbox")
    async def test_execute_code_force_file_true_timeout(self, sys_op: SysOperation):
        timeout_test_code = """
import time
time.sleep(3)
print("This line should not be printed")
        """
        result: ExecuteCodeResult = await sys_op.code().execute_code(
            code=timeout_test_code,
            options={"force_file": True},
            timeout=1,
        )

        assert result.code == StatusCode.SYS_OPERATION_CODE_EXECUTION_ERROR.code
        assert "timeout after 1 seconds" in result.message
        assert result.data.exit_code != 0

    @pytest.mark.skipif(os.environ.get("RUN_JIUWENBOX_TEST") != "1", reason="Requires running Jiuwenbox sandbox")
    async def test_code_stream_python_success(self, sys_op: SysOperation):
        results: list[ExecuteCodeStreamResult] = []
        async for item in sys_op.code().execute_code_stream(code="print('stream-ok')", language="python"):
            results.append(item)

        assert results
        assert results[-1].code == StatusCode.SUCCESS.code
        assert results[-1].data.exit_code == 0
        assert "stream-ok" in "".join(item.data.text or "" for item in results if item.data)

    @pytest.mark.skipif(os.environ.get("RUN_JIUWENBOX_TEST") != "1", reason="Requires running Jiuwenbox sandbox")
    async def test_execute_code_stream_empty_code(self, sys_op: SysOperation):
        empty_code_results = await self.collect_stream_results(sys_op.code().execute_code_stream(code=""))
        assert len(empty_code_results) == 1
        empty_res = empty_code_results[0]
        assert empty_res.code == StatusCode.SYS_OPERATION_CODE_EXECUTION_ERROR.code
        assert "code can not be empty" in empty_res.message
        assert empty_res.data.exit_code != 0

        blank_code_results = await self.collect_stream_results(sys_op.code().execute_code_stream(code="   \n\t"))
        assert len(blank_code_results) == 1
        assert "code can not be empty" in blank_code_results[0].message

    @pytest.mark.skipif(os.environ.get("RUN_JIUWENBOX_TEST") != "1", reason="Requires running Jiuwenbox sandbox")
    async def test_execute_code_stream_unsupported_language(self, sys_op: SysOperation):
        unsupported_results = await self.collect_stream_results(
            sys_op.code().execute_code_stream(code="print(1)", language="java")
        )
        assert len(unsupported_results) == 1
        unsup_res = unsupported_results[0]
        assert unsup_res.code == StatusCode.SYS_OPERATION_CODE_EXECUTION_ERROR.code
        assert "java is not supported" in unsup_res.message
        assert unsup_res.data.exit_code != 0

    @pytest.mark.skipif(os.environ.get("RUN_JIUWENBOX_TEST") != "1", reason="Requires running Jiuwenbox sandbox")
    async def test_execute_code_stream_python_normal(self, sys_op: SysOperation):
        python_code = """
print("hello python")
print("stream test for python")
        """
        results = await self.collect_stream_results(
            sys_op.code().execute_code_stream(code=python_code, language="python", timeout=10)
        )
        assert len(results) >= 2
        assert any(res.data.type == StreamEventType.STDOUT.value and "hello python" in res.data.text for res in results)
        assert any(
            res.data.type == StreamEventType.STDOUT.value and "stream test for python" in res.data.text
            for res in results
        )
        executed_res = results[-1]
        assert executed_res.message == "Code executed successfully"
        assert executed_res.data.exit_code == 0

    @pytest.mark.skipif(os.environ.get("RUN_JIUWENBOX_TEST") != "1", reason="Requires running Jiuwenbox sandbox")
    async def test_execute_code_stream_python_stderr(self, sys_op: SysOperation):
        error_python_code = "print(undefined_variable)"
        results = await self.collect_stream_results(
            sys_op.code().execute_code_stream(code=error_python_code, language="python", timeout=10)
        )
        assert len(results) >= 1
        has_stderr_error = any(
            res.data.type == StreamEventType.STDERR.value or (
                res.data.exit_code is not None and res.data.exit_code != 0
            )
            for res in results
        )
        assert has_stderr_error
        assert results[-1].data.exit_code != 0

    @pytest.mark.skipif(os.environ.get("RUN_JIUWENBOX_TEST") != "1", reason="Requires running Jiuwenbox sandbox")
    async def test_execute_code_stream_javascript_normal(self, sys_op: SysOperation):
        if not await self._sandbox_has_node(sys_op):
            pytest.skip("Node.js not found in jiuwenbox sandbox")

        js_code = """
console.log("hello javascript");
console.log("stream test for js");
        """
        results = await self.collect_stream_results(
            sys_op.code().execute_code_stream(code=js_code, language="javascript", timeout=10)
        )
        assert len(results) >= 2
        assert any(
            res.data.type == StreamEventType.STDOUT.value and "hello javascript" in res.data.text
            for res in results
        )
        assert any(
            res.data.type == StreamEventType.STDOUT.value and "stream test for js" in res.data.text
            for res in results
        )
        assert results[-1].data.exit_code == 0

    @pytest.mark.skipif(os.environ.get("RUN_JIUWENBOX_TEST") != "1", reason="Requires running Jiuwenbox sandbox")
    async def test_execute_code_stream_custom_options(self, sys_op: SysOperation):
        python_code = "print('a'*2048)"
        results = await self.collect_stream_results(
            sys_op.code().execute_code_stream(
                code=python_code,
                language="python",
                options={"chunk_size": 512, "encoding": "utf-8"},
                timeout=10,
            )
        )
        assert len(results) >= 2
        total_a_count = sum(res.data.text.count("a") for res in results if res.data.text)
        assert total_a_count == 2048

    @pytest.mark.skipif(os.environ.get("RUN_JIUWENBOX_TEST") != "1", reason="Requires running Jiuwenbox sandbox")
    async def test_execute_code_stream_custom_environment(self, sys_op: SysOperation):
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
                timeout=10,
            )
        )
        stdout_text = "".join([res.data.text for res in results if res.data.text])
        assert "python_test" in stdout_text
        assert "123456" in stdout_text

    @pytest.mark.skipif(os.environ.get("RUN_JIUWENBOX_TEST") != "1", reason="Requires running Jiuwenbox sandbox")
    async def test_execute_code_stream_timeout(self, sys_op: SysOperation):
        timeout_code = "while True: pass"
        results = await self.collect_stream_results(
            sys_op.code().execute_code_stream(code=timeout_code, language="python", timeout=2)
        )
        assert len(results) >= 1
        assert any("timeout" in res.message.lower() for res in results)

    @pytest.mark.skipif(os.environ.get("RUN_JIUWENBOX_TEST") != "1", reason="Requires running Jiuwenbox sandbox")
    async def test_execute_code_stream_default_params(self, sys_op: SysOperation):
        default_code = "print('default parameter test success')"
        results = await self.collect_stream_results(sys_op.code().execute_code_stream(code=default_code))
        assert len(results) >= 2
        stdout_text = "".join([res.data.text for res in results if res.data.text])
        assert "default parameter test success" in stdout_text
        assert results[-1].message == "Code executed successfully"
        assert results[-1].data.exit_code == 0

    @pytest.mark.skipif(os.environ.get("RUN_JIUWENBOX_TEST") != "1", reason="Requires running Jiuwenbox sandbox")
    async def test_fs_write_read_list_search_and_transfer(self, sys_op: SysOperation, tmp_path: Path):
        write = await sys_op.fs().write_file(
            "/tmp/adapter/hello.txt",
            "hello-adapter",
            prepend_newline=False,
        )
        assert write.code == StatusCode.SUCCESS.code

        read = await sys_op.fs().read_file("/tmp/adapter/hello.txt")
        assert read.code == StatusCode.SUCCESS.code
        assert read.data.content == "hello-adapter"

        await sys_op.fs().write_file("/tmp/adapter/sub/keep.py", "print(1)", prepend_newline=False)
        await sys_op.fs().write_file("/tmp/adapter/sub/drop.log", "drop", prepend_newline=False)

        files = await sys_op.fs().list_files("/tmp/adapter", recursive=True)
        assert files.code == StatusCode.SUCCESS.code
        assert {"hello.txt", "keep.py", "drop.log"}.issubset(
            {item.name for item in files.data.list_items}
        )

        dirs = await sys_op.fs().list_directories("/tmp/adapter")
        assert dirs.code == StatusCode.SUCCESS.code
        assert "sub" in {item.name for item in dirs.data.list_items}

        search = await sys_op.fs().search_files("/tmp/adapter", "*.py")
        assert search.code == StatusCode.SUCCESS.code
        assert [item.name for item in search.data.matching_files] == ["keep.py"]

        local_upload = tmp_path / "upload.txt"
        local_upload.write_text("uploaded")
        upload = await sys_op.fs().upload_file(str(local_upload), "/tmp/adapter/upload.txt", overwrite=True)
        assert upload.code == StatusCode.SUCCESS.code

        local_download = tmp_path / "download.txt"
        download = await sys_op.fs().download_file(
            "/tmp/adapter/upload.txt",
            str(local_download),
            overwrite=True,
        )
        assert download.code == StatusCode.SUCCESS.code
        assert local_download.read_text() == "uploaded"
