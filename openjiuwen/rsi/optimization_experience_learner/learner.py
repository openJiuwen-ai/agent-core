# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Optimization experience learner facade and YAML-backed implementation."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from openjiuwen.rsi.config import (
    OptimizationExperienceLearnerConfig,
)
from openjiuwen.rsi.optimization_experience_learner.schema import (
    OptimizationExperienceArtifact,
    OptimizationExperienceInput,
    OptimizationExperienceRetrievalQuery,
    OptimizationExperienceRetrievalResult,
    OptimizationExperienceStageInput,
)

_VALID_STATUSES = frozenset(
    {
        "provisional",
        "accepted",
        "rejected",
        "expired",
        "deprecated",
    }
)
_DEFAULT_RETRIEVAL_STATUSES = frozenset({"accepted"})
_SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "credential",
        "credentials",
        "password",
        "raw_trace",
        "secret",
        "token",
        "answer",
        "expected_answer",
        "gold_answer",
    }
)


class OptimizationExperienceLearner:
    """Persist and retrieve reusable, case-agnostic optimization experience."""

    def __init__(self, config: OptimizationExperienceLearnerConfig) -> None:
        self.config = config
        self._known_roots: set[Path] = set()

    async def learn(
        self,
        experience_input: OptimizationExperienceInput,
    ) -> str:
        """Write stage-level and aggregate experience records and return the ref path."""
        if not self.config.enabled:
            return ""
        store = ExperienceStore(self.config)
        artifact = store.write_experience(experience_input)
        self._known_roots.add(Path(experience_input.output_dir).expanduser().resolve())
        return artifact.experience_ref_path

    async def retrieve(
        self,
        query: OptimizationExperienceRetrievalQuery,
    ) -> OptimizationExperienceRetrievalResult:
        """Retrieve bounded reusable optimization experience for a stage."""
        if not self.config.enabled:
            return OptimizationExperienceRetrievalResult(
                query=query,
                matches=[],
                metadata={"retrieval_status": "disabled"},
            )
        roots = self._resolve_retrieval_roots(query)
        return ExperienceRetriever(roots).retrieve(query)

    async def retrieve_member_stage_experience(
        self,
        *,
        stage: str,
        eval_ref_path: str,
        analysis_result_path: str,
        harness_refs_path: str,
        target_members: list[str] | None = None,
        candidate_modules: list[str] | None = None,
    ) -> OptimizationExperienceRetrievalResult:
        """Retrieve reusable experience for member analysis or planning stages."""
        return await self.retrieve(
            OptimizationExperienceRetrievalQuery(
                optimization_type="member_harness",
                stage=stage,
                eval_ref_path=eval_ref_path,
                analysis_result_path=analysis_result_path,
                harness_refs_path=harness_refs_path,
                target_members=target_members or [],
                candidate_modules=candidate_modules or [],
                metadata={"candidate_modules": candidate_modules or []},
            )
        )

    async def record_team_skill_experience(
        self,
        *,
        before_team_skill_ref_path: str,
        after_team_skill_ref_path: str,
        eval_ref_path: str,
        candidate_dir: str,
        output_dir: str,
        score: float | None,
    ) -> str:
        """Record reusable experience from a Team Skill optimization."""
        if not self.config.enabled:
            return ""
        experience_input = OptimizationExperienceInput(
            optimization_type="team_skill",
            before_ref_path=before_team_skill_ref_path,
            after_ref_path=after_team_skill_ref_path,
            eval_ref_path=eval_ref_path,
            output_dir=output_dir,
            stages=[
                OptimizationExperienceStageInput(
                    stage="evaluation_feedback_analysis",
                    source_artifact_paths=[
                        str(Path(candidate_dir) / "optimization_metadata.yaml"),
                    ],
                    summary=("Extract reusable signals for diagnosing Team Skill instruction and workflow issues."),
                    metadata={"accepted_score": score, "learning_status": "provisional"},
                ),
                OptimizationExperienceStageInput(
                    stage="candidate_planning",
                    source_artifact_paths=[
                        str(Path(candidate_dir) / "optimization_plan.yaml"),
                    ],
                    summary=("Extract reusable planning patterns for Team Skill instruction updates."),
                    metadata={"learning_status": "provisional"},
                ),
            ],
            metadata={
                "candidate_dir": candidate_dir,
                "learning_status": "provisional",
            },
        )
        return await self.learn(experience_input)

    async def record_member_experience(
        self,
        *,
        before_harness_refs_path: str,
        after_harness_refs_path: str,
        eval_ref_path: str,
        member_optimization_ref_path: str,
        analysis_result_path: str,
        plan_path: str,
        execution_result_path: str,
        verification_path: str,
        fix_result_path: str,
        output_dir: str,
        role: str,
    ) -> str:
        """Record reusable experience from a member harness optimization."""
        if not self.config.enabled:
            return ""
        experience_input = OptimizationExperienceInput(
            optimization_type="member_harness",
            before_ref_path=before_harness_refs_path,
            after_ref_path=after_harness_refs_path,
            eval_ref_path=eval_ref_path,
            output_dir=output_dir,
            role=role,
            stages=[
                OptimizationExperienceStageInput(
                    stage="evaluation_result_analysis",
                    source_artifact_paths=[analysis_result_path],
                    summary=(
                        "Extract reusable patterns for mapping evaluation symptoms to member-level improvement targets."
                    ),
                    metadata={"learning_status": "provisional"},
                ),
                OptimizationExperienceStageInput(
                    stage="optimization_planning",
                    source_artifact_paths=[plan_path],
                    summary=(
                        "Extract reusable action planning patterns, dependency ordering, and constraint handling."
                    ),
                    metadata={"learning_status": "provisional"},
                ),
                OptimizationExperienceStageInput(
                    stage="implementation_and_verification",
                    source_artifact_paths=[
                        execution_result_path,
                        verification_path,
                        fix_result_path,
                    ],
                    summary=("Extract reusable validation and repair patterns from accepted harness changes."),
                    metadata={"learning_status": "provisional"},
                ),
            ],
            metadata={
                "member_optimization_ref_path": member_optimization_ref_path,
                "learning_status": "provisional",
            },
        )
        return await self.learn(experience_input)

    async def update_experience_status(
        self,
        experience_ref_path: str,
        status: str,
        *,
        reason: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Update an experience status across ref, stages, and index."""
        if not self.config.enabled or not experience_ref_path:
            return ""
        ExperienceStore(self.config).update_status(
            experience_ref_path=experience_ref_path,
            status=status,
            reason=reason,
            metadata=metadata or {},
        )
        return experience_ref_path

    async def promote_experience(
        self,
        experience_ref_path: str,
        *,
        reason: str = "epoch_full_evaluation_confirmed",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Mark a provisional experience as accepted."""
        return await self.update_experience_status(
            experience_ref_path,
            "accepted",
            reason=reason,
            metadata=metadata,
        )

    async def reject_experience(
        self,
        experience_ref_path: str,
        *,
        reason: str = "epoch_full_evaluation_rejected",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Mark an experience as rejected."""
        return await self.update_experience_status(
            experience_ref_path,
            "rejected",
            reason=reason,
            metadata=metadata,
        )

    def _resolve_retrieval_roots(
        self,
        query: OptimizationExperienceRetrievalQuery,
    ) -> list[Path]:
        roots: list[Path] = []
        explicit_root = str(query.metadata.get("experience_root", "") or "")
        if explicit_root:
            roots.append(Path(explicit_root).expanduser().resolve())
        roots.extend(sorted(self._known_roots))
        deduped: list[Path] = []
        seen: set[Path] = set()
        for root in roots:
            if root not in seen:
                deduped.append(root)
                seen.add(root)
        return deduped


class ExperienceStore:
    """YAML-backed storage for optimization experiences."""

    def __init__(self, config: OptimizationExperienceLearnerConfig) -> None:
        self.config = config

    def write_experience(
        self,
        experience_input: OptimizationExperienceInput,
    ) -> OptimizationExperienceArtifact:
        output_root = Path(experience_input.output_dir).expanduser().resolve()
        output_root.mkdir(parents=True, exist_ok=True)
        experience_dir = _allocate_experience_dir(
            output_root,
            experience_input.optimization_type,
        )
        created_at = datetime.now(UTC).astimezone().isoformat()
        extractor = ExperienceExtractor()
        learning_status = _status_value(
            experience_input.metadata.get("learning_status"),
            default="provisional",
        )
        stage_payloads = [
            extractor.extract_stage(
                stage_input=stage,
                default_learning_status=learning_status,
            )
            for stage in experience_input.stages
        ]
        stage_paths = _write_stage_records(experience_dir, stage_payloads)
        ref_path = experience_dir / self.config.output_filename
        artifact = OptimizationExperienceArtifact(
            experience_id=experience_dir.name,
            optimization_type=experience_input.optimization_type,
            output_dir=str(experience_dir),
            experience_ref_path=str(ref_path),
            stage_experience_paths=stage_paths,
            role=experience_input.role,
            metadata={
                **_sanitize_mapping(experience_input.metadata),
                "learning_status": learning_status,
                "created_at": created_at,
                "before_ref_path": experience_input.before_ref_path,
                "after_ref_path": experience_input.after_ref_path,
                "eval_ref_path": experience_input.eval_ref_path,
                "model_config_ref": self.config.model_config_ref,
            },
        )
        _write_yaml(ref_path, asdict(artifact))
        self._append_index(output_root, artifact, stage_payloads, stage_paths)
        return artifact

    @staticmethod
    def update_status(
        *,
        experience_ref_path: str,
        status: str,
        reason: str,
        metadata: dict[str, Any],
    ) -> None:
        new_status = _status_value(status, default="provisional")
        ref_path = Path(experience_ref_path).expanduser().resolve()
        ref = _read_yaml(ref_path)
        ref_metadata = dict(ref.get("metadata", {}))
        ref_metadata.update(_sanitize_mapping(metadata))
        ref_metadata["learning_status"] = new_status
        ref_metadata["status_updated_at"] = datetime.now(UTC).astimezone().isoformat()
        if reason:
            ref_metadata["status_reason"] = reason
        ref["metadata"] = ref_metadata
        _write_yaml(ref_path, ref)

        for stage_path in _string_list(ref.get("stage_experience_paths")):
            payload_path = Path(stage_path).expanduser().resolve()
            payload = _read_yaml(payload_path)
            experience = dict(payload.get("experience", {}))
            experience["learning_status"] = new_status
            payload["experience"] = experience
            stage_metadata = dict(payload.get("metadata", {}))
            stage_metadata["learning_status"] = new_status
            if reason:
                stage_metadata["status_reason"] = reason
            payload["metadata"] = stage_metadata
            _write_yaml(payload_path, payload)

        index_path = ref_path.parent.parent / "index.yaml"
        index = _read_index(index_path)
        for item in index["experiences"]:
            if item.get("experience_ref_path") == str(ref_path):
                item["learning_status"] = new_status
                if reason:
                    item["status_reason"] = reason
        _write_yaml(index_path, index)

    @staticmethod
    def _append_index(
        output_root: Path,
        artifact: OptimizationExperienceArtifact,
        stage_payloads: list[dict[str, Any]],
        stage_paths: list[str],
    ) -> None:
        index_path = output_root / "index.yaml"
        index = _read_index(index_path)
        for payload, stage_path in zip(stage_payloads, stage_paths, strict=True):
            experience = dict(payload.get("experience", {}))
            signature = dict(experience.get("problem_signature", {}))
            metadata = dict(payload.get("metadata", {}))
            index["experiences"].append(
                {
                    "experience_id": artifact.experience_id,
                    "optimization_type": artifact.optimization_type,
                    "role": artifact.role,
                    "stage": payload.get("stage", ""),
                    "learning_status": experience.get("learning_status", ""),
                    "component_layer": signature.get("component_layer", ""),
                    "failure_signature": signature.get("failure_signature", ""),
                    "mechanism_type": signature.get("mechanism_type", ""),
                    "target_ref": signature.get("target_ref", ""),
                    "tags": _string_list(metadata.get("tags")),
                    "confidence": experience.get("confidence", "medium"),
                    "summary": payload.get("summary", ""),
                    "experience_ref_path": artifact.experience_ref_path,
                    "stage_experience_path": stage_path,
                    "created_at": artifact.metadata.get("created_at", ""),
                }
            )
        _write_yaml(index_path, index)


class ExperienceExtractor:
    """Extract bounded, case-agnostic experience from stage refs."""

    def extract_stage(
        self,
        *,
        stage_input: OptimizationExperienceStageInput,
        default_learning_status: str,
    ) -> dict[str, Any]:
        source_summaries = [self._extract_source_summary(path) for path in stage_input.source_artifact_paths if path]
        merged = _merge_dicts(source_summaries)
        metadata = _sanitize_mapping(stage_input.metadata)
        status = _status_value(
            metadata.get("learning_status") or merged.get("learning_status"),
            default=default_learning_status,
        )
        failure_signature = _first_text(
            metadata.get("failure_signature"),
            merged.get("failure_signature"),
        )
        mechanism_type = _first_text(
            metadata.get("mechanism_type"),
            merged.get("mechanism_type"),
        )
        component_layer = _first_text(
            metadata.get("component_layer"),
            merged.get("component_layer"),
            merged.get("action_group"),
        )
        target_ref = _first_text(metadata.get("target_ref"), merged.get("target_ref"))
        summary = _first_text(stage_input.summary, merged.get("summary"))
        general_principles = _string_list(metadata.get("general_principles")) or _string_list(
            merged.get("general_principles")
        )
        anti_patterns = _string_list(metadata.get("anti_patterns")) or _string_list(merged.get("anti_patterns"))
        applicable_conditions = _string_list(metadata.get("applicable_conditions"))
        negative_conditions = _string_list(metadata.get("negative_conditions"))

        experience: dict[str, Any] = {
            "scope": "case_agnostic",
            "learning_status": status,
            "problem_signature": {
                "failure_signature": failure_signature,
                "mechanism_type": mechanism_type,
                "component_layer": component_layer,
                "target_ref": target_ref,
            },
            "general_principles": general_principles,
            "anti_patterns": anti_patterns,
            "applicable_conditions": applicable_conditions,
            "negative_conditions": negative_conditions,
            "evidence_refs": list(stage_input.source_artifact_paths),
            "confidence": _first_text(metadata.get("confidence"), merged.get("confidence"), "medium"),
        }
        return {
            "stage": stage_input.stage,
            "source_artifact_paths": list(stage_input.source_artifact_paths),
            "summary": summary,
            "metadata": metadata,
            "experience": experience,
        }

    @staticmethod
    def _extract_source_summary(source_artifact_path: str) -> dict[str, Any]:
        path = Path(source_artifact_path).expanduser()
        if not path.is_file():
            return {}
        data = _read_structured(path)
        if not isinstance(data, dict):
            return {}
        data = _sanitize_mapping(data)
        issue = _first_mapping(data.get("issues"))
        action = _first_mapping(data.get("actions"))
        verification = _first_mapping(data.get("verification"))
        return {
            "failure_signature": _first_text(
                issue.get("failure_signature"),
                issue.get("category"),
                data.get("failure_signature"),
            ),
            "mechanism_type": _first_text(
                issue.get("mechanism_type"),
                issue.get("mechanism"),
                data.get("mechanism_type"),
            ),
            "target_ref": _first_text(
                issue.get("target_ref"),
                issue.get("suspected_team_scope"),
                action.get("role"),
                data.get("target_ref"),
            ),
            "component_layer": _first_text(
                issue.get("component_layer"),
                action.get("action_group"),
                data.get("component_layer"),
            ),
            "action_group": _first_text(action.get("action_group")),
            "summary": _first_text(issue.get("summary"), data.get("summary")),
            "confidence": _first_text(issue.get("confidence"), verification.get("confidence")),
            "general_principles": _string_list(data.get("general_principles")),
            "anti_patterns": _string_list(data.get("anti_patterns")),
        }


class ExperienceRetriever:
    """Index-backed deterministic experience retrieval."""

    def __init__(self, roots: list[Path]) -> None:
        self.roots = roots

    def retrieve(
        self,
        query: OptimizationExperienceRetrievalQuery,
    ) -> OptimizationExperienceRetrievalResult:
        if not self.roots:
            return OptimizationExperienceRetrievalResult(
                query=query,
                matches=[],
                metadata={"retrieval_status": "empty", "searched_roots": []},
            )
        allowed_statuses = _allowed_statuses(query)
        entries = self._load_entries()
        filtered = [entry for entry in entries if _entry_matches_query(entry, query, allowed_statuses)]
        ranked = sorted(
            filtered,
            key=lambda item: (
                _confidence_score(str(item.get("confidence", ""))),
                str(item.get("created_at", "")),
            ),
            reverse=True,
        )
        limit = max(query.limit, 0)
        summary_budget = int(query.metadata.get("summary_char_budget", 1200) or 1200)
        matches = [_bounded_match(entry, summary_budget=summary_budget) for entry in ranked[:limit]]
        return OptimizationExperienceRetrievalResult(
            query=query,
            matches=matches,
            metadata={
                "retrieval_status": "ok",
                "searched_roots": [str(root) for root in self.roots],
                "matched_count": len(filtered),
                "returned_count": len(matches),
                "allowed_statuses": sorted(allowed_statuses),
            },
        )

    def _load_entries(self) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for root in self.roots:
            index = _read_index(root / "index.yaml")
            for entry in index.get("experiences", []):
                if isinstance(entry, dict):
                    entries.append(entry)
        return entries


def _entry_matches_query(
    entry: dict[str, Any],
    query: OptimizationExperienceRetrievalQuery,
    allowed_statuses: set[str],
) -> bool:
    if entry.get("learning_status") not in allowed_statuses:
        return False
    if entry.get("optimization_type") != query.optimization_type:
        return False
    if query.stage and entry.get("stage") != query.stage:
        return False
    if query.target_members and entry.get("role") not in set(query.target_members):
        return False
    candidate_modules = set(query.candidate_modules or _string_list(query.metadata.get("candidate_modules")))
    if candidate_modules:
        component = str(entry.get("component_layer", "") or "")
        if component not in candidate_modules:
            return False
    failure_signature = str(query.metadata.get("failure_signature", "") or "")
    if failure_signature and entry.get("failure_signature") != failure_signature:
        return False
    mechanism_type = str(query.metadata.get("mechanism_type", "") or "")
    if mechanism_type and entry.get("mechanism_type") != mechanism_type:
        return False
    return True


def _bounded_match(entry: dict[str, Any], *, summary_budget: int) -> dict[str, Any]:
    stage_path = Path(str(entry.get("stage_experience_path", ""))).expanduser()
    payload = _read_yaml(stage_path)
    experience = dict(payload.get("experience", {}))
    summary = _truncate(str(payload.get("summary", entry.get("summary", ""))), summary_budget)
    return {
        "experience_id": entry.get("experience_id", ""),
        "optimization_type": entry.get("optimization_type", ""),
        "role": entry.get("role", ""),
        "stage": entry.get("stage", ""),
        "learning_status": entry.get("learning_status", ""),
        "component_layer": entry.get("component_layer", ""),
        "failure_signature": entry.get("failure_signature", ""),
        "mechanism_type": entry.get("mechanism_type", ""),
        "summary": summary,
        "experience": {
            "problem_signature": experience.get("problem_signature", {}),
            "general_principles": _bounded_list(experience.get("general_principles"), summary_budget),
            "anti_patterns": _bounded_list(experience.get("anti_patterns"), summary_budget),
            "applicable_conditions": _bounded_list(
                experience.get("applicable_conditions"),
                summary_budget,
            ),
            "negative_conditions": _bounded_list(
                experience.get("negative_conditions"),
                summary_budget,
            ),
            "confidence": experience.get("confidence", "medium"),
        },
        "source_artifact_paths": payload.get("source_artifact_paths", []),
        "experience_ref_path": entry.get("experience_ref_path", ""),
        "stage_experience_path": entry.get("stage_experience_path", ""),
        "truncated": len(str(payload.get("summary", ""))) > summary_budget,
    }


def _allocate_experience_dir(output_root: Path, optimization_type: str) -> Path:
    prefix = _safe_name(optimization_type)
    index = 1
    while True:
        candidate = output_root / f"{prefix}_experience_{index:03d}"
        if not candidate.exists():
            candidate.mkdir(parents=True)
            return candidate
        index += 1


def _write_stage_records(
    experience_dir: Path,
    stage_payloads: list[dict[str, Any]],
) -> list[str]:
    stage_paths: list[str] = []
    stages_dir = experience_dir / "stages"
    stages_dir.mkdir(parents=True, exist_ok=True)
    for index, payload in enumerate(stage_payloads, start=1):
        stage_path = stages_dir / f"{index:03d}_{_safe_name(str(payload.get('stage', 'stage')))}.yaml"
        _write_yaml(stage_path, payload)
        stage_paths.append(str(stage_path))
    return stage_paths


def _read_index(path: Path) -> dict[str, Any]:
    index = _read_yaml(path)
    experiences = index.get("experiences", [])
    if not isinstance(experiences, list):
        experiences = []
    return {"version": int(index.get("version", 1) or 1), "experiences": experiences}


def _write_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        yaml.safe_dump(payload, file, allow_unicode=True, sort_keys=False)


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    with open(path, "r", encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}
    return data if isinstance(data, dict) else {}


def _read_structured(path: Path) -> Any:
    try:
        if path.suffix.lower() == ".json":
            with open(path, "r", encoding="utf-8") as file:
                return json.load(file)
        return _read_yaml(path)
    except (OSError, json.JSONDecodeError, yaml.YAMLError):
        return {}


def _allowed_statuses(query: OptimizationExperienceRetrievalQuery) -> set[str]:
    configured = _string_list(query.metadata.get("learning_statuses"))
    if configured:
        return {status for status in configured if status in _VALID_STATUSES}
    allowed = set(_DEFAULT_RETRIEVAL_STATUSES)
    if bool(query.metadata.get("allow_provisional", False)):
        allowed.add("provisional")
    return allowed


def _sanitize_mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    sanitized: dict[str, Any] = {}
    for key, item in value.items():
        key_text = str(key)
        if key_text.lower() in _SENSITIVE_KEYS:
            continue
        sanitized[key_text] = _sanitize_value(item)
    return sanitized


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, dict):
        return _sanitize_mapping(value)
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, str) and ("sk-" in value or "Bearer " in value):
        return "[redacted]"
    return value


def _merge_dicts(items: list[dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for item in items:
        for key, value in item.items():
            if key not in merged or not merged[key]:
                merged[key] = value
    return merged


def _first_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                return item
    return {}


def _first_text(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item).strip()]
    return []


def _status_value(value: Any, *, default: str) -> str:
    status = str(value or default).strip()
    if status not in _VALID_STATUSES:
        return default
    return status


def _safe_name(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value.strip())
    return cleaned or "default"


def _confidence_score(value: str) -> int:
    return {"high": 3, "medium": 2, "low": 1}.get(value, 0)


def _truncate(value: str, limit: int) -> str:
    if limit <= 0:
        return ""
    if len(value) <= limit:
        return value
    return value[: max(limit - 15, 0)] + "...[truncated]"


def _bounded_list(value: Any, limit: int) -> list[str]:
    remaining = max(limit, 0)
    result: list[str] = []
    for item in _string_list(value):
        if remaining <= 0:
            break
        text = _truncate(item, remaining)
        result.append(text)
        remaining -= len(text)
    return result


__all__ = [
    "ExperienceExtractor",
    "ExperienceRetriever",
    "ExperienceStore",
    "OptimizationExperienceLearner",
]
