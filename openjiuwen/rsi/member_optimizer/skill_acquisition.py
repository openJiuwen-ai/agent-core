# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Deterministic skill acquisition for member optimizer `skill/search` actions."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Callable, Sequence

import yaml

CommandRunner = Callable[[Sequence[str], Path | None], subprocess.CompletedProcess[str]]
SkillHubFetcher = Callable[[str, str, str], object]

_CANDIDATE_REF_RE = re.compile(r"(?P<ref>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[A-Za-z0-9_./-]+)")
_INSTALL_RE = re.compile(r"(?P<count>\d+(?:\.\d+)?)\s*(?P<unit>[KkMm])?\s*(?:installs?|downloads?)")
_INSTALL_AFTER_LABEL_RE = re.compile(
    r"(?:installs?|downloads?)\s*[:=]\s*(?P<count>\d+(?:\.\d+)?)\s*(?P<unit>[KkMm])?",
    re.IGNORECASE,
)
_SECURITY_RE = re.compile(
    r"(?:security|rating|risk)\s*[:=]?\s*(?P<rating>Critical|High|Medium|Low|Safe)",
    re.IGNORECASE,
)
_URL_RE = re.compile(r"https?://[^\s)\]>,]+")
_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_SKILL_HUB_URL_ENV = "MEMBER_OPTIMIZER_SKILL_HUB_URL"
_SKILL_HUB_TOKEN_ENV = "MEMBER_OPTIMIZER_SKILL_HUB_TOKEN"
_FORBIDDEN_SKILL_SUFFIXES = {
    ".bat",
    ".cmd",
    ".dll",
    ".dylib",
    ".exe",
    ".ps1",
    ".sh",
    ".so",
}
_SKILL_SCRIPT_FORBIDDEN_IMPORT_ROOTS = {
    "httpx",
    "requests",
    "socket",
    "subprocess",
    "urllib",
}
_SKILL_SCRIPT_FORBIDDEN_CALLS = {
    "__import__",
    "compile",
    "eval",
    "exec",
    "input",
    "os.popen",
    "os.spawn",
    "os.system",
    "shutil.move",
    "shutil.rmtree",
    "subprocess.call",
    "subprocess.check_call",
    "subprocess.check_output",
    "subprocess.Popen",
    "subprocess.run",
}


@dataclass(frozen=True, slots=True)
class SkillSearchCandidate:
    """One candidate skill returned by a search backend."""

    ref: str
    owner: str
    repo: str
    skill_path: str
    skill_name: str
    install_count: int | None = None
    security_rating: str | None = None
    source_url: str = ""
    backend: str = ""
    raw: str = ""


@dataclass(slots=True)
class SkillAcquisitionResult:
    """Result payload persisted into an action execution artifact."""

    status: str
    query: str
    candidates: list[dict[str, object]] = field(default_factory=list)
    selected_ref: str = ""
    copied_files: list[str] = field(default_factory=list)
    safety_scan: dict[str, object] = field(default_factory=dict)
    rejections: list[dict[str, str]] = field(default_factory=list)
    error: str = ""


class SkillAcquisition:
    """Search, copy, and validate community skills into a role ExpertHarness."""

    def __init__(
        self,
        *,
        runner: CommandRunner | None = None,
        skill_hub_fetcher: SkillHubFetcher | None = None,
        skill_hub_url: str | None = None,
        skill_hub_token: str | None = None,
        temp_root: Path | None = None,
        max_files: int = 200,
        max_total_bytes: int = 10 * 1024 * 1024,
        min_install_count: int = 100,
        weak_install_count: int = 1000,
    ) -> None:
        self._runner = runner or _default_runner
        self._skill_hub_fetcher = skill_hub_fetcher or _fetch_skill_hub_payload
        self._skill_hub_url = os.environ.get(_SKILL_HUB_URL_ENV, "") if skill_hub_url is None else skill_hub_url
        self._skill_hub_token = os.environ.get(_SKILL_HUB_TOKEN_ENV, "") if skill_hub_token is None else skill_hub_token
        self._temp_root = temp_root
        self._max_files = max_files
        self._max_total_bytes = max_total_bytes
        self._min_install_count = min_install_count
        self._weak_install_count = weak_install_count

    def acquire(
        self,
        *,
        action_worktree: Path,
        query: str,
    ) -> SkillAcquisitionResult:
        """Search for the best skill and copy it into `action_worktree/skills`."""
        normalized_query = str(query or "").strip()
        result = SkillAcquisitionResult(status="failed", query=normalized_query)
        if not normalized_query:
            result.error = "skill/search requires a non-empty candidate_query"
            return result

        candidates = self.search(normalized_query, result)
        result.candidates = [asdict(candidate) for candidate in candidates]
        if not candidates:
            result.error = "no skill candidates found"
            return result

        skills_dir = action_worktree / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)

        if self._temp_root is not None:
            self._temp_root.mkdir(parents=True, exist_ok=True)
        staging_root = Path(
            tempfile.mkdtemp(
                prefix="member_skill_acq_",
                dir=str(self._temp_root) if self._temp_root else None,
            )
        )
        try:
            for candidate in candidates:
                if candidate.security_rating and candidate.security_rating.lower() == "critical":
                    result.rejections.append(
                        {
                            "ref": candidate.ref,
                            "reason": "security rating Critical",
                        }
                    )
                    continue
                if candidate.install_count is not None and candidate.install_count < self._min_install_count:
                    result.rejections.append(
                        {
                            "ref": candidate.ref,
                            "reason": f"install count {candidate.install_count} below {self._min_install_count}",
                        }
                    )
                    continue

                destination = skills_dir / candidate.skill_name
                if destination.exists():
                    result.rejections.append(
                        {
                            "ref": candidate.ref,
                            "reason": f"destination already exists: skills/{candidate.skill_name}",
                        }
                    )
                    continue

                try:
                    source_dir = self._checkout_candidate(
                        candidate=candidate,
                        staging_root=staging_root,
                    )
                    safety = scan_skill_directory(
                        source_dir,
                        max_files=self._max_files,
                        max_total_bytes=self._max_total_bytes,
                    )
                    if safety["status"] != "passed":
                        result.rejections.append(
                            {
                                "ref": candidate.ref,
                                "reason": "; ".join(str(item) for item in safety["errors"]),
                            }
                        )
                        continue

                    shutil.copytree(source_dir, destination, symlinks=False)
                    copied_files = [
                        path.relative_to(action_worktree).as_posix()
                        for path in sorted(destination.rglob("*"))
                        if path.is_file()
                    ]
                    _append_skill_manifest(action_worktree, candidate.skill_name)
                    result.status = "succeeded"
                    result.selected_ref = candidate.ref
                    result.copied_files = copied_files
                    result.safety_scan = safety
                    return result
                except Exception as exc:
                    if destination.exists():
                        shutil.rmtree(destination)
                    result.rejections.append(
                        {
                            "ref": candidate.ref,
                            "reason": str(exc),
                        }
                    )
        finally:
            _cleanup_staging_root(staging_root, result)

        result.error = "all skill candidates were rejected"
        return result

    def search(self, query: str, result: SkillAcquisitionResult | None = None) -> list[SkillSearchCandidate]:
        """Search backends and return quality-ordered candidates."""
        backends = [
            ("npx", _npx_find_command(query)),
            ("skillnet", ["skillnet", "search", query, "--mode", "vector"]),
        ]
        candidates: list[SkillSearchCandidate] = []
        seen_refs: set[str] = set()
        for backend, command in backends:
            try:
                completed = self._runner(command, None)
            except FileNotFoundError:
                if result is not None:
                    result.rejections.append(
                        {
                            "ref": backend,
                            "reason": "search command not found",
                        }
                    )
                continue
            except Exception as exc:
                if result is not None:
                    result.rejections.append(
                        {
                            "ref": backend,
                            "reason": f"search failed: {exc}",
                        }
                    )
                continue
            if completed.returncode != 0:
                if result is not None:
                    result.rejections.append(
                        {
                            "ref": backend,
                            "reason": (completed.stderr or "").strip() or f"exit code {completed.returncode}",
                        }
                    )
                continue
            for candidate in _parse_search_output(completed.stdout or "", backend=backend):
                if candidate.ref in seen_refs:
                    continue
                seen_refs.add(candidate.ref)
                candidates.append(candidate)

        skill_hub_url = str(self._skill_hub_url or "").strip()
        if skill_hub_url:
            try:
                payload = self._skill_hub_fetcher(
                    query,
                    skill_hub_url,
                    str(self._skill_hub_token or ""),
                )
                for candidate in _parse_skill_hub_payload(payload):
                    if candidate.ref in seen_refs:
                        continue
                    seen_refs.add(candidate.ref)
                    candidates.append(candidate)
            except Exception as exc:
                if result is not None:
                    result.rejections.append(
                        {
                            "ref": "skill_hub",
                            "reason": f"search failed: {exc}",
                        }
                    )

        return sorted(candidates, key=_candidate_sort_key)

    def _checkout_candidate(
        self,
        *,
        candidate: SkillSearchCandidate,
        staging_root: Path,
    ) -> Path:
        checkout_dir = staging_root / _safe_checkout_dir_name(candidate.ref)
        if checkout_dir.exists():
            shutil.rmtree(checkout_dir)
        repo_url = f"https://github.com/{candidate.owner}/{candidate.repo}.git"
        clone = self._runner(
            [
                "git",
                "-c",
                "http.sslBackend=openssl",
                "-c",
                "core.longpaths=true",
                "clone",
                "--depth=1",
                "--filter=blob:none",
                "--no-tags",
                "--sparse",
                repo_url,
                str(checkout_dir),
            ],
            None,
        )
        _check_completed(clone, "git clone")
        skill_path = self._resolve_candidate_skill_path(
            candidate=candidate,
            checkout_dir=checkout_dir,
        )
        sparse = self._runner(
            _git_checkout_command(checkout_dir, "sparse-checkout", "set", skill_path),
            None,
        )
        _check_completed(sparse, "git sparse-checkout")

        source_dir = checkout_dir / skill_path
        if not source_dir.is_dir():
            raise FileNotFoundError(f"candidate skill directory not found: {skill_path}")
        return source_dir

    def _resolve_candidate_skill_path(
        self,
        *,
        candidate: SkillSearchCandidate,
        checkout_dir: Path,
    ) -> str:
        """Resolve a search ref path to the actual skill directory in the repo tree."""
        try:
            tree = self._runner(
                _git_checkout_command(checkout_dir, "ls-tree", "-r", "--name-only", "HEAD"),
                None,
            )
        except Exception:
            return candidate.skill_path
        if tree.returncode != 0:
            return candidate.skill_path
        resolved = _select_skill_dir_from_tree(tree.stdout or "", candidate)
        return resolved or candidate.skill_path


def scan_skill_directory(
    skill_dir: Path,
    *,
    max_files: int = 200,
    max_total_bytes: int = 10 * 1024 * 1024,
) -> dict[str, object]:
    """Return a static safety scan result for a copied or staged skill directory."""
    errors: list[str] = []
    files: list[str] = []
    total_bytes = 0
    root = skill_dir.resolve()

    if not skill_dir.is_dir():
        errors.append(f"skill directory not found: {skill_dir}")
        return {"status": "failed", "errors": errors, "files": files, "total_bytes": 0}
    if not (skill_dir / "SKILL.md").is_file():
        errors.append("missing SKILL.md")

    for path in sorted(skill_dir.rglob("*")):
        try:
            resolved = path.resolve()
            resolved.relative_to(root)
        except Exception:
            errors.append(f"path escapes skill directory: {path}")
            continue

        rel = path.relative_to(skill_dir).as_posix()
        parts = Path(rel).parts
        if any(part in {"..", ""} for part in parts):
            errors.append(f"invalid relative path: {rel}")
        if any(part.startswith(".") for part in parts):
            errors.append(f"hidden path not allowed: {rel}")
        if path.is_symlink():
            errors.append(f"symlink not allowed: {rel}")
            continue
        if path.is_dir():
            continue

        files.append(rel)
        if path.suffix.lower() in _FORBIDDEN_SKILL_SUFFIXES:
            errors.append(f"forbidden executable file type: {rel}")
        try:
            total_bytes += path.stat().st_size
        except OSError as exc:
            errors.append(f"cannot stat {rel}: {exc}")
        if path.suffix == ".py":
            errors.extend(_scan_python_skill_script(path, rel))

    if len(files) > max_files:
        errors.append(f"file count {len(files)} exceeds limit {max_files}")
    if total_bytes > max_total_bytes:
        errors.append(f"total size {total_bytes} exceeds limit {max_total_bytes}")

    return {
        "status": "failed" if errors else "passed",
        "errors": errors,
        "files": files,
        "file_count": len(files),
        "total_bytes": total_bytes,
    }


def _scan_python_skill_script(path: Path, rel: str) -> list[str]:
    errors: list[str] = []
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
    except Exception as exc:
        return [f"python parse failed in {rel}: {exc}"]

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                if root in _SKILL_SCRIPT_FORBIDDEN_IMPORT_ROOTS:
                    errors.append(f"dangerous import '{alias.name}' in {rel}")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".", 1)[0]
            if root in _SKILL_SCRIPT_FORBIDDEN_IMPORT_ROOTS:
                errors.append(f"dangerous import '{node.module}' in {rel}")
        elif isinstance(node, ast.Call):
            call_name = _qualified_call_name(node.func)
            if call_name in _SKILL_SCRIPT_FORBIDDEN_CALLS:
                errors.append(f"dangerous call '{call_name}' in {rel}")
    return errors


def _qualified_call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        owner = _qualified_call_name(node.value)
        return f"{owner}.{node.attr}" if owner else node.attr
    return ""


def _parse_search_output(output: str, *, backend: str) -> list[SkillSearchCandidate]:
    candidates: list[SkillSearchCandidate] = []
    for block in _split_candidate_blocks(_strip_ansi(output)):
        match = _CANDIDATE_REF_RE.search(block)
        if not match:
            continue
        ref = match.group("ref")
        parsed = _parse_ref(ref)
        if parsed is None:
            continue
        owner, repo, skill_path, skill_name = parsed
        if _is_placeholder_ref(owner, repo, skill_path):
            continue
        install_count = _parse_install_count(block)
        security = _parse_security_rating(block)
        url_match = _URL_RE.search(block)
        candidates.append(
            SkillSearchCandidate(
                ref=ref,
                owner=owner,
                repo=repo,
                skill_path=skill_path,
                skill_name=skill_name,
                install_count=install_count,
                security_rating=security,
                source_url=url_match.group(0) if url_match else "",
                backend=backend,
                raw=block.strip(),
            )
        )
    return candidates


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _is_placeholder_ref(owner: str, repo: str, skill_path: str) -> bool:
    return owner == "owner" and repo == "repo" and skill_path == "skill"


def _parse_skill_hub_payload(payload: object) -> list[SkillSearchCandidate]:
    """Parse Skill Hub JSON into search candidates.

    Supported response shapes are either a bare list of candidate mappings or a
    mapping containing a `results` list.
    """
    if isinstance(payload, dict):
        items = payload.get("results", [])
    elif isinstance(payload, list):
        items = payload
    else:
        raise ValueError("Skill Hub response must be a list or an object with results")

    if not isinstance(items, list):
        raise ValueError("Skill Hub results must be a list")

    candidates: list[SkillSearchCandidate] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        ref = str(item.get("ref") or item.get("skill_ref") or "").strip()
        if not ref:
            continue
        parsed = _parse_ref(ref)
        if parsed is None:
            continue
        owner, repo, skill_path, skill_name = parsed
        install_count = _coerce_install_count(item.get("install_count", item.get("installs")))
        security = _coerce_security_rating(item.get("security_rating", item.get("security")))
        source_url = str(item.get("source_url") or item.get("url") or "")
        candidates.append(
            SkillSearchCandidate(
                ref=ref,
                owner=owner,
                repo=repo,
                skill_path=skill_path,
                skill_name=skill_name,
                install_count=install_count,
                security_rating=security,
                source_url=source_url,
                backend="skill_hub",
                raw=json.dumps(item, ensure_ascii=False, sort_keys=True),
            )
        )
    return candidates


def _split_candidate_blocks(output: str) -> list[str]:
    blocks: list[str] = []
    current: list[str] = []
    for line in output.splitlines():
        if _CANDIDATE_REF_RE.search(line) and current:
            blocks.append("\n".join(current))
            current = [line]
        else:
            current.append(line)
    if current:
        blocks.append("\n".join(current))
    return blocks


def _select_skill_dir_from_tree(
    tree_output: str,
    candidate: SkillSearchCandidate,
) -> str | None:
    skill_dirs: set[str] = set()
    for line in tree_output.splitlines():
        path_text = line.strip().replace("\\", "/")
        if not path_text:
            continue
        path = PurePosixPath(path_text)
        if path.name != "SKILL.md":
            continue
        parent = path.parent.as_posix()
        if parent and parent != ".":
            skill_dirs.add(parent.strip("/"))

    if not skill_dirs:
        return None

    declared_path = candidate.skill_path.strip("/")
    if declared_path in skill_dirs:
        return declared_path

    parent_mount_path = f"skills/{candidate.skill_name}"
    if parent_mount_path in skill_dirs:
        return parent_mount_path

    name_matches = sorted(
        skill_dir for skill_dir in skill_dirs if PurePosixPath(skill_dir).name == candidate.skill_name
    )
    if len(name_matches) == 1:
        return name_matches[0]

    if len(skill_dirs) == 1:
        return next(iter(skill_dirs))
    return None


def _parse_ref(ref: str) -> tuple[str, str, str, str] | None:
    try:
        repo_part, skill_path = ref.split("@", 1)
        owner, repo = repo_part.split("/", 1)
    except ValueError:
        return None
    skill_path = skill_path.strip("/")
    if not owner or not repo or not skill_path:
        return None
    skill_name = Path(skill_path).name
    if not skill_name:
        return None
    return owner, repo, skill_path, skill_name


def _parse_install_count(text: str) -> int | None:
    match = _INSTALL_RE.search(text) or _INSTALL_AFTER_LABEL_RE.search(text)
    if not match:
        return None
    value = float(match.group("count"))
    unit = (match.group("unit") or "").lower()
    if unit == "k":
        value *= 1_000
    elif unit == "m":
        value *= 1_000_000
    return int(value)


def _coerce_install_count(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)

    text = str(value).strip()
    if not text:
        return None
    parsed = _parse_install_count(f"installs: {text}")
    if parsed is not None:
        return parsed

    match = re.fullmatch(r"(?P<count>\d+(?:\.\d+)?)\s*(?P<unit>[KkMm])?", text)
    if not match:
        return None
    count = float(match.group("count"))
    unit = (match.group("unit") or "").lower()
    if unit == "k":
        count *= 1_000
    elif unit == "m":
        count *= 1_000_000
    return int(count)


def _parse_security_rating(text: str) -> str | None:
    match = _SECURITY_RE.search(text)
    if not match:
        return None
    return match.group("rating").capitalize()


def _coerce_security_rating(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text.capitalize()


def _candidate_sort_key(candidate: SkillSearchCandidate) -> tuple[int, int, str]:
    if candidate.security_rating and candidate.security_rating.lower() == "critical":
        security_rank = 3
    elif candidate.install_count is None:
        security_rank = 2
    elif candidate.install_count < 1000:
        security_rank = 1
    else:
        security_rank = 0
    install_rank = -(candidate.install_count or 0)
    return (security_rank, install_rank, candidate.ref)


def _append_skill_manifest(action_worktree: Path, skill_name: str) -> None:
    manifest = action_worktree / "skills" / "skills.yaml"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    data: object = {}
    if manifest.is_file():
        with open(manifest, encoding="utf-8") as file:
            data = yaml.safe_load(file) or {}

    if isinstance(data, dict):
        skills = data.get("skills") or []
        if not isinstance(skills, list):
            skills = [skills]
    elif isinstance(data, list):
        skills = data
        data = {"skills": skills}
    else:
        skills = []
        data = {"skills": skills}

    # SkillUseRail loads skills by scanning child directories of each mounted
    # skills root. Keep the selected skill at skills/<name>/, but mount the
    # role-local parent directory so the current runtime can discover it.
    _ = skill_name
    entry = "skills"
    normalized = [str(item).replace("\\", "/").strip("/") for item in skills]
    if entry not in normalized:
        skills.append(entry)
    data["skills"] = skills
    with open(manifest, "w", encoding="utf-8") as file:
        yaml.safe_dump(data, file, allow_unicode=True, sort_keys=False)


def _npx_find_command(query: str) -> list[str]:
    if os.name != "nt":
        return ["npx", "skills", "find", query]
    return ["npx.cmd", "skills", "find", query]


def _fetch_skill_hub_payload(query: str, url: str, token: str) -> object:
    request_url = _skill_hub_request_url(url, query)
    headers = {"Accept": "application/json"}
    token = str(token or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(request_url, headers=headers, method="GET")
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read().decode("utf-8")
    return json.loads(body)


def _skill_hub_request_url(url: str, query: str) -> str:
    parts = urllib.parse.urlsplit(url)
    query_items = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    query_items.append(("q", query))
    return urllib.parse.urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            parts.path,
            urllib.parse.urlencode(query_items),
            parts.fragment,
        )
    )


def _check_completed(completed: subprocess.CompletedProcess[str], label: str) -> None:
    if completed.returncode == 0:
        return
    detail = (completed.stderr or "").strip() or (completed.stdout or "").strip()
    message = f"{label} failed with exit code {completed.returncode}"
    if detail:
        message = f"{message}: {detail}"
    raise RuntimeError(message)


def _git_checkout_command(checkout_dir: Path, *args: str) -> list[str]:
    return [
        "git",
        "-c",
        "http.sslBackend=openssl",
        "-c",
        "core.longpaths=true",
        "-C",
        str(checkout_dir),
        *args,
    ]


def _cleanup_staging_root(staging_root: Path, result: SkillAcquisitionResult) -> None:
    """Best-effort cleanup for git checkout temp dirs on Windows."""
    try:
        shutil.rmtree(staging_root)
    except FileNotFoundError:
        return
    except Exception as exc:
        result.rejections.append(
            {
                "ref": "staging_cleanup",
                "reason": str(exc),
            }
        )


def _safe_checkout_dir_name(ref: str) -> str:
    digest = hashlib.sha1(ref.encode("utf-8")).hexdigest()[:12]
    parsed = _parse_ref(ref)
    suffix = parsed[3] if parsed else "skill"
    safe_suffix = re.sub(r"[^A-Za-z0-9_.-]+", "_", suffix).strip("._")
    return f"{digest}_{safe_suffix or 'skill'}"


def _default_runner(
    command: Sequence[str],
    cwd: Path | None,
) -> subprocess.CompletedProcess[str]:
    return _run_command(command, cwd, timeout=120)


def _run_command(
    command: Sequence[str],
    cwd: Path | None,
    *,
    timeout: float,
) -> subprocess.CompletedProcess[str]:
    """Run a command and terminate its complete process tree on timeout."""
    popen_kwargs: dict[str, object] = {}
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True

    process = subprocess.Popen(
        list(command),
        cwd=str(cwd) if cwd else None,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **popen_kwargs,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _terminate_process_tree(process)
        try:
            stdout, stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
            process.wait(timeout=5)
            stdout, stderr = "", ""
        raise subprocess.TimeoutExpired(
            cmd=list(command),
            timeout=timeout,
            output=stdout or exc.output,
            stderr=stderr or exc.stderr,
        ) from None
    return subprocess.CompletedProcess(
        args=list(command),
        returncode=process.returncode,
        stdout=stdout,
        stderr=stderr,
    )


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    """Best-effort process-tree termination while the direct parent still exists."""
    if os.name == "nt":
        taskkill = shutil.which("taskkill")
        if taskkill is None:
            process.kill()
            return
        subprocess.run(
            [taskkill, "/PID", str(process.pid), "/T", "/F"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=10,
        )
        if process.poll() is None:
            process.kill()
        return

    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        if process.poll() is None:
            process.kill()


__all__ = [
    "SkillAcquisition",
    "SkillAcquisitionResult",
    "SkillSearchCandidate",
    "scan_skill_directory",
]
