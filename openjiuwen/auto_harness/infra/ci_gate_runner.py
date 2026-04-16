# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""CI 门控运行器 — 执行 lint / test / type-check 并解析结果。

orchestrator 基础设施，不继承 Tool。
"""

from __future__ import annotations

import asyncio
import logging
import os
import shlex
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List

import yaml

logger = logging.getLogger(__name__)

_DEFAULT_YAML = str(
    Path(__file__).resolve().parent.parent
    / "resources" / "ci_gate.yaml"
)


class CIGateRunner:
    """执行 CI 门控检查并返回结构化结果。

    Args:
        workspace: make 命令的工作目录。
        config_path: ci_gate.yaml 路径，为空则用默认。
    """

    def __init__(
        self,
        workspace: str,
        config_path: str = "",
        python_executable: str = "",
        install_command: str = "",
    ) -> None:
        self._workspace = workspace
        self._python_executable = python_executable
        self._install_command = install_command.strip()
        self._prepared = False
        self._gates = self._load_gates(
            config_path or _DEFAULT_YAML
        )

    @staticmethod
    def _load_gates(path: str) -> List[Dict[str, Any]]:
        """从 YAML 加载门控定义列表。"""
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
            return data.get("ci_gates", [])
        except Exception:
            logger.warning(
                "Failed to load ci_gate.yaml: %s", path
            )
            return []

    def _match_gates(
        self, action: str
    ) -> List[Dict[str, Any]]:
        """根据 action 筛选要执行的门控。"""
        if action == "all":
            return list(self._gates)
        name_map = {"check": "lint"}
        target = name_map.get(action, action)
        return [
            g for g in self._gates if g["name"] == target
        ]

    def _resolve_python_executable(self) -> str:
        """Resolve the Python executable for Python-backed CI gates."""
        candidates: list[str] = []
        if self._python_executable:
            candidates.append(self._python_executable)
        if self._workspace:
            candidates.append(
                str(
                    Path(self._workspace)
                    / ".venv"
                    / "bin"
                    / "python"
                )
            )
        candidates.append(sys.executable)

        for candidate in candidates:
            if candidate and Path(candidate).is_file():
                return candidate

        return (
            shutil.which("python3")
            or shutil.which("python")
            or "python"
        )

    def _command_env(self) -> dict[str, str]:
        """Build shell env that points tools at the chosen Python env."""
        env = {**os.environ, "CI": "1"}
        python_executable = self._resolve_python_executable()
        python_path = Path(python_executable)
        env["AUTO_HARNESS_PYTHON"] = python_executable
        if python_path.name.startswith("python"):
            bin_dir = str(python_path.parent)
            env["VIRTUAL_ENV"] = str(
                python_path.parent.parent
            )
            existing_path = env.get("PATH", "")
            env["PATH"] = (
                f"{bin_dir}:{existing_path}"
                if existing_path
                else bin_dir
            )
        return env

    def _normalize_command(self, cmd: str) -> str:
        """为已知的不可靠门控命令转换为更直接的执行命令。"""
        stripped = cmd.strip()
        python_executable = shlex.quote(
            self._resolve_python_executable()
        )
        if not stripped.startswith("make "):
            if stripped.startswith("python -m "):
                return stripped.replace(
                    "python -m ",
                    f"{python_executable} -m ",
                    1,
                )
            shell_make = " make "
            if shell_make in f" {stripped}":
                make_segment = stripped[stripped.index("make "):]
                normalized_make = self._normalize_command(
                    make_segment
                )
                if normalized_make != make_segment:
                    prefix = stripped[: stripped.index("make ")]
                    return f"{prefix}{normalized_make}".strip()
            return cmd
        try:
            parts = shlex.split(stripped)
        except ValueError:
            return cmd
        if not parts or parts[0] != "make":
            return cmd
        if "test" not in parts[1:]:
            return cmd
        target_index = parts.index("test")
        assignments = parts[target_index + 1 :]
        if any("=" not in item for item in assignments):
            return cmd
        env_map = {
            key: value
            for key, value in (
                item.split("=", 1) for item in assignments
            )
        }
        testflags = env_map.get("TESTFLAGS", "").strip()
        return (
            f"{python_executable} -m pytest {testflags}"
        ).strip()

    async def _ensure_environment(self) -> None:
        """Run the optional install command once before gates execute."""
        if self._prepared:
            return
        if not self._install_command:
            self._prepared = True
            return

        proc = await asyncio.create_subprocess_exec(
            "bash",
            "-c",
            self._install_command,
            cwd=self._workspace,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=self._command_env(),
        )
        stdout, _ = await proc.communicate()
        output = stdout.decode("utf-8", errors="replace")
        if proc.returncode != 0:
            raise RuntimeError(
                "CI gate install command failed: "
                f"{output.strip()[-1000:]}"
            )
        self._prepared = True

    @staticmethod
    def _sanitize_failure_output(output: str) -> str:
        """仅保留 pytest 的 FAILURES 和 short test summary info 区块。"""
        if not output.strip():
            return ""

        lines = output.splitlines()
        headers = {
            "failures": "failures",
            "short test summary info": "short test summary info",
        }
        current_section = ""
        collected: dict[str, list[str]] = {
            "failures": [],
            "short test summary info": [],
        }

        for line in lines:
            stripped = line.strip()
            if stripped.startswith("=") and stripped.endswith("="):
                normalized = stripped.strip("=").strip().lower()
                current_section = headers.get(normalized, "")
                if current_section:
                    collected[current_section].append(line)
                continue
            if current_section:
                collected[current_section].append(line)

        sections = [
            "\n".join(collected["failures"]).strip(),
            "\n".join(collected["short test summary info"]).strip(),
        ]
        sanitized = "\n\n".join(
            section for section in sections if section
        ).strip()
        return sanitized or output.strip()

    async def _run_gate(
        self, gate: Dict[str, Any]
    ) -> Dict[str, Any]:
        """执行单个门控命令并返回结果。"""
        raw_cmd = gate.get("command", "")
        cmd = self._normalize_command(raw_cmd)
        name = gate.get("name", "unknown")
        logger.info(
            "Running CI gate '%s': %s", name, cmd
        )
        try:
            await self._ensure_environment()
            proc = await asyncio.create_subprocess_exec(
                "bash", "-c", cmd,
                cwd=self._workspace,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=self._command_env(),
            )
            stdout, _ = await proc.communicate()
            output = stdout.decode(
                "utf-8", errors="replace"
            )
            output = self._sanitize_failure_output(output)
            passed = proc.returncode == 0
        except Exception as exc:
            output = str(exc)
            passed = False
        return {
            "name": name,
            "passed": passed,
            "output": output[-4000:],
        }

    async def run(
        self, action: str = "all"
    ) -> Dict[str, Any]:
        """执行 CI 门控检查。

        Args:
            action: 要执行的门控类型。

        Returns:
            结构化结果字典，含 passed / gates / errors。
        """
        action = action.strip()
        gates = self._match_gates(action)
        if not gates:
            return {
                "passed": False,
                "gates": [],
                "errors": (
                    f"No gate matched action={action}"
                ),
            }

        results: List[Dict[str, Any]] = []
        for gate in gates:
            results.append(await self._run_gate(gate))

        all_passed = all(r["passed"] for r in results)
        failed = [
            gate for gate in results
            if not gate.get("passed", False)
        ]
        errors = "\n\n".join(
            (
                f"[{gate.get('name', 'unknown')}]\n"
                f"{gate.get('output', '').strip()}"
            ).strip()
            for gate in failed
            if gate.get("output", "").strip()
        )
        return {
            "passed": all_passed,
            "gates": results,
            "errors": errors,
        }
