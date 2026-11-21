# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""VerificationMemory — persists verification results to team memory.

Stores results in TEAM_MEMORY.md under a dedicated section, enabling:
- Accountability: track which tasks passed/failed review
- Continuous improvement: identify recurring quality issues
- Leader awareness: inform consolidation decisions with quality data
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .result import VerificationResult

logger = logging.getLogger(__name__)

_VERIFICATION_SECTION_HEADER = "## Verification History\n\n"
_VERIFICATION_ENTRY_TEMPLATE = (
    "### {task_id} — {status}\n"
    "- **Task**: {task_title}\n"
    "- **Assignee**: {assignee}\n"
    "- **Score**: {score}/100\n"
    "- **Verified At**: {verified_at}\n"
    "- **Reviewer**: {reviewer_model}\n"
    "- **Summary**: {summary}\n"
    "{rework_block}"
    "\n"
)

_REWORK_TEMPLATE = (
    "- **Rework Instructions**: {rework_instructions}\n"
)


class VerificationMemory:
    """Manages persistence of verification results to team memory files."""

    def __init__(self, team_workspace_root: str | None = None) -> None:
        self._workspace_root = team_workspace_root

    def _resolve_memory_path(self) -> Path | None:
        """Resolve the path to TEAM_MEMORY.md."""
        if not self._workspace_root:
            return None
        path = Path(self._workspace_root) / "TEAM_MEMORY.md"
        return path

    def store(self, result: VerificationResult) -> bool:
        """Store a verification result in TEAM_MEMORY.md.

        Creates the file and section if they don't exist.
        Appends the entry to the verification history section.

        Args:
            result: The verification result to store

        Returns:
            True if stored successfully, False otherwise
        """
        path = self._resolve_memory_path()
        if path is None:
            logger.debug("[VerificationMemory] No workspace root, skipping persistence")
            return False

        try:
            path.parent.mkdir(parents=True, exist_ok=True)

            content = ""
            if path.exists():
                content = path.read_text(encoding="utf-8")

            entry = self._format_entry(result)

            if _VERIFICATION_SECTION_HEADER.strip() in content:
                # Append to existing section
                content = content + entry
            else:
                # Create new section at end of file
                if content and not content.endswith("\n\n"):
                    content = content.rstrip() + "\n\n"
                content = content + _VERIFICATION_SECTION_HEADER + entry

            path.write_text(content, encoding="utf-8")
            logger.info(
                "[VerificationMemory] Stored result for task=%s status=%s",
                result.task_id,
                result.status.value,
            )
            return True
        except Exception as exc:
            logger.warning(
                "[VerificationMemory] Failed to store result for task=%s: %s",
                result.task_id,
                exc,
            )
            return False

    def load_history(self, limit: int = 50) -> list[VerificationResult]:
        """Load verification history from TEAM_MEMORY.md.

        Args:
            limit: Maximum number of entries to return

        Returns:
            List of VerificationResult objects
        """
        path = self._resolve_memory_path()
        if path is None or not path.exists():
            return []

        try:
            content = path.read_text(encoding="utf-8")
            # Extract the verification section
            section_start = content.find("## Verification History")
            if section_start == -1:
                return []

            section_end = content.find("\n## ", section_start + 1)
            if section_end == -1:
                section = content[section_start:]
            else:
                section = content[section_start:section_end]

            # Parse all JSON blocks in the section
            results = []
            lines = section.splitlines()
            i = 0
            while i < len(lines) and len(results) < limit:
                if lines[i].strip().startswith("```json"):
                    json_lines = []
                    i += 1
                    while i < len(lines) and lines[i].strip() != "```":
                        json_lines.append(lines[i])
                        i += 1
                    try:
                        data = json.loads("\n".join(json_lines))
                        results.append(VerificationResult.from_dict(data))
                    except Exception as exc:
                        logger.debug(
                            "[VerificationMemory] Failed to parse JSON block: %s",
                            exc,
                        )
                else:
                    i += 1

            return results
        except Exception as exc:
            logger.warning("[VerificationMemory] Failed to load history: %s", exc)
            return []

    def get_quality_trends(self) -> dict[str, Any]:
        """Compute quality trends from stored verification history.

        Returns:
            Dict with pass_rate, avg_score, common_issues, etc.
        """
        history = self.load_history(limit=100)
        if not history:
            return {"pass_rate": 0.0, "avg_score": 0.0, "total": 0}

        total = len(history)
        passed = sum(1 for h in history if h.status.value == "pass")
        avg_score = sum(h.overall_score for h in history) / total

        # Collect common low-scoring dimensions
        dim_scores: dict[str, list[int]] = {}
        for h in history:
            for d in h.dimensions:
                dim_scores.setdefault(d.dimension.value, []).append(d.score)

        weak_dimensions = [
            {"dimension": dim, "avg_score": sum(scores) / len(scores)}
            for dim, scores in dim_scores.items()
            if sum(scores) / len(scores) < 60
        ]
        weak_dimensions.sort(key=lambda x: x["avg_score"])

        return {
            "pass_rate": passed / total,
            "avg_score": avg_score,
            "total": total,
            "weak_dimensions": weak_dimensions,
        }

    @staticmethod
    def _format_entry(result: VerificationResult) -> str:
        """Format a verification result as a markdown entry."""
        rework_block = ""
        if result.rework_instructions:
            rework_block = _REWORK_TEMPLATE.format(
                rework_instructions=result.rework_instructions
            )

        entry = _VERIFICATION_ENTRY_TEMPLATE.format(
            task_id=result.task_id,
            status=result.status.value.upper(),
            task_title=result.task_title,
            assignee=result.assignee,
            score=result.overall_score,
            verified_at=result.verified_at,
            reviewer_model=result.reviewer_model,
            summary=result.summary,
            rework_block=rework_block,
        )

        # Also embed the full JSON for structured retrieval
        json_block = (
            "\n<details>\n<summary>Raw Data</summary>\n\n"
            f"```json\n{json.dumps(result.to_dict(), ensure_ascii=False, indent=2)}\n```\n"
            "</details>\n\n"
        )
        return entry + json_block
