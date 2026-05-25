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
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

import yaml

logger = logging.getLogger(__name__)


def _decode_stdout(stdout: bytes) -> str:
    """Decode subprocess stdout with cross-platform encoding handling.

    Windows consoles often use GBK (cp936) while Unix uses UTF-8.
    This function tries multiple encodings to handle both cases.
    """
    # Build encoding priority list based on platform
    if sys.platform == "win32":
        # Windows: prioritize GBK/CP936 for console output, then UTF-8
        encodings = [
            sys.stdout.encoding or "gbk",
            "gbk",
            "cp936",
            "utf-8",
            "latin-1",
        ]
    else:
        # Unix/Linux: prioritize UTF-8
        encodings = [
            "utf-8",
            sys.stdout.encoding or "utf-8",
            "latin-1",
        ]
    for encoding in encodings:
        try:
            return stdout.decode(encoding)
        except UnicodeDecodeError:
            continue
    # Fallback: decode with replacement chars
    return stdout.decode("utf-8", errors="replace")


def _quote_path(path: str, convert_slashes: bool = True) -> str:
    """Quote path for shell. Converts forward slashes to backslashes on Windows
    for relative paths to avoid cmd.exe misinterpreting "/c" as its /c flag.

    Args:
        path: File path to quote.
        convert_slashes: False for executable paths that may be Unix-style.
    """
    if sys.platform == "win32" and convert_slashes:
        # Only convert relative paths; keep Windows/Unix absolute paths unchanged
        is_relative = not (
            (len(path) >= 2 and path[1] == ":" and path[0].isalpha())
            or path.startswith("\\\\")
            or path.startswith("/")
        )
        if is_relative:
            path = path.replace("/", "\\")

    # Platform-specific special chars. Windows: \ is path separator (not special).
    # Unix: \ is escape char (needs quoting).
    if sys.platform == "win32":
        special_chars = ' \t\n\r"&|;<>()$`!*?[]{}'
    else:
        special_chars = ' \t\n\r"\'&|;<>()$`\\!*?[]{}'

    for char in path:
        if char in special_chars:
            break
    else:
        return path  # No special chars, return unquoted

    if sys.platform == "win32":
        return '"' + path.replace('"', '""') + '"'
    else:
        return shlex.quote(path)

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
        # Cache make availability check
        self._make_available = shutil.which("make") is not None

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
            # Cross-platform venv python path
            if sys.platform == "win32":
                candidates.append(
                    str(
                        Path(self._workspace)
                        / ".venv"
                        / "Scripts"
                        / "python.exe"
                    )
                )
            else:
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

    def set_workspace(self, workspace: str) -> None:
        """Update command workspace."""
        self._workspace = workspace

    async def _run_shell_command(
        self,
        cmd: str,
        cwd: str | None = None,
    ) -> asyncio.subprocess.Process:
        """Create subprocess for shell command with cross-platform support.

        On Windows, uses cmd.exe. On Unix, uses bash for better compatibility
        with shell scripts and complex command chains.

        Args:
            cmd: Shell command string to execute.
            cwd: Working directory for the subprocess.

        Returns:
            asyncio.subprocess.Process instance.
        """
        env = self._command_env()
        if sys.platform == "win32":
            # Windows: use cmd.exe with /c flag
            # cmd.exe handles Windows paths and commands correctly
            return await asyncio.create_subprocess_exec(
                "cmd.exe",
                "/c",
                cmd,
                cwd=cwd or self._workspace,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
            )
        else:
            # Unix/Linux: use bash for better shell compatibility
            return await asyncio.create_subprocess_exec(
                "bash",
                "-c",
                cmd,
                cwd=cwd or self._workspace,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
            )

    def _command_env(self) -> dict[str, str]:
        """Build shell env that points tools at the chosen Python env.

        Note: We intentionally unset VIRTUAL_ENV here. In auto-harness
        scenarios, the host environment may have VIRTUAL_ENV set (e.g.,
        jiuwenclaw's .venv), while install_command like "uv sync --active"
        should operate on the workspace's .venv. Removing VIRTUAL_ENV
        ensures uv determines the target environment based on cwd/project
        location, not the inherited host environment.
        """
        env = {**os.environ, "CI": "1"}
        # Remove inherited VIRTUAL_ENV to prevent uv --active from
        # targeting the host tool's environment instead of workspace.
        env.pop("VIRTUAL_ENV", None)
        python_executable = self._resolve_python_executable()
        python_path = Path(python_executable)
        env["AUTO_HARNESS_PYTHON"] = python_executable
        if python_path.name.startswith("python"):
            bin_dir = str(python_path.parent)
            # Prepend bin_dir to PATH so tools use the configured Python.
            # Use os.pathsep for cross-platform compatibility (; on Windows, : on Unix)
            existing_path = env.get("PATH", "")
            pathsep = os.pathsep
            env["PATH"] = (
                f"{bin_dir}{pathsep}{existing_path}"
                if existing_path
                else bin_dir
            )
        return env

    def _normalize_command(self, cmd: str) -> str:
        """为已知的不可靠门控命令转换为更直接的执行命令。

        当 make 不可用时（常见于 Windows 环境未配置 Git Bash PATH），
        自动转换为等效的 Python 工具调用，确保跨平台兼容性。
        """
        stripped = cmd.strip()
        # Don't convert slashes for python executable path - it should remain
        # in its native format (Windows: backslashes, Unix: forward slashes)
        python_executable = _quote_path(
            self._resolve_python_executable(),
            convert_slashes=False,
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

        # make command detected - check if make is available
        # Handle case where _make_available wasn't initialized (test scenarios)
        make_available = getattr(self, '_make_available', None)
        if make_available is None:
            make_available = shutil.which("make") is not None

        if make_available:
            # make is available, keep original command
            return cmd

        # make not available, convert to Python tool equivalents
        logger.info("make not available, converting '%s' to Python tool equivalent", stripped)
        try:
            parts = shlex.split(stripped)
        except ValueError:
            return cmd
        if not parts or parts[0] != "make":
            return cmd

        # Parse make target and arguments
        target = parts[1] if len(parts) > 1 else ""
        assignments = [p for p in parts[2:] if "=" in p]
        env_map: dict[str, str] = {}
        for item in assignments:
            key, value = item.split("=", 1)
            env_map[key] = value

        # Handle different make targets
        if target == "test":
            testflags = env_map.get("TESTFLAGS", "").strip()
            return (
                f"{python_executable} -m pytest {testflags}"
            ).strip()

        if target == "type-check":
            commits = env_map.get("COMMITS", "0")
            changed_files = self._get_changed_files(commits)
            if not changed_files:
                return f"{python_executable} -c \"print('No files to type-check')\""
            quoted_files = " ".join(_quote_path(f) for f in changed_files)
            return f"{python_executable} -m mypy {quoted_files}"

        if target == "check":
            commits = env_map.get("COMMITS", "0")
            changed_files = self._get_changed_files(commits)
            if not changed_files:
                return f"{python_executable} -c \"print('No files to check')\""
            quoted_files = " ".join(_quote_path(f) for f in changed_files)
            # Chain checks: ruff format + codespell + ruff lint + pylint
            checks = [
                f"{python_executable} -m ruff check --select I {quoted_files}",
                f"{python_executable} -m ruff format --check {quoted_files}",
                f"{python_executable} -m codespell {quoted_files}",
                f"{python_executable} -m ruff check --show-fixes {quoted_files}",
                f"{python_executable} -m pylint {quoted_files}",
            ]
            chain_sep = " & " if sys.platform == "win32" else " ; "
            return chain_sep.join(checks)

        # Unknown target, return original command (will likely fail but preserves behavior)
        return cmd

    def _get_changed_files(self, commits: str) -> list[str]:
        """Get list of changed Python files for CI checks.

        Args:
            commits: Number of commits to check, or "0" for staged changes.

        Returns:
            List of Python file paths relative to workspace.
        """
        try:
            commits_val = int(commits)
            if commits_val > 0:
                diff_option = f"HEAD~{commits_val}.."
            else:
                diff_option = "--cached"

            result = subprocess.run(
                ["git", "diff", "--name-only", diff_option, "--diff-filter=ACMR"],
                cwd=self._workspace,
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                logger.warning(
                    "Failed to get changed files: %s",
                    result.stderr.strip()[-500:] if result.stderr else "unknown error"
                )
                return []

            files = [
                f.strip()
                for f in result.stdout.splitlines()
                if f.strip() and (f.endswith(".py") or f.endswith(".pyi"))
            ]
            return files
        except Exception as e:
            logger.warning("Error getting changed files: %s", e)
            return []

    async def _ensure_environment(self) -> None:
        """Run the optional install command once before gates execute."""
        if self._prepared:
            return
        if not self._install_command:
            self._prepared = True
            return

        proc = await self._run_shell_command(self._install_command)
        stdout, _ = await proc.communicate()
        output = _decode_stdout(stdout)
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
            proc = await self._run_shell_command(cmd)
            stdout, _ = await proc.communicate()
            output = _decode_stdout(stdout)
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
