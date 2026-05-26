# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""ExperienceSharer: upload / download facade above a SharingBackend.

Responsibilities:
- Maintain a per-skill in-memory queue of experiences pending upload.
- On flush: ensure ``skill_id``, upload the initial skill package once, then
  upload experience bundles.
- Download bundles and search skills by ``skill_id``.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Awaitable, Callable, Dict, List, Optional, Tuple

from openjiuwen.agent_evolving.sharing.backends.base import SharingBackend
from openjiuwen.agent_evolving.sharing.types import (
    QueryKeywords,
    SharedExperience,
    SharedSkillBundle,
    SkillPackageMeta,
    SkillSearchResult,
    UploadResult,
)
from openjiuwen.core.common.logging import logger

_DEFAULT_UPLOAD_RETRIES = 3
_DEFAULT_BACKOFF_SECS = 0.5

SkillSharingContextProvider = Callable[[str], Awaitable[Tuple[str, bytes, str, str]]]
# Returns (skill_id, package_bytes, skill_name, description)


class ExperienceSharer:
    """Skill-scoped facade for the sharing path."""

    def __init__(
        self,
        backend: SharingBackend,
        local_cache_dir: Optional[str | os.PathLike] = None,
        max_upload_retries: int = _DEFAULT_UPLOAD_RETRIES,
        backoff_base_secs: float = _DEFAULT_BACKOFF_SECS,
        skill_sharing_context_provider: Optional[SkillSharingContextProvider] = None,
    ) -> None:
        self._backend = backend
        self._local_cache_dir = self._expand(local_cache_dir)
        self._max_upload_retries = max(max_upload_retries, 1)
        self._backoff_base_secs = max(backoff_base_secs, 0.0)
        self._pending_uploads: Dict[str, List[SharedExperience]] = {}
        self._pending_keys: Dict[str, set[Tuple[str, str]]] = {}
        self._lock = asyncio.Lock()
        self._skill_sharing_context_provider = skill_sharing_context_provider

    @staticmethod
    def _expand(path: Optional[str | os.PathLike]) -> Optional[Path]:
        if path is None:
            return None
        return Path(os.path.expanduser(str(path))).resolve()

    def set_skill_sharing_context_provider(
        self,
        provider: Optional[SkillSharingContextProvider],
    ) -> None:
        """Late-bind the skill package provider after construction."""
        self._skill_sharing_context_provider = provider

    @property
    def backend(self) -> SharingBackend:
        return self._backend

    @property
    def local_cache_dir(self) -> Optional[Path]:
        return self._local_cache_dir

    async def resolve_skill_id(self, skill_name: str) -> str:
        """Return ``skill_id`` for a local skill, ensuring frontmatter when possible."""
        provider = self._skill_sharing_context_provider
        if provider is None or not skill_name:
            return ""
        try:
            skill_id, _, _, _ = await provider(skill_name)
        except Exception as exc:  # noqa: BLE001 - resilience boundary
            logger.warning(
                "[ExperienceSharer] resolve_skill_id failed for skill=%s: %s",
                skill_name,
                exc,
            )
            return ""
        return (skill_id or "").strip()

    def has_pending(self, skill_name: str) -> bool:
        return bool(self._pending_uploads.get(skill_name))

    def stage_for_upload(self, skill_name: str, exp: SharedExperience) -> None:
        """Queue ``exp`` for later upload, deduplicating on (skill, record.id)."""
        if not skill_name or exp is None:
            return
        record_id = getattr(exp.record, "id", "") or ""
        dedup_key = (skill_name, record_id)
        keys = self._pending_keys.setdefault(skill_name, set())
        if dedup_key in keys:
            logger.debug(
                "[ExperienceSharer] stage_for_upload deduplicated skill=%s record=%s",
                skill_name,
                record_id,
            )
            return
        keys.add(dedup_key)
        self._pending_uploads.setdefault(skill_name, []).append(exp)
        logger.debug(
            "[ExperienceSharer] staged 1 experience for skill=%s (queue=%d)",
            skill_name,
            len(self._pending_uploads[skill_name]),
        )

    def discard_pending_uploads(self, skill_name: str) -> int:
        """Drop the in-memory upload queue for ``skill_name`` (negative feedback)."""
        count = len(self._pending_uploads.get(skill_name, []))
        self._pending_uploads.pop(skill_name, None)
        self._pending_keys.pop(skill_name, None)
        if count:
            logger.info(
                "[ExperienceSharer] discarded %d pending experience(s) for skill=%s",
                count,
                skill_name,
            )
        return count

    async def flush_pending_uploads(self, skill_name: str) -> UploadResult:
        """Bundle and upload every pending experience for ``skill_name``."""
        async with self._lock:
            experiences = self._pending_uploads.pop(skill_name, [])
            self._pending_keys.pop(skill_name, None)
        if not experiences:
            return UploadResult(ok=True)

        bundle = SharedSkillBundle.make(skill_name=skill_name, experiences=experiences)
        await self._sync_skill_package(bundle, skill_name)
        if not bundle.skill_id:
            reason = "skill_id unavailable"
            logger.warning(
                "[ExperienceSharer] skipping upload for skill=%s: %s",
                skill_name,
                reason,
            )
            return UploadResult(ok=False, reason=reason)

        attempt = 0
        last_result = UploadResult(ok=False, reason="upload not attempted")
        while attempt < self._max_upload_retries:
            attempt += 1
            result = await self._backend.upload_bundle(bundle)
            if result.ok:
                self._mirror_bundle(bundle, kind="uploaded")
                logger.info(
                    "[ExperienceSharer] flushed bundle %s for skill=%s id=%s after %d attempt(s)",
                    result.bundle_id or bundle.bundle_id,
                    skill_name,
                    bundle.skill_id,
                    attempt,
                )
                return result

            last_result = result
            logger.warning(
                "[ExperienceSharer] upload attempt %d/%d rejected for skill=%s id=%s: %s",
                attempt,
                self._max_upload_retries,
                skill_name,
                bundle.skill_id,
                result.reason,
            )
            if not result.retryable:
                return result
            if attempt < self._max_upload_retries and self._backoff_base_secs > 0:
                await asyncio.sleep(self._backoff_base_secs * (2 ** (attempt - 1)))

        logger.error(
            "[ExperienceSharer] giving up upload for skill=%s id=%s after %d attempts (%s)",
            skill_name,
            bundle.skill_id,
            self._max_upload_retries,
            last_result.reason,
        )
        return last_result

    async def download_relevant(
        self,
        skill_id: str,
        query: QueryKeywords,
        top_k: int = 3,
        *,
        skill_name: str = "",
    ) -> List[SharedSkillBundle]:
        """Return up to ``top_k`` relevant bundles and mirror them locally."""
        resolved_id = (skill_id or "").strip()
        if not resolved_id:
            return []

        try:
            bundles = await self._backend.download_bundles(resolved_id, query, top_k=top_k)
        except Exception as exc:  # noqa: BLE001 - resilience boundary
            logger.warning(
                "[ExperienceSharer] backend download failed for skill=%s id=%s: %s",
                skill_name or "?",
                resolved_id,
                exc,
            )
            return []

        for bundle in bundles:
            self._mirror_bundle(bundle, kind="downloaded")
        if bundles:
            logger.info(
                "[ExperienceSharer] downloaded %d bundle(s) for skill=%s id=%s (top_k=%d)",
                len(bundles),
                skill_name or bundle.skill_name or "?",
                resolved_id,
                top_k,
            )
        return bundles

    async def search_skills(
        self,
        query: QueryKeywords,
        top_k: int = 5,
    ) -> List[SkillSearchResult]:
        """Search the hub for skills relevant to ``query``."""
        try:
            return await self._backend.search_skills(query, top_k=top_k)
        except Exception as exc:  # noqa: BLE001 - resilience boundary
            logger.warning("[ExperienceSharer] search_skills failed: %s", exc)
            return []

    async def download_skill_package(self, skill_id: str) -> Optional[bytes]:
        """Download the immutable skill package stored under ``skill_id``."""
        resolved_id = (skill_id or "").strip()
        if not resolved_id:
            return None
        try:
            return await self._backend.download_skill_package(resolved_id)
        except Exception as exc:  # noqa: BLE001 - resilience boundary
            logger.warning(
                "[ExperienceSharer] download_skill_package failed for skill_id=%s: %s",
                resolved_id,
                exc,
            )
            return None

    async def get_skill_package_meta(self, skill_id: str) -> Optional[SkillPackageMeta]:
        try:
            return await self._backend.get_skill_package_meta((skill_id or "").strip())
        except Exception as exc:  # noqa: BLE001 - resilience boundary
            logger.warning(
                "[ExperienceSharer] get_skill_package_meta failed for skill_id=%s: %s",
                skill_id,
                exc,
            )
            return None

    def list_cached_bundles(self, skill_id: str) -> List[SharedSkillBundle]:
        """List bundles previously mirrored into the local download cache."""
        resolved_id = (skill_id or "").strip()
        if self._local_cache_dir is None or not resolved_id:
            return []
        skill_dir = self._local_cache_dir / "downloaded" / resolved_id
        if not skill_dir.is_dir():
            return []
        bundles: List[SharedSkillBundle] = []
        for bundle_file in sorted(skill_dir.glob("*.json")):
            try:
                data = json.loads(bundle_file.read_text(encoding="utf-8"))
                bundles.append(SharedSkillBundle.from_dict(data))
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning(
                    "[ExperienceSharer] cached bundle decode failed for %s: %s",
                    bundle_file,
                    exc,
                )
        return bundles

    async def _sync_skill_package(self, bundle: SharedSkillBundle, skill_name: str) -> None:
        provider = self._skill_sharing_context_provider
        if provider is None:
            return

        try:
            skill_id, package_bytes, resolved_name, description = await provider(skill_name)
        except Exception as exc:  # noqa: BLE001 - resilience boundary
            logger.warning(
                "[ExperienceSharer] skill_sharing_context_provider failed for skill=%s: %s",
                skill_name,
                exc,
            )
            return

        skill_id = (skill_id or "").strip()
        if not skill_id:
            logger.debug(
                "[ExperienceSharer] skill_sharing_context_provider returned empty skill_id for skill=%s",
                skill_name,
            )
            return

        bundle.skill_id = skill_id
        if resolved_name:
            bundle.skill_name = resolved_name

        try:
            already_present = await self._backend.has_skill_package(skill_id)
        except Exception as exc:  # noqa: BLE001 - resilience boundary
            logger.warning(
                "[ExperienceSharer] backend.has_skill_package failed for skill_id=%s: %s",
                skill_id,
                exc,
            )
            return

        if already_present:
            logger.debug(
                "[ExperienceSharer] hub already has skill package for skill_id=%s",
                skill_id,
            )
            return

        if not package_bytes:
            logger.warning(
                "[ExperienceSharer] empty skill package for skill=%s id=%s; skipping package upload",
                skill_name,
                skill_id,
            )
            return

        meta = SkillPackageMeta(
            skill_id=skill_id,
            skill_name=resolved_name or skill_name,
            description=description,
        )
        try:
            await self._backend.upload_skill_package(skill_id, package_bytes, meta)
            logger.info(
                "[ExperienceSharer] uploaded initial skill package for skill=%s id=%s",
                skill_name,
                skill_id,
            )
        except Exception as exc:  # noqa: BLE001 - resilience boundary
            logger.warning(
                "[ExperienceSharer] upload_skill_package failed for skill_id=%s: %s; bundle upload will continue",
                skill_id,
                exc,
            )

    def _mirror_bundle(self, bundle: SharedSkillBundle, *, kind: str) -> None:
        skill_id = (bundle.skill_id or "").strip()
        if self._local_cache_dir is None or not bundle.bundle_id or not skill_id:
            return
        if kind not in ("uploaded", "downloaded"):
            raise ValueError(f"unsupported mirror kind: {kind}")
        target_dir = self._local_cache_dir / kind / skill_id
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            target_file = target_dir / f"{bundle.bundle_id}.json"
            target_file.write_text(
                json.dumps(bundle.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning(
                "[ExperienceSharer] mirror %s failed for bundle=%s: %s",
                kind,
                bundle.bundle_id,
                exc,
            )


__all__ = ["ExperienceSharer", "SkillSharingContextProvider"]
