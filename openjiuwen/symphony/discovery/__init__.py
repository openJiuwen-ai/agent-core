"""Structured live capability discovery and Skill directory browsing."""

from .capability_search import CapabilityAction, CapabilityCandidate, read_fingerprints, search_capabilities

from .directory_toolkit import InstalledSkillsDirectoryToolkit, SKILL_INDEX_TOOL_NAME
from .models import SkillInventory, SkillRecord, inventory_from_records, scan_skill_directories
from .skillfs import (
    SkillFS,
    SkillFSArtifact,
    SkillIndexSnapshot,
    SkillPromptBranch,
    SkillPromptEntry,
    SkillPromptSnapshot,
    SkillRecordsProvider,
    capture_index_snapshot,
)
from .toolkit import (
    SkillDCICommandResult,
    clear_incremental_skill_notice_states,
    consume_incremental_skill_reminder,
    initialize_incremental_skill_notice_state,
)

__all__ = [
    "CapabilityAction",
    "CapabilityCandidate",
    "InstalledSkillsDirectoryToolkit",
    "SKILL_INDEX_TOOL_NAME",
    "SkillDCICommandResult",
    "SkillFS",
    "SkillFSArtifact",
    "SkillIndexSnapshot",
    "SkillInventory",
    "SkillPromptBranch",
    "SkillPromptEntry",
    "SkillPromptSnapshot",
    "SkillRecord",
    "SkillRecordsProvider",
    "capture_index_snapshot",
    "clear_incremental_skill_notice_states",
    "consume_incremental_skill_reminder",
    "initialize_incremental_skill_notice_state",
    "inventory_from_records",
    "read_fingerprints",
    "scan_skill_directories",
    "search_capabilities",
]
