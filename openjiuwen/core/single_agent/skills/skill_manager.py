# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Dict, Optional, Union, List

from pathlib import Path
import yaml
from pydantic import BaseModel



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

    def asdict(self, include_directory: bool = True):
        skill_dict = {
            'name': self.name,
            'description': self.description,
        }
        if (include_directory):
            skill_dict['directory'] = str(self.directory)
        return skill_dict

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
        self.description = ""

    def set_sys_operation_id(self, sys_operation_id: str) -> None:
        self._sys_operation_id = sys_operation_id

    async def _load_yaml(self, path: Path, session_id: str):
        from openjiuwen.core.runner.runner import Runner
        sys_operation = Runner.resource_mgr.get_sys_operation(self._sys_operation_id)

        result = await sys_operation.fs().read_file(str(path), mode="text", encoding="utf-8")

        if result.code != 0:
            raise FileNotFoundError(result.message)

        content = result.data.content if result.data is not None else None
        if content is None:
            raise FileNotFoundError(f"read_file is None：{path}")

        text = content if isinstance(content, str) else str(content)

        if text.startswith("---"):
            _, yaml_block, body = text.split("---", 2)
            return yaml.safe_load(yaml_block), body.lstrip()
        return None, text

    async def _load_description(self, path: Path, session_id: str) -> str:
        """Load the description from a skill file's YAML front matter.

        Args:
            path: The path to the skill file (typically Skill.md).
            session_id: The session ID for file operations.

        Returns:
            The description string from the YAML front matter.

        Raises:
            KeyError: If the file does not contain a description field in the YAML front matter.
        """
        self.description = ""
        yaml_data, _ = await self._load_yaml(path, session_id)
        if yaml_data is None or "description" not in yaml_data:
            raise KeyError("Skill.md file does not contain a description field")
        return yaml_data['description']

    async def _create_skill_from_path(self, path: Path, session_id: str) -> Optional[Skill]:
        """Create a Skill object from a file path.

        Args:
            path: The path to the skill directory or file.
            session_id: The session ID for file operations.

        Returns:
            A Skill object if the description is successfully loaded, None otherwise.
        """
        description = await self._load_description(path, session_id)
        if description is not None:
            skill_dir = path.parent
            return Skill(name=skill_dir.name, description=description, directory=skill_dir)
        return None

    @staticmethod
    def _find_skill_md(file_items) -> tuple[bool, Optional[str]]:
        """Return (found, path) for the first skill.md entry in file_items."""
        for f in file_items:
            if f.name.lower() == "skill.md":
                return True, f.path
        return False, None

    async def _add_to_registry(self, skill: Skill, overwrite: bool) -> None:
        """Add a skill to the registry, raising ValueError on duplicate when overwrite is False."""
        if (not overwrite) and (skill.name in self._registry):
            raise ValueError(f"Skill already exists: {skill.name}")
        self._registry[skill.name] = skill

    async def _register_skill_from_md(self, skill_md_path: Optional[str], session_id: str, overwrite: bool) -> None:
        """Register a skill from a skill.md path; no-op if path is falsy or skill cannot be loaded."""
        if not skill_md_path:
            return
        skill = await self._create_skill_from_path(Path(str(skill_md_path)), session_id)
        if skill is not None:
            await self._add_to_registry(skill, overwrite)

    async def _try_register_dir_as_skill(self, fs, dir_path: str, session_id: str, overwrite: bool) -> bool:
        """Attempt to register dir as a skill directory.

        Returns True if a skill.md file was found (registration may still be skipped
        if the path is invalid or the skill cannot be loaded).
        """
        files_res = await fs.list_files(dir_path, recursive=False)
        if files_res.code != 0:
            return False
        file_items = files_res.data.list_items if files_res.data is not None else None
        if not file_items:
            return False
        found, skill_md_path = self._find_skill_md(file_items)
        if not found:
            return False
        await self._register_skill_from_md(skill_md_path, session_id, overwrite)
        return True

    async def register(
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
        from openjiuwen.core.runner.runner import Runner
        sys_operation = Runner.resource_mgr.get_sys_operation(self._sys_operation_id)
        fs = sys_operation.fs()

        async def _register_root(root: Path):
            dirs_res = await fs.list_directories(str(root), recursive=False)
            if dirs_res.code != 0:
                # root is not a directory — treat it as a direct skill.md file path
                skill = await self._create_skill_from_path(root, session_id)
                if skill is not None:
                    await self._add_to_registry(skill, overwrite)
                return

            dir_items = dirs_res.data.list_items if dirs_res.data is not None else None

            # Check if root itself is a skill directory (directly contains skill.md).
            # This supports passing the skill directory instead of its parent.
            if await self._try_register_dir_as_skill(fs, str(root), session_id, overwrite):
                return

            # root is a parent directory — iterate subdirectories for multiple skills
            if not dir_items:
                return
            for d in dir_items:
                if not d.path or not d.name:
                    continue
                await self._try_register_dir_as_skill(fs, str(d.path), session_id, overwrite)

        if skill_path is not None and isinstance(skill_path, Path):
            await _register_root(skill_path)

        if skill_path is not None and isinstance(skill_path, list):
            for p in skill_path:
                await _register_root(p)

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
