# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""CI 门控运行器 — 执行 lint / test / type-check 并解析结果。

orchestrator 基础设施，不继承 Tool。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

import yaml

logger = logging.getLogger(__name__)


def decode_stdout(stdout: bytes) -> str:
    """Decode subprocess stdout with cross-platform encoding handling."""
    encodings: list[str] = ["utf-8"]
    if sys.platform == "win32":
        # Windows fallback: GBK/CP936 for native console apps
        encodings.extend([
            sys.stdout.encoding or "gbk",
            "gbk",
            "cp936",
        ])
    encodings.append("latin-1")  # never fails
    for encoding in encodings:
        try:
            return stdout.decode(encoding)
        except UnicodeDecodeError:
            continue
    # Unreachable: latin-1 always succeeds
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

# Regex for unified diff hunk header: @@ -old_start[,old_count] +new_start[,new_count] @@
_HUNK_RE = re.compile(
    r"^@@\s*-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s*@@"
)

# Regex for git diff file header
_DIFF_FILE_RE = re.compile(r"^--- (?:a/)?(.+)$|^\+\+\+ (?:b/)?(.+)$")


def _parse_unified_diff_hunks(
    diff_text: str,
) -> dict[str, set[int]]:
    """Parse unified diff text into {filepath: set(changed_new_side_lines)}.

    Only collects new-side (after change) line numbers, which represent
    the lines that now exist in the working tree after the modification.
    These are the lines the agent should be responsible for.
    """
    result: dict[str, set[int]] = {}
    current_file: str | None = None

    for line in diff_text.splitlines():
        # Detect file path from diff headers
        if line.startswith("--- ") or line.startswith("+++ "):
            m = _DIFF_FILE_RE.match(line)
            if m:
                path = m.group(1) or m.group(2) or ""
                # Skip /dev/null for new/deleted files
                if path and path != "/dev/null":
                    # +++ line sets the current file for subsequent hunks
                    if line.startswith("+++ "):
                        current_file = path
                        if current_file not in result:
                            result[current_file] = set()
            continue

        # Parse hunk headers
        m = _HUNK_RE.match(line)
        if m:
            new_start = int(m.group(3))
            new_count_str = m.group(4)
            # If count is omitted, it defaults to 1
            new_count = int(new_count_str) if new_count_str else 1
            # Add lines from new_start to new_start + new_count - 1
            if current_file and new_count > 0:
                for ln in range(new_start, new_start + new_count):
                    result[current_file].add(ln)
            elif current_file and new_count == 0:
                # Pure deletion hunk — no new-side lines to add
                pass
            continue

    return result


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
                check=False,
            )
            if result.returncode != 0:
                stderr_text = decode_stdout(result.stderr)
                logger.warning(
                    "Failed to get changed files: %s",
                    stderr_text.strip()[-500:] or "unknown error"
                )
                return []

            stdout_text = decode_stdout(result.stdout)
            files = [
                f.strip()
                for f in stdout_text.splitlines()
                if f.strip() and (f.endswith(".py") or f.endswith(".pyi"))
            ]
            return files
        except Exception as e:
            logger.warning("Error getting changed files: %s", e)
            return []

    @staticmethod
    def _extract_commits(command: str) -> str:
        """Parse COMMITS=<value> from a make-style command string."""
        match = re.search(r"COMMITS=(\d+)", command)
        return match.group(1) if match else "0"

    def _get_diff_line_ranges(
        self, commits: str
    ) -> dict[str, set[int]]:
        """Return {filepath: set(changed_line_numbers)} from git diff.

        Uses zero-context unified diff (-U0) so only genuinely
        changed lines appear in the ranges — no surrounding
        context lines that might contain pre-existing errors.

        Args:
            commits: Number of commits to diff, or "0" for staged.

        Returns:
            Mapping from repo-relative file path to set of
            line numbers that were added/modified.
        """
        try:
            commits_val = int(commits)
            if commits_val > 0:
                diff_option = f"HEAD~{commits_val}.."
            else:
                diff_option = "--cached"

            result = subprocess.run(
                ["git", "diff", "-U0", diff_option, "--diff-filter=ACMR"],
                cwd=self._workspace,
                capture_output=True,
                check=False,
            )
            if result.returncode != 0:
                return {}

            diff_text = decode_stdout(result.stdout)
            return _parse_unified_diff_hunks(diff_text)
        except Exception as e:
            logger.warning("Error getting diff line ranges: %s", e)
            return {}

    async def _run_tool_command(
        self,
        cmd: str,
    ) -> tuple[int, str]:
        """Run a single tool command and return (returncode, output).

        Uses the same env/shell setup as _run_shell_command but
        returns a simple (code, text) tuple instead of a Process.
        """
        await self._ensure_environment()
        proc = await self._run_shell_command(cmd)
        stdout, _ = await proc.communicate()
        output = decode_stdout(stdout)
        return proc.returncode, output

    def _make_repo_relative(self, filepath: str) -> str:
        """Convert tool-reported file path to repo-relative POSIX.

        Tools may report absolute paths. Strip the workspace prefix
        to get repo-relative paths matching git diff output.
        """
        normalized = filepath.replace("\\", "/")
        ws = self._workspace.replace("\\", "/")
        if normalized.startswith(ws):
            rel = normalized[len(ws):]
            # Strip leading slash
            return rel.lstrip("/")
        return normalized

    @staticmethod
    def _filter_ruff_json_by_line_ranges(
        raw_json: str,
        line_ranges: dict[str, set[int]],
        repo_relative_fn: Any = None,
    ) -> tuple[bool, str]:
        """Filter ruff check JSON violations to only those on changed lines.

        Args:
            raw_json: Output from ``ruff check --output-format=json``.
            line_ranges: {repo_relative_path: set(line_numbers)}.
            repo_relative_fn: Callable to convert absolute paths to
                repo-relative. If None, paths are assumed repo-relative.

        Returns:
            (has_in_range_violations, formatted_text_of_in_range_violations)
        """
        try:
            violations = json.loads(raw_json)
        except (json.JSONDecodeError, TypeError):
            # Not JSON — fall back to returning raw text, consider it a failure
            return bool(raw_json.strip()), raw_json

        if not isinstance(violations, list):
            violations = [violations] if violations else []

        in_range: list[dict] = []
        for v in violations:
            filepath = str(v.get("filename", ""))
            line = int(v.get("location", {}).get("row", 0))
            if repo_relative_fn:
                filepath = repo_relative_fn(filepath)
            allowed = line_ranges.get(filepath)
            if allowed is None or line not in allowed:
                continue
            in_range.append(v)

        if not in_range:
            return False, ""

        # Format filtered violations as readable text
        lines: list[str] = []
        for v in in_range:
            code = v.get("code", "")
            msg = v.get("message", "")
            filepath = v.get("filename", "")
            line_num = v.get("location", {}).get("row", "")
            col = v.get("location", {}).get("column", "")
            lines.append(
                f"{filepath}:{line_num}:{col}: {code} {msg}"
            )
        return True, "\n".join(lines)

    @staticmethod
    def _filter_pylint_json_by_line_ranges(
        raw_json: str,
        line_ranges: dict[str, set[int]],
        repo_relative_fn: Any = None,
    ) -> tuple[bool, str]:
        """Filter pylint JSON violations to only those on changed lines."""
        try:
            violations = json.loads(raw_json)
        except (json.JSONDecodeError, TypeError):
            return bool(raw_json.strip()), raw_json

        if not isinstance(violations, list):
            violations = [violations] if violations else []

        in_range: list[dict] = []
        for v in violations:
            filepath = str(v.get("path", ""))
            line = int(v.get("line", 0))
            if repo_relative_fn:
                filepath = repo_relative_fn(filepath)
            allowed = line_ranges.get(filepath)
            if allowed is None or line not in allowed:
                continue
            in_range.append(v)

        if not in_range:
            return False, ""

        lines: list[str] = []
        for v in in_range:
            symbol = v.get("symbol", "")
            msg = v.get("message", "")
            filepath = v.get("path", "")
            line_num = v.get("line", "")
            msg_id = v.get("message-id", "")
            lines.append(
                f"{filepath}:{line_num}: [{msg_id}] {symbol}: {msg}"
            )
        return True, "\n".join(lines)

    @staticmethod
    def _filter_codespell_by_line_ranges(
        raw_output: str,
        line_ranges: dict[str, set[int]],
        repo_relative_fn: Any = None,
    ) -> tuple[bool, str]:
        """Filter codespell text output to only misspellings on changed lines.

        codespell output format: ``filepath:line_num: word  ==>  suggestion``
        """
        # Pattern: filename:line: info (codespell uses no column prefix)
        pattern = re.compile(r"^(\S+):(\d+):.*$")
        in_range: list[str] = []
        for line in raw_output.splitlines():
            m = pattern.match(line)
            if not m:
                continue
            filepath, line_num_str = m.group(1), m.group(2)
            line_num = int(line_num_str)
            if repo_relative_fn:
                filepath = repo_relative_fn(filepath)
            allowed = line_ranges.get(filepath)
            if allowed is None or line_num not in allowed:
                continue
            in_range.append(line)

        if not in_range:
            return False, ""
        return True, "\n".join(in_range)

    @staticmethod
    def _filter_mypy_by_line_ranges(
        raw_output: str,
        line_ranges: dict[str, set[int]],
        repo_relative_fn: Any = None,
    ) -> tuple[bool, str]:
        """Filter mypy text output to only errors on changed lines.

        mypy output format: ``filepath:line: error: message [code]``
        """
        # Pattern: filename:line: severity: message
        pattern = re.compile(
            r"^(\S+):(\d+):\s*(error|note|warning):\s+(.*)$"
        )
        in_range: list[str] = []
        for line in raw_output.splitlines():
            m = pattern.match(line)
            if not m:
                # Skip summary lines like "Found N errors"
                continue
            filepath, line_num_str = m.group(1), m.group(2)
            line_num = int(line_num_str)
            if repo_relative_fn:
                filepath = repo_relative_fn(filepath)
            allowed = line_ranges.get(filepath)
            if allowed is None or line_num not in allowed:
                continue
            in_range.append(line)

        if not in_range:
            return False, ""
        return True, "\n".join(in_range)

    @staticmethod
    def _filter_format_diff_by_line_ranges(
        raw_diff: str,
        line_ranges: dict[str, set[int]],
        repo_relative_fn: Any = None,
    ) -> tuple[bool, str]:
        """Filter ruff format --check --diff output to changed-line formatting issues.

        The diff output shows which lines would be reformatted. Parse the
        unified diff to find affected lines, then check if any of those
        lines are in the changed-line ranges.
        """
        # Parse the diff hunks to find files + lines that would change
        format_hunks = _parse_unified_diff_hunks(raw_diff)
        in_range_lines: list[str] = []

        for filepath, fmt_lines in format_hunks.items():
            norm_path = filepath
            if repo_relative_fn:
                norm_path = repo_relative_fn(filepath)
            allowed = line_ranges.get(norm_path)
            if allowed is None:
                continue
            overlap = allowed.intersection(fmt_lines)
            if overlap:
                # Some format changes hit changed lines — report it
                in_range_lines.append(
                    f"{filepath}: formatting differs on changed lines "
                    f"{sorted(overlap)}"
                )

        if not in_range_lines:
            return False, ""
        return True, "\n".join(in_range_lines)

    async def _run_check_gate(
        self, commits: str
    ) -> dict[str, Any]:
        """Run the lint/check gate with line-range filtering.

        Only flags violations on lines that were actually changed,
        not pre-existing errors in unchanged lines.
        """
        line_ranges = self._get_diff_line_ranges(commits)
        changed_files = self._get_changed_files(commits)

        if not changed_files:
            return {
                "name": "lint",
                "passed": True,
                "output": "No files to check",
            }

        # Fallback: if line_ranges is empty (diff parse failed),
        # use changed_files as whole-file scope — every line is
        # considered "in range" for those files.
        if not line_ranges:
            logger.warning(
                "Diff line ranges empty for COMMITS=%s; "
                "falling back to whole-file scope",
                commits,
            )
            line_ranges = {
                f: set(range(1, 10**6))
                for f in changed_files
            }

        python_executable = _quote_path(
            self._resolve_python_executable(),
            convert_slashes=False,
        )
        quoted_files = " ".join(
            _quote_path(f) for f in changed_files
        )

        all_violations: list[str] = []
        any_failed = False
        repo_rel_fn = self._make_repo_relative

        # Step 1: ruff check (lint violations on changed lines)
        ruff_cmd = (
            f"{python_executable} -m ruff check "
            f"--output-format=json {quoted_files}"
        )
        code, ruff_output = await self._run_tool_command(ruff_cmd)
        has_ruff, ruff_text = self._filter_ruff_json_by_line_ranges(
            ruff_output, line_ranges, repo_rel_fn,
        )
        if has_ruff:
            any_failed = True
            all_violations.append(f"[ruff] {ruff_text}")

        # Step 2: ruff format --check (format issues on changed lines)
        fmt_cmd = (
            f"{python_executable} -m ruff format "
            f"--check --diff {quoted_files}"
        )
        code, fmt_output = await self._run_tool_command(fmt_cmd)
        has_fmt, fmt_text = self._filter_format_diff_by_line_ranges(
            fmt_output, line_ranges, repo_rel_fn,
        )
        if has_fmt:
            any_failed = True
            all_violations.append(f"[format] {fmt_text}")

        # Step 3: codespell (misspellings on changed lines)
        # codespell may not be a Python module; invoke directly
        # (it's installed as a CLI tool, available via PATH)
        cs_cmd = f"codespell {quoted_files}"
        code, cs_output = await self._run_tool_command(cs_cmd)
        has_cs, cs_text = self._filter_codespell_by_line_ranges(
            cs_output, line_ranges, repo_rel_fn,
        )
        if has_cs:
            any_failed = True
            all_violations.append(f"[codespell] {cs_text}")

        # Step 4: pylint (violations on changed lines)
        pylint_cmd = (
            f"{python_executable} -m pylint "
            f"--output-format=json {quoted_files}"
        )
        code, pylint_output = await self._run_tool_command(pylint_cmd)
        has_pylint, pylint_text = self._filter_pylint_json_by_line_ranges(
            pylint_output, line_ranges, repo_rel_fn,
        )
        if has_pylint:
            any_failed = True
            all_violations.append(f"[pylint] {pylint_text}")

        combined = "\n\n".join(all_violations)
        return {
            "name": "lint",
            "passed": not any_failed,
            "output": combined[-4000:] if combined else "All checks passed (scope: changed lines only)",
        }

    async def _run_type_check_gate(
        self, commits: str
    ) -> dict[str, Any]:
        """Run the type-check gate with line-range filtering."""
        line_ranges = self._get_diff_line_ranges(commits)
        changed_files = self._get_changed_files(commits)

        if not changed_files:
            return {
                "name": "type-check",
                "passed": True,
                "output": "No files to type-check",
            }

        if not line_ranges:
            logger.warning(
                "Diff line ranges empty for COMMITS=%s; "
                "falling back to whole-file scope",
                commits,
            )
            line_ranges = {
                f: set(range(1, 10**6))
                for f in changed_files
            }

        python_executable = _quote_path(
            self._resolve_python_executable(),
            convert_slashes=False,
        )
        quoted_files = " ".join(
            _quote_path(f) for f in changed_files
        )

        mypy_cmd = (
            f"{python_executable} -m mypy "
            f"--show-error-codes --show-column-numbers "
            f"{quoted_files}"
        )
        code, mypy_output = await self._run_tool_command(mypy_cmd)
        has_errors, error_text = self._filter_mypy_by_line_ranges(
            mypy_output, line_ranges, self._make_repo_relative,
        )

        return {
            "name": "type-check",
            "passed": not has_errors,
            "output": error_text[-4000:] if error_text else "Type check passed (scope: changed lines only)",
        }

    async def _ensure_environment(self) -> None:
        """Run the optional install command once before gates execute."""
        if self._prepared:
            return
        if not self._install_command:
            self._prepared = True
            return

        # Pre-install uv if command requires it but uv is not available
        if "uv" in self._install_command:
            await self._ensure_uv_available()

        proc = await self._run_shell_command(self._install_command)
        stdout, _ = await proc.communicate()
        output = decode_stdout(stdout)
        if proc.returncode != 0:
            raise RuntimeError(
                "CI gate install command failed: "
                f"{output.strip()[-1000:]}"
            )
        self._prepared = True

    async def _ensure_uv_available(self) -> None:
        """Ensure uv is installed before running uv-based commands."""
        env = self._command_env()

        # Check if uv is already available
        try:
            proc = await asyncio.create_subprocess_exec(
                "uv",
                "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            await proc.communicate()
            if proc.returncode == 0:
                return  # uv is available
        except FileNotFoundError:
            pass  # uv not found, need to install

        # Install uv via pip
        logger.info("[CIGateRunner] uv not found, installing via pip")
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "pip",
            "install",
            "uv",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            error = stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(
                f"Failed to install uv: {error[:500]}"
            )
        logger.info("[CIGateRunner] uv installed successfully")

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
        """执行单个门控命令并返回结果。

        For lint/check and type-check gates, dispatches to
        line-range-filtered multi-step methods. Other gates
        fall through to the original shell execution.
        """
        raw_cmd = gate.get("command", "")
        name = gate.get("name", "unknown")

        # Dispatch to line-range-filtered multi-step gates
        if name in ("lint", "check"):
            commits = self._extract_commits(raw_cmd)
            logger.info(
                "Running CI gate '%s' (line-range filtered, COMMITS=%s)",
                name, commits,
            )
            return await self._run_check_gate(commits)

        if name == "type-check":
            commits = self._extract_commits(raw_cmd)
            logger.info(
                "Running CI gate '%s' (line-range filtered, COMMITS=%s)",
                name, commits,
            )
            return await self._run_type_check_gate(commits)

        # Original shell execution for other gates (test, etc.)
        cmd = self._normalize_command(raw_cmd)
        logger.info(
            "Running CI gate '%s': %s", name, cmd
        )
        fail_marker = "__CI_CHECK_FAIL__"
        try:
            await self._ensure_environment()
            proc = await self._run_shell_command(cmd)
            stdout, _ = await proc.communicate()
            output = decode_stdout(stdout)
            output = self._sanitize_failure_output(output)
            passed = (
                proc.returncode == 0
                and fail_marker not in output
            )
            output = output.replace(fail_marker, "").strip()
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
