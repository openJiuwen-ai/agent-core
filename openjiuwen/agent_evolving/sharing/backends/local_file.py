# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""LocalFileBackend: simulated experience hub backed by the local filesystem.

Layout under ``hub_path`` (default ``~/.openjiuwen/experience_hub``):

    packages/<skill_id>/skill.tar.gz   - single immutable skill package
    packages/<skill_id>/meta.json      - skill metadata (name, description)
    bundles/<skill_id>/sb_xxx.json     - experience bundles
    index/<skill_id>.jsonl             - per-skill bundle keyword index
    index/global.jsonl                 - global skill search index
    .outbox/<skill_id>/sb_xxx.json     - failed bundle uploads

Partitions use stable ``skill_id`` from SKILL.md frontmatter.  Remote keeps
only the first uploaded skill package; later uploads append experience only.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import List, Optional

from openjiuwen.agent_evolving.sharing.backends.base import SharingBackend
from openjiuwen.agent_evolving.sharing.types import (
    QueryKeywords,
    SharedSkillBundle,
    SkillPackageMeta,
    SkillSearchResult,
    UploadResult,
)
from openjiuwen.core.common.logging import logger

_DEFAULT_HUB_PATH = "~/.openjiuwen/experience_hub"
_GLOBAL_INDEX = "global.jsonl"


def _expand(path: str | os.PathLike) -> Path:
    return Path(os.path.expanduser(str(path))).resolve()


def _jaccard(a: List[str], b: List[str]) -> float:
    set_a = {item.lower() for item in a if item}
    set_b = {item.lower() for item in b if item}
    if not set_a and not set_b:
        return 0.0
    intersection = set_a & set_b
    if intersection:
        return len(intersection) / len(set_a | set_b)

    for kw_a in set_a:
        for kw_b in set_b:
            if kw_a in kw_b or kw_b in kw_a:
                return 0.5 / max(len(set_a | set_b), 1)
    return 0.0


class LocalFileBackend(SharingBackend):
    """Local-filesystem implementation of :class:`SharingBackend`."""

    def __init__(
        self,
        hub_path: Optional[str | os.PathLike] = None,
        dedup_jaccard_threshold: float = 0.85,
    ) -> None:
        self._hub_path = _expand(hub_path) if hub_path else _expand(_DEFAULT_HUB_PATH)
        self._packages_dir = self._hub_path / "packages"
        self._bundles_dir = self._hub_path / "bundles"
        self._index_dir = self._hub_path / "index"
        self._outbox_dir = self._hub_path / ".outbox"
        self._dedup_jaccard_threshold = dedup_jaccard_threshold
        self._lock = asyncio.Lock()

    @property
    def hub_path(self) -> Path:
        return self._hub_path

    @property
    def outbox_dir(self) -> Path:
        return self._outbox_dir

    def _package_dir(self, skill_id: str) -> Path:
        return self._packages_dir / skill_id

    def _package_archive(self, skill_id: str) -> Path:
        return self._package_dir(skill_id) / "skill.tar.gz"

    def _package_meta_path(self, skill_id: str) -> Path:
        return self._package_dir(skill_id) / "meta.json"

    def _bundle_dir(self, skill_id: str) -> Path:
        return self._bundles_dir / skill_id

    def _index_path(self, skill_id: str) -> Path:
        return self._index_dir / f"{skill_id}.jsonl"

    def _global_index_path(self) -> Path:
        return self._index_dir / _GLOBAL_INDEX

    def _outbox_skill_dir(self, skill_id: str) -> Path:
        return self._outbox_dir / skill_id

    def _duplicate_rejection_reason(
        self,
        skill_id: str,
        keywords: List[str],
    ) -> Optional[str]:
        if not keywords:
            return None
        for entry in self._read_index(skill_id):
            existing = entry.get("keywords", []) or []
            score = _jaccard(keywords, existing)
            if score >= self._dedup_jaccard_threshold:
                existing_id = entry.get("bundle_id", "?")
                return (
                    f"keywords overlap existing bundle {existing_id} "
                    f"(jaccard={score:.2f}, threshold={self._dedup_jaccard_threshold:.2f})"
                )
        return None

    async def upload_bundle(self, bundle: SharedSkillBundle) -> UploadResult:
        skill_id = (bundle.skill_id or "").strip()
        if not skill_id:
            return UploadResult(ok=False, reason="bundle.skill_id is required for upload")

        async with self._lock:
            duplicate_reason = self._duplicate_rejection_reason(
                skill_id,
                bundle.keywords_aggregate,
            )
            if duplicate_reason is not None:
                logger.info(
                    "[LocalFileBackend] rejected bundle %s for skill=%s id=%s: %s",
                    bundle.bundle_id,
                    bundle.skill_name,
                    skill_id,
                    duplicate_reason,
                )
                return UploadResult(ok=False, reason=duplicate_reason)

            try:
                self._bundle_dir(skill_id).mkdir(parents=True, exist_ok=True)
                self._index_dir.mkdir(parents=True, exist_ok=True)
                bundle_file = self._bundle_dir(skill_id) / f"{bundle.bundle_id}.json"
                bundle_file.write_text(
                    json.dumps(bundle.to_dict(), ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                index_line = json.dumps(
                    {
                        "bundle_id": bundle.bundle_id,
                        "skill_id": skill_id,
                        "skill_name": bundle.skill_name,
                        "skill_version": bundle.skill_version,
                        "keywords": bundle.keywords_aggregate,
                        "summary": bundle.summary_aggregate,
                        "created_at": bundle.created_at,
                    },
                    ensure_ascii=False,
                )
                with self._index_path(skill_id).open("a", encoding="utf-8") as handle:
                    handle.write(index_line + "\n")
                self._upsert_global_index(skill_id, bundle)
            except OSError as exc:
                logger.warning(
                    "[LocalFileBackend] upload failed for skill_id=%s bundle=%s: %s; routing to outbox",
                    skill_id,
                    bundle.bundle_id,
                    exc,
                )
                self._spool_to_outbox(bundle)
                return UploadResult(ok=False, reason=str(exc), retryable=True)

        logger.info(
            "[LocalFileBackend] uploaded bundle %s for skill=%s id=%s (%d experience(s))",
            bundle.bundle_id,
            bundle.skill_name,
            skill_id,
            len(bundle.experiences),
        )
        return UploadResult(ok=True, bundle_id=bundle.bundle_id)

    def _upsert_global_index(self, skill_id: str, bundle: SharedSkillBundle) -> None:
        entries = self._read_global_index()
        merged_keywords = list(bundle.keywords_aggregate)
        experience_count = 1
        skill_name = bundle.skill_name
        description = ""
        for entry in entries:
            if entry.get("skill_id") != skill_id:
                continue
            for kw in entry.get("keywords", []) or []:
                if kw and kw not in merged_keywords:
                    merged_keywords.append(kw)
            experience_count = int(entry.get("experience_count", 0) or 0) + 1
            skill_name = entry.get("skill_name") or skill_name
            description = entry.get("description") or description
            entries.remove(entry)
            break

        meta = self._read_package_meta(skill_id)
        if meta is not None:
            skill_name = meta.skill_name or skill_name
            description = meta.description or description

        entries.append(
            {
                "skill_id": skill_id,
                "skill_name": skill_name,
                "description": description,
                "keywords": merged_keywords,
                "experience_count": experience_count,
                "updated_at": bundle.created_at,
            }
        )
        self._write_global_index(entries)

    def _spool_to_outbox(self, bundle: SharedSkillBundle) -> None:
        skill_id = (bundle.skill_id or "").strip()
        if not skill_id:
            logger.error(
                "[LocalFileBackend] cannot spool bundle=%s to outbox without skill_id",
                bundle.bundle_id,
            )
            return
        try:
            self._outbox_skill_dir(skill_id).mkdir(parents=True, exist_ok=True)
            outbox_file = self._outbox_skill_dir(skill_id) / f"{bundle.bundle_id}.json"
            outbox_file.write_text(
                json.dumps(bundle.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.error(
                "[LocalFileBackend] outbox spool also failed for bundle=%s: %s",
                bundle.bundle_id,
                exc,
            )

    async def download_bundles(
        self,
        skill_id: str,
        query: QueryKeywords,
        top_k: int = 3,
    ) -> List[SharedSkillBundle]:
        resolved_id = (skill_id or "").strip()
        if not resolved_id:
            return []

        index_entries = self._read_index(resolved_id)
        if not index_entries:
            logger.info(
                "[LocalFileBackend] download_bundles: no index entries for skill_id=%s",
                resolved_id,
            )
            return []

        ranked: List[tuple[float, dict]] = []
        for entry in index_entries:
            score = _jaccard(query.keywords, entry.get("keywords", []) or [])
            ranked.append((score, entry))

        ranked.sort(key=lambda item: item[0], reverse=True)

        results: List[SharedSkillBundle] = []
        for score, entry in ranked[: max(top_k, 0)]:
            if score <= 0.0:
                continue
            bundle = self._load_bundle(resolved_id, entry.get("bundle_id", ""))
            if bundle is not None:
                logger.info(
                    "[LocalFileBackend] selected bundle=%s for skill_id=%s score=%.4f",
                    bundle.bundle_id,
                    resolved_id,
                    score,
                )
                results.append(bundle)
        return results

    async def has_skill_package(self, skill_id: str) -> bool:
        resolved_id = (skill_id or "").strip()
        if not resolved_id:
            return False
        return self._package_archive(resolved_id).is_file()

    async def upload_skill_package(
        self,
        skill_id: str,
        package_bytes: bytes,
        meta: SkillPackageMeta,
    ) -> None:
        resolved_id = (skill_id or "").strip()
        if not resolved_id:
            raise ValueError("skill_id is required for upload_skill_package")
        if not package_bytes:
            raise ValueError("package_bytes is empty")

        async with self._lock:
            if self._package_archive(resolved_id).is_file():
                logger.debug(
                    "[LocalFileBackend] skill package already exists for skill_id=%s; skipping upload",
                    resolved_id,
                )
                return

            package_dir = self._package_dir(resolved_id)
            try:
                package_dir.mkdir(parents=True, exist_ok=True)
                self._package_archive(resolved_id).write_bytes(package_bytes)
                meta_payload = SkillPackageMeta(
                    skill_id=resolved_id,
                    skill_name=meta.skill_name,
                    description=meta.description,
                    uploaded_at=meta.uploaded_at,
                )
                self._package_meta_path(resolved_id).write_text(
                    json.dumps(meta_payload.to_dict(), ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                self._ensure_global_index_entry(resolved_id, meta_payload)
            except OSError as exc:
                logger.warning(
                    "[LocalFileBackend] upload_skill_package failed for skill_id=%s: %s",
                    resolved_id,
                    exc,
                )
                raise

        logger.info(
            "[LocalFileBackend] uploaded skill package for skill_id=%s name=%s (%d bytes)",
            resolved_id,
            meta.skill_name,
            len(package_bytes),
        )

    def _ensure_global_index_entry(self, skill_id: str, meta: SkillPackageMeta) -> None:
        entries = self._read_global_index()
        for entry in entries:
            if entry.get("skill_id") == skill_id:
                entry["skill_name"] = meta.skill_name or entry.get("skill_name", "")
                entry["description"] = meta.description or entry.get("description", "")
                self._write_global_index(entries)
                return
        entries.append(
            {
                "skill_id": skill_id,
                "skill_name": meta.skill_name,
                "description": meta.description,
                "keywords": [],
                "experience_count": 0,
                "updated_at": meta.uploaded_at,
            }
        )
        self._write_global_index(entries)

    async def download_skill_package(self, skill_id: str) -> Optional[bytes]:
        resolved_id = (skill_id or "").strip()
        if not resolved_id:
            return None
        archive = self._package_archive(resolved_id)
        if not archive.is_file():
            return None
        return archive.read_bytes()

    async def get_skill_package_meta(self, skill_id: str) -> Optional[SkillPackageMeta]:
        return self._read_package_meta((skill_id or "").strip())

    def _read_package_meta(self, skill_id: str) -> Optional[SkillPackageMeta]:
        if not skill_id:
            return None
        meta_path = self._package_meta_path(skill_id)
        if not meta_path.is_file():
            return None
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("[LocalFileBackend] meta read failed for %s: %s", skill_id, exc)
            return None
        return SkillPackageMeta.from_dict(data)

    async def search_skills(
        self,
        query: QueryKeywords,
        top_k: int = 5,
    ) -> List[SkillSearchResult]:
        entries = self._read_global_index()
        if not entries:
            return []

        ranked: List[tuple[float, dict]] = []
        for entry in entries:
            keywords = list(entry.get("keywords", []) or [])
            skill_name = str(entry.get("skill_name", "") or "")
            description = str(entry.get("description", "") or "")
            search_terms = keywords + [skill_name, description]
            score = _jaccard(query.keywords, search_terms)
            ranked.append((score, entry))

        ranked.sort(key=lambda item: item[0], reverse=True)

        results: List[SkillSearchResult] = []
        for score, entry in ranked[: max(top_k, 0)]:
            if score <= 0.0:
                continue
            results.append(
                SkillSearchResult(
                    skill_id=str(entry.get("skill_id", "") or ""),
                    skill_name=str(entry.get("skill_name", "") or ""),
                    description=str(entry.get("description", "") or ""),
                    experience_count=int(entry.get("experience_count", 0) or 0),
                    keywords=list(entry.get("keywords", []) or []),
                    score=score,
                )
            )
        return results

    def _read_index(self, skill_id: str) -> List[dict]:
        index_path = self._index_path(skill_id)
        if not index_path.is_file():
            return []
        entries: List[dict] = []
        try:
            with index_path.open("r", encoding="utf-8") as handle:
                for line_no, line in enumerate(handle, start=1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError as exc:
                        logger.warning(
                            "[LocalFileBackend] index corrupt at %s:%d (%s); skipping line",
                            index_path,
                            line_no,
                            exc,
                        )
        except OSError as exc:
            logger.warning("[LocalFileBackend] index read failed for %s: %s", skill_id, exc)
        return entries

    def _read_global_index(self) -> List[dict]:
        path = self._global_index_path()
        if not path.is_file():
            return []
        entries: List[dict] = []
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError as exc:
            logger.warning("[LocalFileBackend] global index read failed: %s", exc)
        return entries

    def _write_global_index(self, entries: List[dict]) -> None:
        self._index_dir.mkdir(parents=True, exist_ok=True)
        lines = [json.dumps(entry, ensure_ascii=False) for entry in entries]
        self._global_index_path().write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")

    def _load_bundle(self, skill_id: str, bundle_id: str) -> Optional[SharedSkillBundle]:
        if not bundle_id:
            return None
        bundle_file = self._bundle_dir(skill_id) / f"{bundle_id}.json"
        if not bundle_file.is_file():
            logger.debug("[LocalFileBackend] bundle file missing: %s", bundle_file)
            return None
        try:
            data = json.loads(bundle_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("[LocalFileBackend] bundle load failed for %s: %s", bundle_file, exc)
            return None
        try:
            return SharedSkillBundle.from_dict(data)
        except Exception as exc:  # noqa: BLE001 - resilience boundary
            logger.warning("[LocalFileBackend] bundle decode failed for %s: %s", bundle_file, exc)
            return None


__all__ = ["LocalFileBackend"]
