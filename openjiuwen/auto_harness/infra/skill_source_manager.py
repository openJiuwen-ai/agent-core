# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Community skill source management — clone, scan, and copy skills."""

from __future__ import annotations

import asyncio
import io
import re
import shutil
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional
from urllib.parse import urlparse

import yaml

from openjiuwen.auto_harness.infra.git_auth import (
    build_git_auth_env,
)
from openjiuwen.core.common.logging import logger

if TYPE_CHECKING:
    from openjiuwen.auto_harness.schema import AutoHarnessConfig


# Known GitHub repos with direct zip download URLs
_GITHUB_ZIP_URLS: Dict[str, str] = {
    "https://github.com/anthropics/skills.git": "https://github.com/anthropics/skills/archive/refs/heads/main.zip",
    "https://github.com/JimLiu/baoyu-skills.git": "https://github.com/JimLiu/baoyu-skills/archive/refs/heads/main.zip",
}


@dataclass
class SkillMatch:
    """A discovered community skill with metadata."""

    name: str
    description: str
    repo_url: str
    skill_dir: Path


def _is_github_repo(repo_url: str) -> bool:
    """Check if repo URL is a known GitHub public repo."""
    return repo_url in _GITHUB_ZIP_URLS


def _github_zip_url(repo_url: str) -> str:
    """Get the direct zip download URL for a GitHub repo."""
    return _GITHUB_ZIP_URLS.get(repo_url, "")


async def _download_github_zip(
    zip_url: str,
    target: Path,
) -> bool:
    """Download and extract a GitHub zip archive.

    Returns True on success, False on failure.
    """
    import aiohttp

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(zip_url) as resp:
                if resp.status != 200:
                    logger.warning(
                        "[SkillSourceManager] download failed for %s: status %d",
                        zip_url,
                        resp.status,
                    )
                    return False
                zip_data = await resp.read()

        # Extract zip to target directory
        with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
            # GitHub zip contains a root folder like "skills-main" or "baoyu-skills-main"
            # We need to strip this prefix when extracting
            names = zf.namelist()
            if not names:
                return False

            # Find the common prefix (the root folder in the zip)
            first_name = names[0]
            prefix = first_name.split("/", 1)[0] + "/" if "/" in first_name else ""

            # Extract, stripping the prefix
            for name in names:
                if not name.startswith(prefix):
                    continue
                rel_path = name[len(prefix):]
                if not rel_path:  # Skip the root folder itself
                    continue
                dest_path = target / rel_path
                if name.endswith("/"):  # Directory
                    dest_path.mkdir(parents=True, exist_ok=True)
                else:  # File
                    dest_path.parent.mkdir(parents=True, exist_ok=True)
                    dest_path.write_bytes(zf.read(name))

        logger.info(
            "[SkillSourceManager] downloaded and extracted %s to %s",
            zip_url,
            str(target),
        )
        return True

    except Exception as e:
        logger.warning(
            "[SkillSourceManager] download/extract failed for %s: %s",
            zip_url,
            str(e),
        )
        return False


def _check_download_timestamp(repo_dir: Path, max_age_hours: int = 24 * 7) -> bool:
    """Check if repo was downloaded recently enough to skip update.

    For zip-downloaded repos (no .git directory), uses the directory's mtime.
    """
    # If has .git, use the git-based check
    if (repo_dir / ".git").is_dir():
        return _check_pull_timestamp(repo_dir)

    # For zip downloads, check directory modification time
    try:
        mtime = repo_dir.stat().st_mtime
        age_hours = (time.time() - mtime) / 3600
        return age_hours < max_age_hours
    except Exception:
        return False


async def _run_git(
    *args: str,
    cwd: str,
    env: dict[str, str] | None = None,
) -> tuple[int, str]:
    """Execute a git command and return (returncode, output)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=cwd,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await proc.communicate()
        output = stdout.decode("utf-8", errors="replace")
        return proc.returncode or 0, output.strip()
    except FileNotFoundError:
        return 1, "git command not found - please install git"
    except PermissionError as e:
        return 1, f"permission denied: {e}"
    except OSError as e:
        return 1, f"os error: {e}"


def _check_pull_timestamp(repo_dir: Path, max_age_hours: int = 24 * 7) -> bool:
    """Check if repo was pulled recently enough to skip update.

    Returns True if repo was pulled within max_age_hours, False otherwise.
    """
    fetch_head = repo_dir / ".git" / "FETCH_HEAD"
    if not fetch_head.is_file():
        return False
    try:
        mtime = fetch_head.stat().st_mtime
        age_hours = (time.time() - mtime) / 3600
        return age_hours < max_age_hours
    except Exception:
        return False


def _repo_name_from_url(url: str) -> str:
    """Extract a filesystem-safe repo name from a git URL.

    Examples:
        https://github.com/anthropics/skills.git → anthropics-skills
        https://github.com/JimLiu/baoyu-skills.git → JimLiu-baoyu-skills
    """
    # Strip trailing .git and scheme
    clean = url.rstrip("/")
    if clean.endswith(".git"):
        clean = clean[:-4]
    # Extract last two path segments: owner/repo
    parts = clean.split("/")
    if len(parts) >= 2:
        slug = f"{parts[-2]}-{parts[-1]}"
    else:
        slug = parts[-1]
    # Sanitize for filesystem
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", slug)
    return slug or "community-skills"


async def ensure_skill_sources(
    config: "AutoHarnessConfig",
    *,
    emit: Any = None,
) -> List[str]:
    """Download or update community skill source repos.

    For known GitHub public repos, uses direct zip download.
    For other repos (e.g., gitcode.com), uses git clone with optional auth.

    Returns the list of cached repo directory paths.
    """
    cache_dir = Path(config.resolved_community_skill_cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    gitcode_username = config.resolve_gitcode_username()
    gitcode_token = config.resolve_gitcode_token()
    git_env = (
        build_git_auth_env(
            username=gitcode_username,
            token=gitcode_token,
        )
        if gitcode_username and gitcode_token
        else None
    )

    cloned_dirs: List[str] = []

    for repo_url in config.community_skill_repos:
        repo_name = _repo_name_from_url(repo_url)
        target = cache_dir / repo_name

        # GitHub public repos: use zip download
        if _is_github_repo(repo_url):
            # Check if already downloaded and recent enough
            if target.is_dir():
                if _check_download_timestamp(target):
                    logger.info(
                        "[SkillSourceManager] skipping download for %s (downloaded within 7d)",
                        repo_url,
                    )
                    cloned_dirs.append(str(target))
                    continue
                # Need update: remove old directory
                logger.info(
                    "[SkillSourceManager] removing old directory for %s",
                    repo_url,
                )
                shutil.rmtree(target)

            # Download zip
            if emit:
                emit(f"正在下载社区 skill 源仓: {repo_url}")
            zip_url = _github_zip_url(repo_url)
            success = await _download_github_zip(zip_url, target)
            if success:
                if emit:
                    emit(f"已下载社区 skill 源仓: {repo_url}")
                cloned_dirs.append(str(target))
            else:
                if emit:
                    emit(f"下载社区 skill 源仓失败: {repo_url}")
            continue

        # Other repos (gitcode.com, etc): use git clone/pull
        if target.is_dir() and (target / ".git").is_dir():
            # Already cloned — check if recent pull to skip update
            if _check_pull_timestamp(target):
                logger.info(
                    "[SkillSourceManager] skipping pull for %s (pulled within 48h)",
                    repo_url,
                )
                cloned_dirs.append(str(target))
                continue

            # Pull updates (fetch + merge) to actually update working directory
            if emit:
                emit(f"正在更新社区 skill 源仓: {repo_url}")
            code, out = await _run_git(
                "pull",
                "--depth",
                "1",
                cwd=str(target),
                env=git_env,
            )
            if code != 0:
                logger.warning(
                    "[SkillSourceManager] pull failed for %s (continuing with cached version): %s",
                    repo_url,
                    out,
                )
            else:
                logger.info(
                    "[SkillSourceManager] pulled updates for %s",
                    repo_url,
                )
            cloned_dirs.append(str(target))
            continue

        # First-time clone for non-GitHub repos
        target.parent.mkdir(parents=True, exist_ok=True)
        if emit:
            emit(f"正在克隆社区 skill 源仓: {repo_url}")
        logger.info(
            "[SkillSourceManager] cloning %s to %s",
            repo_url,
            str(target),
        )
        code, out = await _run_git(
            "clone",
            "--depth",
            "1",
            repo_url,
            str(target),
            cwd=str(target.parent),
            env=git_env,
        )
        if code != 0:
            logger.warning(
                "[SkillSourceManager] clone failed for %s: %s",
                repo_url,
                out,
            )
            if emit:
                emit(f"克隆社区 skill 源仓失败: {repo_url}")
            continue

        logger.info(
            "[SkillSourceManager] cloned %s to %s",
            repo_url,
            str(target),
        )
        if emit:
            emit(f"已克隆社区 skill 源仓: {repo_url}")
        cloned_dirs.append(str(target))

    return cloned_dirs


def scan_skills(
    config: "AutoHarnessConfig",
) -> Dict[str, SkillMatch]:
    """Scan all cached skill source repos for available skills.

    Returns a dict mapping skill_name → SkillMatch.

    Handles different repo structures:
    - anthropics/skills: skills/<name>/SKILL.md
    - baoyu-skills: skills/<name>/SKILL.md
    - Other possible: <name>/SKILL.md (flat structure)
    """
    cache_dir = Path(config.resolved_community_skill_cache_dir)
    if not cache_dir.is_dir():
        return {}

    skill_map: Dict[str, SkillMatch] = {}

    for repo_url in config.community_skill_repos:
        repo_name = _repo_name_from_url(repo_url)
        repo_dir = cache_dir / repo_name
        if not repo_dir.is_dir():
            continue

        # Check for skills/ subdirectory (anthropics/skills structure)
        skills_subdir = repo_dir / "skills"
        scan_dir = skills_subdir if skills_subdir.is_dir() else repo_dir

        for item in sorted(scan_dir.iterdir(), key=lambda p: p.name):
            if not item.is_dir():
                continue
            if item.name.startswith(".") or item.name.startswith("_"):
                continue
            skill_md = item / "SKILL.md"
            if not skill_md.is_file():
                continue

            name = item.name
            description = _load_skill_description(skill_md)
            skill_map[name] = SkillMatch(
                name=name,
                description=description,
                repo_url=repo_url,
                skill_dir=item,
            )

    return skill_map


def _load_skill_description(skill_md: Path) -> str:
    """Load the description field from SKILL.md YAML frontmatter."""
    try:
        text = skill_md.read_text(encoding="utf-8")
    except Exception:
        return ""

    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            _, yaml_block, _ = parts
            try:
                data = yaml.safe_load(yaml_block) or {}
                return str(data.get("description", ""))
            except Exception:
                return ""

    return ""


def copy_skill_to_extension(
    skill_name: str,
    extension_root: Path,
    config: "AutoHarnessConfig",
) -> Optional[Path]:
    """Copy a community skill into an extension's skills/ directory.

    Returns the destination skill directory path, or None if the
    skill was not found in the cache.

    The copy includes automatic frontmatter patching: if the
    source SKILL.md lacks a required ``name`` or ``description``
    field, the missing fields are added.
    """
    skills_map = scan_skills(config)
    match = skills_map.get(skill_name)
    if match is None:
        logger.warning(
            "[SkillSourceManager] community skill '%s' not found in cache",
            skill_name,
        )
        return None

    dest_skills_dir = extension_root / "skills"
    dest_skills_dir.mkdir(parents=True, exist_ok=True)
    dest_skill_dir = dest_skills_dir / skill_name

    if dest_skill_dir.exists():
        shutil.rmtree(dest_skill_dir)

    shutil.copytree(str(match.skill_dir), str(dest_skill_dir))

    # Patch SKILL.md frontmatter if needed
    _patch_skill_frontmatter(dest_skill_dir / "SKILL.md", skill_name)

    logger.info(
        "[SkillSourceManager] copied community skill '%s' from %s to %s",
        skill_name,
        str(match.skill_dir),
        str(dest_skill_dir),
    )
    return dest_skill_dir


def _patch_skill_frontmatter(
    skill_md_path: Path,
    skill_name: str,
) -> None:
    """Ensure SKILL.md has valid name and description frontmatter fields.

    If the frontmatter lacks ``name`` or ``description``, add them.
    Preserve all other existing frontmatter fields and the body content.
    """
    if not skill_md_path.is_file():
        return

    try:
        text = skill_md_path.read_text(encoding="utf-8")
    except Exception as e:
        logger.error(
            "[SkillSourceManager] failed to read SKILL.md: %s: %s",
            skill_md_path,
            e,
        )
        return

    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            _, yaml_block, body = parts
            try:
                data = yaml.safe_load(yaml_block) or {}
            except Exception as e:
                logger.warning(
                    "[SkillSourceManager] YAML parse error in %s: %s, using empty dict",
                    skill_md_path,
                    e,
                )
                data = {}

            needs_patch = False
            if "name" not in data or not data["name"]:
                data["name"] = skill_name
                needs_patch = True
            if "description" not in data or not data["description"]:
                data["description"] = f"Community skill: {skill_name}"
                needs_patch = True

            if needs_patch:
                new_yaml = yaml.dump(
                    data,
                    default_flow_style=False,
                    allow_unicode=True,
                )
                patched = f"---\n{new_yaml}---\n{body.lstrip()}"
                skill_md_path.write_text(patched, encoding="utf-8")
                logger.info(
                    "[SkillSourceManager] patched frontmatter for '%s'",
                    skill_name,
                )
            return

    # No frontmatter at all — add one
    new_content = f"---\nname: {skill_name}\ndescription: Community skill: {skill_name}\n---\n\n{text}"
    skill_md_path.write_text(new_content, encoding="utf-8")


def community_skill_cache_skill_dirs(
    config: "AutoHarnessConfig",
) -> List[str]:
    """Return the skill root directory paths inside each cached repo.

    Handles different repo structures:
    - anthropics/skills: skills/<name>/SKILL.md → return skills/ subdirectory
    - Other possible: <name>/SKILL.md → return repo root
    """
    cache_dir = Path(config.resolved_community_skill_cache_dir)
    if not cache_dir.is_dir():
        return []

    dirs: List[str] = []
    for repo_url in config.community_skill_repos:
        repo_name = _repo_name_from_url(repo_url)
        repo_dir = cache_dir / repo_name
        if not repo_dir.is_dir():
            continue

        # Check for skills/ subdirectory
        skills_subdir = repo_dir / "skills"
        if skills_subdir.is_dir():
            dirs.append(str(skills_subdir))
        else:
            dirs.append(str(repo_dir))
    return dirs


def format_community_skill_list(
    config: "AutoHarnessConfig",
) -> str:
    """Format the available community skills as a prompt-friendly list.

    Returns a multi-line string like:
        可复用社区 Skill 列表:
        - pptx: 创建 Word 文档 (.docx)...
        - meeting: ...
    """
    skills_map = scan_skills(config)
    if not skills_map:
        return "可复用社区 Skill 列表: 无（缓存目录为空或未克隆）"

    lines = ["可复用社区 Skill 列表（优先复用，不要自行设计 SKILL.md）:"]
    for name, match in sorted(skills_map.items()):
        desc = match.description or "(无描述)"
        # Truncate long descriptions for prompt readability
        if len(desc) > 120:
            desc = desc[:117] + "..."
        lines.append(f"- {name}: {desc}")
    return "\n".join(lines)


__all__ = [
    "SkillMatch",
    "ensure_skill_sources",
    "scan_skills",
    "copy_skill_to_extension",
    "community_skill_cache_skill_dirs",
    "format_community_skill_list",
]
