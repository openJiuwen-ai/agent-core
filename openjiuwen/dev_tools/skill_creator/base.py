#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Skill Creator Base Data Structures
"""

import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, List

import yaml


@dataclass
class SkillMetadata:
    """Skill metadata"""
    name: str
    description: str

    def to_frontmatter(self) -> str:
        """Convert to YAML frontmatter"""
        data = {"name": self.name, "description": self.description}
        return yaml.dump(data, allow_unicode=True, default_flow_style=False)


@dataclass
class SkillContent:
    """Skill complete content"""
    metadata: SkillMetadata
    body: str
    path: Optional[Path] = None

    @classmethod
    def from_path(cls, skill_path: Path) -> "SkillContent":
        """Load Skill from directory"""
        skill_path = Path(skill_path)
        skill_md = skill_path / "SKILL.md"

        if not skill_md.exists():
            raise FileNotFoundError(f"SKILL.md not found in {skill_path}")

        content = skill_md.read_text(encoding="utf-8")
        metadata, body = cls._parse_skill_md(content)

        return cls(metadata=metadata, body=body, path=skill_path)

    @staticmethod
    def _parse_skill_md(content: str) -> tuple:
        """Parse SKILL.md"""
        if not content.startswith("---"):
            raise ValueError("No YAML frontmatter found")

        match = re.match(r'^---\n(.*?)\n---\n?(.*)', content, re.DOTALL)
        if not match:
            raise ValueError("Invalid frontmatter format")

        frontmatter = yaml.safe_load(match.group(1))
        body = match.group(2).strip()

        metadata = SkillMetadata(
            name=frontmatter.get("name", ""),
            description=frontmatter.get("description", ""),
        )
        return metadata, body

    def to_skill_md(self) -> str:
        """Convert to SKILL.md content"""
        frontmatter = self.metadata.to_frontmatter()
        return f"---\n{frontmatter}---\n\n{self.body}"


@dataclass
class SkillOptimizationResult:
    """Optimization result"""
    original: SkillContent
    optimized: SkillContent
    changes: List[str] = field(default_factory=list)
