# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Fetch GitCode pull request templates for auto-harness PR drafts."""

from functools import lru_cache
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Sequence

from gitcode_api import AsyncGitCode
from gitcode_api._models import RepositoryGitCodeTemplate

if TYPE_CHECKING:
    from openjiuwen.auto_harness.schema import AutoHarnessConfig

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
_TEMPLATE_SUFFIX_BY_LANGUAGE = {
    "en": ".en",
    "english": ".en",
    "cn": ".zh-CN",
    "zh": ".zh-CN",
    "zh-cn": ".zh-CN",
    "zh_cn": ".zh-CN",
}
_FALLBACK_SUFFIXES = (".zh-CN.md", ".en", "")


@lru_cache(maxsize=1)
def load_pr_template_fallback() -> str:
    """Return the bundled PR template used when GitCode API is unavailable."""
    return (_PROMPTS_DIR / "pr_draft_template_fallback.md").read_text(encoding="utf-8")


@lru_cache(maxsize=3)
def template_suffix_for_language(language: str) -> str:
    """Map auto-harness language config to a GitCode template filename suffix."""
    normalized = language.strip().lower()
    return _TEMPLATE_SUFFIX_BY_LANGUAGE.get(
        normalized,
        ".zh-CN.md",
    )


def pick_pr_template_entry(
    templates: Sequence[RepositoryGitCodeTemplate],
    preferred_suffix: str,
) -> Optional[RepositoryGitCodeTemplate]:
    """Pick the best template metadata object from a GitCode list response."""
    if not templates:
        return None

    def _matches_suffix(entry: RepositoryGitCodeTemplate, suffix: str) -> bool:
        path = (entry.path or "").removesuffix(".md")
        return path.endswith(suffix) or path.endswith(suffix[:3])

    for suffix in [preferred_suffix, *_FALLBACK_SUFFIXES]:
        for entry in templates:
            if _matches_suffix(entry, suffix):
                return entry
    return templates[0]


async def fetch_pr_template(config: "AutoHarnessConfig") -> str:
    """Fetch the upstream PR template text for the configured repository.

    Uses ``AsyncGitCode.pulls.list_templates`` and ``get_template``,
    following the same flow as ``get_pr_template.py``. Falls back to the
    bundled template when the token is missing or the API call fails.
    """
    api_key = config.resolve_gitcode_token()
    if not api_key:
        logger.warning("No GitCode token configured; using bundled PR template")
        return load_pr_template_fallback()

    owner = config.upstream_owner
    repo = config.upstream_repo
    preferred_suffix = template_suffix_for_language(config.language)

    try:
        client = AsyncGitCode(api_key=api_key)
        templates = await client.pulls.list_templates(
            owner=owner,
            repo=repo,
        )
        selected = pick_pr_template_entry(
            templates,
            preferred_suffix,
        )
        if selected is None:
            logger.warning(
                "No PR templates returned for %s/%s; using fallback",
                owner,
                repo,
            )
            return load_pr_template_fallback()

        path = selected.path
        if not path:
            logger.warning(
                "PR template entry missing path for %s/%s; using fallback",
                owner,
                repo,
            )
            return load_pr_template_fallback()

        template_owner = selected.template_owner or owner
        template_repo = selected.template_repo or repo
        text = await client.pulls.get_template(
            path=path,
            owner=template_owner,
            repo=template_repo,
        )
        if not str(text).strip():
            logger.warning(
                "Empty PR template for %s/%s at %s; using fallback",
                template_owner,
                template_repo,
                path,
            )
            return load_pr_template_fallback()
        logger.info(
            "Loaded GitCode PR template %s from %s/%s",
            path,
            template_owner,
            template_repo,
        )
        return str(text)
    except Exception as exc:
        logger.warning(
            "Failed to fetch GitCode PR template for %s/%s: %s",
            owner,
            repo,
            exc,
        )
        return load_pr_template_fallback()
