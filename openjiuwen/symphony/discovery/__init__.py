"""Structured live capability discovery and Skill directory browsing."""

from .capability_search import CapabilityAction, CapabilityCandidate, read_fingerprints, search_capabilities

from .directory_toolkit import InstalledSkillsDirectoryToolkit
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
    CAPABILITY_SEARCH_TOOL_NAME,
    SkillDCICommandResult,
    clear_incremental_skill_notice_states,
    consume_incremental_skill_reminder,
    initialize_incremental_skill_notice_state,
)

__all__ = [
    "CAPABILITY_SEARCH_TOOL_NAME",
    "CapabilityAction",
    "CapabilityCandidate",
    "InstalledSkillsDirectoryToolkit",
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
