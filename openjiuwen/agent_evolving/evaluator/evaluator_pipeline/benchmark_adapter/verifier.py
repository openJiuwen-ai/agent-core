import re
from typing import Any

from openjiuwen.core.common.logging import logger


class Verifier:
    """Runs tests inside container"""

    def __init__(self, config):
        self.config = config

    async def run_tests(self, container) -> dict[str, Any]:
        logger.info("Running tests...")

        workspace = self.config.workspace_dir
        test_cmd = f"cd {workspace} && /opt/venv/bin/python3 -m pytest tests/ -v --tb=short 2>&1 || true"

        result = await container.exec_in_container(
            test_cmd,
            timeout=300,
            workdir=workspace
        )

        output = result.get("stdout", "") + result.get("stderr", "")

        passed = "passed" in output.lower() or "PASSED" in output
        pass_rate = self._calculate_pass_rate(output)
        failed_tests = self._extract_failed_tests(output)

        return {
            "passed": passed and pass_rate >= 1.0,
            "pass_rate": pass_rate,
            "output": output,
            "returncode": result.get("returncode", -1),
            "failed_tests": failed_tests
        }

    @staticmethod
    def _calculate_pass_rate(output: str) -> float:
        passed_match = re.search(r"(\d+)\s+passed", output)
        failed_match = re.search(r"(\d+)\s+failed", output)
        error_match = re.search(r"(\d+)\s+error", output)

        passed = int(passed_match.group(1)) if passed_match else 0
        failed = int(failed_match.group(1)) if failed_match else 0
        errors = int(error_match.group(1)) if error_match else 0

        total = passed + failed + errors
        if total == 0:
            return 0.0

        return passed / total

    @staticmethod
    def _extract_failed_tests(output: str) -> list[str]:
        failed_tests = []

        failed_pattern = re.compile(r"FAILED\s+(.+?)\s+-")
        for match in failed_pattern.finditer(output):
            test_name = match.group(1).strip()
            if test_name not in failed_tests:
                failed_tests.append(test_name)

        error_pattern = re.compile(r"ERROR\s+(.+?)\s+-")
        for match in error_pattern.finditer(output):
            test_name = match.group(1).strip()
            if test_name not in failed_tests:
                failed_tests.append(test_name)

        return failed_tests
