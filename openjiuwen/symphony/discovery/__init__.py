"""Structured live Skill discovery built on Symphony's retriever tree."""

from .config import DiscoverySettings, load_discovery_settings
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
    "DiscoverySettings",
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
    "load_discovery_settings",
    "scan_skill_directories",
]
