# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Dict, Optional, Union, List
from pathlib import Path

import yaml
from pydantic import BaseModel

from openjiuwen.core.runner import Runner


class Skill(BaseModel):
    """Represents a skill with its metadata.
    
    Attributes:
        name: The name of the skill.
        description: The description of the skill.
        directory: The directory path where the skill is located.
    """
    name: str
    description: str = None
    directory: Path

    def __str__(self):
        return f"Skill: {self.name}\nDescription: {self.description}\nDirectory: {self.directory}"

    def __repr__(self):
        return (f"[Skill: {self.name} / Description: {self.description[:min(len(self.description), 30)] + '...'} "
                f"/ Directory: {self.directory}]")


class SkillManager:
    """Manages skill registration and retrieval.
    
    This class maintains a registry of skills and provides methods to register,
    unregister, and query skills. Skills are loaded from YAML files containing
    metadata such as name and description.
    """

    def __init__(
            self,
            sys_operation_id: str
    ):
        """Initialize the skill registry.
        
        Args:
            env: The environment type, either "local" or "sandbox". Defaults to "sandbox".
        """
        self._registry: Dict[str, Skill] = {}
        self._sys_operation_id = sys_operation_id

    def _load_yaml(self, path: Path, session_id: str):
        """Load and parse YAML front matter from a file.
        
        Args:
            path: The file path to read.
            session_id: The session ID for file operations.
            
        Returns:
            A tuple of (yaml_data, body) where yaml_data is the parsed YAML dict
            or None if no YAML front matter exists, and body is the remaining text.
        """
        sys_operation = Runner().resource_mgr.get_sys_operation(self._sys_operation_id)
        text = sys_operation.code().read_file(str(Path))
        if text.startswith("---"):
            _, yaml_block, body = text.split("---", 2)
            return yaml.safe_load(yaml_block), body.lstrip()
        return None, text

    def _load_description(self, path: Path, session_id: str) -> str:
        """Load the description from a skill file's YAML front matter.
        
        Args:
            path: The path to the skill file (typically Skill.md).
            session_id: The session ID for file operations.
            
        Returns:
            The description string from the YAML front matter.
            
        Raises:
            KeyError: If the file does not contain a description field in the YAML front matter.
        """
        yaml_data, _ = self._load_yaml(path, session_id)
        if yaml_data is None or "description" not in yaml_data:
            raise KeyError("Skill.md file does not contain a description field")
        return yaml_data['description']

    def _create_skill_from_path(self, path: Path, session_id: str) -> Optional[Skill]:
        """Create a Skill object from a file path.
        
        Args:
            path: The path to the skill directory or file.
            session_id: The session ID for file operations.
            
        Returns:
            A Skill object if the description is successfully loaded, None otherwise.
        """
        description = self._load_description(path, session_id)
        if description is not None:
            return Skill(name=path.name, description=description, directory=path)
        return None

    def register(
            self,
            skill_path: Union[Path, List[Path]],
            session_id: str = None,
            overwrite: bool = False
    ):
        """Register skill metadata.

        Args:
            skill_path: The path(s) to the skill(s) to register. Can be a single Path
                or a list of Paths.
            session_id: The session ID for file operations.
            overwrite: If True, overwrite existing skill when it already exists;
                otherwise raise an exception.

        Raises:
            ValueError: If skill already exists and overwrite is False.
        """
        if skill_path is not None and isinstance(skill_path, Path):
            skill = self._create_skill_from_path(skill_path, session_id)
            self._registry[skill.name] = skill
        if skill_path is not None and isinstance(skill_path, list):
            for p in skill_path:
                skill = self._create_skill_from_path(p, session_id)
                self._registry[skill.name] = skill

    def unregister(self, name: str):
        """Unregister a skill.

        Args:
            name: The name of the skill to unregister.

        Returns:
            bool: True if successfully unregistered, False otherwise.
        """
        if name in self._registry:
            del self._registry[name]

    def get(self, name: str) -> Optional[Skill]:
        """Get skill metadata by name.

        Args:
            name: The name of the skill.

        Returns:
            Optional[Skill]: The skill object if found, None otherwise.
        """
        if name in self._registry:
            return self._registry[name]
        return None

    def get_all(self) -> List[Skill]:
        """Get all registered skill metadata.

        Returns:
            List[Skill]: A list of all registered skill objects.
        """
        return list(self._registry.values())

    def get_names(self) -> List[str]:
        """Get all registered skill names.

        Returns:
            List[str]: A list of all registered skill names.
        """
        return list(self._registry.keys())

    def has(self, name: str) -> bool:
        """Check if a skill is registered.

        Args:
            name: The name of the skill to check.

        Returns:
            bool: True if the skill is registered, False otherwise.
        """
        return name in self._registry

    def clear(self) -> None:
        """Clear all registered skills from the registry."""
        self._registry.clear()

    def count(self) -> int:
        """Get the number of registered skills.

        Returns:
            int: The number of registered skills.
        """
        return len(self._registry)
