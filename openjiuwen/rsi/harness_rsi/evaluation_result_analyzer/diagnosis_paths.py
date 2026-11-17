# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Budget fixed diagnosis files before copying evidence or calling a model."""

import os
from pathlib import PurePath

_IS_WINDOWS = os.name == "nt"
_WINDOWS_PATH_BUDGET = 247
_INVOCATION_FILENAME = "invocation-" + "0" * 32 + ".jsonl"
_RUNTIME_FILES = (
    "evidence_manifest.json",
    "runtime_log_search.json",
    "task_clauses.json",
    "execution_history.json",
    "evidence_summary.md",
    "repository_snapshot.json",
    "source_patch.diff",
    "runtime_evidence/runtime_config.json",
    "runtime_evidence/termination.json",
    "runtime_evidence/invocation_001.jsonl",
    "current_harness/index.json",
    "current_harness/role_001/runtime.yaml",
    "current_harness/role_001/prompt_001.json",
    "current_harness/role_001/skill_001.md",
    "current_harness/role_001/tools_001.py",
    "current_harness/role_001/rails_001.py",
)
_ARCHIVE_FILES = (
    "attempt.json",
    "evidence/evidence_manifest.json",
    "evidence/runtime_log_search.json",
    "evidence/task_clauses.json",
    "evidence/runtime_evidence/runtime_config.json",
    "evidence/runtime_evidence/termination.json",
    "evidence/runtime_evidence/invocation_001.jsonl",
    "logs/" + _INVOCATION_FILENAME,
)


class DiagnosisPathTooLongError(OSError):
    """A generated diagnosis path exceeds the portable Windows budget."""

    def __init__(self, path: PurePath, path_role: str, length: int, case_id: str, source_stage: str) -> None:
        self.path_role = path_role
        self.length = length
        self.budget = _WINDOWS_PATH_BUDGET
        super().__init__(
            f"Diagnosis {path_role} path too long: {length} UTF-16 units exceeds budget "
            f"{self.budget}; case_id={case_id!r}; stage={source_stage!r}; path={str(path)!r}. "
            "Shorten JIUWENSWARM_DATA_DIR for task artifacts or TEMP/TMP for diagnosis runtime, "
            "then restart the service. Existing task references are not relocated automatically.",
        )


def validate_diagnosis_paths(
    runtime_dir: PurePath,
    attempt_dir: PurePath,
    *,
    case_id: str = "",
    source_stage: str = "",
) -> None:
    """Check fixed generated files, excluding arbitrary repository/package trees.

    Callers provide absolute paths. UTF-16 counts supplementary characters as
    two units, matching Windows paths rather than Python's code-point count.
    The budget is deliberately conservative even on long-path-aware hosts.
    """
    if not _IS_WINDOWS:
        return
    for path_role, root, filenames in (
        ("runtime", runtime_dir, _RUNTIME_FILES),
        ("archive", attempt_dir, _ARCHIVE_FILES),
    ):
        paths = [root, *(root / filename for filename in filenames)]
        # Report the limiting file rather than a shorter parent that also fails.
        path = max(paths, key=lambda item: len(str(item).encode("utf-16-le")))
        length = len(str(path).encode("utf-16-le")) // 2
        if length > _WINDOWS_PATH_BUDGET:
            raise DiagnosisPathTooLongError(path, path_role, length, case_id, source_stage)
