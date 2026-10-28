# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Score a SkillsBench task from its own verifier reward.

Modes (``SKILLSBENCH_VERIFIER_MODE`` / ``verifier_mode``):

* ``local`` (default): rewrite ``/root`` paths in the task's pytest files onto
  the agent workdir, run pytest on the host, and write ``reward.txt``.
* ``wsl``: same rewrite as ``local``, but run pytest inside WSL (``wsl.exe``).
  Skips Docker image builds while still using a Linux Python for scoring.
* ``docker``: build the task image, run ``verifier/test.sh`` in a container,
  and read ``/logs/verifier/reward.txt``.

``hard`` / ``soft`` are the integer reward. This module does not call a model.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Literal

import yaml

from openjiuwen.core.common.logging import logger

RewardRunner = Callable[..., subprocess.CompletedProcess[str]]
VerifierMode = Literal["docker", "local", "wsl"]

_ROOT_PATH_RE = re.compile(r"(?<![\w./\\-])/root(?=/|[\"'\\]|$)")
_SKIP_ENV_NAMES = frozenset({"dockerfile", "skills"})
_SYNTAX_DIRECTIVE = re.compile(r"^[ \t]*#\s*syntax\s*=\s*\S+[ \t]*\r?\n")

# Answer keys that must never reach the agent workdir. SkillsBench packages keep
# the expected outputs next to the inputs (``environment/groundtruth/``), and
# copying that directory turns every rollout into an open-book exam: the agent
# reads ``answers.txt`` instead of recovering the values.
_LEAKY_ENV_NAMES = frozenset(
    {
        "groundtruth",
        "ground_truth",
        "ground-truth",
        "answer",
        "answers",
        "answer_key",
        "answerkey",
        "solution",
        "solutions",
        "gold",
        "golden",
        "oracle",
        "expected",
        "expected_output",
        "expected_outputs",
        "verifier",
        "reference_answers",
    }
)
_LEAKY_FILE_RE = re.compile(
    r"(?i)^(?:answers?|answer[_ -]?key|solutions?|ground[_ -]?truth|expected(?:[_ -].*)?|oracle)"
    r"\.(?:txt|md|json|csv|ya?ml)$"
)


def _env_name_set(var: str) -> frozenset[str]:
    raw = os.getenv(var, "")
    return frozenset(part.strip().lower() for part in raw.split(",") if part.strip())


def skipped_environment_names() -> frozenset[str]:
    """Names dropped when seeding the agent workdir from ``environment/``.

    Defaults cover Dockerfile, the ``skills`` tree and answer keys. Extend with
    ``SKILLSBENCH_ENV_SKIP`` (comma separated) and force-keep a colliding name
    with ``SKILLSBENCH_ENV_KEEP`` when a task really needs it as input.
    """
    keep = _env_name_set("SKILLSBENCH_ENV_KEEP")
    extra = _SKIP_ENV_NAMES | _LEAKY_ENV_NAMES | _env_name_set("SKILLSBENCH_ENV_SKIP")
    return frozenset(extra - keep)


def is_leaky_environment_entry(name: str) -> bool:
    """True when *name* is a build file or answer key that stays out of the workdir."""
    lowered = (name or "").strip().lower()
    if not lowered:
        return False
    if lowered in _env_name_set("SKILLSBENCH_ENV_KEEP"):
        return False
    return lowered in skipped_environment_names() or bool(_LEAKY_FILE_RE.match(lowered))


def _env_ignore(_dir: str, names: list[str]) -> set[str]:
    """``shutil.copytree`` hook dropping answer keys at any nesting depth."""
    return {name for name in names if is_leaky_environment_entry(name)}


def scores_from_reward_text(text: str) -> dict[str, Any]:
    """Map verifier reward text onto skill_train ``hard`` / ``soft``.

    Accepts binary tokens (``0`` / ``1``) and decimal rewards such as
    ``1.0000`` or ``0.3500``: ``hard`` is 1 only at a full score, while
    ``soft`` carries the partial value.
    """
    token = ""
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped:
            token = stripped.split()[0]
            break
    try:
        value = float(token)
    except ValueError:
        value = None
    passed = value is not None and value >= 1.0
    if value is not None and 0.0 <= value <= 1.0:
        soft = value
    elif passed:
        soft = 1.0
    else:
        soft = 0.0
    return {
        "hard": 1 if passed else 0,
        "soft": soft,
        "reward": token,
    }


def scores_from_reward_file(path: str | Path) -> dict[str, Any]:
    """Read ``reward.txt``. A missing or unparseable file scores 0."""
    reward_path = Path(path)
    if not reward_path.is_file():
        scores = scores_from_reward_text("")
        scores["fail_reason"] = "reward_missing"
        return scores
    scores = scores_from_reward_text(reward_path.read_text(encoding="utf-8", errors="replace"))
    try:
        float(scores["reward"])
    except ValueError:
        scores["hard"] = 0
        scores["soft"] = 0.0
        scores["fail_reason"] = f"reward_invalid:{scores['reward']}"
        return scores
    scores["fail_reason"] = "" if scores["hard"] else "verifier_failed"
    return scores


# pytest ``-rA`` short summary: ``FAILED test_x.py::test_y[id with spaces] - msg``.
# The verbose per-test lines start with the file path instead, so anchoring on the
# status keeps them out of the count.
_PYTEST_DETAIL_RE = re.compile(r"^(PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)\s+(\S.*?)(?:\s+-\s+.*)?$")
_PYTEST_DURATION_RE = re.compile(r"\bin \d+(?:\.\d+)?s\b")


def _is_pytest_summary_line(line: str) -> bool:
    """True for pytest's closing ``===== 1 failed, 18 passed in 0.49s =====`` line."""
    stripped = line.strip()
    return stripped.startswith("=") and stripped.endswith("=") and bool(_PYTEST_DURATION_RE.search(stripped))


def _counts_from_pytest_text(text: str) -> tuple[int, int] | None:
    """Return ``(passed, total)`` parsed from pytest ``-rA`` output.

    ``None`` unless the run reached pytest's closing summary line: a container
    that dies mid-run (apt/pip hang, timeout) leaves a partial log, and scoring
    that as a fraction would inflate ``soft``.
    """
    lines = (text or "").splitlines()
    if not any(_is_pytest_summary_line(line) for line in lines):
        return None
    outcomes: dict[str, str] = {}
    for line in lines:
        match = _PYTEST_DETAIL_RE.match(line.strip())
        if match is None:
            continue
        node_id = match.group(2).strip()
        # Aggregated skip lines look like ``SKIPPED [2] test_x.py:7: reason``;
        # they are counts, not node ids, and the per-test rows follow separately.
        if node_id.startswith("["):
            continue
        outcomes.setdefault(node_id, match.group(1))
    total = len(outcomes)
    if total <= 0:
        return None
    passed = sum(1 for status in outcomes.values() if status == "PASSED")
    return passed, total


def _counts_from_ctrf(path: str | Path) -> tuple[int, int] | None:
    """Return ``(passed, total)`` from a CTRF report summary."""
    report = Path(path)
    if not report.is_file():
        return None
    try:
        summary = json.loads(report.read_text(encoding="utf-8")).get("results", {}).get("summary", {})
    except (json.JSONDecodeError, AttributeError, TypeError, OSError):
        return None
    if not isinstance(summary, dict):
        return None
    try:
        total = int(summary.get("tests") or 0)
        passed = int(summary.get("passed") or 0)
    except (TypeError, ValueError):
        return None
    if total <= 0:
        return None
    return max(0, min(passed, total)), total


def _apply_counts(
    scores: dict[str, Any],
    counts: tuple[int, int] | None,
    source: str,
) -> dict[str, Any]:
    """Attach ``soft = passed / total`` without touching the binary ``hard``."""
    if not counts:
        return scores
    passed, total = counts
    if total <= 0:
        return scores
    passed = max(0, min(passed, total))
    scores["soft"] = round(passed / total, 4)
    scores["tests_passed"] = passed
    scores["tests_total"] = total
    scores["soft_source"] = source
    return scores


def resolve_verifier_mode(explicit: str | None = None) -> VerifierMode:
    """Resolve ``local`` / ``wsl`` / ``docker`` from an explicit value or the environment.

    Default is ``local`` so agent workspace files and pytest both use the same
    host workdir (``/root`` literals remapped). Use ``wsl`` to score under Linux
    without building task images. Set ``SKILLSBENCH_VERIFIER_MODE=docker`` only
    when container scoring is required.
    """
    raw = (explicit if explicit is not None else os.getenv("SKILLSBENCH_VERIFIER_MODE", "")).strip().lower()
    if not raw:
        return "local"
    if raw not in {"docker", "local", "wsl"}:
        raise ValueError(
            f"SKILLSBENCH_VERIFIER_MODE must be 'docker', 'local', or 'wsl', got {raw!r}"
        )
    return raw  # type: ignore[return-value]


def windows_to_wsl_path(path: str | Path) -> str:
    """Convert a Windows path to the WSL ``/mnt/<drive>/...`` form."""
    # String inputs may already be WSL paths; do not Path()-normalize them on Windows.
    if isinstance(path, str):
        raw = path.strip()
        if raw.startswith("\\\\?\\"):
            raw = raw[4:]
        unix = raw.replace("\\", "/")
        if unix.startswith("/mnt/") or unix == "/mnt":
            return unix.rstrip("/") or "/mnt"
        if unix.startswith("/") and not (len(raw) >= 2 and raw[1] == ":"):
            return unix.rstrip("/") or "/"

    resolved = Path(path).resolve()
    s = os.fsdecode(resolved)
    if s.startswith("\\\\?\\"):
        s = s[4:]
    if len(s) >= 2 and s[1] == ":":
        drive = s[0].lower()
        rest = s[2:].replace("\\", "/").lstrip("/")
        return f"/mnt/{drive}/{rest}" if rest else f"/mnt/{drive}"
    raise ValueError(f"cannot convert path to WSL form: {path!r}")


def _wsl_bin() -> str:
    for name in ("wsl.exe", "wsl"):
        found = shutil.which(name)
        if found:
            return found
    raise FileNotFoundError("wsl executable not found in PATH")


def map_root_paths(text: str, root_dir: str | Path) -> str:
    """Replace absolute ``/root`` path literals with *root_dir* (POSIX form)."""
    mapped = Path(root_dir).resolve().as_posix()
    return _ROOT_PATH_RE.sub(mapped, text)


def copy_environment_inputs(
    task_dir: str | Path,
    work_dir: str | Path,
    *,
    overwrite: bool = True,
) -> list[str]:
    """Copy ``environment/`` inputs (not Dockerfile / skills / answer keys) to the workdir.

    SkillsBench tasks usually ``COPY`` these into ``/root`` in Docker; the host
    agent and the local verifier both need them next to the produced artifacts.

    Answer keys are filtered out: ``environment/groundtruth/`` (with
    ``answers.txt``) and anything matching :data:`_LEAKY_FILE_RE` never reach the
    agent's cwd, otherwise a rollout can pass by reading the expected values.

    When ``overwrite`` is False (local scoring), seed files are merged in without
    deleting agent outputs already present under the workdir.
    """
    env_dir = Path(task_dir) / "environment"
    dest_root = Path(work_dir)
    dest_root.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    if not env_dir.is_dir():
        return copied
    skipped: list[str] = []
    for child in sorted(env_dir.iterdir()):
        if is_leaky_environment_entry(child.name):
            skipped.append(child.name)
            continue
        target = dest_root / child.name
        if child.is_dir():
            if overwrite and target.exists():
                shutil.rmtree(target)
            if not target.exists():
                shutil.copytree(child, target, ignore=_env_ignore)
            else:
                for src_file in child.rglob("*"):
                    if not src_file.is_file():
                        continue
                    relative = src_file.relative_to(child)
                    if any(is_leaky_environment_entry(part) for part in relative.parts):
                        continue
                    dest = target / relative
                    if overwrite or not dest.exists():
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src_file, dest)
        elif overwrite or not target.exists():
            shutil.copy2(child, target)
        else:
            continue
        copied.append(child.name)
    if skipped:
        logger.info(
            "[skillsbench] env seed skipped answer-key entries=%s (%s)",
            len(skipped),
            ",".join(skipped),
        )
    return copied


def _local_import_dirs(work_dir: Path) -> list[Path]:
    """Dirs the local verifier should put on ``PYTHONPATH``.

    Official tests often ``sys.path.insert(0, "/root/workspace")``. Agents may
    still drop modules at the workdir root; include both so scoring matches
    either layout.
    """
    dirs = [work_dir.resolve()]
    nested = work_dir / "workspace"
    if nested.is_dir():
        dirs.append(nested.resolve())
    return dirs


def _pythonpath_env(work_dir: Path, staging: Path | None = None) -> dict[str, str]:
    """Build an env mapping with workdir (+ ``workspace/``) prepended to ``PYTHONPATH``.

    When *staging* is set (local verifier), also prepend the staged verifier
    tree and ``staging/src`` so helpers like ``oracle_helpers`` import cleanly.
    """
    parts: list[str] = []
    if staging is not None:
        staging_resolved = staging.resolve()
        src = staging_resolved / "src"
        if src.is_dir():
            parts.append(str(src))
        parts.append(str(staging_resolved))
    parts.extend(str(path) for path in _local_import_dirs(work_dir))
    existing = os.environ.get("PYTHONPATH", "").strip()
    if existing:
        parts.append(existing)
    return {"PYTHONPATH": os.pathsep.join(parts)}


_SKIP_VERIFIER_NAMES = frozenset({"test.sh", ".DS_Store"})
_TEXT_REWRITE_SUFFIXES = frozenset(
    {".py", ".sh", ".json", ".mjs", ".escript", ".md", ".txt", ".yaml", ".yml", ".toml"}
)


def _rewrite_verifier_text(text: str, *, mapped_root: str) -> str:
    """Rewrite ``/root`` (and plain ``/app``) literals onto the agent workdir."""
    out = _ROOT_PATH_RE.sub(mapped_root, text)
    # Some tasks hard-code Docker ``/app`` instead of ``/root``.
    out = re.sub(r"(?<![\w./\\-])/app(?=/|[\"'\\]|$)", mapped_root, out)
    return out


def _stage_verifier_tree(
    verifier_dir: Path,
    staging: Path,
    *,
    mapped_root: str,
) -> list[Path]:
    """Copy the verifier tree into *staging*, rewriting path literals in text files.

    Docker scoring mounts the whole ``verifier/`` directory. Local / WSL scoring
    previously only copied ``test_*.py`` / ``conftest.py``, which breaks imports
    of siblings such as ``oracle_helpers.py`` or data like ``verification_params.json``.
    """
    staging.mkdir(parents=True, exist_ok=True)
    tests: list[Path] = []
    for source in sorted(verifier_dir.rglob("*")):
        if not source.is_file():
            continue
        rel = source.relative_to(verifier_dir)
        if any(part == "__pycache__" or part.endswith(".pyc") for part in rel.parts):
            continue
        if source.name in _SKIP_VERIFIER_NAMES:
            continue
        target = staging / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.suffix.lower() in _TEXT_REWRITE_SUFFIXES:
            text = source.read_text(encoding="utf-8", errors="replace")
            target.write_text(_rewrite_verifier_text(text, mapped_root=mapped_root), encoding="utf-8")
        else:
            shutil.copy2(source, target)
        if source.parent == verifier_dir and source.name.startswith("test_") and source.suffix == ".py":
            tests.append(target)
    return tests


def dockerfile_workdir(text: str) -> str:
    """Return the last absolute ``WORKDIR`` in a Dockerfile, or ``/root``."""
    workdir = "/root"
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line.upper().startswith("WORKDIR "):
            continue
        value = line.split(None, 1)[1].strip().strip("\"'")
        if value.startswith("/") and "$" not in value:
            workdir = value.rstrip("/") or "/"
    return workdir


def container_agent_sync(dockerfile_text: str) -> str:
    """Copy the agent workspace onto ``/root`` and the image ``WORKDIR``.

    The agent writes task outputs at relative paths. Verifiers then open those
    files under ``/root`` or under the image workdir (for example ``/app``).

    Legacy SkillsBench verifiers hardcode ``/tests`` paths (``--confcutdir=/tests``,
    ``cwd="/tests"``). BenchFlow, the harness they were written for, aliased the
    verifier mount onto ``/tests``; recreate that alias so those verifiers run
    unchanged. The guard keeps any real ``/tests`` content the image ships.
    """
    targets = ["/root"]
    workdir = dockerfile_workdir(dockerfile_text)
    if workdir not in targets:
        targets.append(workdir)
    copies = " && ".join(
        f"mkdir -p {shlex.quote(target)} && cp -a /agent_out/. {shlex.quote(target)}/"
        for target in targets
    )
    alias = "[ -e /tests ] || ln -s /verifier /tests"
    return f"mkdir -p /logs/verifier && {alias} && {copies} && bash /verifier/test.sh"


def verifier_env_from_task_md(task_dir: str | Path) -> dict[str, str]:
    """Read ``verifier.env`` from the ``task.md`` frontmatter, as strings.

    BenchFlow task packages declare verifier environment there — for example
    ``REPO_ID`` for the BugSwarm fix-build tasks. Docker scoring injects the
    pairs into the container so legacy verifiers reading ``os.getenv(...)``
    behave as they did under the original harness.
    """
    task_md = Path(task_dir) / "task.md"
    if not task_md.is_file():
        return {}
    try:
        text = task_md.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    stripped = text.lstrip("\ufeff")
    if not stripped.startswith("---"):
        return {}
    parts = stripped.split("---", 2)
    if len(parts) < 3:
        return {}
    try:
        loaded = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return {}
    verifier = loaded.get("verifier") if isinstance(loaded, dict) else None
    env = verifier.get("env") if isinstance(verifier, dict) else None
    if not isinstance(env, dict):
        return {}
    return {str(key): str(value) for key, value in env.items() if str(key).strip()}


def drop_external_dockerfile_syntax(text: str) -> str | None:
    """Drop a ``# syntax=docker/dockerfile:...`` pin when the file does not need it.

    That line makes BuildKit pull ``docker/dockerfile`` from Docker Hub before
    the build starts. A plain Dockerfile builds with the local frontend, and
    the pull fails when the registry is unreachable even if the base image is
    already present. Heredoc and ``RUN --mount`` still need the pinned frontend.
    """
    match = _SYNTAX_DIRECTIVE.match(text)
    if match is None:
        return None
    if "<<" in text or "--mount=" in text:
        return None
    return text[match.end():]


def _docker_bin() -> str:
    found = shutil.which("docker")
    if not found:
        raise FileNotFoundError("docker executable not found in PATH")
    return found


def _run(
    cmd: list[str],
    timeout: int,
    runner: RewardRunner | None,
    *,
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    if runner is not None:
        try:
            return runner(cmd, timeout=timeout, cwd=cwd, env=env)
        except TypeError:
            return runner(cmd, timeout=timeout, cwd=cwd)
    full_env = None
    if env is not None:
        full_env = os.environ.copy()
        full_env.update(env)
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        cwd=str(cwd) if cwd is not None else None,
        env=full_env,
    )


_REWARD_GRACE_SEC = 3.0
_DOCKER_RUN_TIMEOUT_FLOOR = 900


def _reuse_docker_image(docker: str, tag: str, runner: RewardRunner | None) -> bool:
    """Return True when *tag* already exists locally and a rebuild is not forced.

    The prebuild script (``scripts/skillsbench_prebuild_docker.py``) bakes the
    verifier dependencies into ``skillsbench-<task>:skilltrain``. Rebuilding on
    every rollout re-runs apt/pip and can hang on the network, so an existing
    image is reused unless ``SKILLSBENCH_DOCKER_REBUILD`` is truthy. A custom
    *runner* (tests) always takes the build path.
    """
    if runner is not None:
        return False
    if os.getenv("SKILLSBENCH_DOCKER_REBUILD", "").strip().lower() in {"1", "true", "yes", "on"}:
        return False
    try:
        probe = subprocess.run(
            [docker, "image", "inspect", tag],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return probe.returncode == 0


def _scores_from_ctrf(path: Path) -> dict[str, Any] | None:
    """Score from a finished CTRF report when ``reward.txt`` is not visible yet."""
    if not path.is_file():
        return None
    try:
        summary = json.loads(path.read_text(encoding="utf-8")).get("results", {}).get("summary", {})
    except (json.JSONDecodeError, AttributeError, TypeError):
        return None
    if not isinstance(summary, dict):
        return None
    tests = int(summary.get("tests") or 0)
    failed = int(summary.get("failed") or 0)
    passed = int(summary.get("passed") or 0)
    if tests <= 0:
        return None
    if failed == 0 and passed == tests:
        scores = scores_from_reward_text("1")
        scores["fail_reason"] = ""
        return _apply_counts(scores, _counts_from_ctrf(path), "ctrf")
    if failed:
        scores = scores_from_reward_text("0")
        scores["fail_reason"] = "verifier_failed"
        return _apply_counts(scores, _counts_from_ctrf(path), "ctrf")
    return None


def _scores_after_verifier_stop(logs: Path, reward_path: Path) -> dict[str, Any]:
    """Read a reward that lands as the timed-out container exits."""
    ctrf_path = logs / "verifier" / "ctrf.json"
    deadline = time.monotonic() + _REWARD_GRACE_SEC
    while True:
        if reward_path.is_file():
            scores = scores_from_reward_file(reward_path)
            return _apply_counts(scores, _counts_from_ctrf(ctrf_path), "ctrf")
        ctrf_scores = _scores_from_ctrf(ctrf_path)
        if ctrf_scores is not None:
            return ctrf_scores
        if time.monotonic() >= deadline:
            break
        time.sleep(0.2)
    scores = scores_from_reward_text("")
    scores["fail_reason"] = "verifier_timeout"
    return scores


def _write_reward(reward_path: Path, passed: bool) -> None:
    reward_path.parent.mkdir(parents=True, exist_ok=True)
    reward_path.write_text("1\n" if passed else "0\n", encoding="utf-8")


def _pytest_command(test_names: list[str]) -> list[str]:
    """Run pytest via the current interpreter in the staging cwd.

    Host-local mode prefers the active env over ``uvx`` so Windows path /
    rootdir discovery stays predictable. Install ``pytest`` (and any task
    extras such as ``python-docx``) into that env when needed.
    """
    return [
        sys.executable,
        "-m",
        "pytest",
        "-rA",
        "-v",
        "--rootdir",
        ".",
        "-p",
        "no:cacheprovider",
        *test_names,
    ]


def _prepare_local_tests(verifier_dir: Path, work_dir: Path, staging: Path) -> list[Path]:
    """Copy verifier pytest files (+ helpers/data) into *staging* with paths remapped."""
    mapped = Path(work_dir).resolve().as_posix()
    return _stage_verifier_tree(verifier_dir, staging, mapped_root=mapped)


def run_task_verifier_local(
    *,
    task_dir: str | Path,
    agent_workdir: str | Path,
    logs_dir: str | Path,
    timeout_sec: int = 900,
    runner: RewardRunner | None = None,
) -> dict[str, Any]:
    """Run the task's pytest verifier on the host with ``/root`` -> workdir."""
    root = Path(task_dir)
    work_dir = Path(agent_workdir).resolve()
    verifier_dir = root / "verifier"
    if not verifier_dir.is_dir():
        scores = scores_from_reward_text("")
        scores["fail_reason"] = "verifier_missing"
        return scores

    logs = Path(logs_dir)
    logs.mkdir(parents=True, exist_ok=True)
    reward_path = logs / "verifier" / "reward.txt"
    if reward_path.exists():
        reward_path.unlink()

    # Fill missing seed inputs without wiping agent artifacts under workdir.
    copy_environment_inputs(root, work_dir, overwrite=False)

    budget = max(1, int(timeout_sec))
    # Manual cleanup: pytest may leave ``__pycache__`` that Windows cannot
    # delete (WinError 145). A raising TemporaryDirectory exit would discard
    # a reward that was already 1.
    tmp = tempfile.mkdtemp(prefix="skillsbench-local-")
    try:
        staging = Path(tmp) / "tests"
        tests = _prepare_local_tests(verifier_dir, work_dir, staging)
        if not tests:
            scores = scores_from_reward_text("")
            scores["fail_reason"] = "local_tests_missing"
            return scores

        cmd = _pytest_command([path.name for path in tests])
        run = _run(
            cmd,
            timeout=budget,
            runner=runner,
            cwd=staging,
            env=_pythonpath_env(work_dir, staging),
        )
        passed = run.returncode == 0
        _write_reward(reward_path, passed)
        scores = scores_from_reward_file(reward_path)
        if not passed and scores["fail_reason"] == "verifier_failed":
            scores["fail_reason"] = "local_verifier_failed"
        tail = (run.stdout or "") + (run.stderr or "")
        _apply_counts(scores, _counts_from_pytest_text(tail), "pytest")
        if tail:
            scores["verifier_output"] = tail[-4000:]
            (logs / "verifier" / "pytest_output.txt").write_text(tail, encoding="utf-8", errors="replace")
        scores["verifier_mode"] = "local"
        return scores
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _prepare_wsl_tests(verifier_dir: Path, wsl_root: str, staging: Path) -> list[Path]:
    """Copy verifier pytest files (+ helpers/data) into *staging* with paths remapped."""
    root = wsl_root.rstrip("/") or "/"
    return _stage_verifier_tree(verifier_dir, staging, mapped_root=root)


def run_task_verifier_wsl(
    *,
    task_dir: str | Path,
    agent_workdir: str | Path,
    logs_dir: str | Path,
    timeout_sec: int = 900,
    runner: RewardRunner | None = None,
) -> dict[str, Any]:
    """Run the task's pytest verifier inside WSL (no Docker image build)."""
    root = Path(task_dir)
    work_dir = Path(agent_workdir).resolve()
    verifier_dir = root / "verifier"
    if not verifier_dir.is_dir():
        scores = scores_from_reward_text("")
        scores["fail_reason"] = "verifier_missing"
        return scores

    logs = Path(logs_dir)
    logs.mkdir(parents=True, exist_ok=True)
    reward_path = logs / "verifier" / "reward.txt"
    if reward_path.exists():
        reward_path.unlink()

    try:
        wsl = _wsl_bin()
    except FileNotFoundError:
        scores = scores_from_reward_text("")
        scores["fail_reason"] = "wsl_not_found"
        return scores

    try:
        wsl_root = windows_to_wsl_path(work_dir)
    except ValueError as exc:
        scores = scores_from_reward_text("")
        scores["fail_reason"] = f"wsl_path:{exc}"
        return scores

    # Fill missing seed inputs without wiping agent artifacts under workdir.
    copy_environment_inputs(root, work_dir, overwrite=False)

    budget = max(1, int(timeout_sec))
    # Stage under logs parent so the temp dir lives on a drive WSL can mount.
    # Do not use TemporaryDirectory: WSL pytest writes ``__pycache__`` that
    # Windows rmtree raises WinError 145 on, and that exception replaces a
    # successful reward with hard=0.
    staging_parent = logs.resolve().parent
    staging_parent.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="skillsbench-wsl-", dir=str(staging_parent))
    try:
        staging = Path(tmp) / "tests"
        tests = _prepare_wsl_tests(verifier_dir, wsl_root, staging)
        if not tests:
            scores = scores_from_reward_text("")
            scores["fail_reason"] = "wsl_tests_missing"
            return scores

        wsl_staging = windows_to_wsl_path(staging)
        py_dirs = []
        staging_src = staging / "src"
        if staging_src.is_dir():
            py_dirs.append(staging_src)
        py_dirs.append(staging)
        py_dirs.extend(_local_import_dirs(work_dir))
        wsl_pythonpath = ":".join(windows_to_wsl_path(path) for path in py_dirs)
        test_names = " ".join(shlex.quote(path.name) for path in tests)
        # Persistent venv (created by scripts/wsl_bootstrap_pytest.sh); CN PyPI mirror.
        pip_index = os.environ.get("PIP_INDEX_URL", "https://mirrors.huaweicloud.com/repository/pypi/simple/")
        pip_host = os.environ.get("PIP_TRUSTED_HOST", "mirrors.huaweicloud.com")
        # Official get-pip (py3.14+); wheels still pulled from PIP_INDEX_URL (Huawei Cloud).
        get_pip = os.environ.get("GET_PIP_URL", "https://bootstrap.pypa.io/get-pip.py")
        # simpo-code-reproduction needs trl==0.9.6 / torch==2.2.2 (Python 3.10).
        # Those pins cannot install on the default 3.14 verifier venv; use the
        # sibling py310 env from scripts/_wsl_install_simpo_deps.sh when present.
        prefer_py310 = root.name == "simpo-code-reproduction" or (
            os.environ.get("SKILLSBENCH_WSL_USE_PY310", "").strip().lower()
            in {"1", "true", "yes"}
        )
        venv_select = ""
        if prefer_py310:
            venv_select = (
                "PY310=\"${SKILLSBENCH_WSL_SIMPO_VENV:-$HOME/.cache/openjiuwen-skillsbench-verifier-py310}\"\n"
                "if [[ -x \"$PY310/bin/python\" ]]; then\n"
                "  VENV=\"$PY310\"\n"
                "fi\n"
            )
        script = (
            "set -e\n"
            "unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy ALL_PROXY all_proxy || true\n"
            "VENV=\"${SKILLSBENCH_WSL_VENV:-$HOME/.cache/openjiuwen-skillsbench-verifier}\"\n"
            f"{venv_select}"
            "PY=\"$VENV/bin/python\"\n"
            # User-local Chromium/OCR/sound libs (scripts/_wsl_install_sysdeps.sh).
            "SYSDEPS=\"${SKILLSBENCH_WSL_SYSDEPS:-$HOME/.cache/openjiuwen-skillsbench-sysdeps}\"\n"
            "if [[ -f \"$SYSDEPS/env.sh\" ]]; then\n"
            "  # shellcheck disable=SC1091\n"
            "  source \"$SYSDEPS/env.sh\"\n"
            "fi\n"
            f"export PIP_INDEX_URL={shlex.quote(pip_index)}\n"
            f"export PIP_TRUSTED_HOST={shlex.quote(pip_host)}\n"
            "if [[ ! -x \"$PY\" ]] || ! \"$PY\" -m pytest --version >/dev/null 2>&1; then\n"
            "  mkdir -p \"$(dirname \"$VENV\")\"\n"
            "  python3 -m venv --without-pip \"$VENV\" 2>/dev/null || python3 -m venv \"$VENV\"\n"
            "  if ! \"$PY\" -m pip --version >/dev/null 2>&1; then\n"
            f"    curl -fsSL {shlex.quote(get_pip)} -o /tmp/get-pip.py\n"
            "    \"$PY\" /tmp/get-pip.py --index-url \"$PIP_INDEX_URL\" "
            "--trusted-host \"$PIP_TRUSTED_HOST\"\n"
            "  fi\n"
            "  \"$PY\" -m pip install -q --disable-pip-version-check "
            "--index-url \"$PIP_INDEX_URL\" --trusted-host \"$PIP_TRUSTED_HOST\" pytest==9.0.2\n"
            "fi\n"
            f"export PYTHONPATH={shlex.quote(wsl_pythonpath)}"
            "${PYTHONPATH:+:$PYTHONPATH}\n"
            # Task binaries (e.g. glm) live in the agent workdir.
            f"export PATH={shlex.quote(wsl_root)}"
            ":$PATH\n"
            # Ignore parent pytest.ini (e.g. jiuwenswarm cov plugins) under /mnt/e/...
            "unset PYTEST_ADDOPTS || true\n"
            "export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1\n"
            "export PYTHONDONTWRITEBYTECODE=1\n"
            f"cd {shlex.quote(wsl_staging)}\n"
            f"\"$PY\" -m pytest -c /dev/null -rA -v --rootdir . "
            f"-p no:cacheprovider {test_names}\n"
        )
        env = {"WSL_UTF8": "1"}
        run = _run(
            [wsl, "-e", "bash", "-lc", script],
            timeout=budget,
            runner=runner,
            env=env,
        )
        passed = run.returncode == 0
        _write_reward(reward_path, passed)
        scores = scores_from_reward_file(reward_path)
        if not passed and scores["fail_reason"] == "verifier_failed":
            scores["fail_reason"] = "wsl_verifier_failed"
        tail = (run.stdout or "") + (run.stderr or "")
        _apply_counts(scores, _counts_from_pytest_text(tail), "pytest")
        if tail:
            scores["verifier_output"] = tail[-4000:]
            (logs / "verifier" / "pytest_output.txt").write_text(tail, encoding="utf-8", errors="replace")
        scores["verifier_mode"] = "wsl"
        return scores
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def run_task_verifier_docker(
    *,
    task_dir: str | Path,
    agent_workdir: str | Path,
    logs_dir: str | Path,
    timeout_sec: int = 900,
    runner: RewardRunner | None = None,
) -> dict[str, Any]:
    """Build the task image, run ``verifier/test.sh``, and read ``reward.txt``."""
    root = Path(task_dir)
    script = root / "verifier" / "test.sh"
    dockerfile = root / "environment" / "Dockerfile"
    if not script.is_file():
        scores = scores_from_reward_text("")
        scores["fail_reason"] = "verifier_missing"
        return scores
    if not dockerfile.is_file():
        scores = scores_from_reward_text("")
        scores["fail_reason"] = "dockerfile_missing"
        return scores

    logs = Path(logs_dir)
    logs.mkdir(parents=True, exist_ok=True)
    reward_path = logs / "verifier" / "reward.txt"
    if reward_path.exists():
        reward_path.unlink()

    try:
        docker = _docker_bin()
    except FileNotFoundError:
        scores = scores_from_reward_text("")
        scores["fail_reason"] = "docker_not_found"
        return scores

    tag = f"skillsbench-{root.name}:skilltrain".lower()
    budget = max(1, int(timeout_sec))
    context = (root / "environment").resolve()
    build_file = dockerfile
    rewritten = drop_external_dockerfile_syntax(dockerfile.read_text(encoding="utf-8"))
    if _reuse_docker_image(docker, tag, runner):
        logger.info(
            "[skillsbench] docker image reuse tag=%s (set SKILLSBENCH_DOCKER_REBUILD=1 to rebuild)",
            tag,
        )
    else:
        logger.info("[skillsbench] docker build tag=%s timeout=%ss", tag, budget)
        build_started = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix="skillsbench-dockerfile-") as tmp:
            if rewritten is not None:
                build_file = Path(tmp) / "Dockerfile"
                build_file.write_text(rewritten, encoding="utf-8")
            build = _run(
                [docker, "build", "-f", str(build_file), "-t", tag, str(context)],
                timeout=budget,
                runner=runner,
            )
        logger.info(
            "[skillsbench] docker build done tag=%s exit=%s elapsed=%.1fs",
            tag,
            build.returncode,
            time.perf_counter() - build_started,
        )
        if build.returncode != 0:
            scores = scores_from_reward_text("")
            scores["fail_reason"] = "image_build_failed"
            tail = (build.stderr or "") + (build.stdout or "")
            scores["verifier_output"] = tail[-4000:]
            log_path = logs / "verifier" / "docker_build.txt"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(tail, encoding="utf-8", errors="replace")
            return scores

    inner = container_agent_sync(dockerfile.read_text(encoding="utf-8"))
    verifier_env = verifier_env_from_task_md(root)
    env_flags: list[str] = []
    for key, value in verifier_env.items():
        env_flags += ["-e", f"{key}={value}"]
    if verifier_env:
        logger.info("[skillsbench] docker run env=%s", verifier_env)
    command = [
        docker,
        "run",
        "--rm",
        *env_flags,
        "-v",
        f"{script.parent.resolve()}:/verifier:ro",
        "-v",
        f"{logs.resolve()}:/logs",
        "-v",
        f"{Path(agent_workdir).resolve()}:/agent_out:ro",
        tag,
        "bash",
        "-lc",
        inner,
    ]
    run_timeout = max(budget, _DOCKER_RUN_TIMEOUT_FLOOR)
    logger.info("[skillsbench] docker run tag=%s timeout=%ss", tag, run_timeout)
    run_started = time.perf_counter()
    try:
        run = _run(command, timeout=run_timeout, runner=runner)
    except subprocess.TimeoutExpired as exc:
        logger.warning(
            "[skillsbench] docker run timeout tag=%s elapsed=%.1fs",
            tag,
            time.perf_counter() - run_started,
        )
        scores = _scores_after_verifier_stop(logs, reward_path)
        tail = f"{exc.stdout or ''}{exc.stderr or ''}"
        if tail:
            scores["verifier_output"] = tail[-4000:]
        scores["verifier_mode"] = "docker"
        return scores
    scores = scores_from_reward_file(reward_path)
    logger.info(
        "[skillsbench] docker run done tag=%s exit=%s hard=%s elapsed=%.1fs",
        tag,
        run.returncode,
        scores.get("hard"),
        time.perf_counter() - run_started,
    )
    if run.returncode != 0 and scores["fail_reason"] == "reward_missing":
        scores["fail_reason"] = "verifier_exit"
    tail = (run.stdout or "") + (run.stderr or "")
    # Parse the full log before it is trimmed for logging; fall back to the
    # CTRF report, which the task's test.sh also writes.
    _apply_counts(scores, _counts_from_pytest_text(tail), "pytest")
    if "tests_total" not in scores:
        _apply_counts(scores, _counts_from_ctrf(logs / "verifier" / "ctrf.json"), "ctrf")
    if tail:
        scores["verifier_output"] = tail[-4000:]
    scores["verifier_mode"] = "docker"
    return scores


def run_task_verifier(
    *,
    task_dir: str | Path,
    agent_workdir: str | Path,
    logs_dir: str | Path,
    timeout_sec: int = 900,
    runner: RewardRunner | None = None,
    verifier_mode: str | None = None,
) -> dict[str, Any]:
    """Score one SkillsBench task via Docker, WSL, or local pytest.

    The agent workspace is treated as ``/root``: Docker copies it there before
    pytest; ``local`` / ``wsl`` rewrite ``/root`` literals onto the workdir path
    (WSL uses the ``/mnt/<drive>/...`` form and runs pytest under Linux).
    """
    mode = resolve_verifier_mode(verifier_mode)
    task_name = Path(task_dir).name
    logger.info(
        "[skillsbench] verifier dispatch task=%s mode=%s timeout=%ss",
        task_name,
        mode,
        timeout_sec,
    )
    kwargs = dict(
        task_dir=task_dir,
        agent_workdir=agent_workdir,
        logs_dir=logs_dir,
        timeout_sec=timeout_sec,
        runner=runner,
    )
    if mode == "local":
        return run_task_verifier_local(**kwargs)
    if mode == "wsl":
        return run_task_verifier_wsl(**kwargs)
    return run_task_verifier_docker(**kwargs)
