"""Skill registry validation for graph construction."""

from __future__ import annotations

from typing import Any, Iterable, List

from openjiuwen.symphony.orchestration.graph.models import GraphDiagnostic, SkillRegistry
from openjiuwen.symphony.shared.fingerprint import (
    CapabilityFingerprint,
    Fingerprint,
    coerce_fingerprint,
)


class SkillRegistryBuilder:
    """Register normalized Skill fingerprints by stable ID."""

    @staticmethod
    def register(
        fingerprints: Iterable[Fingerprint | CapabilityFingerprint | dict[str, Any]],
    ) -> SkillRegistry:
        skills = {}
        diagnostics: List[GraphDiagnostic] = []
        normalized = [coerce_fingerprint(item) for item in fingerprints]

        for fingerprint in sorted(normalized, key=lambda item: item.id):
            if fingerprint.type not in {"skill", "subagent", "agent"}:
                diagnostics.append(
                    GraphDiagnostic(
                        stage="registry",
                        severity="error",
                        code="unsupported_fingerprint_type",
                        message=("Capability Graph accepts skill, subagent, or agent fingerprints."),
                        details={"type": fingerprint.type, "id": fingerprint.id},
                    )
                )
                continue

            if not fingerprint.id:
                diagnostics.append(
                    GraphDiagnostic(
                        stage="registry",
                        severity="error",
                        code="missing_skill_id",
                        message="Skill fingerprint is missing an id.",
                        details={"skill": fingerprint.to_dict()},
                    )
                )
                continue

            if fingerprint.id in skills:
                diagnostics.append(
                    GraphDiagnostic(
                        stage="registry",
                        severity="error",
                        code="duplicate_skill_id",
                        message=f"Duplicate Skill id: {fingerprint.id}",
                        skill_id=fingerprint.id,
                    )
                )
                continue

            if not fingerprint.outputs:
                diagnostics.append(
                    GraphDiagnostic(
                        stage="registry",
                        severity="warning",
                        code="missing_outputs",
                        message="Skill has no declared outputs.",
                        skill_id=fingerprint.id,
                    )
                )

            skills[fingerprint.id] = fingerprint

        return SkillRegistry(skills=skills, diagnostics=diagnostics)
